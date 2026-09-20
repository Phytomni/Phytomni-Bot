# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical Execution V2 setup shared by HTTP-facing tests."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ExecutionStatus,
    SpanStatus,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SpanSpec,
    SQLiteExecutionWorkRepository,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from tests.support.http_fakes import install_tool_handler


def install_terminal_settlement_failure(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    *,
    private_detail: str,
) -> None:
    """Install a Chat handler whose terminal Runtime write fails safely."""

    def fail_terminal_settlement(*_args: Any, **_kwargs: Any) -> bool:
        raise sqlite3.OperationalError(private_detail)

    install_tool_handler(
        monkeypatch,
        server.PhytomniAgents.CHAT_AGENT.value,
        handler,
    )
    monkeypatch.setattr(
        SQLiteExecutionReservationRepository,
        "settle_terminal",
        fail_terminal_settlement,
    )


def reserve_running_execution(
    db_path: str,
    *,
    run_id: str,
    owner: str,
    agent: str,
    execution_id: str | None = None,
) -> str:
    """Reserve one running execution for submission and lifecycle tests."""
    resolved_execution_id = execution_id or f"turn-{run_id}"
    reservations = SQLiteExecutionReservationRepository(
        db_path,
        run_id_factory=lambda: run_id,
    )
    reservations.reserve(
        owner=owner,
        execution_id=resolved_execution_id,
        fingerprint_version=1,
        fingerprint=f"fixture:{resolved_execution_id}",
        command=ExecutionCommand(agent_slug=agent, arguments={}),
    )
    assert reservations.record_observation(
        owner=owner,
        execution_id=resolved_execution_id,
        status=ExecutionStatus.RUNNING,
        tracking_health="healthy",
        cancellation_state="unsupported",
        next_attempt_at=None,
    )
    return resolved_execution_id


def seed_waiting_execution(
    db_path: str,
    *,
    run_id: str,
    owner: str,
    agent: str,
    result: dict[str, Any],
) -> str:
    """Seed one canonical execution paused at an HTTP input boundary."""
    execution_id = f"turn-{run_id}"
    reservations = SQLiteExecutionReservationRepository(
        db_path,
        run_id_factory=lambda: run_id,
        root_span_id_factory=lambda: f"span-{run_id}",
    )
    reservation = reservations.reserve(
        owner=owner,
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=f"fixture:{run_id}",
        command=ExecutionCommand(agent_slug=agent, arguments={}),
    )
    work = SQLiteExecutionWorkRepository(db_path)
    root = work.create_span(
        SpanSpec(
            owner=owner,
            execution_id=execution_id,
            span_id=reservation.root_span_id,
            kind="agent",
            label_key=f"agent.{agent}",
        )
    )
    work.update_span_status(
        execution_id,
        reservation.root_span_id,
        owner=owner,
        status=SpanStatus.WAITING_INPUT,
        expected_revision=root.revision,
    )
    assert reservations.record_observation(
        owner=owner,
        execution_id=execution_id,
        status=ExecutionStatus.WAITING_INPUT,
        tracking_health="healthy",
        cancellation_state="none",
        next_attempt_at=None,
    )
    assert RunRegistry(db_path).update_active_result(
        run_id,
        owner=owner,
        result=result,
    )
    return execution_id
