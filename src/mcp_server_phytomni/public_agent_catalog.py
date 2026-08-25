# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical, transport-neutral metadata for public Phytomni agents.

This module intentionally imports no agent implementation.  Runtime adapters
derive their maps from these immutable records, while drift tests resolve the
named schema and handler objects at their owning boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, NamedTuple

from .runtime.execution_events import EXECUTION_EVENTS_CAPABILITY_V1

Lifecycle = Literal["synchronous", "resumable", "asynchronous"]
Driver = Literal[
    "local_graph",
    "remote_task",
    "remote_fanout",
    "resumable_graph",
    "hybrid",
]
EXECUTION_DRIVER_ORDER: tuple[Driver, ...] = (
    "local_graph",
    "remote_task",
    "remote_fanout",
    "resumable_graph",
    "hybrid",
)
Topology = Literal["serial", "conditional", "parallel", "hybrid"]
CheckpointPolicy = Literal["none", "graph", "coordinator"]
ResumePolicy = Literal["none", "action", "recovery", "action_and_recovery"]
CancellationPolicy = Literal[
    "cooperative", "best_effort", "requested_confirmed"
]
RetryPolicy = Literal["none", "bounded", "provider_bounded"]
JoinPolicy = Literal[
    "not_applicable", "all", "fail_fast", "best_effort", "quorum"
]
DispatchAdapter = Literal["execution_runtime"]
PublicSummaryPolicy = Literal["none", "explicit"]
TraceProducer = Literal["local_semantic", "structured_provider", "hybrid"]

_ANALYSIS_TASK_EXECUTION_DEADLINE_SECONDS = 90_000


class PublicAgentSpec(NamedTuple):
    """One public identity and execution contract."""

    enum_key: str
    tool: str
    slug: str
    schema: str
    handler: str
    lifecycle: Lifecycle
    modes: tuple[str, ...]
    result_contract: str
    driver: Driver
    topology: Topology
    checkpoint: CheckpointPolicy
    resume: ResumePolicy
    cancellation: CancellationPolicy
    deadline_seconds: int
    retry: RetryPolicy
    max_attempts: int
    join: JoinPolicy
    result_mapper: str
    event_contract: str
    artifact_roles: tuple[str, ...]
    transport_views: tuple[str, ...]
    dispatch_adapter: DispatchAdapter
    todo_phases: tuple[str, ...]
    public_summary: PublicSummaryPolicy
    trace_producer: TraceProducer
    trace_operations: tuple[str, ...]
    trace_target_operations: tuple[str, ...]
    result_delivery: bool
    subgraph_dependencies: tuple[str, ...]
    model: str | None = None
    attachment_channels: tuple[str, ...] = ()
    artifacts: bool = False
    legacy_aliases: tuple[str, ...] = ()
    graph_id: str | None = None
    remote_providers: tuple[str, ...] = ()
    http_order: int | None = None


def _spec(
    identity: tuple[str, str, str],
    lifecycle: Lifecycle,
    **metadata: Any,
) -> PublicAgentSpec:
    enum_key, tool, slug = identity
    return PublicAgentSpec(
        enum_key=enum_key,
        tool=tool,
        slug=slug,
        schema=tool,
        handler=metadata.get("handler") or f"handle_{slug}_agent",
        lifecycle=lifecycle,
        modes=metadata.get("modes", ()),
        result_contract="agent_result_v1",
        driver=metadata["driver"],
        topology=metadata["topology"],
        checkpoint=metadata.get("checkpoint", "none"),
        resume=metadata.get("resume", "none"),
        cancellation=metadata.get("cancellation", "cooperative"),
        deadline_seconds=metadata["deadline_seconds"],
        retry=metadata.get("retry", "bounded"),
        max_attempts=metadata.get("max_attempts", 2),
        join=metadata.get("join", "not_applicable"),
        result_mapper=metadata.get("result_mapper", f"map_{slug}_result"),
        event_contract="execution_journal_v2",
        artifact_roles=metadata.get("artifact_roles", ()),
        transport_views=metadata["transport_views"],
        dispatch_adapter="execution_runtime",
        todo_phases=metadata["todo_phases"],
        public_summary=metadata.get("public_summary", "none"),
        trace_producer=metadata["trace_producer"],
        trace_operations=metadata["trace_operations"],
        trace_target_operations=metadata.get("trace_target_operations", ()),
        result_delivery=metadata.get("result_delivery", False),
        subgraph_dependencies=metadata.get("subgraphs", ()),
        model=metadata.get("model"),
        attachment_channels=metadata.get("attachments", ()),
        artifacts=metadata.get("artifacts", False),
        legacy_aliases=metadata.get("aliases", ()),
        graph_id=metadata.get("graph"),
        remote_providers=metadata.get("providers", ()),
        http_order=metadata.get("http_order"),
    )


PUBLIC_AGENT_CATALOG: tuple[PublicAgentSpec, ...] = (
    _spec(
        ("CHAT_AGENT", "ChatAgent", "chat"),
        "resumable",
        driver="resumable_graph",
        topology="conditional",
        checkpoint="graph",
        resume="action_and_recovery",
        deadline_seconds=300,
        transport_views=("http", "mcp", "openai", "expert_router"),
        todo_phases=("understanding", "responding", "follow_up"),
        trace_producer="local_semantic",
        trace_operations=("model.generate", "tool.chat"),
        model="phyto-chat",
        modes=("synchronous", "streaming"),
        attachments=("documents",),
        aliases=("ChatAgent",),
        graph="chat",
        providers=("model_provider",),
    ),
    _spec(
        ("KNOWLEDGE_AGENT", "KnowledgeAgent", "knowledge"),
        "synchronous",
        driver="local_graph",
        topology="conditional",
        deadline_seconds=600,
        transport_views=("http", "mcp", "openai", "expert_router"),
        todo_phases=("retrieving", "generating"),
        trace_producer="local_semantic",
        trace_operations=(
            "knowledge.search",
            "model.generate",
            "tool.knowledge",
        ),
        model="phyto-knowledge",
        modes=("synchronous", "streaming"),
        attachments=("documents",),
        aliases=("KnowledgeAgent", "KnowledgeAgents"),
        graph="knowledge",
        providers=("retrieval_service", "model_provider"),
        subgraphs=("chat",),
    ),
    _spec(
        ("DATA_AGENT", "DataAgent", "data"),
        "synchronous",
        driver="local_graph",
        topology="serial",
        deadline_seconds=600,
        transport_views=("http", "mcp", "expert_router"),
        todo_phases=("retrieving", "rewriting", "querying"),
        trace_producer="local_semantic",
        trace_operations=("data.query", "tool.data"),
        modes=("synchronous",),
        aliases=("DataAgent", "DatabaseAgents"),
        graph="data",
        providers=("retrieval_service", "nl2sql_service"),
        subgraphs=("knowledge", "chat"),
    ),
    _spec(
        ("ANALYST_AGENT", "AnalystAgent", "analyst"),
        "asynchronous",
        driver="remote_task",
        topology="serial",
        cancellation="requested_confirmed",
        deadline_seconds=_ANALYSIS_TASK_EXECUTION_DEADLINE_SECONDS,
        retry="provider_bounded",
        max_attempts=3,
        artifact_roles=("report", "table", "image", "archive"),
        transport_views=("http", "mcp", "expert_router"),
        todo_phases=("preparing", "analyzing", "publishing"),
        trace_producer="structured_provider",
        trace_operations=(
            "analyst.prepare_analysis",
            "analyst.run_workflow",
            "analyst.collect_outputs",
            "artifact.package",
            "remote.analysis",
            "remote.reconcile",
            "tool.analyst",
        ),
        trace_target_operations=("remote.analysis",),
        result_delivery=True,
        subgraphs=("knowledge", "chat"),
        modes=("asynchronous",),
        attachments=("documents", "datasets"),
        artifacts=True,
        aliases=("AnalystAgent", "AnalysisAgents"),
        graph="analyst",
        providers=("analysis_task_platform",),
        http_order=5,
    ),
    _spec(
        ("REVIEW_AGENT", "ReviewAgent", "review"),
        "resumable",
        driver="resumable_graph",
        topology="parallel",
        checkpoint="graph",
        resume="action_and_recovery",
        deadline_seconds=1800,
        max_attempts=3,
        join="all",
        transport_views=("http", "mcp", "openai", "expert_router"),
        todo_phases=(
            "planning",
            "retrieving",
            "drafting",
            "revising",
            "generating",
        ),
        trace_producer="local_semantic",
        trace_operations=(
            "model.generate",
            "review.citation_check",
            "review.draft_dimension",
            "review.final_synthesis",
            "review.retrieve_dimension",
            "tool.review",
        ),
        model="phyto-review",
        modes=("synchronous", "streaming", "input_required"),
        attachments=("documents",),
        aliases=("ReviewAgent", "ReviewAgents"),
        graph="review",
        providers=("retrieval_service", "model_provider"),
        subgraphs=("knowledge", "chat"),
        http_order=3,
    ),
    _spec(
        ("BRIEF_GENE_AGENT", "BriefGeneAgent", "brief_gene"),
        "synchronous",
        driver="local_graph",
        topology="hybrid",
        deadline_seconds=900,
        max_attempts=3,
        join="best_effort",
        transport_views=("http", "mcp", "openai", "expert_router"),
        todo_phases=("annotating", "retrieving", "analyzing", "generating"),
        trace_producer="local_semantic",
        trace_operations=("model.generate", "tool.brief_gene"),
        model="phyto-brief-gene",
        modes=("synchronous", "streaming"),
        graph="brief_gene",
        providers=(
            "annotation_service",
            "retrieval_service",
            "model_provider",
        ),
        http_order=4,
        subgraphs=("knowledge", "chat"),
    ),
    _spec(
        ("DEEP_GENOME_AGENT", "DeepGenomeAgent", "deep_genome"),
        "asynchronous",
        driver="hybrid",
        topology="hybrid",
        checkpoint="coordinator",
        resume="recovery",
        cancellation="best_effort",
        deadline_seconds=7200,
        retry="provider_bounded",
        max_attempts=3,
        join="best_effort",
        artifact_roles=("report", "table", "image", "archive"),
        transport_views=("http", "mcp", "expert_router"),
        todo_phases=("planning", "analyzing", "synthesizing"),
        trace_producer="hybrid",
        trace_operations=(
            "deep_genome.prepare_plan",
            "deep_genome.gather_context",
            "deep_genome.run_analysis_branches",
            "deep_genome.experiment_protocol",
            "deep_genome.synthesize_results",
            "artifact.package",
            "deep_genome.workflow",
            "model.generate",
            "remote.analysis",
            "remote.reconcile",
            "tool.deep_genome",
        ),
        trace_target_operations=("remote.analysis",),
        modes=("asynchronous",),
        artifacts=True,
        aliases=("DeepGenomeAgent",),
        graph="deep_genome",
        providers=("analysis_task_platform", "object_storage"),
        subgraphs=("brief_gene", "analyst", "design", "evolution"),
    ),
    _spec(
        ("IN_SILICO_RESEARCH_AGENT", "InSilicoResearchAgent", "research"),
        "asynchronous",
        driver="hybrid",
        topology="hybrid",
        checkpoint="coordinator",
        resume="recovery",
        cancellation="best_effort",
        deadline_seconds=_ANALYSIS_TASK_EXECUTION_DEADLINE_SECONDS,
        retry="provider_bounded",
        max_attempts=3,
        join="best_effort",
        artifact_roles=(
            "report",
            "citation",
            "dataset",
            "image",
            "archive",
        ),
        transport_views=("http", "mcp", "expert_router"),
        todo_phases=("planning", "researching", "synthesizing"),
        trace_producer="hybrid",
        trace_operations=(
            "research.decompose_objectives",
            "research.dispatch_work",
            "research.collect_evidence",
            "research.synthesize_results",
            "research.package_outputs",
            "artifact.package",
            "model.generate",
            "remote.analysis",
            "remote.reconcile",
            "tool.research",
        ),
        trace_target_operations=("remote.analysis",),
        result_delivery=True,
        modes=("asynchronous",),
        attachments=("documents", "datasets"),
        artifacts=True,
        aliases=("InSilicoResearchAgent",),
        providers=("analysis_task_platform", "mcp_a2a_optional"),
        handler="handle_in_silico_research_agent",
    ),
    _spec(
        ("DIGITAL_DESIGN_AGENT", "DigitalDesignAgent", "design"),
        "asynchronous",
        driver="remote_fanout",
        topology="parallel",
        cancellation="requested_confirmed",
        deadline_seconds=_ANALYSIS_TASK_EXECUTION_DEADLINE_SECONDS,
        retry="provider_bounded",
        max_attempts=3,
        join="best_effort",
        artifact_roles=("report", "table", "image", "archive"),
        transport_views=("http", "mcp", "expert_router"),
        todo_phases=("planning", "designing", "consolidating"),
        trace_producer="structured_provider",
        trace_operations=(
            "design.validate_target",
            "design.run_branches",
            "design.consolidate_candidates",
            "design.package_outputs",
            "artifact.package",
            "remote.analysis",
            "remote.reconcile",
            "tool.design",
        ),
        trace_target_operations=("remote.analysis",),
        result_delivery=True,
        modes=("asynchronous",),
        attachments=("documents",),
        artifacts=True,
        providers=("analysis_task_platform", "mcp_a2a_optional"),
        handler="handle_digital_design_agent",
    ),
    _spec(
        ("GENE_NETWORK_AGENT", "GeneNetworkAgent", "network"),
        "asynchronous",
        driver="remote_fanout",
        topology="parallel",
        cancellation="requested_confirmed",
        deadline_seconds=_ANALYSIS_TASK_EXECUTION_DEADLINE_SECONDS,
        retry="provider_bounded",
        max_attempts=3,
        join="best_effort",
        artifact_roles=("report", "table", "image", "archive"),
        transport_views=("http", "mcp", "expert_router"),
        todo_phases=("planning", "analyzing", "consolidating"),
        public_summary="explicit",
        trace_producer="structured_provider",
        trace_operations=(
            "artifact.package",
            "gene_network.infer_network",
            "gene_network.prepare_inputs",
            "gene_network.rank_regulators",
            "gene_network.synthesize_results",
            "gene_network.validate_target",
            "remote.analysis",
            "remote.reconcile",
            "tool.network",
        ),
        trace_target_operations=("remote.analysis",),
        result_delivery=True,
        modes=("asynchronous",),
        attachments=("documents",),
        artifacts=True,
        providers=("analysis_task_platform", "mcp_a2a_optional"),
        handler="handle_gene_network_agent",
    ),
)

_PUBLIC_AGENT_BY_SLUG = {item.slug: item for item in PUBLIC_AGENT_CATALOG}


def public_agent_spec(slug: str) -> PublicAgentSpec | None:
    """Resolve one canonical Agent without importing business handlers."""
    return _PUBLIC_AGENT_BY_SLUG.get(slug)


def agent_slug_to_tool() -> dict[str, str]:
    """Return the stable HTTP catalog order as slug-to-tool mapping."""
    ordered = sorted(
        enumerate(PUBLIC_AGENT_CATALOG),
        key=lambda pair: (
            pair[1].http_order if pair[1].http_order is not None else pair[0]
        ),
    )
    return {item.slug: item.tool for _index, item in ordered}


def model_to_tool() -> dict[str, str]:
    """Return OpenAI-compatible model ids mapped to public tools."""
    return {
        item.model: item.tool for item in PUBLIC_AGENT_CATALOG if item.model
    }


def remote_agent_slugs() -> frozenset[str]:
    """Return agents whose primary lifecycle is asynchronous."""
    return frozenset(
        item.slug
        for item in PUBLIC_AGENT_CATALOG
        if item.lifecycle == "asynchronous"
    )


def provider_trace_agent_slugs() -> frozenset[str]:
    """Return agents with a normalized structured-provider trace producer."""
    return frozenset(
        item.slug
        for item in PUBLIC_AGENT_CATALOG
        if item.trace_producer in {"structured_provider", "hybrid"}
    )


def result_delivery_agent_slugs() -> frozenset[str]:
    """Return Agents that publish the existing result archive contract."""
    return frozenset(
        item.slug for item in PUBLIC_AGENT_CATALOG if item.result_delivery
    )


def graph_runtime_metadata() -> dict[str, dict[str, object]]:
    """Return canonical public graph ownership and execution metadata."""
    return {
        item.graph_id: {
            "public_agent": item.tool,
            "lifecycle": item.modes,
            "subgraph_dependencies": item.subgraph_dependencies,
            "remote_providers": item.remote_providers,
            "driver": item.driver,
            "topology": item.topology,
        }
        for item in PUBLIC_AGENT_CATALOG
        if item.graph_id is not None
    }


def legacy_aliases() -> dict[str, list[str]]:
    """Return fresh lists of advisory legacy aliases by public tool."""
    return {
        item.tool: list(item.legacy_aliases) for item in PUBLIC_AGENT_CATALOG
    }


def export_public_agent_catalog() -> dict[str, Any]:
    """Return a deterministic JSON-compatible catalog projection."""
    return {
        "schema_version": 1,
        "capabilities": {
            "execution_events": (
                EXECUTION_EVENTS_CAPABILITY_V1.to_public_dict()
            ),
            "execution_runtime": {
                "major_version": 1,
                "journal_major_version": 2,
                "drivers": sorted(
                    {item.driver for item in PUBLIC_AGENT_CATALOG}
                ),
            },
        },
        "agents": [item._asdict() for item in PUBLIC_AGENT_CATALOG],
    }


class RuntimeCatalogSnapshot(NamedTuple):
    """Existing runtime maps checked against the canonical source."""

    argument_models: Mapping[str, type[Any]]
    handlers: Mapping[str, Any]
    slug_to_tool: Mapping[str, str]
    models_to_tools: Mapping[str, str]
    remote_slugs: frozenset[str]
    aliases: Mapping[str, list[str]]


def validate_runtime_catalog(
    snapshot: RuntimeCatalogSnapshot,
) -> tuple[str, ...]:
    """Return bounded drift descriptions for runtime catalog consumers."""
    violations: list[str] = []
    expected_tools = {item.tool for item in PUBLIC_AGENT_CATALOG}
    if expected_tools - set(snapshot.argument_models):
        violations.append("argument model tools drift")
    if expected_tools - set(snapshot.handlers):
        violations.append("handler tools drift")
    for item in PUBLIC_AGENT_CATALOG:
        model = snapshot.argument_models.get(item.tool)
        handler = snapshot.handlers.get(item.tool)
        if model is None or model.__name__ != item.schema:
            violations.append(f"schema drift: {item.tool}")
        if handler is None or handler.__name__ != item.handler:
            violations.append(f"handler drift: {item.tool}")
    if dict(snapshot.slug_to_tool) != agent_slug_to_tool():
        violations.append("slug map drift")
    if dict(snapshot.models_to_tools) != model_to_tool():
        violations.append("model map drift")
    if snapshot.remote_slugs != remote_agent_slugs():
        violations.append("remote lifecycle drift")
    if dict(snapshot.aliases) != legacy_aliases():
        violations.append("legacy alias drift")
    return tuple(violations)
