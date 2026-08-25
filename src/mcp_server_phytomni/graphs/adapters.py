# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Adapter helpers for embedding compiled subgraphs inside parent graphs.

When a parent ``StateGraph`` and a child compiled subgraph share state
keys, the parent can attach the subgraph directly with
``parent.add_node("name", child_app)``. When their state schemas do
not overlap, ``adapter_node`` wraps the call: it projects the parent
state into the subgraph's input shape, invokes the subgraph, and
projects the subgraph's output back into parent-state updates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..runtime.langgraph_runner import invoke_graph


def adapter_node(
    map_in: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    compiled_subgraph: Any,
    map_out: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async parent-graph node bridging to a compiled subgraph.

    Args:
        map_in: Callable projecting the parent ``state`` into the
            subgraph's ``input_schema`` shape. Receives the parent
            state dict as-is; returns a mapping containing only the
            subgraph's declared input fields.
        compiled_subgraph: A compiled LangGraph application (the
            ``workflow.compile()`` result) whose ``ainvoke`` accepts
            the mapping returned by ``map_in``.
        map_out: Callable projecting the subgraph's final state into
            parent-state updates. Receives the subgraph's output dict;
            returns a dict containing only the parent fields the
            subgraph call should write back.

    Returns:
        An async callable suitable for ``parent.add_node("name", ...)``.
        Calling it with the parent state runs ``map_in``,
        ``compiled_subgraph.ainvoke(...)``, and ``map_out`` in
        sequence. Any exception raised by ``map_in``, the subgraph,
        or ``map_out`` propagates unchanged so parent-graph error
        routing sees the failure.
    """

    async def _adapter(state: Mapping[str, Any]) -> dict[str, Any]:
        sub_input = map_in(state)
        sub_output = await invoke_graph(compiled_subgraph, sub_input)
        return map_out(sub_output)

    return _adapter
