# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Data-only fakes for tests that mount the Knowledge subgraph."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

__all__ = [
    "RecordingKnowledgeApp",
    "assert_subgraph_prefixes",
    "install_knowledge_app",
    "knowledge_output",
    "knowledge_state",
]


class _KnowledgeState(TypedDict, total=False):
    """Minimal state accepted by the compiled Knowledge subgraph fake."""

    user_query: str
    retrieved_docs: list[dict[str, Any]]
    final_response: dict[str, Any]


def knowledge_state(**overrides: object) -> dict[str, Any]:
    """Return a fresh minimal Knowledge state with caller overrides."""
    values: dict[str, Any] = {"retrieved_docs": []}
    values.update(overrides)
    return deepcopy(values)


def knowledge_output(content: str) -> dict[str, Any]:
    """Return the canonical Knowledge output used by dispatch tests."""
    return {
        "retrieved_docs": [],
        "final_response": {
            "choices": [{"message": {"content": content}}],
        },
    }


def assert_subgraph_prefixes(node_keys: Iterable[str], *prefixes: str) -> None:
    """Assert that each requested child-subgraph prefix is present."""
    keys = tuple(node_keys)
    for prefix in prefixes:
        assert any(
            key.startswith(prefix) for key in keys
        ), f"Missing {prefix!r} prefix (saw nodes: {sorted(keys)})"


def install_knowledge_app(
    monkeypatch: Any,
    target: str,
    *,
    docs_by_query: Mapping[str, list[dict[str, Any]]] | None = None,
    output: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
) -> RecordingKnowledgeApp:
    """Patch a module's builder and return its recording fake."""
    fake = RecordingKnowledgeApp(
        docs_by_query=docs_by_query,
        output=output,
        error=error,
    )
    monkeypatch.setattr(target, lambda **_kwargs: fake.compiled)
    return fake


class RecordingKnowledgeApp:
    """Compile and record a deterministic one-node Knowledge app."""

    def __init__(
        self,
        *,
        docs_by_query: Mapping[str, list[dict[str, Any]]] | None = None,
        output: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._docs_by_query = deepcopy(dict(docs_by_query or {}))
        self._output = deepcopy(dict(output)) if output is not None else None
        self._error = error
        self.compiled = self.compile()

    def compile(self) -> CompiledStateGraph:
        """Build a real compiled graph so xray expansion remains testable."""

        async def _record(state: _KnowledgeState) -> dict[str, Any]:
            """Capture input and return a fresh configured response."""
            state_copy = deepcopy(dict(state))
            self.calls.append(state_copy)
            if self._error is not None:
                raise self._error
            if self._output is not None:
                return deepcopy(self._output)
            query = str(state_copy.get("user_query", ""))
            return {
                "retrieved_docs": deepcopy(self._docs_by_query.get(query, [])),
            }

        workflow: StateGraph = StateGraph(_KnowledgeState)
        workflow.add_node("record", _record)
        workflow.add_edge(START, "record")
        workflow.add_edge("record", END)
        return workflow.compile()

    def snapshot(self) -> list[dict[str, Any]]:
        """Return an isolated copy of recorded calls for assertions."""
        return deepcopy(self.calls)
