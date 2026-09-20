# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed construction helpers for Execution Runtime V2 tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from mcp_server_phytomni.public_agent_catalog import Driver
from mcp_server_phytomni.runtime.execution_drivers_v2 import (
    DriverOperationHandler,
    LocalGraphDriver,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    EventStatus,
    ExecutionEventType,
    ExecutionEventV2,
    PublicTodoItemV2,
    TodoSnapshotPublicPayload,
)
from mcp_server_phytomni.runtime.execution_log_artifact_v2 import (
    ExecutionLogArtifactStore,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOperation,
    DriverOutcome,
    ExecutionCommand,
    ExecutionDriver,
)
from mcp_server_phytomni.runtime.execution_runtime_v2 import ExecutionRuntime
from mcp_server_phytomni.runtime.execution_target_store_v2 import (
    ExecutionTargetStore,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
)


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeOverrides:
    """Optional collaborators retained by tests for direct assertions."""

    journal: SQLiteExecutionJournal | None = None
    work: SQLiteExecutionWorkRepository | None = None
    target_store: ExecutionTargetStore | None = None
    execution_log_store: ExecutionLogArtifactStore | None = None
    clock: Callable[[], datetime] | None = None


class ExecutionRuntimeStack(NamedTuple):
    """Runtime and the concrete repositories created around it."""

    runtime: ExecutionRuntime
    reservations: SQLiteExecutionReservationRepository
    journal: SQLiteExecutionJournal
    work: SQLiteExecutionWorkRepository


def _isoformat_clock(
    clock: Callable[[], datetime],
) -> Callable[[], str]:
    """Adapt a datetime clock to the journal's serialized clock boundary."""
    return lambda: clock().isoformat()


def build_execution_runtime_stack(
    db_path: str | Path,
    driver: ExecutionDriver,
    driver_key: Driver = "local_graph",
    *,
    overrides: ExecutionRuntimeOverrides | None = None,
) -> ExecutionRuntimeStack:
    """Build one Runtime with concrete SQLite collaborators for a test."""
    resolved = overrides or ExecutionRuntimeOverrides()
    path = str(db_path)
    clock = resolved.clock
    reservations = SQLiteExecutionReservationRepository(
        path,
        clock=clock,
    )
    journal = resolved.journal
    if journal is None:
        journal_clock = None if clock is None else _isoformat_clock(clock)
        journal = SQLiteExecutionJournal(path, clock=journal_clock)
    work = resolved.work or SQLiteExecutionWorkRepository(
        path,
        clock=clock,
    )
    runtime = ExecutionRuntime(
        reservations=reservations,
        journal=journal,
        work=work,
        drivers={driver_key: driver},
        target_store=resolved.target_store,
        execution_log_store=resolved.execution_log_store,
        clock=clock,
    )
    return ExecutionRuntimeStack(runtime, reservations, journal, work)


def build_local_graph_runtime_stack(
    db_path: str | Path,
    start_handler: DriverOperationHandler,
    *,
    overrides: ExecutionRuntimeOverrides | None = None,
) -> ExecutionRuntimeStack:
    """Build a Runtime backed by a typed local-graph start handler."""
    return build_execution_runtime_stack(
        db_path,
        LocalGraphDriver({DriverOperation.START: start_handler}),
        overrides=overrides,
    )


def build_local_graph_runtime(
    db_path: str | Path,
    start_handler: DriverOperationHandler,
    journal: SQLiteExecutionJournal | None = None,
    work: SQLiteExecutionWorkRepository | None = None,
    *,
    overrides: ExecutionRuntimeOverrides | None = None,
) -> ExecutionRuntime:
    """Build only the local-graph Runtime while retaining supplied stores."""
    resolved = overrides or ExecutionRuntimeOverrides()
    dependencies = ExecutionRuntimeOverrides(
        journal=journal if journal is not None else resolved.journal,
        work=work if work is not None else resolved.work,
        target_store=resolved.target_store,
        execution_log_store=resolved.execution_log_store,
        clock=resolved.clock,
    )
    return build_local_graph_runtime_stack(
        db_path,
        start_handler,
        overrides=dependencies,
    ).runtime


def _knowledge_query_arguments() -> dict[str, object]:
    """Return isolated default arguments for one Runtime test request."""
    return {"query": "rice"}


@dataclass(frozen=True, slots=True)
class ExecutionStartRequest:
    """Concise canonical admission values used by Runtime unit tests."""

    execution_id: str
    fingerprint: str
    agent_slug: str = "knowledge"
    arguments: Mapping[str, object] = field(
        default_factory=_knowledge_query_arguments
    )
    owner: str = "alice"
    transport: str = "test"
    fingerprint_version: int = 1


def run_execution_start(
    runtime: ExecutionRuntime,
    request: ExecutionStartRequest,
) -> DriverOutcome:
    """Synchronously start one canonical Runtime test execution."""
    return asyncio.run(
        runtime.start(
            owner=request.owner,
            execution_id=request.execution_id,
            fingerprint_version=request.fingerprint_version,
            fingerprint=request.fingerprint,
            command=ExecutionCommand(
                agent_slug=request.agent_slug,
                arguments=request.arguments,
            ),
            transport=request.transport,
        )
    )


def todo_snapshots(
    events: Iterable[ExecutionEventV2],
    *,
    status: EventStatus | None = None,
) -> list[tuple[PublicTodoItemV2, ...]]:
    """Return typed Todo snapshots, optionally filtered by event status."""
    return [
        event.public_payload.items
        for event in events
        if event.type is ExecutionEventType.TODO_SNAPSHOT
        and (status is None or event.status is status)
        and isinstance(event.public_payload, TodoSnapshotPublicPayload)
    ]


def first_event_of_type(
    events: Iterable[ExecutionEventV2],
    event_type: ExecutionEventType,
) -> ExecutionEventV2:
    """Return the first Runtime event with the requested typed event kind."""
    return next(event for event in events if event.type is event_type)
