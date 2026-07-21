# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline lifecycle tests for the revisioned DeepGenome coordinator."""

# These tests intentionally drive the protected coordinator seams and use
# Pydantic-style uppercase config fields on lightweight fakes.
# pylint: disable=too-many-locals

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.deep_genome import agent as agent_module
from mcp_server_phytomni.agents.deep_genome import design_mount
from mcp_server_phytomni.agents.deep_genome import dispatch as dispatch_module
from mcp_server_phytomni.agents.deep_genome import report as report_module
from mcp_server_phytomni.agents.deep_genome.agent import (
    DeepGenomeAgents,
)
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    DeepGenomeWorkflowError,
    RemoteSubmission,
)
from mcp_server_phytomni.agents.deep_genome.report import DeepGenomeReportMixin
from mcp_server_phytomni.agents.deep_genome.tracking import (
    DeepGenomeTransitionSink,
)
from mcp_server_phytomni.agents.deep_genome.work_items import (
    build_work_item_plan,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeStore,
    DeepGenomeTrackingError,
)

pytestmark = pytest.mark.agent


def _seed_store(tmp_path: Path) -> tuple[DeepGenomeStore, Any]:
    """Reserve a running owner with a durable BriefGene and plan."""
    db = tmp_path / "tasks.db"
    store = DeepGenomeStore(str(db))
    reservation = store.reserve_run(
        run_id="run-lifecycle",
        umbrella_task_id="task-lifecycle",
        owner="alice",
        output_dir="/obs/lifecycle",
    )
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="# BriefGene\n\nprofile",
    )
    plan = build_work_item_plan("osa", "Os01g0100100", "Os01g0100100")
    store.seed_plan(
        reservation,
        plan,
    )
    return store, reservation


def _dispatch_harness() -> Any:
    """Create a dispatch-only object without compiling LangGraph mounts."""
    harness: Any = dispatch_module.DeepGenomeDispatchMixin.__new__(
        dispatch_module.DeepGenomeDispatchMixin
    )
    config = DeepGenomeConfig()
    setattr(config, "TIMEOUT", 1.0)
    setattr(config, "POLL_INTERVAL", 0.0)
    setattr(config, "MAX_POLL", 10.0)
    harness.deep_genome_config = config
    return harness


async def test_fake_backend_persists_acceptance_before_poll_and_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted identities and every fake-backend state reach SQLite first."""
    # pylint: disable=protected-access
    store, reservation = _seed_store(tmp_path)
    monkeypatch.setattr(
        dispatch_module,
        "resolve_tasks_db_path",
        lambda: str(tmp_path / "tasks.db"),
    )
    harness = _dispatch_harness()
    submission = RemoteSubmission("caller-smep", "remote-smep", "/obs/smep")
    submit = AsyncMock(return_value=submission)
    harness._submit_analysis_task = submit
    result_dir = tmp_path / "smep"
    result_dir.mkdir()
    (result_dir / "result.summary").write_text(
        "# SMEP\n\nusable summary", encoding="utf-8"
    )
    harness._download_analysis_result = AsyncMock(return_value=str(result_dir))
    events: list[str] = []
    original_accept = DeepGenomeStore.accept_remote_submission

    def accept(*args: Any, **kwargs: Any) -> Any:
        events.append("accept")
        return original_accept(*args, **kwargs)

    monkeypatch.setattr(DeepGenomeStore, "accept_remote_submission", accept)
    snapshots: list[Any] = []
    original_transition = DeepGenomeStore.apply_work_item_transition

    def transition(*args: Any, **kwargs: Any) -> Any:
        snapshot = original_transition(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(
        DeepGenomeStore,
        "apply_work_item_transition",
        transition,
    )
    statuses = iter(("PENDING", "RUNNING", "SUCCEEDED"))

    async def status(*_: Any, **__: Any) -> dict[str, str]:
        events.append("poll")
        return {"status": next(statuses)}

    monkeypatch.setattr(dispatch_module, "task_status", status)
    state: Any = {
        "task_id": reservation.umbrella_task_id,
        "work_item_key": "smep_analysis",
    }
    dispatch_and_wait = (
        dispatch_module.DeepGenomeDispatchMixin._dispatch_and_wait_analysis
    )

    result = await dispatch_and_wait(
        harness,
        "smep_analysis",
        "osa",
        "Os01g0100100",
        state=state,
    )

    assert result["status"] == "completed"
    assert events[0:2] == ["accept", "poll"]
    with sqlite3.connect(tmp_path / "tasks.db") as connection:
        row = connection.execute(
            "SELECT status, submitted_task_id, poll_task_id "
            "FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? AND work_item_key = ?",
            (reservation.umbrella_task_id, "smep_analysis"),
        ).fetchone()
    assert row == ("succeeded", "caller-smep", "remote-smep")
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.report_revision == 4
    assert "SMEP" in (snapshot.intermediate_report or "")
    assert [item.report_revision for item in snapshots] == [2, 3, 4]
    assert all(item.final_report is None for item in snapshots)
    assert snapshots[0].intermediate_report is not None


async def test_brief_gene_failure_fails_owner_without_remote_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A coordinator exception leaves no optional submission rows."""
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.agent.resolve_tasks_db_path",
        lambda: db_path,
    )
    agent = DeepGenomeAgents.__new__(DeepGenomeAgents)

    async def broken_ainvoke(*_args: Any, **_kwargs: Any) -> Any:
        """Fail before any optional task can be submitted."""
        raise agent_module.RequiredBriefGeneError("brief gene profile failed")

    agent.app = cast(Any, SimpleNamespace(ainvoke=broken_ainvoke))
    config = DeepGenomeConfig()
    setattr(config, "DEEPGENOME_OUT", str(tmp_path))
    agent.deep_genome_config = config
    envelope = await agent.arun(species_code="osa", gene_id="Os01g0100100")
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
    ]
    await asyncio.gather(*pending, return_exceptions=True)

    with sqlite3.connect(db_path) as connection:
        owner = connection.execute(
            "SELECT t.status, r.error, t.intermediate_report, t.final_report "
            "FROM tasks AS t JOIN runs AS r ON r.run_id = t.run_id "
            "WHERE t.task_id = ?",
            (envelope["task_id"],),
        ).fetchone()
        child_count = connection.execute(
            "SELECT COUNT(*) FROM deep_genome_remote_tasks"
        ).fetchone()[0]
    assert owner == ("failed", "brief gene profile failed", None, None)
    assert child_count == 0


async def test_brief_gene_success_is_durable_before_optional_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The required profile transition commits before plan fan-out."""
    # pylint: disable=protected-access
    db = tmp_path / "tasks.db"
    store = DeepGenomeStore(str(db))
    reservation = store.reserve_run(
        run_id="run-brief",
        umbrella_task_id="task-brief",
        owner="alice",
        output_dir="/obs/brief",
    )
    monkeypatch.setattr(
        agent_module,
        "resolve_tasks_db_path",
        lambda: str(db),
    )
    agent = DeepGenomeAgents.__new__(DeepGenomeAgents)

    state = cast(
        Any,
        {
            "task_id": reservation.umbrella_task_id,
            "preamble": None,
        },
    )
    projected = await agent._persist_brief_gene_result(
        {"preamble": "# Deep Genome Analysis of Os01g0100100"}, state
    )

    assert projected["preamble"].startswith("# Deep Genome")
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.report_revision == 1
    assert snapshot.report_stage == "intermediate"


async def test_reserved_profile_seeds_concrete_plan_before_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph state carries reservation identity into plan seeding."""
    # pylint: disable=protected-access
    db = tmp_path / "tasks.db"
    store = DeepGenomeStore(str(db))
    reservation = store.reserve_run(
        run_id="run-plan",
        umbrella_task_id="task-plan",
        owner="alice",
        output_dir="/obs/plan",
    )
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="# BriefGene\n\nprofile",
    )
    monkeypatch.setattr(
        dispatch_module,
        "resolve_tasks_db_path",
        lambda: str(db),
    )
    harness = _dispatch_harness()
    state: Any = {
        "gene_id": "AT1G01010",
        "species_code": "ath",
        "task_id": reservation.umbrella_task_id,
        "run_id": reservation.run_id,
        "owner": reservation.owner,
        "output_dir": reservation.output_dir,
    }

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._prepare_analysis_tasks(
            harness,
            state,
        )
    )

    assert len(result["work_items"]) == 12
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.progress["planning_complete"] is True


def test_send_payload_carries_reserved_lifecycle_identity() -> None:
    """Dynamic branches retain the umbrella identity after Send routing."""
    # pylint: disable=protected-access
    harness = _dispatch_harness()
    state: Any = {
        "analysis_tasks": [
            {
                "analysis_type": "smep_analysis",
                "target_gene": "Os01g0100100",
                "species_code": "osa",
            }
        ],
        "work_items": [
            {
                "section_key": "smep_analysis",
                "work_item_key": "smep_analysis",
                "display_order": 1,
            }
        ],
        "task_id": "task-send",
        "run_id": "run-send",
        "owner": "alice",
        "output_dir": "/obs/send",
        "task_submit_sleep": 0,
    }

    sends = harness._route_analyst_tasks(state)

    assert len(sends) == 1
    payload = sends[0].arg
    assert payload["task_id"] == "task-send"
    assert payload["run_id"] == "run-send"
    assert payload["owner"] == "alice"
    assert payload["output_dir"] == "/obs/send"


async def test_tracking_write_failure_cancels_and_fails_umbrella(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local write failure never becomes a successful optional branch."""
    # pylint: disable=protected-access
    store, reservation = _seed_store(tmp_path)
    monkeypatch.setattr(
        dispatch_module,
        "resolve_tasks_db_path",
        lambda: str(tmp_path / "tasks.db"),
    )
    harness = _dispatch_harness()
    submission = RemoteSubmission("caller-smoc", "remote-smoc", "/obs/smoc")
    harness._submit_analysis_task = AsyncMock(return_value=submission)
    delete = AsyncMock(return_value="deleted")
    monkeypatch.setattr(dispatch_module, "task_delete", delete)

    def broken_transition(*_: Any, **__: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        DeepGenomeStore,
        "apply_work_item_transition",
        broken_transition,
    )
    monkeypatch.setattr(
        dispatch_module,
        "task_status",
        AsyncMock(return_value={"status": "PENDING"}),
    )
    state: Any = {
        "task_id": reservation.umbrella_task_id,
        "work_item_key": "smoc_analysis",
    }
    dispatch_and_wait = (
        dispatch_module.DeepGenomeDispatchMixin._dispatch_and_wait_analysis
    )

    with pytest.raises(
        DeepGenomeTrackingError, match="remote analysis tracking failed"
    ):
        await dispatch_and_wait(
            harness,
            "smoc_analysis",
            "osa",
            "Os01g0100100",
            state=state,
        )

    assert delete.await_count == 1
    assert delete.await_args is not None
    assert delete.await_args.args == ("caller-smoc",)
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.final_report is None
    assert snapshot.intermediate_report is not None


async def test_unsubmitted_failure_is_persisted_before_branch_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A submit exception settles its planned concrete row as failed."""
    # pylint: disable=protected-access
    store, reservation = _seed_store(tmp_path)
    monkeypatch.setattr(
        dispatch_module,
        "resolve_tasks_db_path",
        lambda: str(tmp_path / "tasks.db"),
    )
    harness = _dispatch_harness()
    harness._submit_analysis_task = AsyncMock(
        side_effect=RuntimeError("analysis backend unavailable")
    )
    state: Any = {
        "task_id": reservation.umbrella_task_id,
        "work_item_key": "smoc_analysis",
    }

    with pytest.raises(RuntimeError, match="analysis backend unavailable"):
        await harness._dispatch_and_wait_analysis(
            "smoc_analysis",
            "osa",
            "Os01g0100100",
            state=state,
        )

    with sqlite3.connect(tmp_path / "tasks.db") as connection:
        row = connection.execute(
            "SELECT status FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? AND work_item_key = ?",
            (reservation.umbrella_task_id, "smoc_analysis"),
        ).fetchone()
    assert row == ("failed",)
    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.intermediate_report is not None


async def test_design_mount_failure_settles_both_concrete_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mounted Design fault marks protein and promoter unavailable."""
    reservation = _seed_store(tmp_path)[1]
    monkeypatch.setattr(
        dispatch_module,
        "resolve_tasks_db_path",
        lambda: str(tmp_path / "tasks.db"),
    )

    async def broken_design_ainvoke(*_args: Any, **_kwargs: Any) -> Any:
        """Raise a deterministic producer error."""
        raise RuntimeError("design producer unavailable")

    async def finalize(*_args: Any, **_kwargs: Any) -> Any:
        """The finalize callback must not run after a mount fault."""
        raise AssertionError("finalize must not run")

    state: Any = {
        "species_code": "osa",
        "target_gene": "Os01g0100100",
        "task_index": 1,
        "task_id": reservation.umbrella_task_id,
    }

    async def record_mount_failure(
        callback_state: Any,
        work_item_keys: tuple[str, ...],
    ) -> None:
        """Adapt the graph callback shape to the bound transition sink."""
        tracking = DeepGenomeTransitionSink.from_state(
            callback_state,
            store_path=dispatch_module.resolve_tasks_db_path(),
        )
        await tracking.record_mount_failure(work_item_keys)

    node = design_mount.make_design_mount_node(
        cast(Any, SimpleNamespace(ainvoke=broken_design_ainvoke)),
        finalize,
        record_mount_failure,
    )

    out = await node(state)

    assert out["analysis_completed_branches"] == 1
    with sqlite3.connect(tmp_path / "tasks.db") as connection:
        rows = connection.execute(
            "SELECT work_item_key, status FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? AND work_item_key IN (?, ?) "
            "ORDER BY work_item_key",
            (
                reservation.umbrella_task_id,
                "protein_design",
                "promoter_design",
            ),
        ).fetchall()
    assert rows == [
        ("promoter_design", "failed"),
        ("protein_design", "failed"),
    ]


def test_all_optional_failures_preserve_profile_and_fail_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No usable optional result settles failed with the profile retained."""
    # pylint: disable=protected-access
    store, reservation = _seed_store(tmp_path)
    monkeypatch.setattr(
        report_module,
        "resolve_tasks_db_path",
        lambda: str(tmp_path / "tasks.db"),
    )
    plan = build_work_item_plan("osa", "Os01g0100100", "Os01g0100100")
    for item in plan:
        store.apply_work_item_transition(
            reservation.umbrella_task_id,
            work_item_key=item.work_item_key,
            status="failed",
        )

    report_harness = DeepGenomeReportMixin()
    setattr(report_harness, "deep_genome_config", DeepGenomeConfig())

    state = {
        "task_id": reservation.umbrella_task_id,
        "work_items": [asdict(item) for item in plan],
        "raw_analyst_data": {
            item.work_item_key: {
                "work_item_key": item.work_item_key,
                "status": "failed",
            }
            for item in plan
        },
    }

    with pytest.raises(
        DeepGenomeWorkflowError, match="no usable analysis result"
    ):
        asyncio.run(report_harness._run_report_synthesizer(cast(Any, state)))

    snapshot = store.get_snapshot(reservation.umbrella_task_id)
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.final_report is None
    assert snapshot.intermediate_report is not None
    assert snapshot.degraded_reason == "12 of 12 optional analyses unavailable"


async def test_partial_fake_backend_publishes_final_report_after_cas(
    tmp_path: Path,
) -> None:
    """A usable optional result permits degraded final publication."""
    store, reservation = _seed_store(tmp_path)
    submission = RemoteSubmission(
        "caller-smep",
        "source-smep",
        "/obs/smep",
    )
    tracking = DeepGenomeTransitionSink(store, reservation.umbrella_task_id)
    await tracking.accept_remote_submission("smep_analysis", submission)
    await tracking.persist_work_item_transition(
        "smep_analysis",
        submission,
        "succeeded",
        "# SMEP",
        None,
    )
    for item in build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"):
        if item.work_item_key != "smep_analysis":
            store.apply_work_item_transition(
                reservation.umbrella_task_id,
                work_item_key=item.work_item_key,
                status="failed",
            )
    current = store.get_snapshot(reservation.umbrella_task_id)
    assert current is not None
    assert current.final_report is None
    final = store.publish_final_report(
        reservation.umbrella_task_id,
        final_report="# final degraded report",
        expected_revision=current.report_revision,
    )
    assert final.status == "succeeded"
    assert final.degraded is True
    assert final.final_report == "# final degraded report"
