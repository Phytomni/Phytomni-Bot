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


def test_record_handles_design_three_nested_tasks(
    tasks_db_path: str,
) -> None:
    """``design`` returns up to three nested task submissions.

    Each ``*_design_task`` block carries its own ``task_id`` and
    ``output_dir``, so the chokepoint writes one child row per
    present block under a single shared run.
    """
    _record_submitted_task(
        {
            "protein_design_task": {
                "task_id": "T-DP",
                "output_dir": "/obs/protein",
            },
            "promoter_design_task": {
                "task_id": "T-DM",
                "output_dir": "/obs/promoter",
            },
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
