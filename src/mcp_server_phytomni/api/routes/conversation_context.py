# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Feature-gated HTTP settlement and deletion for conversation context."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException

from ...runtime.conversation_context.projection import agent_thread_id
from ...runtime.conversation_context.service import (
    _bounded_review_stage_metadata,
    review_settlement_metadata_from_turn,
)
from ...runtime.conversation_context.store import (
    ContextVersionConflictError,
    ConversationContextStore,
    ConversationTombstonedError,
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

_SYNC_CONTEXT_AGENTS = (
    "ChatAgent",
    "KnowledgeAgent",
    "DataAgent",
    "ReviewAgent",
    "BriefGeneAgent",
)


@dataclass(frozen=True, slots=True)
class ContextRouteDependencies:
    """Dependencies required by the optional context protocol routes."""

    enabled: Callable[[], bool]
    require_agents: Callable[..., Any]
    get_store: Callable[[], ConversationContextStore]
    acknowledge_review_settlement: Callable[..., Awaitable[bool]] | None = None


def _require_enabled(dependencies: ContextRouteDependencies) -> None:
    """Hide the mutation surface whenever V1 is disabled."""
    if not dependencies.enabled():
        raise HTTPException(status_code=404, detail="not found")


async def _delete_checkpoint_threads(
    conversation_key: Any,
    candidate_thread_ids: Sequence[str] = (),
) -> None:
    """Delete stable and durable turn-scoped threads through the active saver."""
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


def register_conversation_context_routes(
    app: FastAPI,
    dependencies: ContextRouteDependencies,
) -> None:
    """Register V1 context mutations behind auth and the runtime flag."""

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
        _require_enabled(dependencies)
        store = dependencies.get_store()
        key = str(payload.conversation_key)
        staged_turn: StoredTurn | None = store.load_turn(key, payload.turn_id)
        if staged_turn is None:
            raise HTTPException(
                status_code=404, detail="context turn not found"
            )
        review_metadata = review_settlement_metadata_from_turn(staged_turn)
        if review_metadata is not None:
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
                store.mark_review_settlement_failed(key, payload.turn_id)
                raise HTTPException(
                    status_code=503,
                    detail="Review settlement metadata is invalid",
                )
            context = store.load_context(key)
            if context is not None and context.state == "tombstoned":
                raise HTTPException(
                    status_code=409,
                    detail="context settlement conflict",
                )
            if staged_turn.ledger_version != payload.ledger_version:
                raise HTTPException(
                    status_code=409,
                    detail="context settlement conflict",
                )
            if staged_turn.state == "staged":
                current_version = (
                    0 if context is None else context.context_version
                )
                if current_version != staged_turn.base_context_version:
                    raise HTTPException(
                        status_code=409,
                        detail="context settlement conflict",
                    )
            settlement_state = bounded_metadata["settlement_state"]
            if staged_turn.state not in {"staged", "committed"}:
                raise HTTPException(
                    status_code=409,
                    detail="context settlement conflict",
                )
            if staged_turn.state == "committed" and settlement_state != (
                "promoted"
            ):
                raise HTTPException(
                    status_code=409,
                    detail="context settlement conflict",
                )
            if settlement_state == "settling":
                raise HTTPException(
                    status_code=503,
                    detail="Review settlement is pending",
                )
            if settlement_state not in {"pending", "promoted"}:
                raise HTTPException(
                    status_code=409,
                    detail="context settlement conflict",
                )
            if settlement_state == "pending":
                callback = dependencies.acknowledge_review_settlement
                if callback is None:
                    raise HTTPException(
                        status_code=503,
                        detail="Review settlement is not available",
                    )
                try:
                    acknowledged = await callback(
                        key,
                        payload.turn_id,
                        accepted=True,
                        staged_turn=staged_turn,
                        expected_ledger_version=payload.ledger_version,
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="Review settlement is pending",
                    ) from exc
                if not acknowledged:
                    raise HTTPException(
                        status_code=503,
                        detail="Review settlement was not promoted",
                    )
        try:
            settlement = store.commit_staged_turn(
                key,
                payload.turn_id,
                payload.ledger_version,
                payload.ledger_version,
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
        return ContextMutationResponse(
            state=settlement.state,
            context_version=settlement.context.context_version,
        )

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
        _require_enabled(dependencies)
        store = dependencies.get_store()
        key = str(payload.conversation_key)
        previous = store.load_context(key)
        candidate_thread_ids = store.tombstone(key)
        try:
            await _delete_checkpoint_threads(
                payload.conversation_key, candidate_thread_ids
            )
        except Exception:  # noqa: BLE001 - deletion remains retryable
            logger.warning("conversation checkpoint cleanup deferred")
        else:
            store.complete_checkpoint_cleanup(key)
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


__all__ = [
    "ContextRouteDependencies",
    "register_conversation_context_routes",
]
