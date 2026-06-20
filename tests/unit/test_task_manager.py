# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the local TaskManager registry.

Pin the registry contract: update_task writes the columns it names
(the prior param-order bug bound ``WHERE task_id = output_dir`` and
updated zero rows), get_task is a single non-blocking read, and
record_submission upserts a caller-known task_id.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.task_manager import (
    DEFAULT_REMOTE_TASK_TIMEOUT,
    RunContext,
    Submission,
    TaskManager,
    create_task,
    resolve_tasks_db_path,
    update_task,
)

pytestmark = pytest.mark.unit


def _mgr(tmp_path: Path) -> TaskManager:
    """Return a TaskManager backed by an isolated temp database.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        A TaskManager whose SQLite file lives under ``tmp_path``.
    """
    return TaskManager(db_path=str(tmp_path / "tasks.db"))


def test_remote_task_default_timeout_is_one_minute() -> None:
    """Pin the named fallback so a silent bump can never widen request budgets.

    Both module-level ``create_task`` and ``update_task`` resolve the
    fallback timeout from ``DEFAULT_REMOTE_TASK_TIMEOUT`` rather than a
    bare ``60`` magic literal; the value rebounds operator expectations
    if anyone tries to raise it without updating the docstrings.
    """
    assert DEFAULT_REMOTE_TASK_TIMEOUT == 60
    assert callable(create_task)
    assert callable(update_task)


def test_create_update_get_roundtrip(tmp_path: Path) -> None:
    """Verify create -> update -> get returns the written columns.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the round-trip row assertion passes.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()

    mgr.update_task(task_id, "succeeded", "remote-7", "/obs/out")

    assert mgr.get_task(task_id) == {
        "task_id": task_id,
        "status": "succeeded",
        "analysis_id": "remote-7",
        "output_dir": "/obs/out",
        "source_task_id": None,
    }


def test_update_task_writes_named_columns(tmp_path: Path) -> None:
    """Regression: update_task no longer scrambles its parameters.

    The prior tuple ``(status, task_id, analysis_id, output_dir)``
    bound ``WHERE task_id = output_dir`` and wrote ``analysis_id`` into
    the task_id slot, so a task row never updated.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the per-column assertions pass.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()

    mgr.update_task(task_id, "running", "A1", "/d")

    row = mgr.get_task(task_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["analysis_id"] == "A1"
    assert row["output_dir"] == "/d"


def test_get_task_missing_returns_none(tmp_path: Path) -> None:
    """Verify an unknown task id reads back as None.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the missing-id assertion passes.
    """
    assert _mgr(tmp_path).get_task("does-not-exist") is None


def test_get_task_agent_returns_tag(tmp_path: Path) -> None:
    """get_task_agent surfaces the agent tag for reconcile discrimination."""
    mgr = _mgr(tmp_path)
    mgr.record(
        Submission(
            task_id="dg-1",
            status="running",
            output_dir="/obs/run",
            run_context=RunContext(agent="deep_genome"),
        )
    )
    assert mgr.get_task_agent("dg-1") == "deep_genome"


def test_get_task_agent_unknown_id_returns_none(tmp_path: Path) -> None:
    """An unknown id reads back as None, not an error."""
    assert _mgr(tmp_path).get_task_agent("nope") is None


def test_record_submission_upserts_known_id(tmp_path: Path) -> None:
    """Verify record_submission writes then replaces a known id.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the insert and replace assertions pass.
    """
    mgr = _mgr(tmp_path)

    mgr.record_submission("local-42", "submitted", "/obs/run")
    assert mgr.get_task("local-42") == {
        "task_id": "local-42",
        "status": "submitted",
        "analysis_id": "",
        "output_dir": "/obs/run",
        "source_task_id": None,
    }

    mgr.record_submission("local-42", "succeeded", "/obs/run", "rem-9")
    row = mgr.get_task("local-42")
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["analysis_id"] == "rem-9"


def test_set_get_task_log_roundtrip(tmp_path: Path) -> None:
    """Verify set_task_log persists and get_task_log retrieves the log.

    The /v1/runs/{run_id}/logs endpoint caches remote analyst step logs
    locally to avoid polling the analysis platform on every refresh.
    set_task_log serializes a dict to JSON and writes the task_log TEXT
    column; get_task_log reads it back as a dict.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the round-trip assertion passes.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()
    log_payload = {
        "init_info": {"goal": "test"},
        "steps": [{"round": 1, "logs": ["step 1 output"]}],
    }

    updated = mgr.set_task_log(task_id, log_payload)
    assert updated is True

    retrieved = mgr.get_task_log(task_id)
    assert retrieved == log_payload


def test_set_task_log_returns_false_for_unknown_id(tmp_path: Path) -> None:
    """Verify set_task_log returns False when the task_id does not exist.

    The UPDATE statement affects zero rows, so rowcount is 0. The helper
    surfaces this as False rather than raising, so callers can decide
    whether to log, ignore, or error on the missing id.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the False return assertion passes.
    """
    mgr = _mgr(tmp_path)
    assert mgr.set_task_log("does-not-exist", {"key": "value"}) is False


def test_get_task_log_returns_none_for_missing_or_null(
    tmp_path: Path,
) -> None:
    """Verify get_task_log returns None for unknown ids and NULL columns.

    A task row with task_log = NULL (the default for rows created before
    the column existed) must not crash the JSON deserializer. An unknown
    task_id also returns None so the reconcile bridge can detect cache
    misses and fetch from the remote platform.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after both None-return assertions pass.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()

    assert mgr.get_task_log("unknown-id") is None
    assert mgr.get_task_log(task_id) is None


def test_set_get_task_final_report_roundtrip(tmp_path: Path) -> None:
    """Verify set/get_task_final_report round-trips the report markdown.

    DeepGenome runs the whole report workflow in the background and
    writes the assembled markdown to the local row so the poll path
    (GetTaskStatus / run-aggregate) can surface it. set_task_final_report
    writes the final_report TEXT column via a targeted UPDATE;
    get_task_final_report reads it back as the raw markdown string.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the round-trip assertion passes.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()
    markdown = "# Deep Genome Analysis of Os01g0177400\n\nbody\n"

    updated = mgr.set_task_final_report(task_id, markdown)
    assert updated is True

    assert mgr.get_task_final_report(task_id) == markdown


def test_set_task_final_report_survives_update_task(tmp_path: Path) -> None:
    """The terminal status flip must not wipe a persisted final_report.

    The DeepGenome follow-up node writes final_report while the row is
    still ``submitted``; the background done-callback later flips status
    via update_task. update_task only SETs status / analysis_id /
    output_dir, so a targeted final_report write persists through the
    status transition.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the persistence assertion passes.
    """
    mgr = _mgr(tmp_path)
    mgr.record_submission("dg-1", "submitted", "/obs/run")
    mgr.set_task_final_report("dg-1", "REPORT")

    mgr.update_task("dg-1", "succeeded", "", "/obs/run")

    assert mgr.get_task_final_report("dg-1") == "REPORT"


def test_set_task_final_report_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Verify set_task_final_report returns False for an unknown task_id.

    The UPDATE affects zero rows, so rowcount is 0. The helper surfaces
    this as False rather than raising so the best-effort follow-up-node
    write can log and continue instead of crashing the workflow.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the False return assertion passes.
    """
    mgr = _mgr(tmp_path)
    assert mgr.set_task_final_report("does-not-exist", "REPORT") is False


def test_get_task_final_report_returns_none_for_missing_or_null(
    tmp_path: Path,
) -> None:
    """Verify get_task_final_report returns None for unknown ids / NULL.

    A task row with final_report = NULL (every non-DeepGenome task, plus
    rows created before the column existed) must read back as None so the
    formatter falls back to the status line instead of crashing.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after both None-return assertions pass.
    """
    mgr = _mgr(tmp_path)
    task_id = mgr.create_task()

    assert mgr.get_task_final_report("unknown-id") is None
    assert mgr.get_task_final_report(task_id) is None


def test_init_db_adds_task_log_column_to_legacy_four_column_db(
    tmp_path: Path,
) -> None:
    """A pre-existing legacy tasks DB gets task_log added in place.

    A fresh image carries task_log via _CREATE_TASKS_DDL, but pre-
    existing customer databases only have the original four columns.
    _init_db must widen them via guarded ALTER TABLE so /v1/runs/logs
    has a column to read from instead of crashing on a missing column.
    """
    db_path = str(tmp_path / "legacy.db")
    legacy_ddl = (
        "CREATE TABLE tasks ("
        "task_id TEXT PRIMARY KEY, "
        "status TEXT, "
        "analysis_id TEXT, "
        "output_dir TEXT)"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(legacy_ddl)

    TaskManager(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert "task_log" in columns
    assert "final_report" in columns


def test_record_persists_source_task_id(tmp_path: Path) -> None:
    """Verify record writes source_task_id and get_task returns it.

    Cross-tenant dedup mints a caller-owned row whose source_task_id
    records the prior tenant's remote task id for live-status probing.
    The Submission dataclass must accept the optional field, record must
    persist it (with a COALESCE non-clobber guard on conflict), and
    get_task must return it so callers can read the cross-tenant pointer.

    Args:
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the source_task_id round-trip assertion passes.
    """
    db = str(tmp_path / "t.sqlite")
    mgr = TaskManager(db)
    mgr.record(
        Submission(
            task_id="T-caller",
            status="submitted",
            output_dir="agent_data/shared/fp/output/",
            source_task_id="R-prior",
        )
    )
    row = mgr.get_task("T-caller")
    assert row is not None
    assert row["source_task_id"] == "R-prior"


def test_resolve_tasks_db_path_honors_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify the resolver reads the ApiConfig tasks-db path alias.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Pytest temp directory fixture.

    Returns:
        None after the override path assertion passes.
    """
    override = str(tmp_path / "custom_tasks.db")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", override)

    assert resolve_tasks_db_path() == override
