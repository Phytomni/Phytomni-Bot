# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for submit-handler unified run+task registry recording.

Pin the chokepoint: ``_records_submission(agent)`` forwards a submit
handler's result unchanged while persisting both the child task row
and an owning ``runs`` row (``origin="remote"``), the recorder is
best-effort on malformed results, and all five submit-style handlers
are decorated with their canonical agent slug.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from mcp_server_phytomni.mcp.handlers import (
    _record_submitted_task,
    _records_submission,
    handle_analyst_agent,
    handle_deep_genome_agent,
    handle_digital_design_agent,
    handle_gene_network_agent,
    handle_in_silico_research_agent,
)
from mcp_server_phytomni.runtime.request_context import current_run_id
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.server


async def test_decorator_records_task_run_and_passes_result_through(
    tasks_db_path: str,
) -> None:
    """Verify the wrapper logs the run + task yet returns result as-is.

    Args:
        tasks_db_path: Temp registry DB fixture.
    """
    submitted = {"task_id": "T-1", "output_dir": "/obs/run"}

    async def fake_handler(args: Any) -> Any:
        """Return a canned submission result.

        Args:
            args: Ignored tool-argument model.

        Returns:
            The canned submission dict.
        """
        _ = args
        return submitted

    wrapped = _records_submission("analyst")(fake_handler)

    result = await wrapped(object())

    assert result is submitted
    assert TaskManager(tasks_db_path).get_task("T-1") == {
        "task_id": "T-1",
        "status": "submitted",
        "analysis_id": "",
        "output_dir": "/obs/run",
    }
    with sqlite3.connect(tasks_db_path) as conn:
        row = conn.execute(
            "SELECT run_id, user_id, agent, origin FROM tasks "
            "WHERE task_id = ?",
            ("T-1",),
        ).fetchone()
    assert row is not None
    run_id, user_id, agent, origin = row
    assert run_id
    assert user_id == "anonymous"
    assert agent == "analyst"
    assert origin == "remote"
    listing = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(listing) == 1
    assert listing[0].spec.run_id == run_id
    assert listing[0].spec.agent == "analyst"
    assert listing[0].spec.origin == "remote"
    assert listing[0].status == "running"
    assert listing[0].task_ids == ("T-1",)
    # Submit-time write seeds the envelope shape so a client polling
    # /v1/runs/{id} during running sees the same key set as terminal.
    assert listing[0].result == {
        "task_results": [
            {
                "task_id": "T-1",
                "status": "submitted",
                "output_dir": "/obs/run",
            }
        ],
        "live_status": [
            {
                "task_id": "T-1",
                "status": "submitted",
                "output_dir": "/obs/run",
            }
        ],
        "artifacts": [],
    }


def test_record_submitted_task_ignores_malformed_results(
    tasks_db_path: str,
) -> None:
    """Verify non-dict / missing-id results record nothing, no raise.

    Args:
        tasks_db_path: Temp registry DB fixture.
    """
    _record_submitted_task("not a dict", agent="analyst")
    _record_submitted_task({}, agent="analyst")
    _record_submitted_task({"task_id": ""}, agent="analyst")

    assert TaskManager(tasks_db_path).get_task("") is None
    assert not RunRegistry(tasks_db_path).list_runs(owner="anonymous")


def test_all_submit_handlers_are_decorated() -> None:
    """Verify every submit-style handler carries the recorder wrapper.

    ``functools.wraps`` inside the decorator factory keeps the original
    handler reachable via ``__wrapped__`` even when the factory itself
    is parameterised with a static agent slug.
    """
    for handler in (
        handle_analyst_agent,
        handle_deep_genome_agent,
        handle_digital_design_agent,
        handle_gene_network_agent,
        handle_in_silico_research_agent,
    ):
        assert hasattr(handler, "__wrapped__"), handler.__name__


def test_record_binds_run_id_contextvar(tasks_db_path: str) -> None:
    """The chokepoint binds ``current_run_id`` to the freshly minted id.

    Pin the audit-1.2 contract: after a successful chokepoint write,
    the HTTP layer can recover the run id directly from the
    contextvar without re-reading a formatter-specific metadata key.
    """
    assert current_run_id() is None
    _record_submitted_task(
        {"task_id": "T-bind", "output_dir": "/obs/run"},
        agent="analyst",
    )
    bound = current_run_id()
    assert bound is not None
    assert bound.endswith("analyst") or "analyst" in bound
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.run_id == bound


def test_record_handles_research_task_ids_map(tasks_db_path: str) -> None:
    """``research`` returns ``task_ids`` as a name -> id dict.

    Verify every value in the map becomes a child ``tasks`` row under
    one shared ``run_id``; the top-level ``output_dir`` is shared by
    each child since the research wrapper does not nest one per task.
    """
    _record_submitted_task(
        {
            "task_ids": {"goal-a": "T-RA", "goal-b": "T-RB"},
            "output_dir": "/obs/research",
        },
        agent="research",
    )
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.agent == "research"
    assert set(runs[0].task_ids) == {"T-RA", "T-RB"}
    with sqlite3.connect(tasks_db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, output_dir, run_id FROM tasks "
            "WHERE run_id = ? ORDER BY task_id",
            (runs[0].spec.run_id,),
        ).fetchall()
    assert [row[0] for row in rows] == ["T-RA", "T-RB"]
    assert {row[1] for row in rows} == {"/obs/research"}


def test_record_handles_network_nested_task(tasks_db_path: str) -> None:
    """``network`` returns one task nested under ``network_task``."""
    _record_submitted_task(
        {
            "network_task": {
                "task_id": "T-NET",
                "output_dir": "/obs/network",
            }
        },
        agent="network",
    )
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.agent == "network"
    assert runs[0].task_ids == ("T-NET",)


def test_record_handles_design_task_result_list(
    tasks_db_path: str,
) -> None:
    """``design`` returns a ``design_task_result`` list of submissions.

    The agent accumulates one AnalystAgent submission dict per design
    kind (protein / promoter / terminator) via LangGraph's
    ``operator.add`` reducer. The chokepoint writes one child row per
    present item under a single shared run.
    """
    _record_submitted_task(
        {
            "design_task_result": [
                {
                    "task_id": "T-DP",
                    "output_dir": "/obs/protein",
                },
                {
                    "task_id": "T-DM",
                    "output_dir": "/obs/promoter",
                },
            ],
        },
        agent="design",
    )
    runs = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs) == 1
    assert runs[0].spec.agent == "design"
    assert set(runs[0].task_ids) == {"T-DP", "T-DM"}
    with sqlite3.connect(tasks_db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, output_dir FROM tasks "
            "WHERE run_id = ? ORDER BY task_id",
            (runs[0].spec.run_id,),
        ).fetchall()
    output_dirs = dict(rows)
    assert output_dirs == {
        "T-DP": "/obs/protein",
        "T-DM": "/obs/promoter",
    }


def test_record_skips_bind_when_task_row_write_fails(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-success record must not leak a contextvar run_id.

    When ``RunRegistry.create_run`` succeeds but ``TaskManager.record``
    raises mid-loop, the recorder swallows the SQLite error under its
    best-effort contract. The contextvar must stay ``None`` so the
    HTTP layer surfaces ``(None, [])`` instead of a half-populated run
    whose ``task_ids`` column is empty.
    """
    _ = tasks_db_path

    def boom(self: TaskManager, submission: Any) -> None:
        """Raise on every record call to simulate a partial write failure."""
        _ = self, submission
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(TaskManager, "record", boom)
    assert current_run_id() is None
    _record_submitted_task(
        {"task_id": "T-fail", "output_dir": "/obs/run"},
        agent="analyst",
    )
    assert current_run_id() is None


def test_record_short_circuits_on_dedup_hit_passthrough(
    tasks_db_path: str,
) -> None:
    """A dedup-hit return must leave the prior task row + run untouched.

    Reproduces the orphan-run scenario the dedup sentinel prevents:
    a first submit records ``T1`` under freshly-minted ``R1``; a
    second handler return for the same ``task_id`` carrying
    ``dedup_hit=True`` would, without the short-circuit, mint a
    sibling ``R2`` and ``INSERT OR REPLACE`` the tasks row so its
    ``run_id`` flips from ``R1`` to ``R2``. The pin: after the second
    call the tasks row's ``run_id`` is still ``R1``, the runs table
    still has exactly one row, and ``current_run_id()`` still points
    at ``R1`` (the chokepoint never minted a new id).
    """
    _record_submitted_task(
        {"task_id": "T-dedup", "output_dir": "/obs/run"},
        agent="analyst",
    )
    first_run = current_run_id()
    assert first_run is not None
    runs_before = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs_before) == 1

    _record_submitted_task(
        {
            "task_id": "T-dedup",
            "output_dir": "/obs/run",
            "dedup_hit": True,
        },
        agent="analyst",
    )

    with sqlite3.connect(tasks_db_path) as conn:
        row = conn.execute(
            "SELECT run_id FROM tasks WHERE task_id = ?",
            ("T-dedup",),
        ).fetchone()
    assert row is not None
    assert row[0] == first_run

    runs_after = RunRegistry(tasks_db_path).list_runs(owner="anonymous")
    assert len(runs_after) == 1
    assert runs_after[0].spec.run_id == first_run
    assert runs_after[0].task_ids == ("T-dedup",)
    # The chokepoint never minted a new id on the short-circuit, so the
    # contextvar still points at the prior caller's run.
    assert current_run_id() == first_run


def test_record_dedup_hit_without_prior_does_not_bind_or_write(
    tasks_db_path: str,
) -> None:
    """A dedup_hit short-circuit must not create state out of thin air.

    Even if the chokepoint is called with ``dedup_hit=True`` before
    any prior submission has been recorded (e.g., a stale registry
    that has been purged), it must not mint a run id, must not write
    any rows, and must not bind the contextvar.
    """
    assert current_run_id() is None
    _record_submitted_task(
        {
            "task_id": "T-ghost",
            "output_dir": "/obs/run",
            "dedup_hit": True,
        },
        agent="analyst",
    )
    assert current_run_id() is None
    assert TaskManager(tasks_db_path).get_task("T-ghost") is None
    assert not RunRegistry(tasks_db_path).list_runs(owner="anonymous")
