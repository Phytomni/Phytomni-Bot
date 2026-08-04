# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Review conversation adapter orchestration and settlement state."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from ...runtime.conversation_context.models import (
    MAX_CONTEXT_TEXT_CHARS,
    ArtifactRefV1,
    ContextDelta,
    ContextProjection,
    PerAgentMemory,
)
from .conversation import (
    ReviewCheckpointSnapshot,
    ReviewClarificationError,
    ReviewConversationOperation,
    ReviewReportDocument,
    ReviewSection,
    RevisedSection,
    _RestoredReviewCheckpoint,
    classify_review_operation,
    extract_review_checkpoint,
    extract_review_report_document,
    reassemble_report,
    revise_section,
)
from .conversation_checkpoint import (
    _bounded_text,
    _candidate_thread_id,
    _invoke_checkpoint_deleter,
    _invoke_checkpoint_updater,
    _load_review_checkpoint_state,
    _report_document_from_text,
    _required_turn_id,
    _state_values,
)
from .conversation_settlement import (
    _load_restored_candidate_checkpoint,
    _load_restored_stable_checkpoint,
    _parse_review_settlement_metadata,
)
from .conversation_turn import (
    _answer_from_result,
    _evidence_summary,
    _follow_up_questions,
    _projection_has_active_review,
    _prompt_context,
    _reference_metadata,
    _replace_section,
    _response_text,
    _review_summary,
    _section_reference,
    _staged_scope_snapshot,
    _usable_report_document,
    _usable_response_text,
)

_MAX_PROMPT_CHARS = MAX_CONTEXT_TEXT_CHARS
_MAX_SUMMARY_CHARS = 1024

ChatSeam = Callable[[str], Awaitable[Mapping[str, Any] | str | None]]


@dataclass(frozen=True, slots=True)
class _PreparedReviewTurn:
    projection: ContextProjection
    operation: ReviewConversationOperation
    snapshot: ReviewCheckpointSnapshot | None
    section: ReviewSection | None


@dataclass(slots=True)
class _ReviewCheckpointState:
    """Mutable checkpoint state owned by one Review adapter."""

    prepared: _PreparedReviewTurn | None = None
    active_snapshot: ReviewCheckpointSnapshot | None = None
    staged_snapshot: ReviewCheckpointSnapshot | None = None
    report_revision: int = 0
    settled: bool = False
    operation_successful: bool = False
    report_document: ReviewReportDocument | None = None


@dataclass(slots=True)
class _ReviewResultState:
    """Mutable result and cleanup state owned by one Review adapter."""

    last_revised_section: RevisedSection | None = None
    captured_result: dict[str, Any] = field(default_factory=dict)
    candidate_discarded: bool = False
    pending_report_text: str | None = None
    ordered_doc_list: list[dict[str, Any]] = field(default_factory=list)
    settlement_fence: Callable[[], bool] | None = None


@dataclass(slots=True)
class _ReviewAdapterState:
    """Mutable report and settlement state owned by one Review adapter."""

    checkpoint: _ReviewCheckpointState = field(
        default_factory=_ReviewCheckpointState
    )
    result: _ReviewResultState = field(default_factory=_ReviewResultState)


class _ReviewAdapterProperties:
    """Expose stable read-only Review state independently of orchestration."""

    _state: _ReviewAdapterState
    _stable_thread_id: str | None
    _candidate_thread_id: str | None
    _execution_thread_id: str | None
    _agent: Any | None

    @property
    def operation(self) -> ReviewConversationOperation | None:
        """Return the prepared private operation, if any."""
        prepared = self._state.checkpoint.prepared
        return prepared.operation if prepared else None

    @property
    def snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return the bounded checkpoint snapshot used for this turn."""
        prepared = self._state.checkpoint.prepared
        return prepared.snapshot if prepared else None

    @property
    def active_snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return the last committed snapshot, excluding staged changes."""
        return self._state.checkpoint.active_snapshot

    @property
    def staged_snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return a candidate snapshot pending successful settlement."""
        return self._state.checkpoint.staged_snapshot

    @property
    def report_revision(self) -> int:
        """Return the last settled report artifact revision."""
        return self._state.checkpoint.report_revision

    @property
    def stable_thread_id(self) -> str | None:
        """Return the active Review checkpoint thread."""
        return self._stable_thread_id

    @property
    def candidate_thread_id(self) -> str | None:
        """Return the isolated checkpoint thread pending acknowledgement."""
        return self._candidate_thread_id

    @property
    def execution_thread_id(self) -> str | None:
        """Return the thread on which this turn may execute its graph."""
        return self._execution_thread_id

    @property
    def settlement_ready(self) -> bool:
        """Return whether this turn produced a candidate for settlement."""
        prepared = self._state.checkpoint.prepared
        if prepared is not None and prepared.operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            return (
                self._state.checkpoint.operation_successful
                and self.candidate_thread_id is not None
                and self._agent is not None
            )
        return self._state.checkpoint.operation_successful


class ReviewConversationAdapter(_ReviewAdapterProperties):
    """Prepare bounded Review turns and produce bounded context deltas."""

    def __init__(self) -> None:
        self._state = _ReviewAdapterState()
        self._agent: Any | None = None
        self._thread_id: str | None = None
        self._stable_thread_id: str | None = None
        self._execution_thread_id: str | None = None
        self._candidate_thread_id: str | None = None
        self._turn_id: str | None = None

    def set_settlement_fence(self, fence: Callable[[], bool]) -> None:
        """Install the durable claim check used immediately before writes."""
        self._state.result.settlement_fence = fence

    def attach_agent(self, agent: Any) -> None:
        """Attach the graph agent used by durable settlement operations."""
        self._agent = agent

    def _check_settlement_fence(self) -> None:
        """Fail closed when tombstone or another worker revoked the claim."""
        fence = self._state.result.settlement_fence
        if fence is not None and not fence():
            raise RuntimeError("Review settlement claim was fenced")

    def prepare(
        self,
        projection: ContextProjection,
        **options: Any,
    ) -> dict[str, Any]:
        """Classify and project one Review turn."""
        snapshot = options.pop("snapshot", None)
        allow_unresolved_section = options.pop(
            "allow_unresolved_section", False
        )
        report_document = options.pop("report_document", None)
        turn_id = options.pop("turn_id", None)
        if options:
            raise TypeError(
                "unexpected review preparation options: "
                + ", ".join(sorted(options))
            )
        validated_turn_id = _required_turn_id(turn_id)
        self._stable_thread_id = projection.agent_thread_id
        self._thread_id = self._stable_thread_id
        self._turn_id = validated_turn_id
        self._state.checkpoint.report_document = None
        self._state.result.ordered_doc_list = []
        if snapshot is not None and not isinstance(
            snapshot, ReviewCheckpointSnapshot
        ):
            self._state.result.ordered_doc_list = _reference_metadata(snapshot)
            report_document = extract_review_report_document(snapshot)
            snapshot = extract_review_checkpoint(snapshot)
        elif report_document is None and self._agent is None:
            self._state.checkpoint.report_document = None
        if report_document is not None:
            self._state.checkpoint.report_document = report_document
        operation = classify_review_operation(
            projection.current_query,
            projection=projection,
            snapshot=snapshot,
        )
        section: ReviewSection | None = None
        section_id = _section_reference(projection.current_query, snapshot)
        if operation is ReviewConversationOperation.LOCAL_REVISION:
            if (
                snapshot is None and not allow_unresolved_section
            ) or section_id is None:
                raise ReviewClarificationError(
                    "Please name an active Review section to revise."
                )
            if snapshot is not None:
                section = snapshot.section(section_id)
            if snapshot is not None and section is None:
                raise ReviewClarificationError(
                    f"The requested Review section {section_id!r} is unknown."
                )
        self._state.checkpoint.prepared = _PreparedReviewTurn(
            projection=projection,
            operation=operation,
            snapshot=snapshot,
            section=section,
        )
        self._state.checkpoint.active_snapshot = snapshot
        self._state.checkpoint.staged_snapshot = _staged_scope_snapshot(
            projection, snapshot, operation
        )
        self._state.result.last_revised_section = None
        self._state.result.captured_result = {}
        self._state.checkpoint.report_revision = (
            snapshot.report_revision if snapshot else 0
        )
        self._state.checkpoint.settled = False
        self._state.checkpoint.operation_successful = (
            operation is not ReviewConversationOperation.LOCAL_REVISION
        )
        if operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            assert self._turn_id is not None
            self._candidate_thread_id = _candidate_thread_id(
                self._stable_thread_id, self._turn_id
            )
            self._execution_thread_id = self._candidate_thread_id
        else:
            self._candidate_thread_id = None
            self._execution_thread_id = self._stable_thread_id
        self._state.result.candidate_discarded = False
        self._state.result.pending_report_text = None
        prompt_context = _prompt_context(snapshot, section=section)
        return {
            "user_query": projection.current_query,
            "locale": projection.locale,
            "thread_id": projection.agent_thread_id,
            "operation": operation,
            "review_context": prompt_context,
            "prompt_context": prompt_context,
            "evidence_summary": _evidence_summary(snapshot),
            "section_id": section.section_id if section else section_id,
            "section_text": section.text if section else "",
            "report_artifact_id": (
                snapshot.report_artifact_id if snapshot else None
            ),
            "report_revision": self._state.checkpoint.report_revision,
        }

    async def prepare_from_agent(
        self,
        projection: ContextProjection,
        agent: Any,
        thread_id: str,
        *,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Load the private checkpoint, then prepare against its snapshot."""
        validated_turn_id = _required_turn_id(turn_id)
        del thread_id
        stable_thread_id = projection.agent_thread_id
        self._agent = agent
        self._stable_thread_id = stable_thread_id
        self._thread_id = stable_thread_id
        state = await _load_review_checkpoint_state(agent, stable_thread_id)
        snapshot = extract_review_checkpoint(state)
        document = extract_review_report_document(state)
        if snapshot is None and (
            document is not None or _projection_has_active_review(projection)
        ):
            raise ReviewClarificationError(
                "The active Review checkpoint is unavailable."
            )
        prepared = self.prepare(
            projection,
            snapshot=snapshot,
            report_document=document,
            turn_id=validated_turn_id,
        )
        ordered = _reference_metadata(state)
        if ordered:
            self._state.result.ordered_doc_list = ordered
        return prepared

    def settlement_metadata(self) -> dict[str, Any] | None:
        """Return bounded private metadata persisted with one staged turn."""
        operation = self.operation
        if not self.settlement_ready or operation is None:
            return None
        if not self.stable_thread_id or not self._turn_id:
            return None
        candidate = self.candidate_thread_id
        if (
            operation
            in {
                ReviewConversationOperation.NEW_REVIEW,
                ReviewConversationOperation.SCOPE_CHANGE,
            }
            and not candidate
        ):
            return None
        return {
            "version": 1,
            "operation": operation.value,
            "stable_thread_id": self.stable_thread_id[:512],
            "candidate_thread_id": candidate[:512] if candidate else None,
            "turn_id": self._turn_id[:64],
            "report_revision": self._state.checkpoint.report_revision,
            "settlement_state": "pending",
        }

    async def _validate_candidate_settlement(self) -> bool:
        """Validate and retain a new-review candidate checkpoint."""
        if self.candidate_thread_id is None:
            self.mark_failed()
            return False
        state = await _load_review_checkpoint_state(
            self._agent, self.candidate_thread_id
        )
        values = _state_values(state)
        snapshot = extract_review_checkpoint(state)
        document = extract_review_report_document(state)
        if (
            bool(values)
            and snapshot is not None
            and not _usable_report_document(document)
            and await self._persist_captured_candidate_report()
        ):
            state = await _load_review_checkpoint_state(
                self._agent, self.candidate_thread_id
            )
            values = _state_values(state)
            snapshot = extract_review_checkpoint(state)
            document = extract_review_report_document(state)
        candidate_ready = (
            bool(values)
            and snapshot is not None
            and document is not None
            and _usable_report_document(document)
        )
        if not candidate_ready:
            self.mark_failed()
            return False
        assert snapshot is not None
        assert document is not None
        self._state.checkpoint.staged_snapshot = snapshot
        self._state.checkpoint.report_document = document
        self._state.result.pending_report_text = document.text
        return True

    async def _persist_captured_candidate_report(self) -> bool:
        """Repair a lagging candidate checkpoint from this turn's result."""
        report = self._state.result.pending_report_text
        app = getattr(self._agent, "app", None)
        updater = getattr(app, "aupdate_state", None)
        if (
            report is None
            or not _usable_response_text(report)
            or not callable(updater)
            or self.candidate_thread_id is None
        ):
            return False
        self._check_settlement_fence()
        typed_updater = cast(Callable[..., Awaitable[Any]], updater)
        await _invoke_checkpoint_updater(
            typed_updater,
            self.candidate_thread_id,
            {"summary_content": report},
        )
        return True

    async def _validate_active_settlement(
        self,
        operation: ReviewConversationOperation | None,
    ) -> bool:
        """Validate the active checkpoint for follow-up or local revision."""
        if self.stable_thread_id is None:
            self.mark_failed()
            return False
        state = await _load_review_checkpoint_state(
            self._agent, self.stable_thread_id
        )
        snapshot = extract_review_checkpoint(state)
        document = extract_review_report_document(state)
        if snapshot is None or not _usable_report_document(document):
            self.mark_failed()
            return False
        self._state.checkpoint.active_snapshot = snapshot
        self._state.checkpoint.report_document = document
        return operation in {
            ReviewConversationOperation.FOLLOW_UP,
            ReviewConversationOperation.LOCAL_REVISION,
        }

    async def validate_settlement_candidate(self) -> bool:
        """Verify the current private candidate before context staging."""
        if not self.settlement_ready or self._agent is None:
            return False
        operation = self.operation
        if operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            return await self._validate_candidate_settlement()
        return await self._validate_active_settlement(operation)

    async def restore_settlement(
        self,
        metadata: Mapping[str, Any],
        agent: Any,
        result: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> None:
        """Reconstruct a pending turn from durable metadata after restart."""
        expected_stable_thread_id = options.pop(
            "expected_stable_thread_id", None
        )
        expected_turn_id = options.pop("expected_turn_id", None)
        if options:
            raise TypeError(
                "unexpected review settlement options: "
                + ", ".join(sorted(options))
            )
        restored = _parse_review_settlement_metadata(
            metadata,
            expected_stable_thread_id=expected_stable_thread_id,
            expected_turn_id=expected_turn_id,
        )
        answer = _answer_from_result(result or {})
        if not _usable_response_text(answer):
            raise ReviewClarificationError(
                "Review settlement has no usable current answer."
            )
        self._agent = agent
        self._stable_thread_id = restored.stable_thread_id
        self._thread_id = self._stable_thread_id
        self._candidate_thread_id = restored.candidate_thread_id
        self._execution_thread_id = (
            restored.candidate_thread_id or self._stable_thread_id
        )
        self._turn_id = restored.turn_id
        self._state.checkpoint.report_revision = restored.report_revision
        self._state.checkpoint.settled = False
        self._state.result.candidate_discarded = False
        self._state.checkpoint.operation_successful = True
        stable_checkpoint = await _load_restored_stable_checkpoint(
            agent, restored.operation, self._stable_thread_id
        )
        self._state.checkpoint.active_snapshot = stable_checkpoint.snapshot
        self._state.checkpoint.report_document = stable_checkpoint.document
        candidate_checkpoint: _RestoredReviewCheckpoint | None = None
        if restored.candidate_thread_id is not None:
            candidate_checkpoint = await _load_restored_candidate_checkpoint(
                agent, restored.candidate_thread_id
            )
            self._state.checkpoint.staged_snapshot = (
                candidate_checkpoint.snapshot
            )
            self._state.checkpoint.report_document = (
                candidate_checkpoint.document
            )
            assert candidate_checkpoint.document is not None
            self._state.result.pending_report_text = (
                candidate_checkpoint.document.text
            )
        projection = ContextProjection.model_construct(
            current_query="settlement",
            intent_kind="follow_up",
            task_summary="",
            relevant_recent_turns=[],
            relevant_user_turns=[],
            relevant_assistant_summaries=[],
            active_entities=[],
            open_questions=[],
            artifact_refs=[],
            agent_thread_id=self._stable_thread_id,
            locale="en-US",
            token_budget=1,
            context_truncated=False,
        )
        self._state.checkpoint.prepared = _PreparedReviewTurn(
            projection=projection,
            operation=restored.operation,
            snapshot=stable_checkpoint.snapshot,
            section=None,
        )
        if candidate_checkpoint is None:
            self._state.checkpoint.staged_snapshot = None
            self._state.result.pending_report_text = None
        self._state.result.ordered_doc_list = _reference_metadata(
            candidate_checkpoint.state
            if candidate_checkpoint is not None
            else stable_checkpoint.state
        )
        if restored.operation is ReviewConversationOperation.LOCAL_REVISION:
            self._state.result.pending_report_text = answer
            self._state.checkpoint.report_document = (
                _report_document_from_text(answer)
            )

    def mark_failed(self) -> None:
        """Discard any candidate produced by a failed or incomplete turn."""
        self._state.checkpoint.operation_successful = False
        self._state.checkpoint.staged_snapshot = None
        self._state.result.pending_report_text = None

    def capture_result(self, result: Mapping[str, Any]) -> None:
        """Capture only bounded metadata from a completed Review result."""
        if self._state.checkpoint.prepared is None:
            raise RuntimeError("prepare must run before capture_result")
        if not self._state.checkpoint.operation_successful:
            return
        ordered = _reference_metadata(result)
        if ordered:
            self._state.result.ordered_doc_list = ordered
        answer = _answer_from_result(result)
        captured_answer = (
            _bounded_text(answer, _MAX_SUMMARY_CHARS)
            if self._state.checkpoint.prepared.operation
            is ReviewConversationOperation.FOLLOW_UP
            else ""
        )
        document = extract_review_report_document(result.get("phytomni_state"))
        checkpoint = extract_review_checkpoint(result.get("phytomni_state"))
        if not _usable_response_text(answer):
            self.mark_failed()
            return
        self._state.result.captured_result = {
            "result": {
                "formatted": {
                    "answer": captured_answer,
                    "follow_up_questions": _follow_up_questions(result),
                }
            }
        }
        if document is not None and document.text:
            self._state.checkpoint.report_document = document
            self._state.result.pending_report_text = document.text
        if checkpoint is not None:
            self._state.checkpoint.staged_snapshot = checkpoint
        if self._state.result.last_revised_section is not None:
            base = (
                self._state.checkpoint.active_snapshot
                or self._state.checkpoint.prepared.snapshot
            )
            if base is not None:
                self._state.checkpoint.staged_snapshot = _replace_section(
                    base, self._state.result.last_revised_section
                )
        if (
            self._state.checkpoint.prepared.operation
            is ReviewConversationOperation.LOCAL_REVISION
        ):
            if not _answer_from_result(result):
                self.mark_failed()
            elif self._state.checkpoint.report_document is not None:
                self._state.result.pending_report_text = (
                    self._state.checkpoint.report_document.replace(
                        self._state.result.last_revised_section
                    )
                    if self._state.result.last_revised_section is not None
                    else None
                )

    def settle(self, success: bool) -> int:
        """Advance the report revision only after successful settlement."""
        if not success or not self._state.checkpoint.operation_successful:
            self._state.checkpoint.staged_snapshot = None
            return self._state.checkpoint.report_revision
        if not self._state.checkpoint.settled:
            self._state.checkpoint.report_revision += 1
            if self._state.checkpoint.staged_snapshot is not None:
                self._state.checkpoint.active_snapshot = replace(
                    self._state.checkpoint.staged_snapshot,
                    report_revision=self._state.checkpoint.report_revision,
                )
            self._state.checkpoint.settled = True
        return self._state.checkpoint.report_revision

    async def settle_async(self, success: bool) -> int:
        """Settle the candidate and persist private report state."""
        prepared = self._state.checkpoint.prepared
        if prepared is None:
            raise RuntimeError("prepare must run before settle_async")
        if not success or not self._state.checkpoint.operation_successful:
            return self.settle(False)
        if self._state.checkpoint.settled:
            return self._state.checkpoint.report_revision
        next_revision = self._state.checkpoint.report_revision + 1
        self._check_settlement_fence()
        if prepared.operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            if self.candidate_thread_id is None:
                raise RuntimeError(
                    "Review graph settlement requires a turn-scoped candidate"
                )
            await self._promote_candidate(next_revision)
        else:
            await self._update_stable_checkpoint(next_revision)
        return self.settle(True)

    async def _update_stable_checkpoint(self, revision: int) -> None:
        """Persist a bounded follow-up or revision on the active thread."""
        app = getattr(self._agent, "app", None)
        updater = getattr(app, "aupdate_state", None)
        if not callable(updater) or self.stable_thread_id is None:
            if self._agent is not None:
                raise RuntimeError(
                    "Review stable checkpoint cannot be updated"
                )
            return
        values: dict[str, Any] = {"report_revision": revision}
        if self._state.result.pending_report_text is not None:
            values["summary_content"] = self._state.result.pending_report_text
        self._check_settlement_fence()
        typed_updater = cast(Callable[..., Awaitable[Any]], updater)
        await _invoke_checkpoint_updater(
            typed_updater, self.stable_thread_id, values
        )

    async def _promote_candidate(self, revision: int) -> None:
        """Copy an isolated graph result after acknowledgement."""
        if self._agent is None or self.candidate_thread_id is None:
            raise RuntimeError("Review candidate checkpoint is unavailable")
        if self.stable_thread_id is None:
            raise RuntimeError(
                "Review stable checkpoint thread is unavailable"
            )
        candidate = await _load_review_checkpoint_state(
            self._agent, self.candidate_thread_id
        )
        candidate_values = _state_values(candidate)
        candidate_document = extract_review_report_document(candidate)
        if (
            not candidate_values
            or extract_review_checkpoint(candidate) is None
            or not _usable_report_document(candidate_document)
        ):
            raise RuntimeError("Review candidate checkpoint is not promotable")
        app = getattr(self._agent, "app", None)
        updater = getattr(app, "aupdate_state", None)
        if not callable(updater):
            raise ReviewClarificationError(
                "Review stable checkpoint cannot be promoted."
            )
        values = dict(candidate_values)
        values["report_revision"] = revision
        if self._state.result.pending_report_text is not None:
            values["summary_content"] = self._state.result.pending_report_text
        self._check_settlement_fence()
        typed_updater = cast(Callable[..., Awaitable[Any]], updater)
        await _invoke_checkpoint_updater(
            typed_updater, self.stable_thread_id, values
        )

    async def discard_pending_candidate(self) -> None:
        """Delete an unacknowledged candidate without touching active state."""
        if (
            self._state.result.candidate_discarded
            or self._agent is None
            or self.candidate_thread_id is None
        ):
            return
        app = getattr(self._agent, "app", None)
        deleter = getattr(app, "adelete_thread", None)
        if not callable(deleter):
            checkpointer = getattr(self._agent, "checkpointer", None)
            deleter = getattr(checkpointer, "adelete_thread", None)
        if callable(deleter):
            result = _invoke_checkpoint_deleter(
                deleter, self.candidate_thread_id
            )
            if inspect.isawaitable(result):
                await result
        self._state.result.candidate_discarded = True

    def follow_up_prompt(self) -> str:
        """Build the bounded prompt for an existing-claim follow-up."""
        if self._state.checkpoint.prepared is None:
            raise RuntimeError("prepare must run before follow_up_prompt")
        context = _prompt_context(self._state.checkpoint.prepared.snapshot)
        return (
            "Answer the current Review follow-up using only bounded context. "
            "Do not reconstruct or quote the complete prior report.\n\n"
            f"{context}\n\n[current question]\n"
            f"{self._state.checkpoint.prepared.projection.current_query}"
        )[:_MAX_PROMPT_CHARS]

    async def follow_up(
        self,
        chat: ChatSeam,
    ) -> dict[str, Any]:
        """Answer a follow-up through Review's existing chat seam."""
        if self._state.checkpoint.prepared is None:
            raise RuntimeError("prepare must run before follow_up")
        response = await chat(self.follow_up_prompt())
        answer = _response_text(response)
        if not _usable_response_text(answer):
            self.mark_failed()
            raise ReviewClarificationError(
                "Review follow-up returned empty or invalid content."
            )
        return self._answer_result(
            answer, ReviewConversationOperation.FOLLOW_UP
        )

    async def local_revision(
        self,
        chat: ChatSeam,
    ) -> dict[str, Any]:
        """Revise only the validated section and reassemble the report."""
        if (
            self._state.checkpoint.prepared is None
            or self._state.checkpoint.prepared.section is None
        ):
            raise ReviewClarificationError(
                "Please name an active Review section to revise."
            )
        section = self._state.checkpoint.prepared.section
        revised = await revise_section(
            section_id=section.section_id,
            section_text=section.text,
            instruction=(
                self._state.checkpoint.prepared.projection.current_query
            ),
            evidence_summary=_evidence_summary(
                self._state.checkpoint.prepared.snapshot
            ),
            chat=chat,
        )
        self._state.result.last_revised_section = revised
        report_source: ReviewReportDocument | Sequence[ReviewSection] = (
            self._state.checkpoint.report_document
            if self._state.checkpoint.report_document is not None
            else (
                self._state.checkpoint.prepared.snapshot.sections
                if self._state.checkpoint.prepared.snapshot is not None
                else (section,)
            )
        )
        report = reassemble_report(report_source, revised)
        self._state.result.pending_report_text = report
        self._state.checkpoint.operation_successful = bool(report.strip())
        return self._answer_result(
            report, ReviewConversationOperation.LOCAL_REVISION
        )

    def delta(self, result: Mapping[str, Any] | None = None) -> ContextDelta:
        """Convert a successful Review result into bounded metadata."""
        if self._state.checkpoint.prepared is None:
            raise RuntimeError("prepare must run before delta")
        result = result or self._state.result.captured_result
        snapshot = (
            self._state.checkpoint.staged_snapshot
            if self._state.checkpoint.prepared.operation
            in {
                ReviewConversationOperation.NEW_REVIEW,
                ReviewConversationOperation.SCOPE_CHANGE,
            }
            else self._state.checkpoint.prepared.snapshot
        )
        summary = _review_summary(
            self._state.checkpoint.prepared.operation,
            self._state.checkpoint.prepared.projection,
            snapshot,
            result,
        )
        artifact_upserts: list[ArtifactRefV1] = []
        artifact_id = (
            snapshot.report_artifact_id if snapshot is not None else None
        )
        authorized = {
            item.artifact_id: item
            for item in (
                self._state.checkpoint.prepared.projection.artifact_refs
            )
        }
        if artifact_id in authorized:
            artifact_upserts.append(authorized[artifact_id])
        return ContextDelta(
            summary_update=summary[:MAX_CONTEXT_TEXT_CHARS],
            open_question_updates=_follow_up_questions(result),
            artifact_upserts=artifact_upserts,
            agent_memory_update=PerAgentMemory(
                agent_id="ReviewAgent",
                thread_id=(
                    self._state.checkpoint.prepared.projection.agent_thread_id
                ),
                summary=summary[:MAX_CONTEXT_TEXT_CHARS],
                checkpoint_ref=(
                    artifact_id if artifact_id in authorized else None
                ),
            ),
        )

    def _answer_result(
        self,
        content: str,
        operation: ReviewConversationOperation,
    ) -> dict[str, Any]:
        """Shape a cited-compatible answer without exposing private state."""
        return {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "doc_list": [
                            dict(item)
                            for item in self._state.result.ordered_doc_list
                        ],
                        "total": 10000,
                        "follow_up_questions": [],
                    }
                }
            ],
            "phytomni_state": {
                "review_operation": operation.value,
                "report_artifact_id": (
                    self._state.checkpoint.prepared.snapshot.report_artifact_id
                    if (
                        self._state.checkpoint.prepared
                        and self._state.checkpoint.prepared.snapshot
                    )
                    else None
                ),
                "report_revision": self._state.checkpoint.report_revision,
            },
        }
