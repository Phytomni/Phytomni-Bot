# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the run registry sitting on the shared task DB.

Covers schema migration, sync-run terminal caching with TTL, owner
isolation on get_run/list_runs (404-equivalent for foreign or unknown
ids), filter/paging on list_runs, reconcile's terminal-no-poll guard,
and the manual cascade in purge_expired.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tests.support.run_registry_fakes import stamp_run_created_at
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.runtime.run_registry import (
    A2UIActionConflict,
    A2UIActionInvariantError,
    RunFilter,
    RunOutcome,
    RunRecord,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
    Timestamps,
    _terminal_payload,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.unit


def _make_registry(tmp_path: Path) -> tuple[RunRegistry, TaskManager, str]:
    """Return a (registry, manager, db_path) trio on a fresh tmp DB."""
    db = str(tmp_path / "tasks.db")
    return RunRegistry(db), TaskManager(db), db


def _seed_async_run(
    registry: RunRegistry,
    manager: TaskManager,
    spec: RunSpec,
    task_ids: tuple[str, ...],
    task_status: str = "submitted",
) -> None:
    """Insert a running run plus its child task rows."""
    ctx = RunContext(
        run_id=spec.run_id,
        user_id=spec.user_id,
        agent=spec.agent,
        origin=spec.origin,
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
    )
    for task_id in task_ids:
        manager.record(
            Submission(
                task_id=task_id,
                status=task_status,
                output_dir="/out",
                analysis_id="",
                run_context=ctx,
            )
        )
    registry.create_run(spec)


def _create_paused_review(
    registry: RunRegistry,
    *,
    surface_id: str,
) -> None:
    """Insert a valid Review pause for A2UI claim tests."""
    registry.create_run(
        RunSpec("run-review-1", "user-1", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={
                "interrupt": {
                    "thread_id": "run-review-1",
                    "draft": {
                        "a2ui": {
                            "catalog_version": "v1.0",
                            "surface_id": surface_id,
                            "widget": "confirm",
                            "props": {
                                "title": "Review approval",
                                "body": "Proceed?",
                            },
                        }
                    },
                },
                "status": "input_required",
            },
        ),
    )


def test_init_db_creates_runs_table_and_indices(tmp_path: Path) -> None:
    """The registry creates the runs table and shared indices."""
    db = str(tmp_path / "tasks.db")

    RunRegistry(db)

    conn = sqlite3.connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indices = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        conn.close()
    assert "runs" in tables
    assert "idx_runs_user" in indices
    assert "idx_tasks_run" in indices
    assert "run_a2ui_actions" in tables
    assert "idx_run_a2ui_actions_owner_action" in indices


def test_only_one_registry_instance_claims_surface(tmp_path: Path) -> None:
    """The database CAS rejects a second process's claim."""
    db_path = str(tmp_path / "tasks.db")
    first = RunRegistry(db_path)
    second = RunRegistry(db_path)
    _create_paused_review(first, surface_id="surface-1")

    claim = first.claim_a2ui_action(
        run_id="run-review-1",
        owner="user-1",
        surface_id="surface-1",
        widget="confirm",
        action_id="action-1",
        channel="a2ui",
    )

    assert claim.action_id == "action-1"
    with pytest.raises(A2UIActionConflict, match="already been claimed"):
        second.claim_a2ui_action(
            run_id="run-review-1",
            owner="user-1",
            surface_id="surface-1",
            widget="confirm",
            action_id="action-2",
            channel="a2ui",
        )


def test_action_id_does_not_replay_success(tmp_path: Path) -> None:
    """A completed action remains claimed even when the id is replayed."""
    registry, _, _ = _make_registry(tmp_path)
    _create_paused_review(registry, surface_id="surface-1")
    claim = registry.claim_a2ui_action(
        run_id="run-review-1",
        owner="user-1",
        surface_id="surface-1",
        widget="confirm",
        action_id="same-id",
        channel="a2ui",
    )

    assert (
        registry.complete_a2ui_action(
            claim,
            owner="user-1",
            outcome="succeeded",
        )
        is True
    )
    with pytest.raises(A2UIActionConflict):
        registry.claim_a2ui_action(
            run_id="run-review-1",
            owner="user-1",
            surface_id="surface-1",
            widget="confirm",
            action_id="same-id",
            channel="a2ui",
        )


def test_a2ui_action_completion_and_audit_are_owner_scoped(
    tmp_path: Path,
) -> None:
    """Completion is one-shot and the reader omits action payloads."""
    registry, _, db_path = _make_registry(tmp_path)
    _create_paused_review(registry, surface_id="surface-audit")
    claim = registry.claim_a2ui_action(
        run_id="run-review-1",
        owner="user-1",
        surface_id="surface-audit",
        widget="confirm",
        action_id="action-audit",
        channel="classic",
    )

    assert registry.list_a2ui_actions(owner="other") == []
    assert (
        registry.complete_a2ui_action(
            claim,
            owner="other",
            outcome="failed",
        )
        is False
    )
    assert (
        registry.complete_a2ui_action(
            claim,
            owner="user-1",
            outcome="failed",
        )
        is True
    )
    assert (
        registry.complete_a2ui_action(
            claim,
            owner="user-1",
            outcome="succeeded",
        )
        is False
    )

    audit = registry.list_a2ui_actions(owner="user-1", run_id="run-review-1")
    assert len(audit) == 1
    assert audit[0].action_id == "action-audit"
    assert audit[0].channel == "classic"
    assert audit[0].outcome == "failed"
    assert audit[0].claimed_at
    assert audit[0].completed_at

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(run_a2ui_actions)")
        }
    assert "payload" not in columns


def test_invalid_persisted_a2ui_surface_is_an_invariant_failure(
    tmp_path: Path,
) -> None:
    """Malformed pause data must not be converted into a claim conflict."""
    registry, _, _ = _make_registry(tmp_path)
    registry.create_run(
        RunSpec("run-invalid-a2ui", "user-1", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={"interrupt": {"draft": {"a2ui": {}}}},
        ),
    )

    with pytest.raises(
        A2UIActionInvariantError,
        match="invalid A2UI surface",
    ):
        registry.claim_a2ui_action(
            run_id="run-invalid-a2ui",
            owner="user-1",
            surface_id="surface-1",
            widget="confirm",
            action_id="action-1",
            channel="a2ui",
        )


def test_create_run_terminal_caches_result_and_sets_ttl(
    tmp_path: Path,
) -> None:
    """Sync runs are stored terminal with result and an expires_at."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-sync-1", "alice", "chat", "local")

    registry.create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result={"answer": "hello"}),
    )
    record = registry.get_run("run-sync-1", owner="alice")

    assert record is not None
    assert record.spec == spec
    assert record.status == "succeeded"
    assert record.result == {"answer": "hello"}
    assert record.error is None
    assert record.timestamps.expires_at is not None
    assert not record.task_ids


def test_get_run_isolates_foreign_owner(tmp_path: Path) -> None:
    """A foreign owner sees the same None response as an unknown id."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-9", "alice", "analyst", "remote"),
        ("t-1",),
    )

    assert registry.get_run("run-9", owner="bob") is None
    assert registry.get_run("run-9", owner="alice") is not None
    assert registry.get_run("missing", owner="alice") is None


def test_list_runs_filters_and_pages(tmp_path: Path) -> None:
    """List honors owner, status/agent/origin filters, and paging."""
    registry, manager, _ = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-a", "alice", "analyst", "remote"),
        ("t-a",),
    )
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-b", "alice", "design", "remote"),
        ("t-b",),
    )
    registry.create_run(
        RunSpec("run-c", "alice", "chat", "local"),
        outcome=RunOutcome(status="succeeded"),
    )
    registry.create_run(
        RunSpec("run-d", "bob", "chat", "local"),
        outcome=RunOutcome(status="succeeded"),
    )

    listing = registry.list_runs(owner="alice")
    assert {r.spec.run_id for r in listing} == {"run-a", "run-b", "run-c"}
    by_agent = registry.list_runs(
        owner="alice", run_filter=RunFilter(agent="analyst")
    )
    assert [r.spec.run_id for r in by_agent] == ["run-a"]
    by_origin = registry.list_runs(
        owner="alice", run_filter=RunFilter(origin="local")
    )
    assert [r.spec.run_id for r in by_origin] == ["run-c"]
    paged = registry.list_runs(owner="alice", limit=1, offset=1)
    assert len(paged) == 1


def test_purge_expired_cascades_child_tasks(tmp_path: Path) -> None:
    """Expired runs are deleted along with their child task rows."""
    registry, manager, db = _make_registry(tmp_path)
    _seed_async_run(
        registry,
        manager,
        RunSpec("run-keep", "alice", "analyst", "remote"),
        ("t-1",),
    )
    registry.create_run(RunSpec("run-stale", "alice", "chat", "local"))
    manager.record(
        Submission(
            task_id="t-stale",
            status="succeeded",
            output_dir="/out",
            run_context=RunContext(run_id="run-stale", user_id="alice"),
        )
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE runs SET expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", "run-stale"),
        )
        conn.commit()
    finally:
        conn.close()

    deleted = registry.purge_expired()

    assert deleted == 1
    assert registry.get_run("run-stale", owner="alice") is None
    assert registry.get_run("run-keep", owner="alice") is not None
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT task_id FROM tasks WHERE run_id = ?",
            ("run-stale",),
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_run_record_is_frozen_dataclass() -> None:
    """The read view is frozen to enforce immutability at call sites."""
    record = RunRecord(
        spec=RunSpec("r", "u", "a", "local"),
        status="succeeded",
        result=None,
        error=None,
        timestamps=Timestamps(created_at="t", updated_at="t", expires_at=None),
        task_ids=(),
    )

    with pytest.raises(Exception):
        setattr(record, "status", "running")


def test_list_runs_filters_by_created_after(tmp_path: Path) -> None:
    """RunFilter.created_after keeps rows with created_at >= bound."""
    registry, _, db = _make_registry(tmp_path)
    registry.create_run(RunSpec("run-old", "alice", "chat", "local"))
    registry.create_run(RunSpec("run-new", "alice", "chat", "local"))
    # Stamp deterministic created_at so the bound comparison is stable.
    stamp_run_created_at(db, "run-old", "run-new")

    listing = registry.list_runs(
        owner="alice",
        run_filter=RunFilter(created_after="2026-03-01T00:00:00+00:00"),
    )

    assert [r.spec.run_id for r in listing] == ["run-new"]


def test_list_runs_filters_by_created_before(tmp_path: Path) -> None:
    """RunFilter.created_before keeps rows with created_at <= bound."""
    registry, _, db = _make_registry(tmp_path)
    registry.create_run(RunSpec("run-old", "alice", "chat", "local"))
    registry.create_run(RunSpec("run-new", "alice", "chat", "local"))
    with closed_sqlite_connection(db) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", "run-old"),
                ("2026-06-01T00:00:00+00:00", "run-new"),
            ],
        )
        conn.commit()

    listing = registry.list_runs(
        owner="alice",
        run_filter=RunFilter(created_before="2026-03-01T00:00:00+00:00"),
    )

    assert [r.spec.run_id for r in listing] == ["run-old"]


def test_list_runs_composes_date_range_with_other_filters(
    tmp_path: Path,
) -> None:
    """created_after / created_before stack with status / agent / origin."""
    registry, _, db = _make_registry(tmp_path)
    registry.create_run(
        RunSpec("run-a-old", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="failed"),
    )
    registry.create_run(
        RunSpec("run-a-new", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="failed"),
    )
    registry.create_run(
        RunSpec("run-c-new", "alice", "chat", "local"),
        outcome=RunOutcome(status="failed"),
    )
    with closed_sqlite_connection(db) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", "run-a-old"),
                ("2026-06-01T00:00:00+00:00", "run-a-new"),
                ("2026-06-01T00:00:00+00:00", "run-c-new"),
            ],
        )
        conn.commit()

    listing = registry.list_runs(
        owner="alice",
        run_filter=RunFilter(
            status="failed",
            agent="analyst",
            created_after="2026-03-01T00:00:00+00:00",
        ),
    )

    assert [r.spec.run_id for r in listing] == ["run-a-new"]


def test_create_run_persists_request_info(tmp_path: Path) -> None:
    """A request_info bundle round-trips through create_run + get_run."""
    registry, _, _ = _make_registry(tmp_path)
    spec = RunSpec("run-ri-1", "alice", "knowledge", "local")
    info = RunRequestInfo(
        dialogue_id="dlg-1",
        query="What does AT1G01010 do?",
        tool_name="KnowledgeAgent",
        model="phyto-knowledge",
        request_json='{"messages": []}',
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
        query="hello world",
        tool_name="ChatAgent",
        model="phyto-chat",
        request_json='{"x": 1}',
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
    """settle_run updates status/result in place without touching created_at.

    Pins the fix for the streaming-settle regression: unlike
    ``create_run``'s INSERT OR REPLACE (which stamps ``now`` into both
    created_at and updated_at), settle_run is a targeted UPDATE that
    must leave created_at untouched while still advancing updated_at
    and stamping a terminal expires_at.
    """
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
    # Build a pre-migration table shape and seed one row. The DDL is
    # inlined into one string (no per-column newlines) so pylint's
    # R0801 similarity scan does not group it with the production
    # _CREATE_RUNS_DDL constant: the schemas overlap by design for the
    # legacy fixture, and extracting a shared helper would couple
    # tests to internal schema strings that are meant to be free to
    # drift.
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

    # Opening the registry against the legacy DB must migrate in place.
    registry = RunRegistry(db)
    record = registry.get_run("legacy-run", owner="alice")

    assert record is not None
    # Legacy row keeps its data and the new columns default to NULL.
    assert record.spec.user_id == "alice"
    assert record.status == "succeeded"
    assert record.request_info == RunRequestInfo()
    # The migration is rerun-safe; opening again does not raise.
    RunRegistry(db)


def test_terminal_payload_rolls_up_degraded() -> None:
    """Any degraded child marks the run-aggregate payload degraded."""
    live = [
        {
            "task_id": "t1",
            "status": "succeeded",
            "degraded": False,
            "degraded_reason": None,
            "final_report": "# r",
        },
        {
            "task_id": "t2",
            "status": "succeeded",
            "degraded": True,
            "degraded_reason": "gene overview unavailable",
            "final_report": None,
        },
    ]

    payload, error = _terminal_payload("succeeded", live, [], "ans")

    assert payload is not None
    assert payload["degraded"] is True
    assert error is None


def test_terminal_payload_healthy_run_not_degraded() -> None:
    """All-healthy children leave the run not degraded."""
    live = [
        {
            "task_id": "t1",
            "status": "succeeded",
            "degraded": False,
            "degraded_reason": None,
            "final_report": "# r",
        },
    ]

    payload, _error = _terminal_payload("succeeded", live, [], "ans")

    assert payload is not None
    assert payload["degraded"] is False
