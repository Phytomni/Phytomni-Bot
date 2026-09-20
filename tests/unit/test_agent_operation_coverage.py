# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Representative agents declare the finite operation seams they inherit."""

from __future__ import annotations

from pathlib import Path

from tests.support.public_agent_catalog_expectations import (
    EXPECTED_TRACE_OPERATIONS,
)

from mcp_server_phytomni.runtime.execution_stage_v2 import (
    ExecutionStageSignal,
    empty_execution_stage_state,
    reduce_execution_stage,
)
from mcp_server_phytomni.runtime.execution_trace_detail import (
    AGENT_OPERATION_COVERAGE,
    OPERATION_PRESENTER_REGISTRY,
    serialize_operation_record_capability,
)


def test_representative_agent_operation_coverage_is_finite_and_public() -> (
    None
):
    """Verify representative agent operation coverage is finite and public."""

    assert AGENT_OPERATION_COVERAGE == EXPECTED_TRACE_OPERATIONS
    assert serialize_operation_record_capability()["agent_coverage"] == {
        agent: list(operations)
        for agent, operations in EXPECTED_TRACE_OPERATIONS.items()
    }
    for operations in AGENT_OPERATION_COVERAGE.values():
        assert all(
            OPERATION_PRESENTER_REGISTRY.resolve(key).operation_key == key
            for key in operations
        )


def test_coverage_uses_shared_seams_instead_of_changing_agent_results() -> (
    None
):
    """Verify coverage uses shared seams instead of changing agent results."""
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


def test_network_submission_and_terminal_events_are_distinct() -> None:
    """Verify network submission and terminal events are distinct."""

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
