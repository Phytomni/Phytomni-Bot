# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Representative agents declare the finite operation seams they inherit."""

from __future__ import annotations

from pathlib import Path

EXPECTED_COVERAGE = {
    "analyst": (
        "analyst.prepare_analysis",
        "analyst.run_workflow",
        "analyst.collect_outputs",
        "artifact.package",
        "remote.analysis",
        "remote.reconcile",
        "tool.analyst",
    ),
    "brief_gene": ("model.generate", "tool.brief_gene"),
    "chat": ("model.generate", "tool.chat"),
    "data": ("data.query", "tool.data"),
    "deep_genome": (
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
    "design": (
        "design.validate_target",
        "design.run_branches",
        "design.consolidate_candidates",
        "design.package_outputs",
        "artifact.package",
        "remote.analysis",
        "remote.reconcile",
        "tool.design",
    ),
    "knowledge": ("knowledge.search", "model.generate", "tool.knowledge"),
    "network": (
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
    "research": (
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
    "review": (
        "model.generate",
        "review.citation_check",
        "review.draft_dimension",
        "review.final_synthesis",
        "review.retrieve_dimension",
        "tool.review",
    ),
}


def test_representative_agent_operation_coverage_is_finite_and_public() -> (
    None
):
    from mcp_server_phytomni.runtime.execution_trace_detail import (
        AGENT_OPERATION_COVERAGE,
        OPERATION_PRESENTER_REGISTRY,
        serialize_operation_record_capability,
    )

    assert AGENT_OPERATION_COVERAGE == EXPECTED_COVERAGE
    assert serialize_operation_record_capability()["agent_coverage"] == {
        agent: list(operations)
        for agent, operations in EXPECTED_COVERAGE.items()
    }
    for operations in AGENT_OPERATION_COVERAGE.values():
        assert all(
            OPERATION_PRESENTER_REGISTRY.resolve(key).operation_key == key
            for key in operations
        )


def test_coverage_uses_shared_seams_instead_of_changing_agent_results() -> (
    None
):
    root = Path(__file__).parents[2] / "src" / "mcp_server_phytomni" / "agents"
    sources = {
        "knowledge": (root / "knowledge" / "retrieval.py").read_text(),
        "data": (root / "data" / "nl2sql.py").read_text(),
        "analyst": (root / "analyst" / "graph.py").read_text(),
        "network": (root / "network" / "agent.py").read_text(),
        "research": (root / "research" / "dispatch_runtime.py").read_text(),
        "deep_genome": (root / "deep_genome" / "remote_io.py").read_text(),
    }

    assert '"knowledge.search"' in sources["knowledge"]
    assert '"data.query"' in sources["data"]
    assert "instrument_provider_submission" in sources["analyst"]
    assert "submit_analyst_via_subgraph" in sources["network"]
    assert "submit_remote_analysis" in sources["research"]
    assert "submit_analyst_via_subgraph" in sources["deep_genome"]


def test_network_submission_remote_consolidation_and_root_terminal_are_distinct() -> (
    None
):
    from mcp_server_phytomni.runtime.execution_stage_v2 import (
        ExecutionStageSignal,
        empty_execution_stage_state,
        reduce_execution_stage,
    )

    state = empty_execution_stage_state()
    state = reduce_execution_stage(
        state,
        ExecutionStageSignal.PROVIDER_SUBMITTED,
        occurred_at="2026-08-22T04:00:00Z",
    )
    assert (state.stage, state.child_status, state.root_status) == (
        "orchestration",
        "succeeded",
        "running",
    )
    state = reduce_execution_stage(
        state,
        ExecutionStageSignal.PROVIDER_STATE_CHANGED,
        occurred_at="2026-08-22T04:00:05Z",
    )
    assert state.stage == "scientific_execution"
    assert state.root_status == "running"
    state = reduce_execution_stage(
        state,
        ExecutionStageSignal.CONSOLIDATION_STARTED,
        occurred_at="2026-08-22T04:01:00Z",
    )
    assert state.stage == "consolidation"
    assert state.root_status == "running"
    state = reduce_execution_stage(
        state,
        ExecutionStageSignal.ANSWER_AVAILABLE,
        occurred_at="2026-08-22T04:01:10Z",
    )
    state = reduce_execution_stage(
        state,
        ExecutionStageSignal.ROOT_SUCCEEDED,
        occurred_at="2026-08-22T04:01:12Z",
    )
    assert state.stage == "response_settlement"
    assert state.root_status == "succeeded"
