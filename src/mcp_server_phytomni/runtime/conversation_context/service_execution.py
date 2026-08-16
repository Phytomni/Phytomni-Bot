# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small execution seams used by the conversation-context service."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from .models import (
    BusinessContext,
    ContextDelta,
    ContextProjection,
    ConversationEnvelopeV1,
)
from .projection import validate_context_delta
from .service_types import (
    _SYNC_CONTEXT_AGENTS,
    AgentOutcome,
    AgentSelection,
    AsyncAcceptanceError,
    AsyncAgentAcceptance,
    ContextStageMetadata,
    PreparedTurn,
    PrepareStatus,
)
from .store import StagedTurn, StoredTurn

if TYPE_CHECKING:
    from .service import ConversationContextService


_LOGGER = logging.getLogger(__name__)


_DISPLAY_OUTPUT_KEYS = frozenset(
    {
        "answer",
        "content",
        "full_report",
        "markdown",
        "output",
        "report",
        "table",
        "tabular",
    }
)
_PRIVATE_REVIEW_STAGE_KEY = "_review_settlement"


def _display_output_strings(value: object) -> tuple[str, ...]:
    """Collect values that are exposed as an agent's visible output."""
    outputs: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, Mapping):
            for key, nested in node.items():
                if str(key) in _DISPLAY_OUTPUT_KEYS:
                    if isinstance(nested, str):
                        outputs.append(nested)
                    else:
                        outputs.append(
                            json.dumps(
                                nested,
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            )
                        )
                visit(nested)
        elif isinstance(node, (list, tuple)):
            for nested in node:
                visit(nested)

    visit(value)
    return tuple(item.strip() for item in outputs if item.strip())


def _without_display_output(
    value: str | None, display_outputs: tuple[str, ...]
) -> str | None:
    """Drop free-form delta text that duplicates visible agent output."""
    if value is None:
        return None
    candidate = value.strip()
    if candidate and any(
        candidate in output or output in candidate
        for output in display_outputs
    ):
        return None
    return value


def _metadata_only_delta(
    delta: ContextDelta, result: Mapping[str, Any]
) -> ContextDelta:
    """Prevent visible answer/report/table text from entering Bot context."""
    display_outputs = _display_output_strings(result)
    if not display_outputs:
        return delta

    memory = delta.agent_memory_update
    if memory is not None:
        memory = memory.model_copy(
            update={
                "summary": _without_display_output(
                    memory.summary, display_outputs
                )
                or ""
            }
        )
    return delta.model_copy(
        update={
            "summary_update": _without_display_output(
                delta.summary_update, display_outputs
            ),
            "open_question_updates": [
                question
                for question in delta.open_question_updates
                if _without_display_output(question, display_outputs)
                is not None
            ],
            "agent_memory_update": memory,
        }
    )


async def select_agent_for_turn(
    service: ConversationContextService,
    envelope: ConversationEnvelopeV1,
    context: BusinessContext,
) -> tuple[AgentSelection, str]:
    """Select an agent and retain the source of the routing decision."""
    if envelope.mode == "instant":
        return AgentSelection("ChatAgent", "INSTANT_LOCK"), "instant_lock"
    if envelope.requested_agent_id is not None:
        return (
            AgentSelection(envelope.requested_agent_id, "EXPLICIT_SELECTION"),
            "explicit_selection",
        )
    selection = await service.router(
        envelope.current_message.content,
        tuple(envelope.allowed_agent_ids),
        context,
    )
    return selection, "router"


async def delegate_async_turn(
    service: ConversationContextService,
    request: _AsyncTurnRequest,
) -> PreparedTurn | None:
    """Run and stage an asynchronous-only agent, or return ``None``."""
    if request.selection.selected_agent_id in _SYNC_CONTEXT_AGENTS:
        return None
    try:
        acceptance = await service.delegate_async(
            request.selection.selected_agent_id, request.envelope
        )
    except BaseException:
        service.store.mark_turn_failed(request.key, request.envelope.turn_id)
        raise
    return finish_async_turn(service, request, acceptance)


async def invoke_sync_turn(
    service: ConversationContextService,
    key: str,
    envelope: ConversationEnvelopeV1,
    selection: AgentSelection,
    projection: ContextProjection,
) -> AgentOutcome:
    """Invoke one synchronous agent and mark cancellation/failure."""
    try:
        return await service.invoke(
            selection.selected_agent_id, envelope, projection
        )
    except BaseException:
        service.store.mark_turn_failed(key, envelope.turn_id)
        raise


def review_metadata_for_outcome(
    envelope: ConversationEnvelopeV1,
    projection: ContextProjection,
    outcome: AgentOutcome,
    *,
    is_review: bool,
    bounded_metadata: Any,
) -> tuple[dict[str, Any] | None, bool]:
    """Validate private Review metadata before it reaches durable state."""
    if not is_review:
        return None, False
    if (
        outcome.context_delta_error
        or outcome.context_delta is None
        or outcome.private_stage_metadata is None
    ):
        return None, True
    review_metadata = bounded_metadata(outcome.private_stage_metadata)
    invalid = (
        review_metadata is None
        or review_metadata["stable_thread_id"] != projection.agent_thread_id
        or review_metadata["turn_id"] != envelope.turn_id
    )
    return review_metadata, invalid


def context_delta_for_outcome(
    envelope: ConversationEnvelopeV1,
    selection: AgentSelection,
    outcome: AgentOutcome,
    *,
    is_review: bool,
) -> tuple[ContextDelta, bool, bool]:
    """Normalize and validate the metadata delta from an agent outcome."""
    if outcome.context_delta_error or outcome.context_delta is None:
        return ContextDelta(), True, False
    delta = _metadata_only_delta(outcome.context_delta, outcome.result)
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
        return ContextDelta(), True, is_review
    return delta, False, False


class _StageRequest(NamedTuple):
    """Values needed to persist one staged agent outcome."""

    key: str
    envelope: ConversationEnvelopeV1
    result: dict[str, Any]
    selection: AgentSelection
    route_source: str
    proposed: BusinessContext
    rebuilt: bool
    degraded: bool
    context_truncated: bool
    review_metadata: dict[str, Any] | None


class _SyncTurnRequest(NamedTuple):
    """Values carried from invocation into outcome settlement."""

    key: str
    envelope: ConversationEnvelopeV1
    context: BusinessContext
    rebuilt: bool
    selection: AgentSelection
    route_source: str
    projection: ContextProjection
    prepared_turn: PreparedTurn
    outcome: AgentOutcome


class _AsyncTurnRequest(NamedTuple):
    """Values carried from preparation into async acceptance settlement."""

    key: str
    envelope: ConversationEnvelopeV1
    context: BusinessContext
    rebuilt: bool
    selection: AgentSelection
    route_source: str
    prepared_turn: PreparedTurn


def stage_outcome(
    service: ConversationContextService,
    request: _StageRequest,
) -> tuple[StoredTurn, ContextStageMetadata]:
    """Persist the proposed context and return its public stage metadata."""
    key = request.key
    envelope = request.envelope
    selection = request.selection
    route_source = request.route_source
    proposed = request.proposed
    stage = ContextStageMetadata.from_public(
        {
            "selected_agent_id": selection.selected_agent_id,
            "route_source": route_source,
            "route_reason_code": selection.reason_code,
            "base_business_context_version": (
                envelope.base_business_context_version
            ),
            "proposed_business_context_version": (
                envelope.base_business_context_version + 1
            ),
            "last_applied_ledger_cursor": envelope.ledger_cursor,
            "context_truncated": request.context_truncated,
            "context_rebuilt": request.rebuilt,
            "context_degraded": request.degraded,
        }
    )
    stage_metadata: dict[str, Any] = stage.as_public_dict()
    if request.review_metadata is not None:
        stage_metadata[_PRIVATE_REVIEW_STAGE_KEY] = request.review_metadata
    stored = service.store.stage_turn(
        key,
        envelope.turn_id,
        StagedTurn(
            operation=envelope.operation,
            base_context_version=envelope.base_business_context_version,
            selected_agent_id=selection.selected_agent_id,
            route_source=route_source,
            result=request.result,
            delta=proposed.model_dump(mode="json"),
            ledger_version=envelope.ledger_version,
            schema_version=proposed.schema_version,
            ledger_cursor=envelope.ledger_cursor,
            observed_mode=envelope.mode,
            stage_metadata=stage_metadata,
        ),
    )
    return stored, stage


async def finish_sync_turn(
    service: ConversationContextService,
    request: _SyncTurnRequest,
    *,
    bounded_metadata: Any,
) -> PreparedTurn:
    """Validate an outcome, advance context, and persist its staged turn."""
    if request.outcome.status != "succeeded":
        service.store.mark_turn_failed(request.key, request.envelope.turn_id)
        return PreparedTurn(
            PrepareStatus.IN_PROGRESS,
            stored_turn=request.prepared_turn.stored_turn,
        )
    is_review = request.selection.selected_agent_id == "ReviewAgent"
    review_metadata, review_failed = review_metadata_for_outcome(
        request.envelope,
        request.projection,
        request.outcome,
        is_review=is_review,
        bounded_metadata=bounded_metadata,
    )
    if review_failed:
        service.store.mark_turn_failed(request.key, request.envelope.turn_id)
        return PreparedTurn(
            PrepareStatus.IN_PROGRESS,
            stored_turn=request.prepared_turn.stored_turn,
        )
    delta, degraded, delta_failed = context_delta_for_outcome(
        request.envelope,
        request.selection,
        request.outcome,
        is_review=is_review,
    )
    if delta_failed:
        service.store.mark_turn_failed(request.key, request.envelope.turn_id)
        return PreparedTurn(
            PrepareStatus.IN_PROGRESS,
            stored_turn=request.prepared_turn.stored_turn,
        )
    proposed = service.advance_context(
        request.context,
        request.envelope,
        delta,
        add_current_user_turn=not request.rebuilt,
    )
    try:
        stored, stage = stage_outcome(
            service,
            _StageRequest(
                key=request.key,
                envelope=request.envelope,
                result=request.outcome.result,
                selection=request.selection,
                route_source=request.route_source,
                proposed=proposed,
                rebuilt=request.rebuilt,
                degraded=degraded,
                context_truncated=request.projection.context_truncated,
                review_metadata=review_metadata,
            ),
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "conversation context stage degraded: stage=%s error=%s",
            "sync",
            exc.__class__.__name__,
        )
        return PreparedTurn(
            PrepareStatus.READY,
            context=proposed,
            projection=request.projection,
            stored_turn=request.prepared_turn.stored_turn,
            result=request.outcome.result,
            context_persistence_degraded=True,
        )
    return PreparedTurn(
        PrepareStatus.RETURN_STAGED,
        context=proposed,
        projection=request.projection,
        stored_turn=stored,
        result=request.outcome.result,
        stage=stage,
    )


def _durable_async_run_id(
    acceptance: AsyncAgentAcceptance,
) -> str | None:
    """Return a run id only when the native 202 contract is proven."""
    result = acceptance.result
    run_id = result.get("run_id")
    if (
        acceptance.status_code != 202
        or result.get("status") != "running"
        or not isinstance(run_id, str)
        or not run_id
        or result.get("id") != run_id
    ):
        return None
    return run_id


def finish_async_turn(
    service: ConversationContextService,
    request: _AsyncTurnRequest,
    acceptance: AsyncAgentAcceptance,
) -> PreparedTurn:
    """Advance and stage context after durable async acceptance."""
    if request.prepared_turn.stored_turn is None:
        raise AsyncAcceptanceError("async turn was not durably prepared")
    if _durable_async_run_id(acceptance) is None:
        service.store.mark_turn_failed(request.key, request.envelope.turn_id)
        raise AsyncAcceptanceError(
            "async agent response did not prove durable running acceptance"
        )
    proposed = service.advance_context(
        request.context,
        request.envelope,
        ContextDelta(),
        add_current_user_turn=not request.rebuilt,
    )
    try:
        stored, stage = stage_outcome(
            service,
            _StageRequest(
                key=request.key,
                envelope=request.envelope,
                result=acceptance.result,
                selection=request.selection,
                route_source=request.route_source,
                proposed=proposed,
                rebuilt=request.rebuilt,
                degraded=False,
                context_truncated=False,
                review_metadata=None,
            ),
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "conversation context stage degraded: stage=%s error=%s",
            "async",
            exc.__class__.__name__,
        )
        return PreparedTurn(
            PrepareStatus.READY,
            context=proposed,
            stored_turn=request.prepared_turn.stored_turn,
            result=acceptance.result,
            context_persistence_degraded=True,
        )
    return PreparedTurn(
        PrepareStatus.RETURN_STAGED,
        context=proposed,
        projection=None,
        stored_turn=stored,
        result=acceptance.result,
        stage=stage,
    )


__all__ = [
    "_metadata_only_delta",
    "context_delta_for_outcome",
    "delegate_async_turn",
    "finish_async_turn",
    "finish_sync_turn",
    "invoke_sync_turn",
    "review_metadata_for_outcome",
    "select_agent_for_turn",
    "stage_outcome",
]
