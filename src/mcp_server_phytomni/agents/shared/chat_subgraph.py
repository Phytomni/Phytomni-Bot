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

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...runtime.langgraph_runner import invoke_graph
from ...runtime.operation_instrumentation_v2 import (
    instrument_operation_invocation,
)
from ..chat.service import _cached_chat_app
from .graph_routing import make_after_router

CHAT_APP: CompiledStateGraph = _cached_chat_app()

type ChatInvokeContextFactory = Callable[[], AbstractAsyncContextManager[None]]
type ChatOperationResolver = Callable[
    [Any], tuple[str, Mapping[str, Any]] | None
]


def _default_chat_input(state: Any) -> dict[str, Any]:
    """Project the common consumer payload into the chat input state."""
    return state["chat_payload"]


def _default_chat_output(chat_output: dict[str, Any]) -> Any:
    """Project the common chat response into the consumer state."""
    return chat_output.get("response") or {}


def make_chat_node_wrapper(
    *,
    build_input_fn: Callable[[Any], dict[str, Any]],
    extract_output_fn: Callable[[dict[str, Any]], Any],
    response_key: str,
    invoke_context_factory: ChatInvokeContextFactory | None = None,
    operation_resolver: ChatOperationResolver | None = None,
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
        chat_app = CHAT_APP
        chat_input = build_input_fn(state)

        async def invoke() -> dict[str, Any]:
            if invoke_context_factory is None:
                return await invoke_graph(chat_app, chat_input)
            async with invoke_context_factory():
                return await invoke_graph(chat_app, chat_input)

        operation = (
            operation_resolver(state)
            if operation_resolver is not None
            else None
        )
        chat_output = (
            await instrument_operation_invocation(
                operation[0],
                invoke,
                detail=operation[1],
            )
            if operation is not None
            else await invoke()
        )
        return {response_key: extract_output_fn(chat_output)}

    return _chat_node


def mount_chat_node(
    workflow: StateGraph[Any, Any, Any, Any],
    *,
    build_input_fn: Callable[[Any], dict[str, Any]] = _default_chat_input,
    extract_output_fn: Callable[[dict[str, Any]], Any] = _default_chat_output,
    response_key: str = "chat_response",
    invoke_context_factory: ChatInvokeContextFactory | None = None,
    operation_resolver: ChatOperationResolver | None = None,
) -> None:
    """Register the shared ``chat`` wrapper on a consumer workflow.

    This helper owns only the repeated node-registration call. Consumers may
    provide their own state projections and response key, and remain
    responsible for every edge and router around the node.

    Args:
        workflow: Uncompiled consumer ``StateGraph`` receiving the node.
        build_input_fn: Consumer-state to ``ChatInput`` projection. Defaults
            to the common ``state["chat_payload"]`` field.
        extract_output_fn: Chat final-state to response projection. Defaults
            to the common ``response`` field.
        response_key: Consumer state key receiving the projected response.
            Defaults to ``chat_response``.
        invoke_context_factory: Optional async context factory wrapping the
            shared chat invocation. Defaults to no additional context.
    """
    workflow.add_node(
        "chat",
        make_chat_node_wrapper(
            build_input_fn=build_input_fn,
            extract_output_fn=extract_output_fn,
            response_key=response_key,
            invoke_context_factory=invoke_context_factory,
            operation_resolver=operation_resolver,
        ),
    )


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

    return make_after_router(
        pending_post_key=pending_post_key,
        default=default,
        label="chat",
    )
