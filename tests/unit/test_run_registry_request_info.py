# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Request-info and migration coverage for the run registry."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tests.support.sqlite import closed_sqlite_connection
from tests.unit.test_run_registry import _make_registry

from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.unit


def test_create_run_persists_request_info(tmp_path: Path) -> None:
    """A request_info bundle round-trips through create_run + get_run."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-ri-1", "alice", "knowledge", "local")
    info = RunRequestInfo(
        dialogue_id="dlg-1",
        request_id="req-1",
        query="What does AT1G01010 do?",
        tool_name="KnowledgeAgent",
        model="phyto-knowledge",
        request_json='{"messages": []}',
        locale="zh-CN",
    )

    registry.create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result={"answer": "x"}),
        request_info=info,
    )
    record = registry.get_run("run-ri-1", owner="alice")

    assert record is not None
    assert record.request_info == info


def test_create_run_without_request_info_defaults_to_null(
    tmp_path: Path,
) -> None:
    """Omitting request_info leaves every per-request column NULL."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-noreq", "alice", "chat", "local")

    registry.create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result={"answer": "x"}),
    )
    record = registry.get_run("run-noreq", owner="alice")

    assert record is not None
    assert record.request_info == RunRequestInfo()


def test_update_request_info_overwrites_existing(tmp_path: Path) -> None:
    """update_request_info replaces every column on an owned run."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-up", "alice", "chat", "local")
    registry.create_run(spec, outcome=RunOutcome(status="running"))

    new_info = RunRequestInfo(
        dialogue_id="dlg-7",
        request_id="req-7",
        query="hello world",
        tool_name="ChatAgent",
        model="phyto-chat",
        request_json='{"x": 1}',
        locale="zh-CN",
    )
    updated = registry.update_request_info(
        "run-up", owner="alice", request_info=new_info
    )

    assert updated is True
    record = registry.get_run("run-up", owner="alice")
    assert record is not None
    assert record.request_info == new_info


def test_update_request_info_returns_false_for_unknown_run(
    tmp_path: Path,
) -> None:
    """update_request_info silently returns False for missing rows."""
    registry, _, _ = _make_registry(tmp_path)

    updated = registry.update_request_info(
        "run-missing", owner="alice", request_info=RunRequestInfo()
    )

    assert updated is False


def test_update_request_info_enforces_owner_isolation(
    tmp_path: Path,
) -> None:
    """A foreign owner cannot retro-stamp another user's run."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-iso", "alice", "chat", "local")
    registry.create_run(spec, outcome=RunOutcome(status="running"))

    updated = registry.update_request_info(
        "run-iso",
        owner="bob",
        request_info=RunRequestInfo(query="injected"),
    )

    assert updated is False
    record = registry.get_run("run-iso", owner="alice")
    assert record is not None
    assert record.request_info.query is None


def test_settle_run_preserves_created_at(tmp_path: Path) -> None:
    """settle_run updates status/result in place without touching created_at."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-settle-1", "alice", "chat", "local")
    registry.create_run(spec, outcome=RunOutcome(status="running"))
    before = registry.get_run("run-settle-1", owner="alice")
    assert before is not None
    created_at = before.timestamps.created_at
    updated_at_before = before.timestamps.updated_at
    assert before.timestamps.expires_at is None

    updated = registry.settle_run(
        "run-settle-1",
        owner="alice",
        status="succeeded",
        result={"answer": "done"},
    )

    assert updated is True
    record = registry.get_run("run-settle-1", owner="alice")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result == {"answer": "done"}
    assert record.timestamps.created_at == created_at
    assert record.timestamps.updated_at >= updated_at_before
    assert record.timestamps.expires_at is not None


def test_settle_run_enforces_owner_isolation(tmp_path: Path) -> None:
    """A foreign-owner settle_run call returns False and changes nothing."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-settle-2", "alice", "chat", "local")
    registry.create_run(spec, outcome=RunOutcome(status="running"))
    before = registry.get_run("run-settle-2", owner="alice")
    assert before is not None

    updated = registry.settle_run(
        "run-settle-2", owner="bob", status="succeeded"
    )

    assert updated is False
    after = registry.get_run("run-settle-2", owner="alice")
    assert after == before


def test_init_db_migrates_legacy_table_in_place(tmp_path: Path) -> None:
    """An old database without request-info columns migrates cleanly."""
    db = str(tmp_path / "legacy.db")
    legacy_columns = (
        "run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "agent TEXT NOT NULL, origin TEXT NOT NULL, "
        "status TEXT NOT NULL, result_json TEXT, error TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "expires_at TEXT"
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"CREATE TABLE runs ({legacy_columns})")
        conn.execute(
            """
            INSERT INTO runs (
                run_id, user_id, agent, origin, status, result_json,
                error, created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "alice",
                "chat",
                "local",
                "succeeded",
                None,
                None,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    registry = RunRegistry(db)
    record = registry.get_run("legacy-run", owner="alice")

    assert record is not None
    assert record.spec.user_id == "alice"
    assert record.status == "succeeded"
    assert record.request_info == RunRequestInfo()
    with closed_sqlite_connection(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert "locale" in columns
    assert "request_id" in columns
    RunRegistry(db)
