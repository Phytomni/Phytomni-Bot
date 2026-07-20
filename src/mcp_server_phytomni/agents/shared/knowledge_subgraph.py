# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Structural wiring helpers for per-consumer KnowledgeAgent subgraphs.

Consumers register a knowledge node via ``add_node`` instead of
calling retrieval helpers inline so xray expands the knowledge
block. Unlike ``chat_subgraph.CHAT_APP`` (process-wide singleton),
each consumer needs a KA specialized to its own config, so the
compiled app is passed as a kwarg and the wrapper closure captures
it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...runtime.memory import MemoryGraphContext
from ..knowledge.agent import KnowledgeAgent
from ..knowledge.state import KnowledgeInput, KnowledgeOutput, KnowledgeState

type KnowledgeApp = CompiledStateGraph[
    KnowledgeState, MemoryGraphContext, KnowledgeInput, KnowledgeOutput
]


def build_knowledge_app(
    knowledge_config: Any,
    sensitive_config: Any | None = None,
) -> KnowledgeApp:
    """Compile a KnowledgeAgent subgraph for the given config.

    Each consumer agent should call this once at ``__init__`` time
    and store the returned :class:`CompiledStateGraph` on
    ``self.knowledge_app``; the consumer's ``_build_graph`` then
    passes ``self.knowledge_app`` to :func:`make_knowledge_node_wrapper`
    as a kwarg so the wrapper closure captures the compiled subgraph
    and parent ``find_subgraph_pregel`` discovers it at xray time.
    """
    return KnowledgeAgent(
        knowledge_config=knowledge_config,
        sensitive_config=sensitive_config,
    ).app


def make_knowledge_node_wrapper(
    *,
    knowledge_app: CompiledStateGraph[Any, Any, Any, Any],
    build_input_fn: Callable[[Any], dict[str, Any]],
    extract_output_fn: Callable[[dict[str, Any]], Any],
    response_key: str,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return an async node body that wires consumer state to KA IO.

    The wrapper closure captures ``knowledge_app``;
    ``find_subgraph_pregel`` walks the closure free variables at
    parent compile time so xray expands the knowledge block inside
    the consumer's render.

    Args:
        knowledge_app: Compiled KA subgraph for this consumer.
        build_input_fn: Pure mapping from consumer state to the
            ``KnowledgeInput`` dict the KA subgraph accepts.
        extract_output_fn: Pure mapping from the KA subgraph's
            final state to the value stored under ``response_key``.
        response_key: Name of the consumer state key to which the
            extracted KA response is written.

    Returns:
        Async callable suitable for ``StateGraph.add_node``; on
        invocation it returns a state delta dict containing only
        ``response_key``.
    """

    async def _knowledge_node(state: Any) -> dict[str, Any]:
        ki = cast(KnowledgeInput, build_input_fn(state))
        ko = await knowledge_app.ainvoke(ki)
        return {response_key: extract_output_fn(ko)}

    return _knowledge_node


def _default_knowledge_input(state: Any) -> dict[str, Any]:
    """Project the common consumer payload into KnowledgeAgent input."""
    return state["knowledge_payload"]


def _default_knowledge_output(knowledge_output: dict[str, Any]) -> Any:
    """Keep the common KnowledgeAgent output unchanged."""
    return knowledge_output


def mount_knowledge_node(
    workflow: StateGraph[Any, Any, Any, Any],
    *,
    knowledge_app: KnowledgeApp,
    build_input_fn: Callable[[Any], dict[str, Any]] = _default_knowledge_input,
    extract_output_fn: Callable[
        [dict[str, Any]], Any
    ] = _default_knowledge_output,
    response_key: str = "knowledge_response",
) -> None:
    """Register the shared ``knowledge`` wrapper on a consumer workflow.

    This helper owns only the repeated node-registration call. Consumers keep
    their own prep/post nodes, graph edges, state keys, and projections while
    the compiled app remains captured by the wrapper for xray discovery.

    Args:
        workflow: Uncompiled consumer ``StateGraph`` receiving the node.
        knowledge_app: Consumer-specific compiled KnowledgeAgent subgraph.
        build_input_fn: Consumer-state to ``KnowledgeInput`` projection.
            Defaults to ``state["knowledge_payload"]``.
        extract_output_fn: Knowledge final-state to response projection.
            Defaults to the unchanged final state.
        response_key: Consumer state key receiving the projected response.
            Defaults to ``knowledge_response``.
    """
    workflow.add_node(
        "knowledge",
        make_knowledge_node_wrapper(
            knowledge_app=knowledge_app,
            build_input_fn=build_input_fn,
            extract_output_fn=extract_output_fn,
            response_key=response_key,
        ),
    )


def make_knowledge_after_router(
    *,
    pending_post_key: str = "pending_post_knowledge",
    default: str | None = None,
) -> Callable[[Any], str]:
    """Return a router callable for ``add_conditional_edges``.

    Mirrors :func:`agents.shared.chat_subgraph.make_chat_after_router`
    but with a knowledge-scoped state key so consumer graphs that
    route through both chat and knowledge subgraphs do not collide
    on the same router slot.

    Args:
        pending_post_key: Name of the consumer state key carrying
            the post-knowledge node name.
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
            f"knowledge after-router has no branch: "
            f"state[{pending_post_key!r}] is unset or empty and no "
            "default was configured"
        )

    return _router
