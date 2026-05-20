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
