# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Detached background-run reconciliation contracts."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.runtime import run_registry
from mcp_server_phytomni.runtime.background_policy import (
    BACKGROUND_SUBMISSION_AGENT_SLUGS,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.live_tasks import (
    deregister_live_task,
    register_live_task,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.unit


def _reserve_zero_child(
    registry: RunRegistry,
    *,
    run_id: str,
    agent: str = "analyst",
    origin: str = "remote",
) -> None:
    """Reserve one running row without attaching child tasks."""
    registry.reserve_run(
        RunSpec(run_id, "alice", agent, origin),
        request_info=RunRequestInfo(request_id=f"request-{run_id}"),
        result=empty_execution_projection(),
    )


def _record_child(
    db_path: str,
    *,
    run_id: str,
    task_id: str,
    agent: str = "analyst",
) -> None:
    """Attach one fully owned child row to a reserved run."""
    TaskManager(db_path).record(
        Submission(
            task_id=task_id,
            status="submitted",
            output_dir="/out",
            run_context=RunContext(
                run_id=run_id,
                user_id="alice",
                agent=agent,
                origin="remote",
                created_at="2026-08-02T00:00:00+00:00",
                updated_at="2026-08-02T00:00:00+00:00",
            ),
        )
    )


def _assert_worker_lost(record: Any) -> None:
    """Assert the bounded worker-loss terminal projection."""
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_worker_lost"
    assert record.result == empty_execution_projection(degraded=True)
    assert record.task_ids == ()


@pytest.mark.asyncio
async def test_zero_child_live_worker_remains_running(tmp_path: Path) -> None:
    """A process-local live worker owns its zero-child running interval."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    _reserve_zero_child(registry, run_id="run-live")
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    register_live_task("run-live", task)
    try:
        record = await registry.reconcile("run-live", owner="alice")
        assert record is not None
        assert record.status == "running"
        assert record.task_ids == ()
        assert record.error is None
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        deregister_live_task("run-live")


@pytest.mark.asyncio
async def test_zero_child_missing_worker_settles_worker_lost(
    tmp_path: Path,
) -> None:
    """An absent detached worker deterministically fails its reservation."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    _reserve_zero_child(registry, run_id="run-missing")

    _assert_worker_lost(await registry.reconcile("run-missing", owner="alice"))


@pytest.mark.asyncio
async def test_zero_child_done_worker_settles_worker_lost(
    tmp_path: Path,
) -> None:
    """A completed worker with no accepted child cannot remain running."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    _reserve_zero_child(registry, run_id="run-done")
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    register_live_task("run-done", task)
    try:
        _assert_worker_lost(
            await registry.reconcile("run-done", owner="alice")
        )
    finally:
        deregister_live_task("run-done")


@pytest.mark.asyncio
async def test_zero_child_failed_worker_settles_worker_lost(
    tmp_path: Path,
) -> None:
    """A failed observed worker with no child settles to the safe code."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    _reserve_zero_child(registry, run_id="run-failed")

    async def fail() -> None:
        raise RuntimeError("synthetic failure")

    task = asyncio.create_task(fail())
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await task
    register_live_task("run-failed", task)
    try:
        _assert_worker_lost(
            await registry.reconcile("run-failed", owner="alice")
        )
    finally:
        deregister_live_task("run-failed")


@pytest.mark.asyncio
async def test_fresh_registry_after_restart_settles_worker_lost(
    tmp_path: Path,
) -> None:
    """A fresh registry needs no elapsed-time guess after worker loss."""
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    _reserve_zero_child(registry, run_id="run-restart")
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    register_live_task("run-restart", task)
    try:
        deregister_live_task("run-restart")
        fresh = RunRegistry(db_path)
        _assert_worker_lost(
            await fresh.reconcile("run-restart", owner="alice")
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        deregister_live_task("run-restart")


@pytest.mark.asyncio
async def test_accepted_child_reconciles_without_live_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted child remains the durable reconciliation source."""
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    _reserve_zero_child(registry, run_id="run-child")
    _record_child(db_path, run_id="run-child", task_id="task-child")

    async def submitted(task_id: str) -> dict[str, str]:
        assert task_id == "task-child"
        return {"task_id": task_id, "status": "submitted"}

    monkeypatch.setattr(run_registry, "reconcile_task", submitted)
    record = await registry.reconcile("run-child", owner="alice")

    assert record is not None
    assert record.status == "running"
    assert record.task_ids == ("task-child",)
    assert record.error is None


@pytest.mark.asyncio
async def test_child_accepted_during_worker_lost_cas_remains_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child attached after the first read defeats worker-lost settlement."""
    db_path = str(tmp_path / "tasks.db")
    registry = RunRegistry(db_path)
    _reserve_zero_child(registry, run_id="run-racing-child")
    original_fail = registry.fail_running_run
    fail_calls = 0

    def accept_before_fail(
        run_id: str,
        *,
        owner: str,
        result: dict[str, Any],
        error: str,
    ) -> bool:
        """Attach through the real reservation seam before the failure CAS."""
        nonlocal fail_calls
        fail_calls += 1
        assert registry.record_reserved_submissions(
            run_id,
            owner=owner,
            agent="analyst",
            submissions=(
                Submission(
                    task_id="task-racing-child",
                    status="submitted",
                    output_dir="/out",
                    run_context=RunContext(
                        run_id=run_id,
                        user_id=owner,
                        agent="analyst",
                        origin="remote",
                        created_at="2026-08-02T00:00:00+00:00",
                        updated_at="2026-08-02T00:00:00+00:00",
                    ),
                ),
            ),
            result=empty_execution_projection(),
            now="2026-08-02T00:00:00+00:00",
        )
        return original_fail(run_id, owner=owner, result=result, error=error)

    async def submitted(task_id: str) -> dict[str, str]:
        assert task_id == "task-racing-child"
        return {"task_id": task_id, "status": "submitted"}

    monkeypatch.setattr(registry, "fail_running_run", accept_before_fail)
    monkeypatch.setattr(run_registry, "reconcile_task", submitted)

    record = await registry.reconcile("run-racing-child", owner="alice")

    assert fail_calls == 1
    assert record is not None
    assert record.status == "running"
    assert record.task_ids == ("task-racing-child",)
    assert record.error is None


@pytest.mark.asyncio
async def test_terminal_run_skips_worker_and_child_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal rows return before liveness or child probes."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-terminal", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="succeeded", result={"winner": True}),
    )

    def liveness_probe(_run_id: str) -> bool:
        raise AssertionError("terminal run probed worker liveness")

    async def child_probe(_task_id: str) -> dict[str, str]:
        raise AssertionError("terminal run probed child status")

    monkeypatch.setattr(run_registry, "is_live_running", liveness_probe)
    monkeypatch.setattr(run_registry, "reconcile_task", child_probe)

    record = await registry.reconcile("run-terminal", owner="alice")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result == {"winner": True}


@pytest.mark.parametrize(
    ("agent", "origin"),
    [
        ("deep_genome", "remote"),
        ("review", "local"),
        ("analyst", "local"),
        ("future_agent", "remote"),
    ],
)
@pytest.mark.asyncio
async def test_non_policy_zero_child_run_remains_running(
    tmp_path: Path,
    agent: str,
    origin: str,
) -> None:
    """Non-policy zero-child runs keep their pre-existing lifecycle."""
    registry = RunRegistry(str(tmp_path / f"{agent}-{origin}.db"))
    _reserve_zero_child(
        registry,
        run_id=f"run-{agent}-{origin}",
        agent=agent,
        origin=origin,
    )

    record = await registry.reconcile(f"run-{agent}-{origin}", owner="alice")
    assert record is not None
    assert record.status == "running"
    assert record.error is None


def test_reconcile_consumer_uses_canonical_policy_object() -> None:
    """The reconciliation consumer shares the API's exact policy object."""
    assert getattr(api_app, "_BACKGROUND_SUBMISSION_AGENT_SLUGS") is (
        BACKGROUND_SUBMISSION_AGENT_SLUGS
    )
