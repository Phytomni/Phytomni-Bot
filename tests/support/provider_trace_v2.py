# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared provider-trace identities and durable execution boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from mcp_server_phytomni.public_agent_catalog import PublicAgentSpec
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationRecord,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionContext,
    ExecutionServices,
)
from mcp_server_phytomni.runtime.execution_runtime_v2 import (
    build_execution_context,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
    WorkUnitRecord,
)
from mcp_server_phytomni.runtime.provider_trace_v2 import (
    ProviderTraceAdapterResult,
    ProviderTraceObservation,
    ProviderTraceRecord,
)
from tests.support.execution_supervisor_v2 import (
    create_root_span,
    reserve_test_execution,
)


@dataclass(frozen=True)
class ExecutionTraceBoundary:
    """Durable services and identity for one provider-trace test."""

    db_path: str
    reservations: SQLiteExecutionReservationRepository
    journal: SQLiteExecutionJournal
    work: SQLiteExecutionWorkRepository
    reservation: ExecutionReservationRecord
    context: ExecutionContext
    services: ExecutionServices


def build_execution_trace_boundary(
    db_path: str,
    *,
    spec: PublicAgentSpec,
    execution_id: str,
    fingerprint: str,
    arguments: dict[str, Any],
) -> ExecutionTraceBoundary:
    """Create one canonical Runtime boundary for trace integration tests."""
    reservations = SQLiteExecutionReservationRepository(db_path)
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    reservation = reserve_test_execution(
        reservations,
        execution_id=execution_id,
        agent_slug=spec.slug,
        fingerprint=fingerprint,
        arguments=arguments,
    )
    create_root_span(
        work,
        reservation,
        label_key=f"agent.{spec.slug}",
    )
    context = build_execution_context(reservation, spec, "test")
    return ExecutionTraceBoundary(
        db_path=db_path,
        reservations=reservations,
        journal=journal,
        work=work,
        reservation=reservation,
        context=context,
        services=ExecutionServices(
            journal=journal,
            reservations=reservations,
            work=work,
            clock=lambda: datetime.now(UTC),
        ),
    )


def provider_trace_unit() -> WorkUnitRecord:
    """Return the bounded public identity used by presenter tests."""
    return cast(
        WorkUnitRecord,
        SimpleNamespace(
            execution_id="execution-public",
            work_unit_id="private-analysis-work-unit",
            parent_span_id="root-span",
            attempt=1,
        ),
    )


def provider_trace_observation(
    record: ProviderTraceRecord,
) -> ProviderTraceObservation:
    """Wrap one private provider record in the canonical observation."""
    return ProviderTraceObservation(
        schema_version=1,
        adapter_version="analysis-delta-v1",
        source_revision=7,
        next_cursor="private-cursor",
        snapshot_complete=False,
        health="healthy",
        records=(record,),
    )


def provider_trace_adapter_result(
    records: tuple[ProviderTraceRecord, ...],
    *,
    next_cursor: str,
    overlap_identities: tuple[str, ...] = (),
    source_revision: int = 1,
) -> ProviderTraceAdapterResult:
    """Build one healthy incremental provider-trace adapter result."""
    return ProviderTraceAdapterResult(
        observation=ProviderTraceObservation(
            schema_version=1,
            adapter_version="analysis-delta-v1",
            source_revision=source_revision,
            next_cursor=next_cursor,
            snapshot_complete=False,
            health="healthy",
            records=records,
        ),
        overlap_identities=overlap_identities,
    )


def provider_trace_presenter_arguments(
    record: ProviderTraceRecord,
) -> tuple[WorkUnitRecord, ProviderTraceObservation, ProviderTraceRecord]:
    """Return the shared typed argument bundle for public presenters."""
    return provider_trace_unit(), provider_trace_observation(record), record
