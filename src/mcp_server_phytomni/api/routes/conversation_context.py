# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP settlement and deletion for conversation context."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException

from ...runtime.conversation_context.locking import (
    acquire_review_mutation_lock,
)
from ...runtime.conversation_context.projection import agent_thread_id
from ...runtime.conversation_context.service import (
    _bounded_review_stage_metadata,
    review_settlement_metadata_from_turn,
)
from ...runtime.conversation_context.service_types import (
    _SYNC_CONTEXT_AGENTS,
)
from ...runtime.conversation_context.store import (
    ContextVersionConflictError,
    ConversationContextStore,
    ConversationTombstonedError,
    ReviewMutationLockTimeoutError,
    StoredTurn,
)
from ...runtime.langgraph_runner import ensure_checkpointer
from ..auth import ApiPrincipal
from ..schemas import (
    ContextMutationResponse,
    ContextSettlementRequest,
    ContextTombstoneRequest,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextRouteDependencies:
    """Dependencies required by the context protocol routes."""

    require_agents: Callable[..., Any]
    get_store: Callable[[], ConversationContextStore]
    acknowledge_review_settlement: Callable[..., Awaitable[bool]] | None = None


async def _delete_checkpoint_threads(
    conversation_key: Any,
    candidate_thread_ids: Sequence[str] = (),
) -> None:
    """Delete stable and durable turn-scoped threads through the saver."""
    checkpointer = ensure_checkpointer()
    stable_thread_ids = {
        agent_thread_id(conversation_key, agent_id)
        for agent_id in _SYNC_CONTEXT_AGENTS
    }
    for agent_id in _SYNC_CONTEXT_AGENTS:
        await checkpointer.adelete_thread(
            agent_thread_id(conversation_key, agent_id)
        )
    for candidate_thread_id in dict.fromkeys(candidate_thread_ids):
        if candidate_thread_id not in stable_thread_ids:
            await checkpointer.adelete_thread(candidate_thread_id)


async def _settle_review_turn(
    *,
    store: ConversationContextStore,
    key: str,
    payload: ContextSettlementRequest,
    dependencies: ContextRouteDependencies,
) -> ContextMutationResponse:
    """Reserve and promote Review while the cross-worker lock is held."""
    staged_turn, bounded_metadata = _load_and_validate_review_turn(
        store, key, payload
    )
    context = store.load_context(key)
    _validate_review_context_state(
        staged_turn, bounded_metadata, context, payload
    )
    settlement_state = bounded_metadata["settlement_state"]
    if settlement_state in {"pending", "settling", "promoting"}:
        await _acknowledge_review_settlement(
            key, payload, staged_turn, dependencies
        )
    settlement = _commit_review_turn(store, key, payload)
    return ContextMutationResponse(
        state=settlement.state,
        context_version=settlement.context.context_version,
    )


def _load_and_validate_review_turn(
    store: ConversationContextStore,
    key: str,
    payload: ContextSettlementRequest,
) -> tuple[StoredTurn, Mapping[str, Any]]:
    """Load and validate durable Review settlement metadata."""
    staged_turn = store.load_turn(key, payload.turn_id)
    if staged_turn is None:
        raise HTTPException(status_code=404, detail="context turn not found")
    has_private_marker = isinstance(staged_turn.stage_metadata, Mapping) and (
        "_review_settlement" in staged_turn.stage_metadata
    )
    review_metadata = review_settlement_metadata_from_turn(staged_turn)
    if review_metadata is None and has_private_marker:
        store.mark_turn_failed(key, payload.turn_id)
        raise HTTPException(
            status_code=503, detail="Review settlement metadata is invalid"
        )
    if review_metadata is None:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        )
    bounded_metadata = _bounded_review_stage_metadata(
        review_metadata, allow_terminal=True
    )
    try:
        expected_stable = agent_thread_id(UUID(key), "ReviewAgent")
    except ValueError:
        expected_stable = None
    if (
        bounded_metadata is None
        or bounded_metadata["turn_id"] != payload.turn_id
        or bounded_metadata["stable_thread_id"] != expected_stable
    ):
        store.mark_review_settlement_failed(
            key, payload.turn_id, mutation_lock_held=True
        )
        raise HTTPException(
            status_code=503, detail="Review settlement metadata is invalid"
        )
    return staged_turn, bounded_metadata


def _validate_review_context_state(
    staged_turn: StoredTurn,
    bounded_metadata: Mapping[str, Any],
    context: Any,
    payload: ContextSettlementRequest,
) -> None:
    """Validate context version and state before Review promotion."""
    if context is not None and context.state == "tombstoned":
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        )
    if staged_turn.ledger_version != payload.ledger_version:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        )
    if staged_turn.state == "staged":
        current_version = 0 if context is None else context.context_version
        if current_version != staged_turn.base_context_version:
            raise HTTPException(
                status_code=409, detail="context settlement conflict"
            )
    settlement_state = bounded_metadata["settlement_state"]
    if staged_turn.state not in {"staged", "committed"}:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        )
    if staged_turn.state == "committed" and settlement_state != "promoted":
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        )
    if settlement_state not in {
        "pending",
        "settling",
        "promoting",
        "promoted",
    }:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        )


async def _acknowledge_review_settlement(
    key: str,
    payload: ContextSettlementRequest,
    staged_turn: StoredTurn,
    dependencies: ContextRouteDependencies,
) -> None:
    """Ask the Review adapter to promote pending settlement metadata."""
    callback = dependencies.acknowledge_review_settlement
    if callback is None:
        raise HTTPException(
            status_code=503, detail="Review settlement is not available"
        )
    try:
        acknowledged = await callback(
            key,
            payload.turn_id,
            accepted=True,
            staged_turn=staged_turn,
            expected_ledger_version=payload.ledger_version,
            mutation_lock_held=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Review settlement is pending"
        ) from exc
    if not acknowledged:
        raise HTTPException(
            status_code=503, detail="Review settlement was not promoted"
        )


def _commit_review_turn(
    store: ConversationContextStore,
    key: str,
    payload: ContextSettlementRequest,
) -> Any:
    """Commit a validated Review turn and map storage failures to HTTP."""
    try:
        return store.commit_staged_turn(
            key,
            payload.turn_id,
            payload.ledger_version,
            payload.ledger_version,
            mutation_lock_held=True,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="context turn not found"
        ) from exc
    except ConversationTombstonedError as exc:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        ) from exc
    except ContextVersionConflictError as exc:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        ) from exc


async def _settle_review_context_route(
    store: ConversationContextStore,
    key: str,
    payload: ContextSettlementRequest,
    dependencies: ContextRouteDependencies,
) -> ContextMutationResponse:
    """Acquire the mutation lock and settle one Review context turn."""
    try:
        mutation_lock = await acquire_review_mutation_lock(store)
    except ReviewMutationLockTimeoutError as exc:
        raise HTTPException(
            status_code=503, detail="Review settlement is busy"
        ) from exc
    try:
        return await _settle_review_turn(
            store=store,
            key=key,
            payload=payload,
            dependencies=dependencies,
        )
    finally:
        mutation_lock.release()


async def _commit_context_route(
    store: ConversationContextStore,
    key: str,
    payload: ContextSettlementRequest,
) -> ContextMutationResponse:
    """Acquire the mutation lock and commit a non-Review context turn."""
    try:
        mutation_lock = await acquire_review_mutation_lock(store)
    except ReviewMutationLockTimeoutError as exc:
        raise HTTPException(
            status_code=503, detail="context settlement is busy"
        ) from exc
    try:
        settlement = store.commit_staged_turn(
            key,
            payload.turn_id,
            payload.ledger_version,
            payload.ledger_version,
            mutation_lock_held=True,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="context turn not found"
        ) from exc
    except ConversationTombstonedError as exc:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        ) from exc
    except ContextVersionConflictError as exc:
        raise HTTPException(
            status_code=409, detail="context settlement conflict"
        ) from exc
    finally:
        mutation_lock.release()
    return ContextMutationResponse(
        state=settlement.state,
        context_version=settlement.context.context_version,
    )


async def _settle_context_route(
    payload: ContextSettlementRequest,
    dependencies: ContextRouteDependencies,
) -> ContextMutationResponse:
    """Dispatch one settlement request to its Review or standard path."""
    store = dependencies.get_store()
    key = str(payload.conversation_key)
    staged_turn = store.load_turn(key, payload.turn_id)
    if staged_turn is None:
        raise HTTPException(status_code=404, detail="context turn not found")
    review_metadata = review_settlement_metadata_from_turn(staged_turn)
    has_private_marker = isinstance(staged_turn.stage_metadata, Mapping) and (
        "_review_settlement" in staged_turn.stage_metadata
    )
    if review_metadata is not None or has_private_marker:
        return await _settle_review_context_route(
            store, key, payload, dependencies
        )
    return await _commit_context_route(store, key, payload)


async def _tombstone_context_body(
    store: ConversationContextStore,
    key: str,
    payload: ContextTombstoneRequest,
) -> ContextMutationResponse:
    """Tombstone context state and perform best-effort checkpoint cleanup."""
    previous = store.load_context(key)
    candidate_thread_ids = store.tombstone(key, mutation_lock_held=True)
    try:
        await _delete_checkpoint_threads(
            payload.conversation_key, candidate_thread_ids
        )
    except (RuntimeError, OSError):
        logger.warning("conversation checkpoint cleanup deferred")
    else:
        store.complete_checkpoint_cleanup(key, mutation_lock_held=True)
    context = store.load_context(key)
    assert context is not None
    return ContextMutationResponse(
        state=(
            "already_applied"
            if previous is not None and previous.state == "tombstoned"
            else "tombstoned"
        ),
        context_version=context.context_version,
    )


async def _tombstone_context_route(
    payload: ContextTombstoneRequest,
    dependencies: ContextRouteDependencies,
) -> ContextMutationResponse:
    """Acquire the mutation lock and tombstone one context."""
    store = dependencies.get_store()
    key = str(payload.conversation_key)
    try:
        mutation_lock = await acquire_review_mutation_lock(store)
    except ReviewMutationLockTimeoutError as exc:
        raise HTTPException(
            status_code=503, detail="conversation deletion is busy"
        ) from exc
    try:
        return await _tombstone_context_body(store, key, payload)
    finally:
        mutation_lock.release()


def register_conversation_context_routes(
    app: FastAPI,
    dependencies: ContextRouteDependencies,
) -> None:
    """Register authenticated V1 context mutation routes."""

    @app.post(
        "/v1/conversation-context/settle",
        response_model=ContextMutationResponse,
    )
    async def settle_context(
        payload: ContextSettlementRequest,
        principal: ApiPrincipal = Depends(dependencies.require_agents),
    ) -> ContextMutationResponse:
        """Commit exactly one staged delta, or return its prior commit."""
        del principal
        return await _settle_context_route(payload, dependencies)

    @app.post(
        "/v1/conversation-context/tombstone",
        response_model=ContextMutationResponse,
    )
    async def tombstone_context(
        payload: ContextTombstoneRequest,
        principal: ApiPrincipal = Depends(dependencies.require_agents),
    ) -> ContextMutationResponse:
        """Clear context first, then finish idempotent checkpoint cleanup."""
        del principal
        return await _tombstone_context_route(payload, dependencies)


__all__ = [
    "ContextRouteDependencies",
    "register_conversation_context_routes",
]
