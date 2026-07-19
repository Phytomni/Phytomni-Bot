# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Data-only fakes for mounted Knowledge and DeepGenome subgraphs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome.coordinator import WorkItemOutcome

__all__ = [
    "DEEP_GENOME_GENERIC_NODE_NAMES",
    "RecordingMountApp",
    "RecordingKnowledgeApp",
    "assert_degraded_mount",
    "assert_subgraph_prefixes",
    "install_knowledge_app",
    "knowledge_output",
    "knowledge_state",
    "mount_app",
    "mount_host",
    "mount_state",
]


DEEP_GENOME_GENERIC_NODE_NAMES = (
    "gene_expression_tissues_node",
    "gene_expression_cultivars_node",
    "gene_expression_treatments_node",
    "gene_expression_genotypes_node",
    "single_cell_node",
    "promoter_node",
    "smep_node",
    "smoc_node",
    "protein_structure_node",
)


class _MountState(TypedDict, total=False):
    """Minimal common state accepted by the design/evolution mount fakes."""

    query: str
    species_code: str
    gene_id: str
    target_gene: str
    task_index: int
    target_taxids: str
    is_polling: bool
    design_task_result: list[Any]
    task_ids: dict[str, Any]
    failures: list[dict[str, Any]]
    evolution_agents_task: dict[str, Any]


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


def mount_state(
    kind: Literal["design", "evolution"], **overrides: object
) -> dict[str, Any]:
    """Return a fresh DeepGenome mount state for one mounted subgraph kind."""
    defaults = {
        "design": {
            "species_code": "osa",
            "target_gene": "g1",
            "task_index": 5,
        },
        "evolution": {
            "species_code": "osa",
            "target_gene": "g1",
            "task_index": 3,
        },
    }
    values = deepcopy(defaults[kind])
    values.update(overrides)
    return values


def assert_degraded_mount(
    delta: Mapping[str, Any],
    expected_kind: Literal["design", "evolution"],
) -> None:
    """Assert the shared degraded mount contract for one expected kind."""
    labels = {
        "design": "digital_design",
        "evolution": "evolution_analysis",
    }
    assert delta["analysis_completed_branches"] == 1
    failures = delta["failures"]
    assert isinstance(failures, list) and failures
    assert failures[0]["task_label"] == labels[expected_kind]
    records = delta["raw_analyst_data"]
    assert isinstance(records, Mapping)
    assert any(
        isinstance(record, Mapping) and record.get("status") == "failed"
        for record in records.values()
    )


class RecordingMountApp:
    """Compile and record a deterministic mounted-subgraph fake."""

    def __init__(
        self,
        state_schema: Any = _MountState,
        *,
        output: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._state_schema = state_schema
        self._output = deepcopy(dict(output or {}))
        self._error = error
        self._calls: list[dict[str, Any]] = []
        self.captured: dict[str, Any] = {}
        self.compiled = self.compile()

    def compile(self) -> CompiledStateGraph:
        """Build a real compiled graph so xray expansion remains testable."""

        async def _record(state: Any) -> dict[str, Any]:
            state_copy = deepcopy(dict(state))
            self._calls.append(state_copy)
            self.captured["input"] = state_copy
            if self._error is not None:
                raise self._error
            return deepcopy(self._output)

        workflow: StateGraph = StateGraph(self._state_schema)
        workflow.add_node("record", _record)
        workflow.add_edge(START, "record")
        workflow.add_edge("record", END)
        return workflow.compile()

    def snapshot(self) -> list[dict[str, Any]]:
        """Return an isolated copy of recorded mount calls."""
        return deepcopy(self._calls)


def mount_app(
    *,
    state_schema: Any = _MountState,
    output: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
) -> RecordingMountApp:
    """Build a compiled mount fake with deterministic output or failure."""
    return RecordingMountApp(
        state_schema,
        output=output,
        error=error,
    )


def mount_host(*, summary_key: str) -> Any:
    """Build the common DeepGenome finalize host used by mount tests."""
    downloaded: list[str] = []

    def _raise_if_agent_failed(result: dict) -> None:
        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError("agent failed")

    async def _download_analysis_result(
        _context: Any, output_path: str, _run_identity: Any
    ) -> str:
        downloaded.append(output_path)
        return f"{output_path}/results"

    async def _poll_remote_submission(
        submission: Any,
        context: Any,
        run_identity: Any,
        **_kwargs: Any,
    ) -> tuple[Any, str]:
        results_dir = await _download_analysis_result(
            context,
            submission.output_dir,
            run_identity,
        )
        return (
            WorkItemOutcome("succeeded", "# usable result", None),
            results_dir,
        )

    def _generate_sub_summary(
        *,
        analysis_type: str,
        gene_id: str,
        state: Any,
        results_dir: Any = None,
        display_order_override: int | None = None,
    ) -> dict[str, str]:
        del state, display_order_override
        return {summary_key: f"{analysis_type}:{gene_id}:{results_dir}"}

    return SimpleNamespace(
        deep_genome_config=SimpleNamespace(USER_ID="u"),
        _raise_if_agent_failed=_raise_if_agent_failed,
        _download_analysis_result=_download_analysis_result,
        _poll_remote_submission=_poll_remote_submission,
        _generate_sub_summary=_generate_sub_summary,
        downloaded=downloaded,
    )


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
