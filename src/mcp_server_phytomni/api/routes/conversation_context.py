# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Feature-gated HTTP settlement and deletion for conversation context."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from ...runtime.conversation_context.projection import agent_thread_id
from ...runtime.conversation_context.store import (
    ContextVersionConflictError,
    ConversationContextStore,
    ConversationTombstonedError,
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


def _require_enabled(dependencies: ContextRouteDependencies) -> None:
    """Hide the mutation surface whenever V1 is disabled."""
    if not dependencies.enabled():
        raise HTTPException(status_code=404, detail="not found")


async def _delete_checkpoint_threads(conversation_key: Any) -> None:
    """Delete every synchronous-agent thread through the active saver."""
    checkpointer = ensure_checkpointer()
    for agent_id in _SYNC_CONTEXT_AGENTS:
        await checkpointer.adelete_thread(
            agent_thread_id(conversation_key, agent_id)
        )


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
        store.tombstone(key)
        try:
            await _delete_checkpoint_threads(payload.conversation_key)
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
