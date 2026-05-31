# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Structural wiring helpers for the shared chat subgraph.

Consumer agents register a chat node via ``add_node`` instead of
calling the chat service inline so LangGraph's xray traversal can
expand the chat block inside the consumer's render. The module-level
:data:`CHAT_APP` constant closes over the factory-built wrapper so
``find_subgraph_pregel`` discovers the compiled subgraph at the
parent graph's ``compile()`` time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..chat.service import _cached_chat_app

CHAT_APP: CompiledStateGraph = _cached_chat_app()


def make_chat_node_wrapper(
    *,
    build_input_fn: Callable[[Any], dict[str, Any]],
    extract_output_fn: Callable[[dict[str, Any]], Any],
    response_key: str,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return an async node body that adapts consumer state to chat IO.

    The returned coroutine projects the consumer's state into a
    ``ChatInput`` dict, awaits the module-level :data:`CHAT_APP`, and
    projects the chat subgraph's final state back into a single-key
    state delta. Holding the compiled chat app at module scope (not
    inside the closure) keeps the wrapper hashable and lets
    ``find_subgraph_pregel`` discover the subgraph through the
    closure's ``__globals__`` lookup at parent compile time.

    Args:
        build_input_fn: Pure mapping from consumer state to the
            ``ChatInput`` dict the chat subgraph accepts.
        extract_output_fn: Pure mapping from the chat subgraph's
            final state to the value stored under ``response_key``.
        response_key: Name of the consumer state key to which the
            extracted chat response is written.

    Returns:
        Async callable suitable for ``StateGraph.add_node``; on
        invocation it returns a state delta dict containing only
        ``response_key``. The return type uses ``Callable[..., ...]``
        so the same wrapper unifies with each consumer's typed state
        TypedDict at registration time.
    """

    async def _chat_node(state: Any) -> dict[str, Any]:
        chat_input = build_input_fn(state)
        chat_output = await CHAT_APP.ainvoke(chat_input)
        return {response_key: extract_output_fn(chat_output)}

    return _chat_node


def make_chat_after_router(
    *,
    pending_post_key: str = "pending_post",
    default: str | None = None,
) -> Callable[[Any], str]:
    """Return a router callable for ``add_conditional_edges``.

    Consumer agents stage the name of the post-chat node on
    ``state[pending_post_key]`` before the chat node runs; the
    returned router reads that key to branch back to the staged node
    after the shared chat subgraph completes. When the key is missing
    or empty the router falls back to ``default``; when no default is
    configured either, the router raises ``ValueError`` so a misuse
    surfaces immediately rather than silently routing the graph to a
    wrong successor.

    Args:
        pending_post_key: Name of the consumer state key carrying the
            post-chat node name.
        default: Optional fallback node name when the state key is
            unset or empty.

    Returns:
        Callable suitable for ``add_conditional_edges`` that returns
        the next node name.
    """

    def _router(state: Any) -> str:
        pending = state.get(pending_post_key)
        if isinstance(pending, str) and pending:
            return pending
        if default is not None:
            return default
        raise ValueError(
            f"chat after-router has no branch: state[{pending_post_key!r}] "
            "is unset or empty and no default was configured"
        )

    return _router
