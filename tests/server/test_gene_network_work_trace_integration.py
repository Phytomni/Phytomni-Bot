# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Real Gene Network handler integration for the public work trace."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.server


async def test_real_gene_network_handler_produces_targetable_terminal_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server_phytomni.agents.network import (
        agent as network_agent_module,
    )
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
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionCommand,
        ExecutionContext,
        ExecutionServices,
    )
    from mcp_server_phytomni.runtime.execution_trace_target_v2 import (
        resolve_trace_target,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SpanSpec,
        SQLiteExecutionWorkRepository,
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
        ProviderTraceObservation,
        ProviderTraceRecord,
    )
    from mcp_server_phytomni.runtime.request_context import request_context

    db_path = str(tmp_path / "gene-network-handler-trace.db")
    monkeypatch.setenv("TASKS_DB_PATH", db_path)
    reservations = SQLiteExecutionReservationRepository(db_path)
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    reservation = reservations.reserve(
        owner="alice",
        execution_id="execution-gene-network-trace",
        fingerprint_version=1,
        fingerprint="n" * 64,
        command=ExecutionCommand(
            agent_slug="network",
            arguments={"species_code": "osa", "to_id": "TO:0000011"},
        ),
    )
    work.create_span(
        SpanSpec(
            owner=reservation.owner,
            execution_id=reservation.execution_id,
            span_id=reservation.root_span_id,
            kind="agent",
            label_key="agent.network",
        )
    )
    spec = public_agent_spec("network")
    assert spec is not None
    context = ExecutionContext(
        owner_ref=reservation.owner,
        execution_id=reservation.execution_id,
        fingerprint_version=reservation.fingerprint_version,
        fingerprint=reservation.fingerprint,
        agent=spec,
        root_span_id=reservation.root_span_id,
        current_span_id=reservation.root_span_id,
        transport="test",
        run_id=reservation.run_id,
        deadline_at=datetime.fromisoformat(reservation.deadline_at),
    )
    services = ExecutionServices(
        journal=journal,
        reservations=reservations,
        work=work,
        clock=lambda: datetime.now(UTC),
    )
    sensitive = SensitiveConfig.load()
    monkeypatch.setattr(
        handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(
            sensitive=sensitive,
            obs_credentials=("test-access", "test-secret"),
        ),
    )

    async def allocate_output_dir(*_args, **_kwargs):
        return "/obs/phytomni/users/alice/gene_network_task/test-run"

    monkeypatch.setattr(
        network_agent_module,
        "create_output_dir",
        allocate_output_dir,
    )

    async def submit_provider(_agent, _config, _sensitive, request, **_kwargs):
        async def accepted():
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
        request_context("alice", "request-network-trace", reservation.run_id),
        bind_execution_boundary(context, services),
    ):
        result = await handlers.handle_gene_network_agent(
            GeneNetworkAgent(
                species_code="osa",
                to_id="TO:0000011",
            )
        )
    clear_agent_registry("GeneNetworkAgents")

    assert result["network_task"]["task_id"] == (
        "private-provider-network-task"
    )
    assert result["task_ids"] == ["private-provider-network-task"]

    analysis = next(
        unit
        for unit in work.list_due_provider_work_units(
            now=datetime.now(UTC), limit=10
        )
        if unit.operation_key == "remote.analysis"
    )
    status = ["running", "succeeded"]

    async def poll_status(_current):
        value = status.pop(0)
        return ProviderObservation(
            status=value,
            source_revision=1 if value == "running" else 2,
        )

    trace_polls = 0

    async def poll_trace(_current, _checkpoint):
        nonlocal trace_polls
        trace_polls += 1
        return ProviderTraceAdapterResult(
            observation=ProviderTraceObservation(
                schema_version=1,
                adapter_version="analysis-delta-v1",
                source_revision=1,
                next_cursor="private-terminal-cursor",
                snapshot_complete=False,
                health="healthy",
                records=(
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
            ),
            overlap_identities=("private-phase-record", "private-tool-record"),
        )

    def present(unit, observation, record):
        span = work.find_span_by_work_unit_id(
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
        reservations=reservations,
        journal=journal,
        work=work,
        pollers={"analysis_task_platform": poll_status},
        trace_pollers={"analysis_task_platform": poll_trace},
        trace_presenter=present,
        trace_agent_slugs=frozenset({"network"}),
    )
    assert await reconciler.reconcile(analysis) is True
    current = work.get_work_unit(
        analysis.execution_id, analysis.work_unit_id, owner=analysis.owner
    )
    assert await reconciler.reconcile(current) is True
    terminal = work.get_work_unit(
        analysis.execution_id, analysis.work_unit_id, owner=analysis.owner
    )
    assert terminal.status.value == "succeeded"
    assert trace_polls == 1

    projection = journal.get_projection(
        reservation.execution_id, owner=reservation.owner
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
        journal,
        owner=reservation.owner,
        execution_id=reservation.execution_id,
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
        SQLiteExecutionJournal(db_path),
        owner=reservation.owner,
        execution_id=reservation.execution_id,
        target_id=analysis_operation.target.id,
        limit=100,
    )
    assert replayed == trace
