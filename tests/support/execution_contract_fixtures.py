# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared HTTP and migration fixtures for execution lifecycle contracts."""

from __future__ import annotations

import sqlite3

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.a2ui_runtime import ReviewExecution
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    current_execution_boundary,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.run_registry import RunRecord
from tests.support.handler_fakes import review_success_result


def assert_failed_execution_projection(
    tasks_db_path: str,
    record: RunRecord,
) -> None:
    """Assert a failed V1 run is terminal in its canonical V2 journal."""
    execution_id = record.request_info.execution_id
    assert execution_id
    projection = SQLiteExecutionJournal(tasks_db_path).get_projection(
        execution_id,
        owner="u1",
    )
    assert projection.status.value == "failed"
    assert projection.terminal is not None
    assert projection.terminal.status == "failed"


def install_successful_review(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str | None,
) -> None:
    """Install a successful Review seam with fixed or active run identity."""

    async def fake_review(**_kwargs: object) -> ReviewExecution:
        active_run_id = run_id
        if active_run_id is None:
            boundary = current_execution_boundary(required=True)
            assert boundary is not None
            active_run_id = boundary.context.run_id
            assert active_run_id is not None
        return ReviewExecution(
            run_id=active_run_id,
            status="succeeded",
            result=review_success_result(),
        )

    monkeypatch.setattr(api_app, "_run_review_with_interrupt", fake_review)


def create_legacy_runs_table(connection: sqlite3.Connection) -> None:
    """Create the pre-execution-runtime runs table used by migration tests."""
    connection.execute(
        "CREATE TABLE runs ("
        "run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "agent TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL)"
    )


__all__ = [
    "assert_failed_execution_projection",
    "create_legacy_runs_table",
    "install_successful_review",
]
