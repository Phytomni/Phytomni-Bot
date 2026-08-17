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

import inspect
import sqlite3
from pathlib import Path

import pytest
from tests.support.run_registry_fakes import (
    fixed_run_context,
    stamp_run_created_at,
)
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
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


def test_record_reserved_submissions_preserves_public_signature() -> None:
    """The compatibility facade keeps class and bound call signatures."""
    method = getattr(RunRegistry, "record_reserved_submissions")
    assert str(inspect.signature(method)) == (
        "(self, run_id: 'str', *, owner: 'str', agent: 'str', "
        "submissions: 'Sequence[Submission]', result: 'dict[str, Any]', "
        "now: 'str') -> 'bool'"
    )
    instance = RunRegistry.__new__(RunRegistry)
    bound_method = getattr(instance, "record_reserved_submissions")
    assert str(inspect.signature(bound_method)) == (
        "(run_id: 'str', *, owner: 'str', agent: 'str', "
        "submissions: 'Sequence[Submission]', result: 'dict[str, Any]', "
        "now: 'str') -> 'bool'"
    )
    signature = inspect.signature(method)
    expected_annotations = {
        name: parameter.annotation
        for name, parameter in signature.parameters.items()
        if name != "self"
    }
    expected_annotations["return"] = signature.return_annotation
    assert method.__annotations__ == expected_annotations
    assert method.__qualname__ == "RunRegistry.record_reserved_submissions"
    assert method.__module__ == RunRegistry.__module__


def _seed_async_run(
    registry: RunRegistry,
    manager: TaskManager,
    spec: RunSpec,
    task_ids: tuple[str, ...],
    task_status: str = "submitted",
) -> None:
    """Insert a running run plus its child task rows."""
    ctx = fixed_run_context(spec)
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


def test_reserve_run_rejects_collision_without_overwriting(
    tmp_path: Path,
) -> None:
    """A reservation collision preserves the established owner and metadata."""
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    first = RunSpec(
        run_id="run-fixed",
        user_id="alice",
        agent="analyst",
        origin="remote",
    )
    registry.reserve_run(
        first,
        request_info=RunRequestInfo(
            request_id="req-first",
            locale="en-US",
        ),
        result=empty_execution_projection(),
    )

    with pytest.raises(sqlite3.IntegrityError):
        registry.reserve_run(
            RunSpec(
                run_id="run-fixed",
                user_id="mallory",
                agent="design",
                origin="remote",
            ),
            request_info=RunRequestInfo(request_id="req-second"),
            result=empty_execution_projection(),
        )

    stored = registry.get_run("run-fixed", owner="alice")
    assert stored is not None
    assert stored.spec.agent == "analyst"
    assert stored.request_info.request_id == "req-first"
    assert registry.get_run("run-fixed", owner="mallory") is None


def test_update_running_result_is_owner_and_status_scoped(
    tmp_path: Path,
) -> None:
    """Only an owner can update a running projection, never a terminal one."""
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-1",
            user_id="alice",
            agent="research",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-1"),
        result=empty_execution_projection(),
    )
    manager = TaskManager(db_path)
    manager.record(
        Submission(
            task_id="task-winner",
            status="succeeded",
            output_dir="/out/winner",
            run_context=RunContext(
                run_id="run-1",
                user_id="alice",
                agent="research",
                origin="remote",
            ),
        )
    )
    manager.set_task_final_report("task-winner", "# Persisted winner")
    updated = empty_execution_projection()
    updated["execution"]["warnings"] = [{"code": "partial_submission"}]

    assert (
        registry.update_running_result(
            "run-1", owner="mallory", result=updated
        )
        is False
    )
    assert registry.update_running_result(
        "run-1", owner="alice", result=updated
    )
    record = registry.get_run("run-1", owner="alice")
    assert record is not None
    assert record.result == updated

    assert registry.settle_run(
        "run-1",
        owner="alice",
        status="succeeded",
        result=updated,
        expected_revision=record.revision,
    )
    assert (
        registry.update_running_result(
            "run-1", owner="alice", result=empty_execution_projection()
        )
        is False
    )
    assert (
        registry.fail_running_run(
            "run-1",
            owner="alice",
            result=empty_execution_projection(degraded=True),
            error="background_submission_failed",
        )
        is False
    )
    record = registry.get_run("run-1", owner="alice")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result == updated
    assert record.task_ids == ("task-winner",)
    assert manager.get_task_final_report("task-winner") == (
        "# Persisted winner"
    )


def test_update_running_result_keeps_submit_delivery_marker(
    tmp_path: Path,
) -> None:
    """A later running projection must not drop submit-time archive delivery.

    HTTP background workers overwrite the reserved result with a formatted
    tool envelope that has ``delivery: null``. Harvest only packs a zip
    when ``delivery.required`` is still on the stored run.
    """
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-keep-delivery",
            user_id="alice",
            agent="analyst",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-keep-delivery"),
        result=empty_execution_projection(result_archive_required=True),
    )
    incoming = empty_execution_projection()
    incoming["execution"]["tasks"] = [
        {"id": "task-1", "accepted": True, "status": "submitted"}
    ]
    incoming["execution"]["delivery"] = None

    assert registry.update_running_result(
        "run-keep-delivery",
        owner="alice",
        result=incoming,
    )
    record = registry.get_run("run-keep-delivery", owner="alice")
    assert record is not None
    assert record.result is not None
    delivery = record.result["execution"]["delivery"]
    assert delivery["required"] is True
    assert delivery["status"] == "pending"
    assert record.result["execution"]["tasks"][0]["id"] == "task-1"


def test_fail_running_run_is_owner_scoped(tmp_path: Path) -> None:
    """A background failure can settle only its owner's running row."""
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    registry.reserve_run(
        RunSpec(
            run_id="run-fail",
            user_id="alice",
            agent="design",
            origin="remote",
        ),
        request_info=RunRequestInfo(request_id="req-fail"),
        result=empty_execution_projection(),
    )
    degraded = empty_execution_projection(degraded=True)
    current = registry.get_run("run-fail", owner="alice")
    assert current is not None

    assert (
        registry.fail_running_run(
            "run-fail",
            owner="mallory",
            result=degraded,
            error="background_submission_failed",
        )
        is False
    )
    assert registry.fail_running_run(
        "run-fail",
        owner="alice",
        result=degraded,
        error="background_submission_failed",
        expected_revision=current.revision,
    )
    record = registry.get_run("run-fail", owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.result == degraded
    assert record.error == "background_submission_failed"


@pytest.mark.parametrize(
    "foreign_context",
    (
        RunContext(run_id="run-foreign", user_id="alice", agent="analyst"),
        RunContext(run_id="run-target", user_id="mallory", agent="analyst"),
        RunContext(run_id="run-target", user_id="alice", agent="design"),
    ),
)
def test_record_reserved_submissions_rejects_foreign_task_identity_atomically(
    tmp_path: Path,
    foreign_context: RunContext,
) -> None:
    """Foreign child identities cannot be reparented by a reserved run."""
    registry, manager, db_path = _make_registry(tmp_path)
    target_context = RunContext(
        run_id="run-target", user_id="alice", agent="analyst"
    )
    registry.reserve_run(
        RunSpec("run-target", "alice", "analyst", "remote"),
        request_info=RunRequestInfo(request_id="req-target"),
        result=empty_execution_projection(),
    )
    manager.record(
        Submission(
            task_id="task-existing",
            status="submitted",
            output_dir="/foreign",
            run_context=foreign_context,
        )
    )

    assert (
        registry.record_reserved_submissions(
            "run-target",
            owner="alice",
            agent="analyst",
            submissions=(
                Submission(
                    task_id="task-new",
                    status="submitted",
                    output_dir="/new",
                    run_context=target_context,
                ),
                Submission(
                    task_id="task-existing",
                    status="submitted",
                    output_dir="/replacement",
                    run_context=target_context,
                ),
            ),
            result=empty_execution_projection(),
            now="2026-07-30T00:00:00+00:00",
        )
        is False
    )

    with closed_sqlite_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT run_id, user_id, agent, output_dir FROM tasks "
            "WHERE task_id = 'task-existing'"
        ).fetchone()
        new_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_id = 'task-new'"
        ).fetchone()[0]
    assert existing == (
        foreign_context.run_id,
        foreign_context.user_id,
        foreign_context.agent,
        "/foreign",
    )
    assert new_count == 0
    record = registry.get_run("run-target", owner="alice")
    assert record is not None
    assert record.result == empty_execution_projection()


def test_record_reserved_submissions_allows_same_run_idempotency(
    tmp_path: Path,
) -> None:
    """A retry may update a child only when its immutable owner matches."""
    registry, manager, _db_path = _make_registry(tmp_path)
    context = RunContext(run_id="run-target", user_id="alice", agent="analyst")
    registry.reserve_run(
        RunSpec("run-target", "alice", "analyst", "remote"),
        request_info=RunRequestInfo(request_id="req-target"),
        result=empty_execution_projection(),
    )
    manager.record(
        Submission(
            task_id="task-owned",
            status="submitted",
            output_dir="/initial",
            run_context=context,
        )
    )
    result = empty_execution_projection()
    result["execution"]["warnings"] = [{"code": "retry"}]
    submission = Submission(
        task_id="task-owned",
        status="submitted",
        output_dir="/retry",
        run_context=context,
    )

    for _ in range(2):
        assert registry.record_reserved_submissions(
            "run-target",
            owner="alice",
            agent="analyst",
            submissions=(submission,),
            result=result,
            now="2026-07-30T00:00:00+00:00",
        )

    record = registry.get_run("run-target", owner="alice")
    assert record is not None
    assert record.task_ids == ("task-owned",)
    assert record.result == result
    assert manager.get_task("task-owned") == {
        "task_id": "task-owned",
        "status": "submitted",
        "analysis_id": "",
        "output_dir": "/retry",
        "source_task_id": None,
    }


def test_record_reserved_submissions_rejects_duplicate_batch_conflict(
    tmp_path: Path,
) -> None:
    """A duplicate batch ID cannot let a later context reparent a child."""
    registry, _manager, db_path = _make_registry(tmp_path)
    context = RunContext(run_id="run-target", user_id="alice", agent="analyst")
    registry.reserve_run(
        RunSpec("run-target", "alice", "analyst", "remote"),
        request_info=RunRequestInfo(request_id="req-target"),
        result=empty_execution_projection(),
    )

    assert (
        registry.record_reserved_submissions(
            "run-target",
            owner="alice",
            agent="analyst",
            submissions=(
                Submission(
                    task_id="task-other",
                    status="submitted",
                    output_dir="/other",
                    run_context=context,
                ),
                Submission(
                    task_id="task-duplicate",
                    status="submitted",
                    output_dir="/first",
                    run_context=context,
                ),
                Submission(
                    task_id="task-duplicate",
                    status="submitted",
                    output_dir="/second",
                    run_context=RunContext(
                        run_id="run-other", user_id="mallory", agent="design"
                    ),
                ),
            ),
            result=empty_execution_projection(),
            now="2026-07-30T00:00:00+00:00",
        )
        is False
    )

    with closed_sqlite_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 0


def test_record_reserved_submissions_rejects_duplicate_same_identity_batch(
    tmp_path: Path,
) -> None:
    """One reserved batch may contain each immutable child identity once."""
    registry, _manager, db_path = _make_registry(tmp_path)
    context = RunContext(run_id="run-target", user_id="alice", agent="analyst")
    registry.reserve_run(
        RunSpec("run-target", "alice", "analyst", "remote"),
        request_info=RunRequestInfo(request_id="req-target"),
        result=empty_execution_projection(),
    )
    duplicate = Submission(
        task_id="task-duplicate",
        status="submitted",
        output_dir="/same",
        run_context=context,
    )

    assert (
        registry.record_reserved_submissions(
            "run-target",
            owner="alice",
            agent="analyst",
            submissions=(duplicate, duplicate),
            result=empty_execution_projection(),
            now="2026-07-30T00:00:00+00:00",
        )
        is False
    )
    with closed_sqlite_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 0


def test_record_reserved_submissions_rejects_missing_context_collision(
    tmp_path: Path,
) -> None:
    """A context-free reserved retry cannot clear an owned child identity."""
    registry, manager, db_path = _make_registry(tmp_path)
    context = RunContext(run_id="run-target", user_id="alice", agent="analyst")
    registry.reserve_run(
        RunSpec("run-target", "alice", "analyst", "remote"),
        request_info=RunRequestInfo(request_id="req-target"),
        result=empty_execution_projection(),
    )
    manager.record(
        Submission(
            task_id="task-owned",
            status="submitted",
            output_dir="/owned",
            run_context=context,
        )
    )

    assert (
        registry.record_reserved_submissions(
            "run-target",
            owner="alice",
            agent="analyst",
            submissions=(
                Submission(
                    task_id="task-owned",
                    status="submitted",
                    output_dir="/cleared",
                ),
            ),
            result=empty_execution_projection(),
            now="2026-07-30T00:00:00+00:00",
        )
        is False
    )
    with closed_sqlite_connection(db_path) as conn:
        stored = conn.execute(
            "SELECT run_id, user_id, agent, output_dir FROM tasks "
            "WHERE task_id = 'task-owned'"
        ).fetchone()
    assert stored == ("run-target", "alice", "analyst", "/owned")


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

    with closed_sqlite_connection(db_path) as conn:
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


def test_invalid_persisted_locale_fails_closed(tmp_path: Path) -> None:
    """A stored locale outside the public enum cannot hydrate a run."""
    registry, _, db = _make_registry(tmp_path)
    registry.create_run(RunSpec("run-locale-bad", "alice", "chat", "local"))
    with closed_sqlite_connection(db) as conn:
        conn.execute(
            "UPDATE runs SET locale = ? WHERE run_id = ?",
            ("fr-FR", "run-locale-bad"),
        )
        conn.commit()

    with pytest.raises(ValueError, match="unsupported persisted locale"):
        registry.get_run("run-locale-bad", owner="alice")


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
