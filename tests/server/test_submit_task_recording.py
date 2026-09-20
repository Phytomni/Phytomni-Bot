# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Remote submission recording stays inside the canonical Runtime run."""

from __future__ import annotations

from typing import Any

import pytest
from tests.support.http_execution_fixtures import reserve_running_execution
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.mcp.handlers import (
    handle_analyst_agent,
    handle_deep_genome_agent,
    handle_digital_design_agent,
    handle_gene_network_agent,
    handle_in_silico_research_agent,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.request_context import (
    current_accepted_task_ids,
    current_recorder_degraded,
    current_run_id,
    request_context,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import (
    extract_task_submissions,
    record_submitted_task,
    records_submission,
)
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.server


@pytest.mark.parametrize(
    ("agent", "result", "expected"),
    [
        (
            "analyst",
            {"task_id": "analyst-1", "output_dir": "/safe/a"},
            (("analyst-1", "/safe/a", None, None),),
        ),
        (
            "deep_genome",
            {"task_id": "genome-1", "output_dir": "/safe/g"},
            (("genome-1", "/safe/g", None, None),),
        ),
        (
            "research",
            {"task_ids": ["research-1", "research-2"], "output_dir": "/r"},
            (
                ("research-1", "/r", None, None),
                ("research-2", "/r", None, None),
            ),
        ),
        (
            "network",
            {"network_task": {"task_id": "network-1", "output_dir": "/n"}},
            (("network-1", "/n", None, None),),
        ),
        (
            "design",
            {
                "design_task_result": [
                    {"task_id": "design-1", "output_dir": "/d1"},
                    {"task_id": "design-2", "output_dir": "/d2"},
                ]
            },
            (
                ("design-1", "/d1", None, None),
                ("design-2", "/d2", None, None),
            ),
        ),
    ],
)
def test_extractors_cover_every_remote_agent(
    agent: str,
    result: dict[str, Any],
    expected: tuple[tuple[str, str, str | None, str | None], ...],
) -> None:
    """Verify extractors cover every remote agent."""
    assert extract_task_submissions(result, agent) == expected


def test_all_submit_handlers_are_decorated() -> None:
    """Verify every submit-style handler carries the recorder wrapper.
    ``functools.wraps`` inside the decorator factory keeps the original
    handler reachable via ``__wrapped__`` even when the factory itself is
    parameterised with a static agent slug."""
    for handler in (
        handle_analyst_agent,
        handle_deep_genome_agent,
        handle_digital_design_agent,
        handle_gene_network_agent,
        handle_in_silico_research_agent,
    ):
        assert hasattr(handler, "__wrapped__"), handler.__name__


async def test_decorator_preserves_result_but_requires_runtime(
    tasks_db_path: str,
) -> None:
    """Verify decorator preserves result but requires runtime."""
    submitted = {"task_id": "T-1", "output_dir": "/obs/run"}

    async def fake_handler(_args: Any) -> Any:
        return submitted

    with request_context("alice", "request-no-runtime"):
        result = await records_submission("analyst")(fake_handler)(object())
        assert result is submitted
        assert current_recorder_degraded() is True
        assert current_accepted_task_ids() == ("T-1",)
    assert TaskManager(tasks_db_path).get_task("T-1") is None
    assert not RunRegistry(tasks_db_path).list_runs(owner="alice")


@pytest.mark.parametrize(
    ("agent", "result", "expected_ids", "expected_dirs"),
    [
        (
            "analyst",
            {"task_id": "analyst-1", "output_dir": "/safe/a"},
            ("analyst-1",),
            ["/safe/a"],
        ),
        (
            "research",
            {"task_ids": ["research-1", "research-2"], "output_dir": "/r"},
            ("research-1", "research-2"),
            ["/r", "/r"],
        ),
        (
            "network",
            {"network_task": {"task_id": "network-1", "output_dir": "/n"}},
            ("network-1",),
            ["/n"],
        ),
        (
            "design",
            {
                "design_task_result": [
                    {"task_id": "design-1", "output_dir": "/d1"},
                    {"task_id": "design-2", "output_dir": "/d2"},
                ]
            },
            ("design-1", "design-2"),
            ["/d1", "/d2"],
        ),
    ],
)
def test_recorder_attaches_children_to_one_reserved_runtime_run(
    tasks_db_path: str,
    agent: str,
    result: dict[str, Any],
    expected_ids: tuple[str, ...],
    expected_dirs: list[str],
) -> None:
    """Verify recorder attaches children to one reserved runtime run."""
    run_id = f"run-{agent}"
    reserve_running_execution(
        tasks_db_path,
        run_id=run_id,
        execution_id=f"turn-{agent}",
        owner="alice",
        agent=agent,
    )
    with request_context("alice", f"request-{agent}", run_id):
        record_submitted_task(result, agent=agent)
        assert current_run_id() == run_id
        assert current_accepted_task_ids() == expected_ids
        assert current_recorder_degraded() is False

    record = RunRegistry(tasks_db_path).get_run(run_id, owner="alice")
    assert record is not None
    assert record.request_info.execution_id == f"turn-{agent}"
    assert record.task_ids == expected_ids
    assert record.result is not None
    assert record.result["execution"]["output_dirs"] == expected_dirs
    with closed_sqlite_connection(tasks_db_path) as connection:
        run_count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE user_id = ?", ("alice",)
        ).fetchone()[0]
    assert run_count == 1


def test_agent_or_owner_mismatch_degrades_without_second_run(
    tasks_db_path: str,
) -> None:
    """Verify agent or owner mismatch degrades without second run."""
    reserve_running_execution(
        tasks_db_path,
        run_id="run-one",
        execution_id="turn-one",
        owner="alice",
        agent="analyst",
    )
    with request_context("alice", "request-one", "run-one"):
        record_submitted_task(
            {
                "design_task_result": [
                    {"task_id": "wrong-agent", "output_dir": "/d"}
                ]
            },
            agent="design",
        )
        assert current_recorder_degraded() is True
    with request_context("bob", "request-two", "run-one"):
        record_submitted_task(
            {"task_id": "wrong-owner", "output_dir": "/a"},
            agent="analyst",
        )
        assert current_recorder_degraded() is True
    assert len(RunRegistry(tasks_db_path).list_runs(owner="alice")) == 1
    assert not RunRegistry(tasks_db_path).list_runs(owner="bob")


def test_malformed_and_dedup_results_never_create_state(
    tasks_db_path: str,
) -> None:
    """Verify malformed and dedup results never create state."""
    record_submitted_task("not a dict", agent="analyst")
    record_submitted_task({}, agent="analyst")
    record_submitted_task(
        {"task_id": "ghost", "output_dir": "/x", "dedup_hit": True},
        agent="analyst",
    )
    assert TaskManager(tasks_db_path).get_task("ghost") is None
    assert not RunRegistry(tasks_db_path).list_runs(owner="anonymous")


def test_submission_projection_keeps_bounded_warnings(
    tasks_db_path: str,
) -> None:
    """Verify submission projection keeps bounded warnings."""
    reserve_running_execution(
        tasks_db_path,
        run_id="run-warning",
        execution_id="turn-warning",
        owner="alice",
        agent="research",
    )
    with request_context("alice", "request-warning", "run-warning"):
        record_submitted_task(
            {
                "task_ids": ["warning-1"],
                "output_dir": "/r",
                "submission_warnings": [
                    {
                        "code": "partial_submission",
                        "retryable": False,
                        "rejected_count": 1,
                        "secret": "drop-me",
                    }
                ],
            },
            agent="research",
        )
    record = RunRegistry(tasks_db_path).get_run("run-warning", owner="alice")
    assert record is not None and record.result is not None
    expected = empty_execution_projection(result_archive_required=True)
    expected["execution"]["tasks"] = [
        {
            "id": "warning-1",
            "accepted": True,
            "status": "submitted",
            "kind": "research",
            "error_code": None,
        }
    ]
    expected["execution"]["output_dirs"] = ["/r"]
    expected["execution"]["warnings"] = [
        {
            "code": "partial_submission",
            "retryable": False,
            "rejected_count": 1,
        }
    ]
    expected["execution"]["tracking"] = {"degraded": True}
    assert record.result == expected
