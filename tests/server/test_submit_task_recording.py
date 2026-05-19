# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for submit-handler local task-registry recording.

Pin the Phase 9.2 chokepoint: the ``_records_submission`` decorator
forwards a submit handler's result unchanged while persisting its
task_id/output_dir to the local registry, the recorder is best-effort
on malformed results, and all five submit-style handlers are decorated.
"""

from __future__ import annotations

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
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.server


async def test_decorator_records_and_passes_result_through(
    tasks_db_path: str,
) -> None:
    """Verify the wrapper logs the task yet returns the result as-is.

    Args:
        tasks_db_path: Temp registry DB fixture.

    Returns:
        None after the pass-through and recorded-row assertions pass.
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

    wrapped = _records_submission(fake_handler)

    result = await wrapped(object())

    assert result is submitted
    assert TaskManager(tasks_db_path).get_task("T-1") == {
        "task_id": "T-1",
        "status": "submitted",
        "analysis_id": "",
        "output_dir": "/obs/run",
    }


def test_record_submitted_task_ignores_malformed_results(
    tasks_db_path: str,
) -> None:
    """Verify non-dict / missing-id results record nothing, no raise.

    Args:
        tasks_db_path: Temp registry DB fixture.

    Returns:
        None after the no-row assertions pass.
    """
    _record_submitted_task("not a dict")
    _record_submitted_task({})
    _record_submitted_task({"task_id": ""})

    assert TaskManager(tasks_db_path).get_task("") is None


def test_all_submit_handlers_are_decorated() -> None:
    """Verify every submit-style handler carries the recorder wrapper.

    Returns:
        None after asserting each handler exposes ``__wrapped__``
        (set by ``functools.wraps`` in ``_records_submission``).
    """
    for handler in (
        handle_analyst_agent,
        handle_deep_genome_agent,
        handle_digital_design_agent,
        handle_gene_network_agent,
        handle_in_silico_research_agent,
    ):
        assert hasattr(handler, "__wrapped__"), handler.__name__
