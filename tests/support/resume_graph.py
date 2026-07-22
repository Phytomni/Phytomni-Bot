# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared interrupting graph fixture for resume tests."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

__all__ = ["build_resume_app"]


class _ResumeState(TypedDict):
    """State for the minimal pause-and-resume graph."""

    value: str
    decision: NotRequired[dict[str, Any]]
    final: NotRequired[str]


def build_resume_app(checkpointer: Any) -> Any:
    """Build a graph that pauses once, then finalizes on approval."""

    async def gate(state: _ResumeState) -> dict[str, Any]:
        decision = interrupt({"draft": state["value"]})
        return {"decision": decision}

    async def finalize(state: _ResumeState) -> dict[str, str]:
        decision = state.get("decision", {})
        approved = decision.get("approved")
        return {"final": "ok" if approved else "redo"}

    graph = StateGraph(_ResumeState)
    graph.add_node("gate", gate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "gate")
    graph.add_edge("gate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
