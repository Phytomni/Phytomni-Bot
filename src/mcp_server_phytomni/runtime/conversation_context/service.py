# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Orchestrate one Bot conversation turn and its Go-ledger settlement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from weakref import WeakValueDictionary

from ...agents.review.conversation import _candidate_thread_id
from ...config.defaults import ApiConfig
from .models import (
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_TEXT_CHARS,
    BusinessContext,
    ContextDelta,
    ContextProjection,
    ConversationEnvelopeV1,
    RoleTaggedTurn,
)
from .projection import (
    build_context_projection,
    rebuild_business_context,
    validate_context_delta,
)
from .store import (
    ContextVersionConflictError,
    ConversationContextStore,
    StagedTurn,
    StoredBusinessContext,
    StoredTurn,
)


class PrepareStatus(StrEnum):
    READY = "ready"
    RETURN_STAGED = "return_staged"
    RETURN_COMMITTED = "return_committed"
    REBUILD_REQUIRED = "rebuild_required"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class AgentSelection:
    selected_agent_id: str
    reason_code: str


@dataclass(frozen=True)
class AgentOutcome:
    result: dict[str, Any]
    assistant_summary: str | None = None
    context_delta: ContextDelta | None = None
    context_delta_error: bool = False
    status: str = "succeeded"
    private_stage_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ContextStageMetadata:
    selected_agent_id: str
    route_source: str
    route_reason_code: str
    base_business_context_version: int
    proposed_business_context_version: int
    last_applied_ledger_cursor: int
    context_truncated: bool
    context_rebuilt: bool
    context_degraded: bool


@dataclass(frozen=True)
class PreparedTurn:
    status: PrepareStatus
    context: BusinessContext | None = None
    projection: ContextProjection | None = None
    stored_turn: StoredTurn | None = None
    result: dict[str, Any] | None = None
    stage: ContextStageMetadata | None = None


class SettlementMismatchError(RuntimeError):
    """Raised when an acknowledgment does not match the staged proposal."""


_PRIVATE_REVIEW_STAGE_KEY = "_review_settlement"
_PUBLIC_STAGE_FIELDS = frozenset(
    {
        "selected_agent_id",
        "route_source",
        "route_reason_code",
        "base_business_context_version",
        "proposed_business_context_version",
        "last_applied_ledger_cursor",
        "context_truncated",
        "context_rebuilt",
        "context_degraded",
    }
)
_REVIEW_STAGE_FIELDS = frozenset(
    {
        "version",
        "operation",
        "stable_thread_id",
        "candidate_thread_id",
        "turn_id",
        "report_revision",
        "settlement_state",
        "settlement_claim_token",
        "settlement_claimed_at",
    }
)
_REVIEW_OPERATIONS = frozenset(
    {"new_review", "follow_up", "local_revision", "scope_change"}
)
_REVIEW_SETTLEMENT_STATES = frozenset(
    {"pending", "settling", "promoted", "rejected", "failed"}
)


def _bounded_review_stage_metadata(
    value: Mapping[str, Any],
    *,
    allow_terminal: bool = False,
) -> dict[str, Any] | None:
    """Keep only bounded checkpoint identities in durable turn metadata."""
    operation = value.get("operation")
    if not isinstance(operation, str) or operation not in _REVIEW_OPERATIONS:
        return None
    settlement_state = value.get("settlement_state")
    if (
        not isinstance(settlement_state, str)
        or settlement_state not in _REVIEW_SETTLEMENT_STATES
        or (not allow_terminal and settlement_state != "pending")
    ):
        return None
    result: dict[str, Any] = {}
    for key in _REVIEW_STAGE_FIELDS:
        candidate = value.get(key)
        if key in {"version", "report_revision"}:
            if (
                isinstance(candidate, bool)
                or not isinstance(candidate, int)
                or (key == "version" and candidate != 1)
                or (key == "report_revision" and candidate < 0)
            ):
                continue
            result[key] = candidate
        elif key in {"settlement_claim_token", "settlement_claimed_at"}:
            if (
                isinstance(candidate, str)
                and candidate == candidate.strip()
                and candidate
                and len(candidate) <= 64
            ):
                result[key] = candidate
        elif key == "candidate_thread_id":
            if candidate is None:
                result[key] = None
            elif (
                isinstance(candidate, str)
                and candidate == candidate.strip()
                and candidate
                and len(candidate) <= 512
                and "/" not in candidate
                and "\\" not in candidate
            ):
                result[key] = candidate[:512]
        elif (
            isinstance(candidate, str)
            and candidate == candidate.strip()
            and candidate
        ):
            limit = 64 if key == "turn_id" else 512
            if (
                len(candidate) <= limit
                and "/" not in candidate
                and "\\" not in candidate
            ):
                result[key] = candidate
    required = {
        "version",
        "operation",
        "stable_thread_id",
        "turn_id",
        "report_revision",
        "settlement_state",
    }
    if not required.issubset(result):
        return None
    if operation in {"new_review", "scope_change"}:
        candidate = result.get("candidate_thread_id")
        if not isinstance(candidate, str) or not candidate:
            return None
        if candidate != _candidate_thread_id(
            result["stable_thread_id"], result["turn_id"]
        ):
            return None
    elif result.get("candidate_thread_id") is not None:
        return None
    if settlement_state == "settling":
        if not {
            "settlement_claim_token",
            "settlement_claimed_at",
        }.issubset(result):
            return None
        try:
            datetime.fromisoformat(result["settlement_claimed_at"])
        except (TypeError, ValueError):
            return None
    elif (
        "settlement_claim_token" in result or "settlement_claimed_at" in result
    ):
        return None
    return result


def review_settlement_metadata_from_turn(
    turn: StoredTurn | None,
) -> dict[str, Any] | None:
    """Read bounded private Review metadata without exposing report content."""
    if turn is None or not isinstance(turn.stage_metadata, Mapping):
        return None
    metadata = turn.stage_metadata.get(_PRIVATE_REVIEW_STAGE_KEY)
    if not isinstance(metadata, Mapping):
        return None
    return dict(metadata)


Router = Callable[
    [str, tuple[str, ...], BusinessContext], Awaitable[AgentSelection]
]
Invoker = Callable[
    [str, ConversationEnvelopeV1, ContextProjection], Awaitable[AgentOutcome]
]
AsyncDelegator = Callable[
    [str, ConversationEnvelopeV1], Awaitable[dict[str, object]]
]

_SYNC_CONTEXT_AGENTS = frozenset(
    {
        "ChatAgent",
        "KnowledgeAgent",
        "DataAgent",
        "ReviewAgent",
        "BriefGeneAgent",
    }
)


class ConversationContextService:
    """Keep transaction work short and serialize only one conversation."""

    def __init__(
        self,
        store: ConversationContextStore,
        *,
        router: Router,
        invoke: Invoker,
        delegate_async: AsyncDelegator,
        api_config: ApiConfig | None = None,
    ) -> None:
        self.store = store
        self.router = router
        self.invoke = invoke
        self.delegate_async = delegate_async
        self.api_config = api_config or ApiConfig()
        self._locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _key(envelope: ConversationEnvelopeV1) -> str:
        return str(envelope.conversation_key)

    @staticmethod
    def _rebuild_context(
        envelope: ConversationEnvelopeV1,
    ) -> BusinessContext:
        """Recover only from the bounded ledger data supplied by Go."""
        return rebuild_business_context(
            conversation_key=envelope.conversation_key,
            ledger_entries=[
                item.model_dump(mode="json") for item in envelope.history_delta
            ],
            artifact_refs=envelope.artifact_refs,
            ledger_cursor=envelope.ledger_cursor,
            ledger_version=envelope.ledger_version,
            observed_mode=envelope.mode,
        )

    def _context_for(
        self,
        envelope: ConversationEnvelopeV1,
        stored: StoredBusinessContext | None,
    ) -> tuple[BusinessContext | None, bool, PrepareStatus | None]:
        if stored is None:
            if envelope.base_business_context_version != 0:
                return None, False, PrepareStatus.REBUILD_REQUIRED
            return self._rebuild_context(envelope), True, None
        if stored.state != "active":
            return None, False, PrepareStatus.REBUILD_REQUIRED
        if stored.context_version != envelope.base_business_context_version:
            return None, False, PrepareStatus.REBUILD_REQUIRED
        if envelope.operation == "rebuild":
            return self._rebuild_context(envelope), True, None
        if stored.schema_version != 1:
            return None, False, PrepareStatus.REBUILD_REQUIRED
        if stored.observed_mode != envelope.mode:
            return None, False, PrepareStatus.REBUILD_REQUIRED
        if envelope.operation == "replace":
            # A replacement can invalidate every semantic fact retained at its
            # cursor and after it, so Go must resend an explicit rebuild.
            return None, False, PrepareStatus.REBUILD_REQUIRED
        if envelope.ledger_cursor != stored.ledger_cursor + 1:
            return None, False, PrepareStatus.REBUILD_REQUIRED
        try:
            context = BusinessContext.model_validate(stored.context)
        except ValueError:
            return None, False, PrepareStatus.REBUILD_REQUIRED
        if (
            context.version != stored.context_version
            or context.last_applied_ledger_cursor != stored.ledger_cursor
            or context.last_applied_ledger_version != stored.ledger_version
        ):
            return None, False, PrepareStatus.REBUILD_REQUIRED
        return context, False, None

    def _matches_duplicate(
        self, turn: StoredTurn, envelope: ConversationEnvelopeV1
    ) -> bool:
        """Reject retry envelopes that change an already-owned turn proposal."""
        if (
            turn.operation != envelope.operation
            or turn.base_context_version
            != envelope.base_business_context_version
        ):
            return False
        if turn.state == "staged":
            return turn.ledger_version == envelope.ledger_version
        if turn.state == "committed":
            stored = self.store.load_context(self._key(envelope))
            return (
                stored is not None
                and stored.context.get("last_applied_ledger_version")
                == turn.ledger_version
            )
        return True

    async def _prepare_locked(
        self, envelope: ConversationEnvelopeV1
    ) -> PreparedTurn:
        """Prepare a turn while the conversation lock is held."""
        key = self._key(envelope)
        begun = self.store.begin_turn(
            key,
            envelope.turn_id,
            envelope.operation,
            envelope.base_business_context_version,
        )
        turn = begun.turn
        if not begun.created:
            if not self._matches_duplicate(turn, envelope):
                raise ValueError("duplicate turn proposal does not match")
            if turn.state == "staged":
                return PreparedTurn(
                    PrepareStatus.RETURN_STAGED,
                    stored_turn=turn,
                    result=turn.result,
                    stage=self._stage_from_turn(turn),
                )
            if turn.state == "committed":
                return PreparedTurn(
                    PrepareStatus.RETURN_COMMITTED,
                    stored_turn=turn,
                    result=turn.result,
                    stage=self._stage_from_turn(turn),
                )
            if turn.state == "failed":
                _context, _rebuilt, failure = self._context_for(
                    envelope, self.store.load_context(key)
                )
                if failure is not None:
                    return PreparedTurn(failure, stored_turn=turn)
            return PreparedTurn(PrepareStatus.IN_PROGRESS, stored_turn=turn)
        stored = self.store.load_context(key)
        context, _rebuilt, failure = self._context_for(envelope, stored)
        if failure is not None:
            self.store.mark_turn_failed(key, envelope.turn_id)
            return PreparedTurn(failure, stored_turn=turn)
        assert context is not None
        return PreparedTurn(
            PrepareStatus.READY, context=context, stored_turn=turn
        )

    async def prepare_turn(
        self, envelope: ConversationEnvelopeV1
    ) -> PreparedTurn:
        """Validate, deduplicate, and prepare a turn without invoking an agent."""
        if not isinstance(envelope, ConversationEnvelopeV1):
            raise TypeError("envelope must be ConversationEnvelopeV1")
        async with self._lock(self._key(envelope)):
            return await self._prepare_locked(envelope)

    @staticmethod
    def _stage_from_turn(turn: StoredTurn) -> ContextStageMetadata | None:
        if turn.stage_metadata is None:
            return None
        public_metadata = {
            key: turn.stage_metadata[key]
            for key in _PUBLIC_STAGE_FIELDS
            if key in turn.stage_metadata
        }
        if len(public_metadata) != len(_PUBLIC_STAGE_FIELDS):
            return None
        return ContextStageMetadata(
            **public_metadata,
        )

    def update_review_settlement_metadata(
        self,
        key: str,
        turn_id: str,
        updates: Mapping[str, Any],
    ) -> bool:
        """Persist a small Review settlement marker on a staged turn."""
        return self.store.update_review_settlement_metadata(
            key, turn_id, updates
        )

    async def execute_turn(
        self, envelope: ConversationEnvelopeV1
    ) -> PreparedTurn:
        """Run the full prepare, invoke, stage lifecycle."""
        if not isinstance(envelope, ConversationEnvelopeV1):
            raise TypeError("envelope must be ConversationEnvelopeV1")
        key = self._key(envelope)
        async with self._lock(key):
            prepared = await self._prepare_locked(envelope)
            if prepared.status is not PrepareStatus.READY:
                return prepared
            assert (
                prepared.context is not None
                and prepared.stored_turn is not None
            )
            context = prepared.context
            rebuilt = prepared.context.version == 0
            if envelope.mode == "instant":
                selection = AgentSelection("ChatAgent", "INSTANT_LOCK")
                route_source = "instant_lock"
            elif envelope.requested_agent_id is not None:
                selection = AgentSelection(
                    envelope.requested_agent_id, "EXPLICIT_SELECTION"
                )
                route_source = "explicit_selection"
            else:
                selection = await self.router(
                    envelope.current_message.content,
                    tuple(envelope.allowed_agent_ids),
                    context,
                )
                route_source = "router"
            if selection.selected_agent_id not in envelope.allowed_agent_ids:
                self.store.mark_turn_failed(key, envelope.turn_id)
                raise ValueError(
                    "selected agent is outside the envelope allowlist"
                )
            if selection.selected_agent_id not in _SYNC_CONTEXT_AGENTS:
                try:
                    result = await self.delegate_async(
                        selection.selected_agent_id, envelope
                    )
                except BaseException:
                    self.store.mark_turn_failed(key, envelope.turn_id)
                    raise
                return PreparedTurn(
                    PrepareStatus.READY, context=context, result=result
                )
            projection = build_context_projection(
                conversation_key=envelope.conversation_key,
                current_query=envelope.current_message.content,
                locale=envelope.current_message.locale,
                selected_agent_id=selection.selected_agent_id,
                context=context,
                authorized_artifacts=envelope.artifact_refs,
                api_config=self.api_config,
                exclude_current_user_turn=rebuilt,
            )
            try:
                outcome = await self.invoke(
                    selection.selected_agent_id, envelope, projection
                )
            except BaseException:
                self.store.mark_turn_failed(key, envelope.turn_id)
                raise
            if outcome.status != "succeeded":
                self.store.mark_turn_failed(key, envelope.turn_id)
                return PreparedTurn(
                    PrepareStatus.IN_PROGRESS, stored_turn=prepared.stored_turn
                )
            is_review = selection.selected_agent_id == "ReviewAgent"
            review_metadata: dict[str, Any] | None = None
            if is_review:
                if (
                    outcome.context_delta_error
                    or outcome.context_delta is None
                    or outcome.private_stage_metadata is None
                ):
                    self.store.mark_turn_failed(key, envelope.turn_id)
                    return PreparedTurn(
                        PrepareStatus.IN_PROGRESS,
                        stored_turn=prepared.stored_turn,
                    )
                review_metadata = _bounded_review_stage_metadata(
                    outcome.private_stage_metadata
                )
                if (
                    review_metadata is None
                    or review_metadata["stable_thread_id"]
                    != projection.agent_thread_id
                    or review_metadata["turn_id"] != envelope.turn_id
                ):
                    self.store.mark_turn_failed(key, envelope.turn_id)
                    return PreparedTurn(
                        PrepareStatus.IN_PROGRESS,
                        stored_turn=prepared.stored_turn,
                    )
            degraded = (
                outcome.context_delta_error or outcome.context_delta is None
            )
            delta = ContextDelta() if degraded else outcome.context_delta
            assert delta is not None
            if not degraded:
                try:
                    validate_context_delta(
                        delta,
                        conversation_key=envelope.conversation_key,
                        selected_agent_id=selection.selected_agent_id,
                        authorized_artifact_ids={
                            item.artifact_id for item in envelope.artifact_refs
                        },
                    )
                except ValueError:
                    if is_review:
                        self.store.mark_turn_failed(key, envelope.turn_id)
                        return PreparedTurn(
                            PrepareStatus.IN_PROGRESS,
                            stored_turn=prepared.stored_turn,
                        )
                    degraded = True
                    delta = ContextDelta()
            proposed = self._advance_context(
                context,
                envelope,
                delta,
                assistant_summary=(
                    None if degraded else outcome.assistant_summary
                ),
                add_current_user_turn=not rebuilt,
            )
            stage = ContextStageMetadata(
                selected_agent_id=selection.selected_agent_id,
                route_source=route_source,
                route_reason_code=selection.reason_code,
                base_business_context_version=envelope.base_business_context_version,
                proposed_business_context_version=envelope.base_business_context_version
                + 1,
                last_applied_ledger_cursor=envelope.ledger_cursor,
                context_truncated=projection.context_truncated,
                context_rebuilt=rebuilt,
                context_degraded=degraded,
            )
            stage_metadata: dict[str, Any] = {
                "selected_agent_id": stage.selected_agent_id,
                "route_source": stage.route_source,
                "route_reason_code": stage.route_reason_code,
                "base_business_context_version": (
                    stage.base_business_context_version
                ),
                "proposed_business_context_version": (
                    stage.proposed_business_context_version
                ),
                "last_applied_ledger_cursor": stage.last_applied_ledger_cursor,
                "context_truncated": stage.context_truncated,
                "context_rebuilt": stage.context_rebuilt,
                "context_degraded": stage.context_degraded,
            }
            if review_metadata is not None:
                stage_metadata[_PRIVATE_REVIEW_STAGE_KEY] = review_metadata
            stored = self.store.stage_turn(
                key,
                envelope.turn_id,
                StagedTurn(
                    operation=envelope.operation,
                    base_context_version=envelope.base_business_context_version,
                    selected_agent_id=selection.selected_agent_id,
                    route_source=route_source,
                    result=outcome.result,
                    delta=proposed.model_dump(mode="json"),
                    ledger_version=envelope.ledger_version,
                    schema_version=proposed.schema_version,
                    ledger_cursor=envelope.ledger_cursor,
                    observed_mode=envelope.mode,
                    stage_metadata=stage_metadata,
                ),
            )
            return PreparedTurn(
                PrepareStatus.RETURN_STAGED,
                context=proposed,
                projection=projection,
                stored_turn=stored,
                result=outcome.result,
                stage=stage,
            )

    @staticmethod
    def _advance_context(
        context: BusinessContext,
        envelope: ConversationEnvelopeV1,
        delta: ContextDelta,
        *,
        assistant_summary: str | None,
        add_current_user_turn: bool,
    ) -> BusinessContext:
        """Apply a validated delta and advance only Bot-owned state."""
        data = context.model_dump(mode="python")
        if delta.summary_update is not None:
            data["task_summary"] = delta.summary_update
        entities = {
            item["entity_id"]: item for item in data["active_entities"]
        }
        entities.update(
            {
                item.entity_id: item.model_dump(mode="python")
                for item in delta.entity_upserts
            }
        )
        for item in delta.entity_removals:
            entities.pop(str(item), None)
        data["active_entities"] = list(entities.values())
        if delta.open_question_updates:
            data["open_questions"] = list(delta.open_question_updates)
        artifacts = {
            item["artifact_id"]: item for item in data["artifact_index"]
        }
        artifacts.update(
            {
                item.artifact_id: item.model_dump(mode="python")
                for item in delta.artifact_upserts
            }
        )
        data["artifact_index"] = list(artifacts.values())
        if delta.agent_memory_update is not None:
            memory = delta.agent_memory_update
            data["per_agent_memory"][memory.agent_id] = memory.model_dump(
                mode="python"
            )
        recent_turns = list(data["recent_turns"])
        if add_current_user_turn:
            recent_turns.append(
                RoleTaggedTurn(
                    role="user",
                    content=envelope.current_message.content[
                        :MAX_CONTEXT_TEXT_CHARS
                    ],
                ).model_dump(mode="python")
            )
        if assistant_summary:
            recent_turns.append(
                RoleTaggedTurn(
                    role="assistant",
                    content=assistant_summary[:MAX_CONTEXT_TEXT_CHARS],
                ).model_dump(mode="python")
            )
        data["recent_turns"] = recent_turns[-MAX_CONTEXT_ITEMS:]
        data.update(
            {
                "version": envelope.base_business_context_version + 1,
                "last_applied_ledger_cursor": envelope.ledger_cursor,
                "last_applied_ledger_version": envelope.ledger_version,
                "observed_mode": envelope.mode,
            }
        )
        return BusinessContext.model_validate(data)

    async def acknowledge_settlement(
        self, envelope: ConversationEnvelopeV1, ledger_version: str
    ) -> Any:
        """Commit exactly the staged proposal accepted by the Go ledger."""
        key = self._key(envelope)
        async with self._lock(key):
            turn = self.store.begin_turn(
                key,
                envelope.turn_id,
                envelope.operation,
                envelope.base_business_context_version,
            ).turn
            if not self._matches_duplicate(turn, envelope):
                raise SettlementMismatchError(
                    "settlement does not match turn proposal"
                )
            if turn.state == "committed":
                if turn.ledger_version != ledger_version:
                    raise SettlementMismatchError(
                        "repeated settlement has a different ledger version"
                    )
                return self.store.load_context(key)
            if (
                turn.state != "staged"
                or turn.ledger_version != envelope.ledger_version
            ):
                raise SettlementMismatchError(
                    "settlement does not match staged turn"
                )
            try:
                return self.store.commit_staged_turn(
                    key,
                    envelope.turn_id,
                    envelope.ledger_version,
                    ledger_version,
                ).context
            except (ContextVersionConflictError, KeyError) as exc:
                raise SettlementMismatchError(
                    "settlement compare-and-swap failed"
                ) from exc


__all__ = [
    "AgentOutcome",
    "AgentSelection",
    "ContextStageMetadata",
    "ConversationContextService",
    "PrepareStatus",
    "PreparedTurn",
    "SettlementMismatchError",
    "review_settlement_metadata_from_turn",
]
