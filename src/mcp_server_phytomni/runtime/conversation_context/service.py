# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Orchestrate one Bot conversation turn and its Go-ledger settlement."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from weakref import WeakValueDictionary

from ...agents.review.conversation import _candidate_thread_id
from ...config.defaults import ApiConfig
from ...contracts.conversation_context import CONTEXT_STAGE_FIELDS
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
)
from .review_support import _REVIEW_SETTLEMENT_STATES
from .service_execution import (
    _AsyncTurnRequest,
    _SyncTurnRequest,
    delegate_async_turn,
    finish_sync_turn,
    invoke_sync_turn,
    select_agent_for_turn,
)
from .service_types import (
    AgentOutcome,
    AgentSelection,
    AsyncAcceptanceError,
    AsyncAgentAcceptance,
    ContextStageMetadata,
    ContextStoreUnavailableError,
    PreparedTurn,
    PrepareStatus,
)
from .store import (
    ContextVersionConflictError,
    ConversationContextStore,
    StoredBusinessContext,
    StoredTurn,
)

for _service_type in (
    AsyncAcceptanceError,
    AsyncAgentAcceptance,
    AgentOutcome,
    AgentSelection,
    ContextStoreUnavailableError,
    PrepareStatus,
    PreparedTurn,
):
    _service_type.__module__ = __name__


class SettlementMismatchError(RuntimeError):
    """Raised when an acknowledgment does not match the staged proposal."""


_PRIVATE_REVIEW_STAGE_KEY = "_review_settlement"
_PUBLIC_STAGE_FIELDS = CONTEXT_STAGE_FIELDS
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
        "settlement_fence",
        "settlement_ledger_version",
        "settlement_base_context_version",
    }
)
_REVIEW_OPERATIONS = frozenset(
    {"new_review", "follow_up", "local_revision", "scope_change"}
)
_INVALID_REVIEW_FIELD = object()


def _bounded_integer(
    value: object,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    exact: int | None = None,
) -> int | None:
    """Return an integer that fits one bounded Review metadata field."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if exact is not None and value != exact:
        return None
    if minimum is not None and value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _bounded_text(
    value: object, *, limit: int, reject_path_chars: bool = False
) -> str | None:
    """Return non-empty text within a metadata limit."""
    if not isinstance(value, str):
        return None
    if value != value.strip() or not value:
        return None
    if len(value) > limit:
        return None
    if reject_path_chars and ("/" in value or "\\" in value):
        return None
    return value


def _bounded_review_stage_field(key: str, candidate: object) -> object:
    """Normalize one private Review stage field or return its invalid
    marker."""
    bounded: int | str | None
    if key in {"version", "report_revision"}:
        bounded = _bounded_integer(
            candidate,
            minimum=0 if key == "report_revision" else None,
            exact=1 if key == "version" else None,
        )
    elif key in {"settlement_claim_token", "settlement_claimed_at"}:
        bounded = _bounded_text(candidate, limit=64)
    elif key == "settlement_fence":
        bounded = _bounded_integer(candidate, minimum=1, maximum=2**63 - 1)
    elif key == "settlement_base_context_version":
        bounded = _bounded_integer(candidate, minimum=0)
    elif key == "settlement_ledger_version":
        bounded = _bounded_text(candidate, limit=64)
    elif key == "candidate_thread_id":
        if candidate is None:
            return None
        bounded = _bounded_text(candidate, limit=512, reject_path_chars=True)
    else:
        limit = 64 if key == "turn_id" else 512
        bounded = _bounded_text(
            candidate,
            limit=limit,
            reject_path_chars=key
            in {
                "operation",
                "stable_thread_id",
                "turn_id",
                "settlement_state",
            },
        )
    return _INVALID_REVIEW_FIELD if bounded is None else bounded


def _review_stage_identity(
    value: Mapping[str, Any], *, allow_terminal: bool
) -> tuple[str, str] | None:
    """Validate the operation and settlement state of a Review marker."""
    operation = value.get("operation")
    if not isinstance(operation, str) or operation not in _REVIEW_OPERATIONS:
        return None
    settlement_state = value.get("settlement_state")
    if not isinstance(settlement_state, str):
        return None
    if settlement_state not in _REVIEW_SETTLEMENT_STATES:
        return None
    if not allow_terminal and settlement_state != "pending":
        return None
    return operation, settlement_state


def _review_stage_candidate_valid(
    result: Mapping[str, Any], operation: str
) -> bool:
    """Ensure candidate checkpoint identity matches its Review operation."""
    candidate = result.get("candidate_thread_id")
    if operation in {"new_review", "scope_change"}:
        if not isinstance(candidate, str) or not candidate:
            return False
        return candidate == _candidate_thread_id(
            result["stable_thread_id"], result["turn_id"]
        )
    return candidate is None


_REVIEW_CLAIM_FIELDS = frozenset(
    {"settlement_claim_token", "settlement_claimed_at"}
)
_REVIEW_FENCING_FIELDS = frozenset(
    {
        "settlement_fence",
        "settlement_ledger_version",
        "settlement_base_context_version",
    }
)


def _review_claim_fields_present(
    result: Mapping[str, Any], settlement_state: str
) -> bool:
    """Validate presence and absence of claim fields for one state."""
    present = result.keys()
    if settlement_state == "promoted":
        return _REVIEW_FENCING_FIELDS.issubset(
            present
        ) and _REVIEW_CLAIM_FIELDS.isdisjoint(present)
    if settlement_state in {"settling", "promoting"}:
        return (_REVIEW_CLAIM_FIELDS | _REVIEW_FENCING_FIELDS).issubset(
            present
        )
    return _REVIEW_CLAIM_FIELDS.isdisjoint(present)


def _review_claim_timestamp_valid(
    result: Mapping[str, Any], settlement_state: str
) -> bool:
    """Validate the timestamp when a Review claim is active."""
    if settlement_state not in {"settling", "promoting"}:
        return True
    try:
        datetime.fromisoformat(result["settlement_claimed_at"])
    except (TypeError, ValueError):
        return False
    return True


def _review_settlement_claim_fields_valid(
    result: Mapping[str, Any], settlement_state: str
) -> bool:
    """Validate claim and promotion fields for one settlement state."""
    return _review_claim_fields_present(
        result, settlement_state
    ) and _review_claim_timestamp_valid(result, settlement_state)


def _review_settlement_fence_fields_valid(
    result: Mapping[str, Any], settlement_state: str
) -> bool:
    """Reject durable claim fencing fields on unclaimed markers."""
    if settlement_state not in {"pending", "rejected", "failed"}:
        return True
    return not any(field in result for field in _REVIEW_FENCING_FIELDS)


def _review_settlement_fields_valid(
    result: Mapping[str, Any], settlement_state: str
) -> bool:
    """Validate all state-dependent fields of a private Review marker."""
    return _review_settlement_claim_fields_valid(
        result, settlement_state
    ) and _review_settlement_fence_fields_valid(result, settlement_state)


def _bounded_review_stage_metadata(
    value: Mapping[str, Any],
    *,
    allow_terminal: bool = False,
) -> dict[str, Any] | None:
    """Keep only bounded checkpoint identities in durable turn metadata."""
    identity = _review_stage_identity(value, allow_terminal=allow_terminal)
    if identity is None:
        return None
    operation, settlement_state = identity
    result: dict[str, Any] = {}
    for key in _REVIEW_STAGE_FIELDS:
        bounded = _bounded_review_stage_field(key, value.get(key))
        if bounded is not _INVALID_REVIEW_FIELD:
            result[key] = bounded
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
    if not _review_stage_candidate_valid(result, operation):
        return None
    if not _review_settlement_fields_valid(result, settlement_state):
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
    [str, ConversationEnvelopeV1], Awaitable[AsyncAgentAcceptance]
]


@dataclass(frozen=True, slots=True)
class ConversationContextDependencies:
    """Collaborators required by one conversation-context service."""

    store: ConversationContextStore
    router: Router
    invoke: Invoker
    delegate_async: AsyncDelegator
    api_config: ApiConfig | None = None


class ConversationContextService:
    """Apply one V1 envelope and serialize work per conversation key.

    Always on in this process and independent of ``MEMORY_ENABLED``.
    The Go gateway decides whether to send an envelope. Transactions
    stay short; the same ``conversation_key`` never overlaps.
    """

    def __init__(
        self,
        store: ConversationContextStore | None = None,
        *,
        dependencies: ConversationContextDependencies | None = None,
        **legacy: Any,
    ) -> None:
        if dependencies is not None and (store is not None or legacy):
            raise TypeError(
                "dependencies cannot be combined with legacy collaborators"
            )
        if dependencies is None:
            if store is None:
                raise TypeError("store is required")
            dependencies = ConversationContextDependencies(
                store=store,
                router=legacy.pop("router"),
                invoke=legacy.pop("invoke"),
                delegate_async=legacy.pop("delegate_async"),
                api_config=legacy.pop("api_config", None),
            )
            if legacy:
                raise TypeError(
                    "unknown conversation service collaborator: "
                    + next(iter(legacy))
                )
        self.store = dependencies.store
        self.router = dependencies.router
        self.invoke = dependencies.invoke
        self.delegate_async = dependencies.delegate_async
        self.api_config = dependencies.api_config or ApiConfig()
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
        context: BusinessContext | None = None
        rebuilt = False
        failure: PrepareStatus | None = None
        if envelope.operation == "rebuild":
            if stored is not None and stored.state != "active":
                failure = PrepareStatus.REBUILD_REQUIRED
            else:
                context = self._rebuild_context(envelope)
                rebuilt = True
        elif stored is None:
            if envelope.base_business_context_version == 0:
                context = self._rebuild_context(envelope)
                rebuilt = True
            else:
                failure = PrepareStatus.REBUILD_REQUIRED
        elif (
            stored.state != "active"
            or stored.context_version != envelope.base_business_context_version
        ):
            failure = PrepareStatus.REBUILD_REQUIRED
        elif (
            stored.schema_version != 1
            or stored.observed_mode != envelope.mode
            or envelope.operation == "replace"
            or envelope.ledger_cursor != stored.ledger_cursor + 1
        ):
            # A replacement can invalidate every semantic fact retained at its
            # cursor and after it, so Go must resend an explicit rebuild.
            failure = PrepareStatus.REBUILD_REQUIRED
        else:
            try:
                context = BusinessContext.model_validate(stored.context)
            except ValueError:
                failure = PrepareStatus.REBUILD_REQUIRED
        if context is not None and stored is not None and not rebuilt:
            context_mismatch = (
                context.version != stored.context_version
                or context.last_applied_ledger_cursor != stored.ledger_cursor
                or context.last_applied_ledger_version != stored.ledger_version
            )
            if context_mismatch:
                failure = PrepareStatus.REBUILD_REQUIRED
                context = None
        return context, rebuilt, failure

    def _matches_duplicate(
        self, turn: StoredTurn, envelope: ConversationEnvelopeV1
    ) -> bool:
        """Reject retries that change an owned turn proposal."""
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

    def _should_reopen_existing_turn(
        self, turn: StoredTurn, envelope: ConversationEnvelopeV1
    ) -> bool:
        """Allow replace/rebuild to supersede a finished same-id turn."""
        if envelope.operation not in {"replace", "rebuild"}:
            return False
        if turn.state == "staged":
            return False
        if (
            turn.operation == envelope.operation
            and turn.base_context_version
            == envelope.base_business_context_version
            and turn.state == "in_progress"
        ):
            return False
        return not (
            turn.state == "committed"
            and self._matches_duplicate(turn, envelope)
        )

    def _project_existing_turn(
        self,
        turn: StoredTurn,
        envelope: ConversationEnvelopeV1,
        *,
        inspect_only: bool,
    ) -> PreparedTurn:
        """Return the durable replay state without creating a turn."""
        if (
            turn.operation != envelope.operation
            or turn.base_context_version
            != envelope.base_business_context_version
        ):
            raise ValueError("duplicate turn proposal does not match")
        if turn.state == "staged":
            if turn.ledger_version != envelope.ledger_version:
                raise ValueError("duplicate turn proposal does not match")
            return PreparedTurn(
                PrepareStatus.RETURN_STAGED,
                stored_turn=turn,
                result=turn.result,
                stage=self._stage_from_turn(turn),
            )
        if turn.state == "committed":
            if not inspect_only and not self._matches_duplicate(
                turn, envelope
            ):
                raise ValueError("duplicate turn proposal does not match")
            return PreparedTurn(
                PrepareStatus.RETURN_COMMITTED,
                stored_turn=turn,
                result=turn.result,
                stage=self._stage_from_turn(turn),
            )
        if turn.state == "failed" and not inspect_only:
            _context, _rebuilt, failure = self._context_for(
                envelope, self.store.load_context(self._key(envelope))
            )
            if failure is not None:
                return PreparedTurn(failure, stored_turn=turn)
        return PreparedTurn(PrepareStatus.IN_PROGRESS, stored_turn=turn)

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
            if self._should_reopen_existing_turn(turn, envelope):
                turn = self.store.reopen_turn(
                    key,
                    envelope.turn_id,
                    envelope.operation,
                    envelope.base_business_context_version,
                )
            else:
                return self._project_existing_turn(
                    turn, envelope, inspect_only=False
                )
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
        """Validate, deduplicate, and prepare without invoking an agent."""
        if not isinstance(envelope, ConversationEnvelopeV1):
            raise TypeError("envelope must be ConversationEnvelopeV1")
        async with self._lock(self._key(envelope)):
            return await self._prepare_locked(envelope)

    async def inspect_replay(
        self, envelope: ConversationEnvelopeV1
    ) -> PreparedTurn | None:
        """Return a matching existing turn without beginning lifecycle work."""
        if not isinstance(envelope, ConversationEnvelopeV1):
            raise TypeError("envelope must be ConversationEnvelopeV1")
        key = self._key(envelope)
        async with self._lock(key):
            turn = self.store.load_turn(key, envelope.turn_id)
            if turn is None:
                return None
            try:
                return self._project_existing_turn(
                    turn, envelope, inspect_only=True
                )
            except ValueError:
                # A replace/rebuild probe is not a matching replay; the
                # prepare path reopens the same durable turn id.
                return None

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
        return ContextStageMetadata.from_public(public_metadata)

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
            try:
                prepared = await self._prepare_locked(envelope)
            except (sqlite3.Error, OSError):
                raise ContextStoreUnavailableError from None
            if prepared.status is not PrepareStatus.READY:
                return prepared
            assert (
                prepared.context is not None
                and prepared.stored_turn is not None
            )
            context = prepared.context
            rebuilt = prepared.context.version == 0
            selection, route_source = await select_agent_for_turn(
                self, envelope, context
            )
            if selection.selected_agent_id not in envelope.allowed_agent_ids:
                self.store.mark_turn_failed(key, envelope.turn_id)
                raise ValueError(
                    "selected agent is outside the envelope allowlist"
                )
            delegated = await delegate_async_turn(
                self,
                _AsyncTurnRequest(
                    key=key,
                    envelope=envelope,
                    context=context,
                    rebuilt=rebuilt,
                    selection=selection,
                    route_source=route_source,
                    prepared_turn=prepared,
                ),
            )
            if delegated is not None:
                return delegated
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
            outcome = await invoke_sync_turn(
                self, key, envelope, selection, projection
            )
            return await finish_sync_turn(
                self,
                _SyncTurnRequest(
                    key=key,
                    envelope=envelope,
                    context=context,
                    rebuilt=rebuilt,
                    selection=selection,
                    route_source=route_source,
                    projection=projection,
                    prepared_turn=prepared,
                    outcome=outcome,
                ),
                bounded_metadata=_bounded_review_stage_metadata,
            )

    @staticmethod
    def advance_context(
        context: BusinessContext,
        envelope: ConversationEnvelopeV1,
        delta: ContextDelta,
        *,
        add_current_user_turn: bool,
    ) -> BusinessContext:
        """Apply a validated metadata delta without display output."""
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

    _advance_context = advance_context

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
    "AsyncAcceptanceError",
    "AsyncAgentAcceptance",
    "ConversationContextService",
    "AgentOutcome",
    "AgentSelection",
    "ContextStageMetadata",
    "ContextStoreUnavailableError",
    "SettlementMismatchError",
    "PrepareStatus",
    "PreparedTurn",
    "review_settlement_metadata_from_turn",
]
