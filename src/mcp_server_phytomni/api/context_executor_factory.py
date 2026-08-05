# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Build the Bot-owned conversation-context executor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..agents.review.agent import load_review_settlement_adapter
from ..runtime.conversation_context.adapters import ConversationContextExecutor
from ..runtime.conversation_context.store import StoredTurn


def build_context_executor(
    runtime: Any,
    *,
    app_attr: Callable[[str], Any],
    api_config_factory: Callable[[], Any],
    review_settlement_loader: Callable[
        [Mapping[str, Any], StoredTurn], Any
    ] = load_review_settlement_adapter,
) -> ConversationContextExecutor:
    """Build a lazy executor while preserving application patch seams."""

    async def select_agent(*args: Any, **kwargs: Any) -> Any:
        """Resolve the selector through the public app module."""
        return await app_attr("select_agent_tool")(*args, **kwargs)

    async def load_review_settlement(
        metadata: Mapping[str, Any], staged_turn: StoredTurn
    ) -> Any:
        """Rebuild Review promotion state through its agent seam."""
        return await review_settlement_loader(metadata, staged_turn)

    return ConversationContextExecutor(
        store_factory=runtime.get_conversation_context_store,
        select_agent=select_agent,
        api_config_factory=api_config_factory,
        review_settlement_loader=load_review_settlement,
    )
