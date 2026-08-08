# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Data-only fakes for mounted Knowledge and DeepGenome subgraphs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome.coordinator import WorkItemOutcome

__all__ = [
    "ANALYST_CHAT_MOUNT_TOPOLOGY",
    "BRIEF_GENE_CHAT_MOUNT_TOPOLOGY",
    "BRIEF_GENE_GRAPH_NODE_NAMES",
    "BRIEF_GENE_STATE_PREAMBLE_FIELDS",
    "ChatMountTopology",
    "DATA_CHAT_MOUNT_TOPOLOGY",
    "assert_agent_chat_mount_topology",
    "assert_chat_mount_topology",
    "DEEP_GENOME_GENERIC_NODE_NAMES",
    "KNOWLEDGE_CHAT_MOUNT_TOPOLOGY",
    "RecordingMountApp",
    "RecordingKnowledgeApp",
    "REVIEW_CHAT_MOUNT_TOPOLOGY",
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


BRIEF_GENE_GRAPH_NODE_NAMES = frozenset(
    {
        "query_judge_node",
        "fetch_annotation_node",
        "fetch_homology_interactions_node",
        "retrieve_prep_tasks_node",
        "retrieve_worker_node",
        "retrieve_reduce_node",
        "section_discovery_node",
        "section_cloning_node",
        "section_functional_node",
        "section_application_node",
        "introduction_node",
        "render_node",
        "follow_up_prep_node",
        "follow_up_post_node",
        "chat",
    }
)


@dataclass(frozen=True)
class ChatMountTopology:
    """Expected public and structural contract for one chat-mount graph."""

    input_fields: set[str]
    required_input_fields: set[str]
    output_fields: set[str]
    expected_nodes: set[str]
    expected_edges: set[tuple[str, str, bool]]


ANALYST_CHAT_MOUNT_TOPOLOGY = ChatMountTopology(
    input_fields={
        "query",
        "goal_description",
        "preset_plan",
        "data_list",
        "obs_file_list",
        "compute_resource",
        "output_dir",
        "output_dir_is_result_child",
        "is_polling",
        "is_auto_select",
        "is_preset_plan",
        "locale",
        "dispatch_fingerprint",
        "input_fingerprint",
        "research_grant_sidecar",
    },
    required_input_fields={"query"},
    output_fields={
        "task_id",
        "output_dir",
        "job_name",
        "compute_resource",
        "plan",
        "tool_usages",
        "task_status",
        "goal_description",
        "method_context",
        "plan_feedback",
        "plan_retries",
        "extracted_tools",
        "error_detail",
    },
    expected_nodes={
        "__end__",
        "__start__",
        "chat",
        "check_post_node",
        "check_prep_node",
        "data_select_post_node",
        "data_select_prep_node",
        "knowledge",
        "method_retrieve_post_node",
        "method_retrieve_prep_node",
        "parse_query_post_node",
        "parse_query_prep_node",
        "plan_post_node",
        "plan_prep_node",
        "pooling_node",
        "submit_node",
        "tool_extract_post_node",
        "tool_extract_prep_node",
        "tool_retrieve_node",
    },
    expected_edges={
        ("__start__", "parse_query_prep_node", False),
        ("chat", "check_post_node", True),
        ("chat", "data_select_post_node", True),
        ("chat", "parse_query_post_node", True),
        ("chat", "plan_post_node", True),
        ("chat", "tool_extract_post_node", True),
        ("check_post_node", "plan_prep_node", True),
        ("check_post_node", "tool_extract_prep_node", True),
        ("check_prep_node", "chat", True),
        ("check_prep_node", "check_post_node", True),
        ("data_select_post_node", "method_retrieve_prep_node", True),
        ("data_select_post_node", "tool_extract_prep_node", True),
        ("data_select_prep_node", "chat", False),
        ("knowledge", "method_retrieve_post_node", True),
        ("method_retrieve_post_node", "plan_prep_node", False),
        ("method_retrieve_prep_node", "knowledge", False),
        ("parse_query_post_node", "data_select_prep_node", True),
        ("parse_query_post_node", "method_retrieve_prep_node", True),
        ("parse_query_post_node", "tool_extract_prep_node", True),
        ("parse_query_prep_node", "chat", True),
        ("parse_query_prep_node", "parse_query_post_node", True),
        ("plan_post_node", "check_prep_node", False),
        ("plan_prep_node", "chat", False),
        ("pooling_node", "__end__", True),
        ("pooling_node", "pooling_node", True),
        ("submit_node", "__end__", True),
        ("submit_node", "pooling_node", True),
        ("tool_extract_post_node", "tool_retrieve_node", False),
        ("tool_extract_prep_node", "chat", False),
        ("tool_retrieve_node", "submit_node", False),
    },
)


BRIEF_GENE_CHAT_MOUNT_TOPOLOGY = ChatMountTopology(
    input_fields={"user_query", "is_follow_up", "locale"},
    required_input_fields={"user_query"},
    output_fields={
        "gene_id",
        "species_code",
        "go_string",
        "kegg_string",
        "interpro_string",
        "description_string",
        "gene_structure_string",
        "orthologs_data",
        "paralogs_data",
        "interaction_data",
        "section1_markdown",
        "section2_markdown",
        "section3_markdown",
        "section4_markdown",
        "introduction_report",
        "retrieved_docs",
        "final_response",
        "follow_up_questions",
    },
    expected_nodes={
        "__end__",
        "__start__",
        "chat",
        "fetch_annotation_node",
        "fetch_homology_interactions_node",
        "follow_up_post_node",
        "follow_up_prep_node",
        "introduction_node",
        "query_judge_node",
        "render_node",
        "retrieve_prep_tasks_node",
        "retrieve_reduce_node",
        "retrieve_worker_node",
        "section_application_node",
        "section_cloning_node",
        "section_discovery_node",
        "section_functional_node",
    },
    expected_edges={
        ("__start__", "query_judge_node", False),
        ("chat", "follow_up_post_node", True),
        ("fetch_annotation_node", "retrieve_prep_tasks_node", False),
        ("fetch_homology_interactions_node", "__end__", False),
        ("follow_up_post_node", "__end__", False),
        ("follow_up_prep_node", "chat", False),
        ("introduction_node", "render_node", False),
        ("query_judge_node", "fetch_annotation_node", True),
        ("query_judge_node", "fetch_homology_interactions_node", False),
        ("query_judge_node", "retrieve_prep_tasks_node", True),
        ("render_node", "__end__", True),
        ("render_node", "follow_up_prep_node", True),
        ("retrieve_prep_tasks_node", "retrieve_worker_node", True),
        ("retrieve_reduce_node", "section_application_node", False),
        ("retrieve_reduce_node", "section_cloning_node", False),
        ("retrieve_reduce_node", "section_discovery_node", False),
        ("retrieve_reduce_node", "section_functional_node", False),
        ("retrieve_worker_node", "retrieve_reduce_node", False),
        ("section_application_node", "introduction_node", False),
        ("section_cloning_node", "introduction_node", False),
        ("section_discovery_node", "introduction_node", False),
        ("section_functional_node", "introduction_node", False),
    },
)

BRIEF_GENE_STATE_PREAMBLE_FIELDS = {
    "orthologs_data",
    "paralogs_data",
    "interaction_data",
    "ortholog_count",
    "ortholog_species_count",
    "paralog_count",
    "interaction_count",
    "cross_species_alias_count",
    "cross_species_alias_species_count",
    "gene_structure_string",
    "section1_markdown",
    "section2_markdown",
    "section3_markdown",
    "section4_markdown",
    "introduction_report",
    "gene_profile_completed_branches",
}


DATA_CHAT_MOUNT_TOPOLOGY = ChatMountTopology(
    input_fields={"user_query", "is_rewrite", "locale", "dialog_id"},
    required_input_fields={"user_query"},
    output_fields={"final_response"},
    expected_nodes={
        "__end__",
        "__start__",
        "chat",
        "knowledge",
        "retrieve_post_node",
        "retrieve_prep_node",
        "rewrite_post_node",
        "rewrite_prep_node",
        "search_node",
    },
    expected_edges={
        ("__start__", "retrieve_prep_node", True),
        ("__start__", "search_node", True),
        ("chat", "rewrite_post_node", False),
        ("knowledge", "retrieve_post_node", True),
        ("retrieve_post_node", "rewrite_prep_node", False),
        ("retrieve_prep_node", "knowledge", False),
        ("rewrite_post_node", "search_node", False),
        ("rewrite_prep_node", "chat", False),
        ("search_node", "__end__", False),
    },
)


KNOWLEDGE_CHAT_MOUNT_TOPOLOGY = ChatMountTopology(
    input_fields={
        "user_query",
        "obs_file_list",
        "repo_id_dict",
        "is_generate",
        "is_follow_up",
        "locale",
        "conversation_messages",
        "retrieval_query",
        "answer_context",
    },
    required_input_fields={"user_query"},
    output_fields={"retrieved_docs", "final_response"},
    expected_nodes={
        "__end__",
        "__start__",
        "chat",
        "follow_up_post_node",
        "follow_up_prep_node",
        "generate_post_node",
        "generate_prep_node",
        "process_files_node",
        "retrieve_node",
    },
    expected_edges={
        ("__start__", "process_files_node", True),
        ("__start__", "retrieve_node", True),
        ("chat", "follow_up_post_node", True),
        ("chat", "generate_post_node", True),
        ("follow_up_post_node", "__end__", False),
        ("follow_up_prep_node", "chat", False),
        ("generate_post_node", "__end__", True),
        ("generate_post_node", "follow_up_prep_node", True),
        ("generate_prep_node", "chat", False),
        ("process_files_node", "retrieve_node", False),
        ("retrieve_node", "__end__", True),
        ("retrieve_node", "generate_prep_node", True),
    },
)


REVIEW_CHAT_MOUNT_TOPOLOGY = ChatMountTopology(
    input_fields={"original_user_query", "obs_file_list", "locale"},
    required_input_fields={"original_user_query"},
    output_fields={"final_response", "summary_content"},
    expected_nodes={
        "__end__",
        "__start__",
        "approval_node",
        "chat",
        "draft_dispatch",
        "draft_reduce_node",
        "draft_worker_node",
        "follow_up_post_node",
        "follow_up_prep_node",
        "plan_query_post_node",
        "plan_query_prep_node",
        "retrieve_dispatch",
        "retrieve_reduce_node",
        "retrieve_worker_node",
        "review_results_dispatch",
        "review_results_reduce_node",
        "review_results_worker_node",
        "revised_dispatch",
        "revised_reduce_node",
        "revised_worker_node",
        "summary_post_node",
        "summary_prep_node",
    },
    expected_edges={
        ("__start__", "plan_query_prep_node", False),
        ("approval_node", "follow_up_prep_node", True),
        ("approval_node", "summary_prep_node", True),
        ("chat", "follow_up_post_node", True),
        ("chat", "plan_query_post_node", True),
        ("chat", "summary_post_node", True),
        ("draft_dispatch", "draft_worker_node", True),
        ("draft_reduce_node", "review_results_dispatch", False),
        ("draft_worker_node", "draft_reduce_node", False),
        ("follow_up_post_node", "__end__", False),
        ("follow_up_prep_node", "chat", False),
        ("plan_query_post_node", "retrieve_dispatch", False),
        ("plan_query_prep_node", "chat", False),
        ("retrieve_dispatch", "retrieve_worker_node", True),
        ("retrieve_reduce_node", "draft_dispatch", False),
        ("retrieve_worker_node", "retrieve_reduce_node", False),
        ("review_results_dispatch", "review_results_worker_node", True),
        ("review_results_reduce_node", "revised_dispatch", False),
        ("review_results_worker_node", "review_results_reduce_node", False),
        ("revised_dispatch", "revised_worker_node", True),
        ("revised_reduce_node", "summary_prep_node", False),
        ("revised_worker_node", "revised_reduce_node", False),
        ("summary_post_node", "approval_node", False),
        ("summary_prep_node", "chat", False),
    },
)


def assert_chat_mount_topology(
    app: Any,
    checkpointer: Any,
    topology: ChatMountTopology,
) -> None:
    """Assert the public and structural contract of a chat-mount graph.

    The five consumer graphs intentionally keep different state schemas and
    routes, but their shared ``chat`` mount must remain visible and
    checkpointer-backed. Keeping the assertions here makes each consumer test
    declare only its own contract data instead of duplicating inspection code.
    """
    input_schema = app.get_input_schema().model_json_schema()
    definitions = input_schema.get("$defs", {})
    input_ref = input_schema.get("$ref")
    input_name = input_ref.rsplit("/", maxsplit=1)[-1] if input_ref else None
    input_definition = (
        definitions[input_name]
        if input_name is not None
        else next(iter(definitions.values()))
    )
    assert set(input_definition["properties"]) == topology.input_fields
    assert (
        set(input_definition.get("required", ()))
        == topology.required_input_fields
    )

    output_schema = app.get_output_schema().model_json_schema()
    assert set(output_schema["properties"]) == topology.output_fields

    graph = app.get_graph()
    assert {
        node.id for node in graph.nodes.values()
    } == topology.expected_nodes
    assert {
        (edge.source, edge.target, edge.conditional) for edge in graph.edges
    } == topology.expected_edges

    xray_nodes = app.get_graph(xray=True).nodes
    assert any(name.startswith("chat:") for name in xray_nodes)
    assert isinstance(checkpointer, InMemorySaver)


def assert_agent_chat_mount_topology(
    agent: Any,
    topology: ChatMountTopology,
) -> None:
    """Apply a topology contract to an agent's app and checkpoint."""
    assert_chat_mount_topology(agent.app, agent.checkpointer, topology)


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
