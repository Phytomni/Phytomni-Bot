# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bounded conversation operations for the Review agent.

The Review graph owns the complete report checkpoint.  This module exposes a
small projection of that checkpoint so follow-ups and local section edits do
not put the full report back into a prompt or shared conversation context.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, cast

from ...common.responses import message_content
from ...runtime.conversation_context.models import (
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_TEXT_CHARS,
    ArtifactRefV1,
    ContextDelta,
    ContextProjection,
    PerAgentMemory,
)
from ...runtime.langgraph_runner import build_runnable_config

_MAX_SOURCE_IDS = MAX_CONTEXT_ITEMS
_MAX_HEADINGS = MAX_CONTEXT_ITEMS
_MAX_CLAIMS = MAX_CONTEXT_ITEMS
_MAX_GAPS = MAX_CONTEXT_ITEMS
_MAX_SECTION_CHARS = MAX_CONTEXT_TEXT_CHARS
_MAX_CLAIM_CHARS = 512
_MAX_PROMPT_CHARS = MAX_CONTEXT_TEXT_CHARS
_MAX_SUMMARY_CHARS = 1024

_REVISION_WORDS = re.compile(
    r"\b(?:revise|revision|rewrite|rewritten|edit|edited|update|updated|"
    r"correct|shorten|expand|tighten|improve|change)\w*\b",
    re.IGNORECASE,
)
_SCOPE_WORDS = re.compile(
    r"\b(?:new|different|another|instead|broaden|narrow|focus on|"
    r"change (?:the )?scope|new source(?:s| set)?|source set|"
    r"new research question|start (?:a )?new review|now investigate)\b",
    re.IGNORECASE,
)
_SECTION_PATTERN = re.compile(
    r"\b(?:section|subsection|part)\s*[:#-]?\s*"
    r"(?:\d+|[A-Za-z][A-Za-z0-9 _-]{1,80})",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class ReviewConversationOperation(StrEnum):
    """Private operation selected for one Review context turn."""

    NEW_REVIEW = "new_review"
    FOLLOW_UP = "follow_up"
    LOCAL_REVISION = "local_revision"
    SCOPE_CHANGE = "scope_change"


class ReviewClarificationError(ValueError):
    """Raised when a Review turn cannot identify an active section."""


@dataclass(frozen=True, slots=True)
class ReviewSection:
    """One bounded report section retained for local reassembly."""

    section_id: str
    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class RevisedSection:
    """The result of revising one validated section."""

    section_id: str
    text: str
    heading: str = ""


@dataclass(frozen=True, slots=True)
class ReviewCheckpointSnapshot:
    """Bounded semantic data recovered from a Review graph checkpoint."""

    research_question: str
    source_ids: tuple[str, ...] = ()
    outline_headings: tuple[str, ...] = ()
    key_claims: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()
    sections: tuple[ReviewSection, ...] = ()
    report_artifact_id: str | None = None
    report_revision: int = 0

    def section(self, section_id: str) -> ReviewSection | None:
        """Return one section by stable id or case-insensitive heading."""
        normalized = _slug(section_id)
        for item in self.sections:
            if (
                item.section_id == normalized
                or _slug(item.heading) == normalized
            ):
                return item
        return None


@dataclass(frozen=True, slots=True)
class _PreparedReviewTurn:
    projection: ContextProjection
    operation: ReviewConversationOperation
    snapshot: ReviewCheckpointSnapshot | None
    section: ReviewSection | None


ChatSeam = Callable[[str], Awaitable[Mapping[str, Any] | str | None]]


def _bounded_text(value: object, limit: int = MAX_CONTEXT_TEXT_CHARS) -> str:
    """Return bounded text without carrying arbitrary checkpoint values."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _bounded_items(
    values: object,
    *,
    limit: int,
    item_limit: int = MAX_CONTEXT_TEXT_CHARS,
) -> tuple[str, ...]:
    """Keep an ordered, deduplicated sequence of bounded strings."""
    if isinstance(values, str) or not isinstance(values, Sequence):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _bounded_text(value, item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _slug(value: str) -> str:
    """Make a stable, human-readable section identifier."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _state_values(state: object) -> Mapping[str, Any]:
    """Unwrap LangGraph snapshots and checkpoint tuples to channel values."""
    current: object = state
    if not isinstance(current, Mapping):
        values = getattr(current, "values", None)
        if callable(values):
            values = None
        if values is None:
            values = getattr(current, "checkpoint", None)
        if values is not None:
            current = values
    if not isinstance(current, Mapping):
        return {}
    for key in ("channel_values", "values", "state"):
        nested = current.get(key)
        if isinstance(nested, Mapping):
            return nested
    return current


def _source_ids(state: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract opaque source identifiers and discard document bodies."""
    values: list[str] = []
    seen: set[str] = set()
    for key in ("all_raw_doc_list", "add_doc_list", "source_ids"):
        raw = state.get(key)
        if isinstance(raw, str) or not isinstance(raw, Sequence):
            continue
        for item in raw:
            candidate: object = item
            if isinstance(item, Mapping):
                candidate = (
                    item.get("source_id")
                    or item.get("doc_id")
                    or item.get("sourceId")
                    or item.get("id")
                )
            if not isinstance(candidate, (str, int)):
                continue
            value = str(candidate).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            values.append(value)
            if len(values) >= _MAX_SOURCE_IDS:
                return tuple(values)
    return tuple(values)


def _section_values(state: Mapping[str, Any]) -> tuple[ReviewSection, ...]:
    """Extract section text from revised reports without the full summary."""
    dimensions = _bounded_items(
        state.get("research_dimensions"),
        limit=_MAX_HEADINGS,
        item_limit=256,
    )
    raw_reports = state.get("revised_reports")
    report_rows = (
        []
        if isinstance(raw_reports, str)
        or not isinstance(raw_reports, Sequence)
        else list(raw_reports)
    )
    if not report_rows:
        raw_drafts = state.get("draft_contents")
        if isinstance(raw_drafts, Sequence) and not isinstance(
            raw_drafts, str
        ):
            report_rows = list(raw_drafts)

    sections: list[ReviewSection] = []
    used_ids: set[str] = set()
    count = max(len(dimensions), len(report_rows))
    for index in range(count):
        row = report_rows[index] if index < len(report_rows) else {}
        if isinstance(row, Mapping):
            heading_value = (
                row.get("subtopic") or row.get("heading") or row.get("title")
            )
            text_value = (
                row.get("revised_report")
                or row.get("text")
                or row.get("content")
            )
        else:
            heading_value = None
            text_value = row
        heading = _bounded_text(
            heading_value
            or (dimensions[index] if index < len(dimensions) else ""),
            256,
        )
        text = _bounded_text(text_value, _MAX_SECTION_CHARS)
        if not heading and not text:
            continue
        heading = heading or f"Section {index + 1}"
        section_id = _slug(heading) or f"section-{index + 1}"
        if section_id in used_ids:
            section_id = f"{section_id}-{index + 1}"
        used_ids.add(section_id)
        sections.append(ReviewSection(section_id, heading, text))
    return tuple(sections[:_MAX_HEADINGS])


def _claim_summary(text: str) -> str:
    """Reduce one section to a short claim, never a report-sized field."""
    for fragment in _SENTENCE_SPLIT.split(text):
        candidate = re.sub(r"^\s*[#>*-]+\s*", "", fragment).strip()
        if candidate:
            return candidate[:_MAX_CLAIM_CHARS]
    return ""


def _explicit_claims(state: Mapping[str, Any]) -> tuple[str, ...]:
    """Read claim summaries from structured checkpoint fields only."""
    for key in ("key_claim_summaries", "claim_summaries", "key_claims"):
        raw = state.get(key)
        if isinstance(raw, Mapping):
            raw = list(raw.values())
        if isinstance(raw, Sequence) and not isinstance(raw, str):
            claims: list[str] = []
            for item in raw:
                if isinstance(item, Mapping):
                    item = (
                        item.get("summary")
                        or item.get("claim_summary")
                        or item.get("text")
                    )
                text = _bounded_text(item, _MAX_CLAIM_CHARS)
                if text:
                    claims.append(text)
            if claims:
                return tuple(claims[:_MAX_CLAIMS])
    return ()


def _feedback_metadata(
    state: Mapping[str, Any],
    field_names: tuple[str, ...],
) -> tuple[str, ...]:
    """Read short structured claim/gap fields from review feedback rows."""
    raw_rows = state.get("review_contents") or state.get("review_feedback")
    if isinstance(raw_rows, (Mapping, str)):
        raw_rows = [raw_rows]
    if not isinstance(raw_rows, Sequence):
        return ()
    values: list[str] = []
    for row in raw_rows[:_MAX_CLAIMS]:
        payload: Mapping[str, Any] | None = None
        if isinstance(row, Mapping):
            payload = row
        elif isinstance(row, str):
            try:
                parsed = json.loads(row[:_MAX_SECTION_CHARS])
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, Mapping):
                payload = parsed
        if payload is None:
            continue
        for field_name in field_names:
            candidate = payload.get(field_name)
            if isinstance(candidate, Sequence) and not isinstance(
                candidate, str
            ):
                values.extend(
                    item
                    for item in candidate
                    if isinstance(item, str) and item.strip()
                )
            elif isinstance(candidate, str) and candidate.strip():
                values.append(candidate)
    return _bounded_items(
        values,
        limit=_MAX_CLAIMS,
        item_limit=_MAX_CLAIM_CHARS,
    )


def _artifact_id(state: Mapping[str, Any]) -> str | None:
    """Read only an opaque report artifact identifier."""
    candidate: object = state.get("report_artifact_id")
    if candidate is None:
        report = state.get("report_artifact")
        if isinstance(report, Mapping):
            candidate = report.get("artifact_id")
    if candidate is None:
        candidate = state.get("artifact_id")
    if not isinstance(candidate, str):
        return None
    value = candidate.strip()
    if not value or "/" in value or "\\" in value:
        return None
    return value[:128]


def _report_revision(state: Mapping[str, Any]) -> int:
    """Read a non-negative artifact revision from the checkpoint."""
    candidate = state.get("report_revision")
    if candidate is None:
        report = state.get("report_artifact")
        if isinstance(report, Mapping):
            candidate = report.get("revision")
    if candidate is None:
        return 0
    if isinstance(candidate, bool):
        return 0
    try:
        revision = int(candidate)
    except (TypeError, ValueError):
        return 0
    return max(0, revision)


def extract_review_checkpoint(
    state: object,
) -> ReviewCheckpointSnapshot | None:
    """Extract a bounded Review snapshot from a graph checkpoint."""
    values = _state_values(state)
    question = _bounded_text(
        values.get("original_user_query") or values.get("user_query"),
        1024,
    )
    sections = _section_values(values)
    headings = _bounded_items(
        values.get("research_dimensions")
        or [item.heading for item in sections],
        limit=_MAX_HEADINGS,
        item_limit=256,
    )
    claims = _explicit_claims(values)
    if not claims:
        claims = _feedback_metadata(
            values,
            ("key_claim_summary", "claim_summary", "key_claims"),
        )
    if not claims:
        claims = tuple(
            summary
            for summary in (_claim_summary(item.text) for item in sections)
            if summary
        )[:_MAX_CLAIMS]
    gaps = _bounded_items(
        values.get("evidence_gaps")
        or values.get("unresolved_evidence_gaps")
        or values.get("open_questions"),
        limit=_MAX_GAPS,
        item_limit=_MAX_CLAIM_CHARS,
    )
    if not gaps:
        gaps = _feedback_metadata(
            values,
            ("evidence_gaps", "unresolved_evidence_gaps", "open_questions"),
        )
    if (
        not question
        and not sections
        and not headings
        and not _source_ids(values)
    ):
        return None
    return ReviewCheckpointSnapshot(
        research_question=question,
        source_ids=_source_ids(values),
        outline_headings=headings,
        key_claims=claims,
        evidence_gaps=gaps,
        sections=sections,
        report_artifact_id=_artifact_id(values),
        report_revision=_report_revision(values),
    )


async def load_review_checkpoint(
    agent: Any,
    thread_id: str,
) -> ReviewCheckpointSnapshot | None:
    """Load Review's private checkpoint through its derived thread ID."""
    config = build_runnable_config(thread_id)
    app = getattr(agent, "app", None)
    getter = cast(
        Callable[[Any], Awaitable[Any]] | None,
        getattr(app, "aget_state", None),
    )
    if callable(getter):
        snapshot = await getter(config)
        extracted = extract_review_checkpoint(snapshot)
        if extracted is not None:
            return extracted
    checkpointer = getattr(agent, "checkpointer", None)
    getter = cast(
        Callable[[Any], Awaitable[Any]] | None,
        getattr(checkpointer, "aget", None),
    )
    if callable(getter):
        checkpoint = await getter(config)
        return extract_review_checkpoint(checkpoint)
    return None


def _section_reference(
    query: str,
    snapshot: ReviewCheckpointSnapshot | None,
) -> str | None:
    """Resolve a section name or ordinal from the current instruction."""
    lowered = query.lower()
    if snapshot is not None:
        for index, section in enumerate(snapshot.sections, start=1):
            if re.search(rf"\bsection\s*{index}\b", lowered):
                return section.section_id
            if section.heading and section.heading.lower() in lowered:
                return section.section_id
    match = _SECTION_PATTERN.search(query)
    if match is None:
        return None
    token = match.group(0)
    token = re.sub(
        r"^(?:section|subsection|part)\s*[:#-]?\s*",
        "",
        token,
        flags=re.IGNORECASE,
    )
    return _slug(token)


def _projection_has_active_review(projection: ContextProjection) -> bool:
    """Use only bounded semantic fields to detect a prior Review turn."""
    context_text = " ".join(
        (
            projection.task_summary,
            *projection.relevant_assistant_summaries,
        )
    )
    return bool(
        re.search(r"\breview\b|\bresearch\b", context_text, re.IGNORECASE)
        or any(
            entity.entity_id.startswith("review:")
            for entity in projection.active_entities
        )
    )


def classify_review_operation(
    query: str | ContextProjection,
    *,
    projection: ContextProjection | None = None,
    snapshot: ReviewCheckpointSnapshot | None = None,
    active_review: bool | None = None,
    has_active_review: bool | None = None,
) -> ReviewConversationOperation:
    """Classify one Review turn without invoking retrieval or generation."""
    if isinstance(query, ContextProjection):
        projection = query
        text = query.current_query
    else:
        text = query
    active = active_review
    if active is None:
        active = has_active_review
    if active is None:
        active = snapshot is not None or (
            projection is not None
            and _projection_has_active_review(projection)
        )
    if not active:
        return ReviewConversationOperation.NEW_REVIEW
    if _REVISION_WORDS.search(text) and _section_reference(text, snapshot):
        return ReviewConversationOperation.LOCAL_REVISION
    if _SCOPE_WORDS.search(text) or re.match(
        r"^\s*(?:review|investigate|compare|research)\b", text, re.IGNORECASE
    ):
        return ReviewConversationOperation.SCOPE_CHANGE
    return ReviewConversationOperation.FOLLOW_UP


def _prompt_context(
    snapshot: ReviewCheckpointSnapshot | None,
    *,
    section: ReviewSection | None = None,
) -> str:
    """Render only bounded Review metadata for a generation prompt."""
    if snapshot is None:
        return "[review context]\nNo active Review checkpoint."
    fragments = [
        f"[research question]\n{snapshot.research_question}",
        "[source ids]\n" + (", ".join(snapshot.source_ids) or "none"),
        "[outline headings]\n"
        + (
            "\n".join(f"- {item}" for item in snapshot.outline_headings)
            or "none"
        ),
        "[key claims]\n"
        + ("\n".join(f"- {item}" for item in snapshot.key_claims) or "none"),
        "[unresolved evidence gaps]\n"
        + (
            "\n".join(f"- {item}" for item in snapshot.evidence_gaps) or "none"
        ),
    ]
    if section is not None:
        fragments.append(
            f"[requested section: {section.heading}]\n{section.text}"
        )
    return "\n\n".join(fragments)[:_MAX_PROMPT_CHARS]


def _evidence_summary(snapshot: ReviewCheckpointSnapshot | None) -> str:
    """Return a bounded claim/gap summary for local section revision."""
    if snapshot is None:
        return "No bounded evidence summary is available."
    parts = [*snapshot.key_claims, *snapshot.evidence_gaps]
    return "\n".join(f"- {item}" for item in parts)[:_MAX_PROMPT_CHARS]


def _staged_scope_snapshot(
    projection: ContextProjection,
    snapshot: ReviewCheckpointSnapshot | None,
    operation: ReviewConversationOperation,
) -> ReviewCheckpointSnapshot | None:
    """Stage a new research focus without replacing the active checkpoint."""
    if operation not in {
        ReviewConversationOperation.NEW_REVIEW,
        ReviewConversationOperation.SCOPE_CHANGE,
    }:
        return None
    revision = snapshot.report_revision if snapshot is not None else 0
    return ReviewCheckpointSnapshot(
        research_question=projection.current_query[:1024],
        source_ids=(),
        outline_headings=(),
        key_claims=(),
        evidence_gaps=(),
        sections=(),
        report_artifact_id=(
            snapshot.report_artifact_id if snapshot is not None else None
        ),
        report_revision=revision,
    )


def _replace_section(
    snapshot: ReviewCheckpointSnapshot,
    revised: RevisedSection,
) -> ReviewCheckpointSnapshot:
    """Build a staged snapshot with one section changed."""
    sections = tuple(
        (
            replace(
                section,
                text=revised.text,
                heading=revised.heading or section.heading,
            )
            if section.section_id == _slug(revised.section_id)
            else section
        )
        for section in snapshot.sections
    )
    return replace(snapshot, sections=sections)


def _answer_from_result(result: Mapping[str, Any]) -> str:
    """Read one answer from either a native or agent.run envelope."""
    nested = result.get("result")
    if isinstance(nested, Mapping):
        formatted = nested.get("formatted")
        if isinstance(formatted, Mapping):
            answer = formatted.get("answer")
            if isinstance(answer, str):
                return answer.strip()
        raw = nested.get("raw")
        if isinstance(raw, Mapping):
            answer = message_content(raw)
            if answer:
                return answer.strip()
    answer = message_content(result)
    return answer.strip()


def _response_text(response: object) -> str:
    """Read bounded text from a chat response or direct test seam."""
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, Mapping):
        return message_content(response).strip()
    return ""


def _follow_up_questions(result: Mapping[str, Any]) -> list[str]:
    """Extract a bounded follow-up list without copying report state."""
    nested = result.get("result")
    if isinstance(nested, Mapping):
        formatted = nested.get("formatted")
        if isinstance(formatted, Mapping):
            raw_questions = formatted.get("follow_up_questions")
            if isinstance(raw_questions, Sequence) and not isinstance(
                raw_questions, str
            ):
                return list(
                    _bounded_items(
                        raw_questions,
                        limit=MAX_CONTEXT_ITEMS,
                        item_limit=512,
                    )
                )
    return []


def _review_summary(
    operation: ReviewConversationOperation,
    projection: ContextProjection,
    snapshot: ReviewCheckpointSnapshot | None,
    result: Mapping[str, Any],
) -> str:
    """Build a compact context summary while excluding full report text."""
    if operation is ReviewConversationOperation.FOLLOW_UP:
        return _answer_from_result(result)[:_MAX_SUMMARY_CHARS]
    if operation is ReviewConversationOperation.LOCAL_REVISION:
        return "Review section revision completed."
    question = (
        snapshot.research_question
        if snapshot is not None
        else projection.current_query
    )
    headings = ", ".join(snapshot.outline_headings) if snapshot else ""
    return (
        f"Review completed for {question[:512]}. Sections: {headings[:480]}"[
            :_MAX_SUMMARY_CHARS
        ]
    )


class ReviewConversationAdapter:
    """Prepare bounded Review turns and produce bounded context deltas."""

    def __init__(self) -> None:
        self._prepared: _PreparedReviewTurn | None = None
        self._active_snapshot: ReviewCheckpointSnapshot | None = None
        self._staged_snapshot: ReviewCheckpointSnapshot | None = None
        self._last_revised_section: RevisedSection | None = None
        self._captured_result: dict[str, Any] = {}
        self._report_revision = 0
        self._settled = False

    def prepare(
        self,
        projection: ContextProjection,
        *,
        snapshot: ReviewCheckpointSnapshot | Mapping[str, Any] | None = None,
        allow_unresolved_section: bool = False,
    ) -> dict[str, Any]:
        """Classify and project one Review turn."""
        if snapshot is not None and not isinstance(
            snapshot, ReviewCheckpointSnapshot
        ):
            snapshot = extract_review_checkpoint(snapshot)
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
        self._prepared = _PreparedReviewTurn(
            projection=projection,
            operation=operation,
            snapshot=snapshot,
            section=section,
        )
        self._active_snapshot = snapshot
        self._staged_snapshot = _staged_scope_snapshot(
            projection, snapshot, operation
        )
        self._last_revised_section = None
        self._captured_result = {}
        self._report_revision = snapshot.report_revision if snapshot else 0
        self._settled = False
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
            "report_revision": self._report_revision,
        }

    async def prepare_from_agent(
        self,
        projection: ContextProjection,
        agent: Any,
        thread_id: str,
    ) -> dict[str, Any]:
        """Load the private checkpoint, then prepare against its snapshot."""
        snapshot = await load_review_checkpoint(agent, thread_id)
        return self.prepare(projection, snapshot=snapshot)

    @property
    def operation(self) -> ReviewConversationOperation | None:
        """Return the prepared private operation, if any."""
        return self._prepared.operation if self._prepared else None

    @property
    def snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return the bounded checkpoint snapshot used for this turn."""
        return self._prepared.snapshot if self._prepared else None

    @property
    def active_snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return the last committed snapshot, excluding staged scope changes."""
        return self._active_snapshot

    @property
    def staged_snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return a candidate snapshot pending successful settlement."""
        return self._staged_snapshot

    @property
    def report_revision(self) -> int:
        """Return the last settled report artifact revision."""
        return self._report_revision

    def capture_result(self, result: Mapping[str, Any]) -> None:
        """Capture only bounded metadata from a completed Review result."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before capture_result")
        captured_answer = (
            _bounded_text(_answer_from_result(result), _MAX_SUMMARY_CHARS)
            if self._prepared.operation
            is ReviewConversationOperation.FOLLOW_UP
            else ""
        )
        self._captured_result = {
            "result": {
                "formatted": {
                    "answer": captured_answer,
                    "follow_up_questions": _follow_up_questions(result),
                }
            }
        }
        checkpoint = extract_review_checkpoint(result.get("phytomni_state"))
        if checkpoint is not None:
            self._staged_snapshot = checkpoint
        if self._last_revised_section is not None:
            base = self._active_snapshot or self._prepared.snapshot
            if base is not None:
                self._staged_snapshot = _replace_section(
                    base, self._last_revised_section
                )

    def settle(self, success: bool) -> int:
        """Advance the report revision only after successful settlement."""
        if not success:
            self._staged_snapshot = None
            return self._report_revision
        if not self._settled:
            self._report_revision += 1
            if self._staged_snapshot is not None:
                self._active_snapshot = replace(
                    self._staged_snapshot,
                    report_revision=self._report_revision,
                )
            self._settled = True
        return self._report_revision

    def follow_up_prompt(self) -> str:
        """Build the bounded prompt for an existing-claim follow-up."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before follow_up_prompt")
        context = _prompt_context(self._prepared.snapshot)
        return (
            "Answer the current Review follow-up using only the bounded context. "
            "Do not reconstruct or quote the complete prior report.\n\n"
            f"{context}\n\n[current question]\n"
            f"{self._prepared.projection.current_query}"
        )[:_MAX_PROMPT_CHARS]

    async def follow_up(
        self,
        chat: ChatSeam,
    ) -> dict[str, Any]:
        """Answer a follow-up through Review's existing chat seam."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before follow_up")
        response = await chat(self.follow_up_prompt())
        answer = _response_text(response) or "No answer generated."
        return self._answer_result(
            answer, ReviewConversationOperation.FOLLOW_UP
        )

    async def local_revision(
        self,
        chat: ChatSeam,
    ) -> dict[str, Any]:
        """Revise only the validated section and reassemble the report."""
        if self._prepared is None or self._prepared.section is None:
            raise ReviewClarificationError(
                "Please name an active Review section to revise."
            )
        section = self._prepared.section
        revised = await revise_section(
            section_id=section.section_id,
            section_text=section.text,
            instruction=self._prepared.projection.current_query,
            evidence_summary=_evidence_summary(self._prepared.snapshot),
            chat=chat,
        )
        self._last_revised_section = revised
        report = reassemble_report(
            (
                self._prepared.snapshot.sections
                if self._prepared.snapshot is not None
                else (section,)
            ),
            revised,
        )
        return self._answer_result(
            report, ReviewConversationOperation.LOCAL_REVISION
        )

    def delta(self, result: Mapping[str, Any] | None = None) -> ContextDelta:
        """Convert a successful Review result into bounded semantic metadata."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before delta")
        result = result or self._captured_result
        snapshot = (
            self._staged_snapshot
            if self._prepared.operation
            in {
                ReviewConversationOperation.NEW_REVIEW,
                ReviewConversationOperation.SCOPE_CHANGE,
            }
            else self._prepared.snapshot
        )
        summary = _review_summary(
            self._prepared.operation,
            self._prepared.projection,
            snapshot,
            result,
        )
        artifact_upserts: list[ArtifactRefV1] = []
        artifact_id = (
            snapshot.report_artifact_id if snapshot is not None else None
        )
        authorized = {
            item.artifact_id: item
            for item in self._prepared.projection.artifact_refs
        }
        if artifact_id in authorized:
            artifact_upserts.append(authorized[artifact_id])
        return ContextDelta(
            summary_update=summary[:MAX_CONTEXT_TEXT_CHARS],
            open_question_updates=_follow_up_questions(result),
            artifact_upserts=artifact_upserts,
            agent_memory_update=PerAgentMemory(
                agent_id="ReviewAgent",
                thread_id=self._prepared.projection.agent_thread_id,
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
                        "doc_list": [],
                        "total": 10000,
                        "follow_up_questions": [],
                    }
                }
            ],
            "phytomni_state": {
                "review_operation": operation.value,
                "report_artifact_id": (
                    self._prepared.snapshot.report_artifact_id
                    if self._prepared and self._prepared.snapshot
                    else None
                ),
                "report_revision": self._report_revision,
            },
        }


async def revise_section(
    *,
    section_id: str,
    section_text: str,
    instruction: str,
    evidence_summary: str,
    chat: ChatSeam | None = None,
) -> RevisedSection:
    """Use the focused Review chat seam to revise exactly one section."""
    prompt = (
        "Revise only the requested Review section. Return only the revised "
        "section text, without a heading or commentary.\n\n"
        f"[section id]\n{_bounded_text(section_id, 128)}\n\n"
        f"[section text]\n{_bounded_text(section_text)}\n\n"
        f"[instruction]\n{_bounded_text(instruction)}\n\n"
        f"[bounded evidence summary]\n{_bounded_text(evidence_summary)}"
    )[:_MAX_PROMPT_CHARS]
    if chat is None:
        return RevisedSection(
            section_id=section_id, text=_bounded_text(section_text)
        )
    response: object = chat(prompt)
    if inspect.isawaitable(response):
        response = await response
    text = _response_text(response) or _bounded_text(section_text)
    return RevisedSection(
        section_id=section_id,
        text=_bounded_text(text),
        heading=section_id,
    )


def review_clarification_result(message: str) -> dict[str, Any]:
    """Shape a clarification into the unchanged chat-completion envelope."""
    return {
        "choices": [
            {
                "message": {
                    "content": _bounded_text(message, _MAX_SUMMARY_CHARS),
                    "doc_list": [],
                    "total": 10000,
                    "follow_up_questions": [],
                }
            }
        ]
    }


def _section_parts(
    sections: Sequence[ReviewSection | Mapping[str, str]],
) -> tuple[ReviewSection, ...]:
    """Normalize section mappings accepted by the reassembly helper."""
    normalized: list[ReviewSection] = []
    for index, item in enumerate(sections, start=1):
        if isinstance(item, ReviewSection):
            normalized.append(item)
            continue
        heading = _bounded_text(item.get("heading") or item.get("title"), 256)
        text = _bounded_text(item.get("text") or item.get("content"))
        section_id = (
            _slug(item.get("section_id") or heading) or f"section-{index}"
        )
        normalized.append(ReviewSection(section_id, heading, text))
    return tuple(normalized)


def _coerce_revised_section(
    revised_section: RevisedSection | Mapping[str, str],
) -> RevisedSection:
    """Normalize the focused revision result once for both report forms."""
    if isinstance(revised_section, RevisedSection):
        return revised_section
    return RevisedSection(
        section_id=_slug(revised_section.get("section_id", "")),
        text=_bounded_text(revised_section.get("text")),
        heading=_bounded_text(revised_section.get("heading"), 256),
    )


def _replace_markdown_section(
    report: str,
    revised: RevisedSection,
) -> str:
    """Replace one Markdown heading body while preserving other bytes."""
    target = _slug(revised.heading or revised.section_id)
    headings = list(
        re.finditer(r"(?m)^(#{1,6})[ \t]+([^\n]+?)[ \t]*$", report)
    )
    for index, heading in enumerate(headings):
        if _slug(heading.group(2)) != target:
            continue
        body_start = heading.end()
        body_end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(report)
        )
        body = report[body_start:body_end]
        leading = body[: len(body) - len(body.lstrip())]
        trailing = body[len(body.rstrip()) :]
        return (
            report[:body_start]
            + leading
            + revised.text
            + trailing
            + report[body_end:]
        )
    return report


def reassemble_report(
    sections: Sequence[ReviewSection | Mapping[str, str]] | str,
    revised_section: RevisedSection | Mapping[str, str],
) -> str:
    """Reassemble a report while replacing only one section's text."""
    revised = _coerce_revised_section(revised_section)
    if isinstance(sections, str):
        return _replace_markdown_section(sections, revised)
    parts = _section_parts(sections)
    rendered: list[str] = []
    for section in parts:
        text = (
            revised.text
            if section.section_id == _slug(revised.section_id)
            else section.text
        )
        heading = section.heading or revised.heading
        rendered.append(f"## {heading}\n{text}" if heading else text)
    return "\n\n".join(rendered)


__all__ = [
    "ReviewCheckpointSnapshot",
    "ReviewClarificationError",
    "ReviewConversationAdapter",
    "ReviewConversationOperation",
    "ReviewSection",
    "RevisedSection",
    "classify_review_operation",
    "extract_review_checkpoint",
    "load_review_checkpoint",
    "reassemble_report",
    "review_clarification_result",
    "revise_section",
]
