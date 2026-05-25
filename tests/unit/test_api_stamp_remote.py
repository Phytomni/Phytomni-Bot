# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``_stamp_remote_request_info`` (api/app.py).

Pins the three branches the 2026-05-25 audit (AF-002) flagged as
untested: ``run_id=None`` no-op (analyst dedup-hit), normal stamp
with SQL verification, and the silent swallow path when
``RunRegistry.update_request_info`` raises ``sqlite3.Error``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.api.app import _stamp_remote_request_info
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.unit


def _make_registry(tmp_path: Path) -> tuple[RunRegistry, str]:
    """Return a fresh registry + its DB path on a tmp file."""
    db = str(tmp_path / "tasks.db")
    return RunRegistry(db), db


def test_stamp_remote_request_info_skips_when_run_id_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A None run_id is the analyst dedup-hit / chokepoint-failure path.

    The helper must short-circuit before touching the registry so the
    response stays on the 202 happy path and no spurious row write or
    cross-tenant overwrite slips through.
    """
    calls: list[tuple[str, str]] = []

    def fake_update(*args: Any, **kwargs: Any) -> bool:
        """Record any RunRegistry write attempt; should never fire."""
        calls.append(("update_request_info", str((args, kwargs))))
        return True

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.run_registry."
        "RunRegistry.update_request_info",
        fake_update,
    )

    _stamp_remote_request_info(
        run_id=None,
        owner="alice",
        request_info=RunRequestInfo(dialogue_id="should-not-write"),
    )

    assert not calls
    # Even after the no-op, the registry stays empty.
    registry, _ = _make_registry(tmp_path)
    assert not registry.list_runs(owner="alice")


def test_stamp_remote_request_info_writes_five_columns_on_owned_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal stamp updates dialogue/query/tool/model/request_json."""
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.api.app.resolve_tasks_db_path",
        lambda: db_path,
    )
    registry = RunRegistry(db_path)
    spec = RunSpec("run-stamp-1", "alice", "analyst", "remote")
    registry.create_run(spec, status="running")
    info = RunRequestInfo(
        dialogue_id="dlg-stamp",
        query="goal-text",
        tool_name="AnalystAgent",
        model=None,
        request_json='{"arguments": {"goal_description": "goal-text"}}',
    )

    _stamp_remote_request_info(
        run_id="run-stamp-1", owner="alice", request_info=info
    )

    record = registry.get_run("run-stamp-1", owner="alice")
    assert record is not None
    assert record.request_info == info


def test_stamp_remote_request_info_swallows_sqlite_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SQLite failure inside backfill must not propagate.

    The remote chokepoint already returned a 202 to the user before
    the backfill fires, so re-raising the SQLite error would crash the
    request handler after the response was sent. The audit explicitly
    accepts the silent swallow as best-effort bookkeeping; this test
    pins that behaviour so a future refactor cannot flip it without
    surfacing the change.
    """

    def boom(*_args: Any, **_kwargs: Any) -> bool:
        """Simulate a corrupted-DB write failure inside the registry."""
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.run_registry."
        "RunRegistry.update_request_info",
        boom,
    )

    # No exception escapes — the swallow contract holds.
    _stamp_remote_request_info(
        run_id="run-x",
        owner="alice",
        request_info=RunRequestInfo(dialogue_id="dlg-x"),
    )
