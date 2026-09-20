# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Real Gene Network handler integration for the public work trace."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.support.provider_trace_v2 import (
    ExecutionTraceBoundary,
    build_execution_trace_boundary,
    provider_trace_adapter_result,
)

from mcp_server_phytomni.agents.network import agent as network_agent_module
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.mcp import handlers
from mcp_server_phytomni.mcp.schemas import GeneNetworkAgent
from mcp_server_phytomni.public_agent_catalog import public_agent_spec
from mcp_server_phytomni.runtime.agent_registry import clear_agent_registry
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    bind_execution_boundary,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_trace_target_v2 import (
    resolve_trace_target,
)
from mcp_server_phytomni.runtime.gene_network_provider_trace_v2 import (
    present_gene_network_provider_record,
)
from mcp_server_phytomni.runtime.provider_instrumentation_v2 import (
    instrument_provider_submission,
)
from mcp_server_phytomni.runtime.provider_reconciliation_v2 import (
    ProviderObservation,
    ProviderReconciler,
)
from mcp_server_phytomni.runtime.provider_trace_v2 import (
    ProviderTraceAdapterResult,
    ProviderTraceRecord,
)
from mcp_server_phytomni.runtime.request_context import request_context

pytestmark = pytest.mark.server


def _trace_harness(db_path: str) -> ExecutionTraceBoundary:
    """Create the canonical execution boundary used by the integration test."""
    spec = public_agent_spec("network")
    assert spec is not None
    return build_execution_trace_boundary(
        db_path,
        spec=spec,
        execution_id="execution-gene-network-trace",
        fingerprint="n" * 64,
        arguments={"species_code": "osa", "to_id": "TO:0000011"},
    )


async def _invoke_handler(
    harness: ExecutionTraceBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Run the real handler with only its remote provider edge replaced."""
    monkeypatch.setenv("TASKS_DB_PATH", harness.db_path)
    sensitive = SensitiveConfig.load()
    monkeypatch.setattr(
        handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(
            sensitive=sensitive,
            obs_credentials=("test-access", "test-secret"),
        ),
    )

    async def allocate_output_dir(*_args: object, **_kwargs: object) -> str:
        return "/obs/phytomni/users/alice/gene_network_task/test-run"

    monkeypatch.setattr(
        network_agent_module,
        "create_output_dir",
        allocate_output_dir,
    )

    async def submit_provider(
        _agent: object,
        _config: object,
        _sensitive: object,
        request: dict[str, Any],
        **_kwargs: object,
    ) -> dict[str, Any]:
        async def accepted() -> dict[str, Any]:
            return {
                "task_id": "private-provider-network-task",
                "output_dir": request["output_dir"],
                "task_status": "SUBMITTED",
                "plan": None,
                "tool_usages": None,
            }

        return await instrument_provider_submission(
            provider_kind="analysis_task_platform",
            operation_key="remote.analysis",
            call=accepted,
            identity_from_result=lambda value: value["task_id"],
        )

    clear_agent_registry("GeneNetworkAgents")
    monkeypatch.setattr(
        network_agent_module,
        "submit_analyst_via_subgraph",
        submit_provider,
    )
    with (
        request_context(
            "alice", "request-network-trace", harness.reservation.run_id
        ),
        bind_execution_boundary(harness.context, harness.services),
    ):
        result = await handlers.handle_gene_network_agent(
            GeneNetworkAgent(species_code="osa", to_id="TO:0000011")
        )
    clear_agent_registry("GeneNetworkAgents")
    return cast(dict[str, Any], result)


async def _reconcile_analysis(
    harness: ExecutionTraceBoundary,
    analysis: Any,
) -> None:
    """Drive the submitted provider task through one traced terminal poll."""
    statuses = ["running", "succeeded"]

    async def poll_status(_current: object) -> ProviderObservation:
        value = statuses.pop(0)
        return ProviderObservation(
            status=value,
            source_revision=1 if value == "running" else 2,
        )

    trace_polls = 0

    async def poll_trace(
        _current: object, _checkpoint: object
    ) -> ProviderTraceAdapterResult:
        nonlocal trace_polls
        trace_polls += 1
        return provider_trace_adapter_result(
            (
                ProviderTraceRecord(
                    source_identity="private-phase-record",
                    record_class="semantic_phase",
                    semantic_code="gene_network.prepare_inputs",
                    status="succeeded",
                ),
                ProviderTraceRecord(
                    source_identity="private-tool-record",
                    record_class="semantic_tool",
                    semantic_code="gene_network.infer_network",
                    status="succeeded",
                ),
            ),
            next_cursor="private-terminal-cursor",
            overlap_identities=(
                "private-phase-record",
                "private-tool-record",
            ),
        )

    def present(unit: Any, observation: Any, record: Any):
        span = harness.work.find_span_by_work_unit_id(
            unit.execution_id, unit.work_unit_id, owner=unit.owner
        )
        assert span is not None
        return present_gene_network_provider_record(
            unit,
            observation,
            record,
            analysis_span_id=span.span_id,
        )

    reconciler = ProviderReconciler(
        reservations=harness.reservations,
        journal=harness.journal,
        work=harness.work,
        pollers={"analysis_task_platform": poll_status},
        trace_pollers={"analysis_task_platform": poll_trace},
        trace_presenter=present,
        trace_agent_slugs=frozenset({"network"}),
    )
    assert await reconciler.reconcile(analysis) is True
    current = harness.work.get_work_unit(
        analysis.execution_id, analysis.work_unit_id, owner=analysis.owner
    )
    assert await reconciler.reconcile(current) is True
    terminal = harness.work.get_work_unit(
        analysis.execution_id, analysis.work_unit_id, owner=analysis.owner
    )
    assert terminal.status.value == "succeeded"
    assert trace_polls == 1


async def test_real_gene_network_handler_produces_targetable_terminal_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify real gene network handler produces targetable terminal trace."""

    harness = _trace_harness(str(tmp_path / "gene-network-handler-trace.db"))
    result = await _invoke_handler(harness, monkeypatch)

    assert result["network_task"]["task_id"] == (
        "private-provider-network-task"
    )
    assert result["task_ids"] == ["private-provider-network-task"]

    analysis = next(
        unit
        for unit in harness.work.list_due_provider_work_units(
            now=datetime.now(UTC), limit=10
        )
        if unit.operation_key == "remote.analysis"
    )
    await _reconcile_analysis(harness, analysis)

    projection = harness.journal.get_projection(
        harness.reservation.execution_id,
        owner=harness.reservation.owner,
    )
    analysis_operation = next(
        operation
        for operation in projection.operations
        if operation.operation_key == "remote.analysis"
    )
    submit_operation = next(
        operation
        for operation in projection.operations
        if operation.operation_key == "remote.submit"
    )
    assert analysis_operation.status == "succeeded"
    assert submit_operation.status == "succeeded"
    assert analysis_operation.target is not None
    trace = resolve_trace_target(
        harness.journal,
        owner=harness.reservation.owner,
        execution_id=harness.reservation.execution_id,
        target_id=analysis_operation.target.id,
        limit=100,
    )
    assert trace is not None
    assert {item.kind for item in trace.items} >= {
        "phase",
        "tool",
        "reasoning_summary",
        "decision",
    }
    public = str(trace.model_dump(mode="json"))
    assert "private-provider-network-task" not in public
    assert "private-terminal-cursor" not in public
    replayed = resolve_trace_target(
        SQLiteExecutionJournal(harness.db_path),
        owner=harness.reservation.owner,
        execution_id=harness.reservation.execution_id,
        target_id=analysis_operation.target.id,
        limit=100,
    )
    assert replayed == trace
