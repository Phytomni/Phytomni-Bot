# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""A2A correlation coverage for the shared run registry."""

from __future__ import annotations

from pathlib import Path

import pytest
from a2a.types import TaskState

from mcp_server_phytomni.api.a2a.executor import task_from_run_record
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.run_registry import (
    A2ACorrelation,
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.unit


def test_reserve_run_preserves_a2a_correlation(tmp_path: Path) -> None:
    """Reservations retain safe A2A identifiers without raw request data."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    correlation = A2ACorrelation(
        task_id="a2a-task-reserved",
        context_id="a2a-context-reserved",
        message_id="a2a-message-reserved",
    )
    registry.reserve_run(
        RunSpec("run-a2a-reserved", "alice", "research", "remote"),
        request_info=RunRequestInfo(
            request_id="req-a2a-reserved",
            a2a=correlation,
        ),
        result=empty_execution_projection(),
    )

    record = registry.get_run("run-a2a-reserved", owner="alice")
    assert record is not None
    assert record.a2a == correlation


def test_a2a_correlation_round_trips_and_is_owner_scoped(
    tmp_path: Path,
) -> None:
    """A2A ids attach to one run and lookup cannot cross owners."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-a2a", "alice", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result={"answer": "x"}),
        a2a=A2ACorrelation(
            task_id="a2a-task-1",
            context_id="a2a-context-1",
            message_id="a2a-message-1",
        ),
    )

    record = registry.get_run("run-a2a", owner="alice")
    assert record is not None
    assert record.a2a == A2ACorrelation(
        task_id="a2a-task-1",
        context_id="a2a-context-1",
        message_id="a2a-message-1",
    )
    assert registry.get_run_by_a2a_task("a2a-task-1", owner="alice") == record
    assert registry.get_run_by_a2a_task("a2a-task-1", owner="bob") is None


def test_update_a2a_correlation_preserves_terminal_payload(
    tmp_path: Path,
) -> None:
    """Retrofitting protocol ids does not replace the run result."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-a2a-update", "alice", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result={"answer": "done"}),
    )

    assert registry.update_a2a_correlation(
        "run-a2a-update",
        owner="alice",
        correlation=A2ACorrelation(task_id="task-2", context_id="ctx-2"),
    )
    record = registry.get_run("run-a2a-update", owner="alice")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result == {"answer": "done"}
    assert record.a2a == A2ACorrelation(task_id="task-2", context_id="ctx-2")


def test_task_projection_bounds_history_and_hides_raw_payload(
    tmp_path: Path,
) -> None:
    """GetTask projection returns bounded history and safe artifacts."""
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-task", "alice", "chat", "local"),
        outcome=RunOutcome(
            status="succeeded",
            result={
                "formatted": {
                    "answer": "done",
                    "metadata": {"safe": True},
                },
                "raw": {"secret": "must not cross"},
            },
        ),
        request_info=RunRequestInfo(
            request_json=(
                '{"message":{"messageId":"m1","contextId":"c1",'
                '"role":"ROLE_USER","parts":[{"text":"hello"}]}}'
            ),
        ),
        a2a=A2ACorrelation(task_id="task-1", context_id="c1"),
    )
    record = registry.get_run("run-task", owner="alice")
    assert record is not None

    task = task_from_run_record(record, history_length=1)

    assert task.id == "task-1"
    assert task.context_id == "c1"
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert len(task.history) == 1
    assert task.artifacts[0].parts[0].text == "done"
    assert "secret" not in str(task)


def test_task_projection_honors_configured_history_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GetTask can disable history projection with the operator cap."""
    monkeypatch.setenv("PHYTOMNI_A2A_MAX_HISTORY_MESSAGES", "0")
    registry = RunRegistry(str(tmp_path / "tasks.db"))
    registry.create_run(
        RunSpec("run-history-cap", "alice", "chat", "local"),
        outcome=RunOutcome(status="succeeded"),
        request_info=RunRequestInfo(
            request_json=(
                '{"message":{"messageId":"m1","contextId":"c1",'
                '"role":"ROLE_USER","parts":[{"text":"hello"}]}}'
            ),
        ),
        a2a=A2ACorrelation(task_id="task-history-cap", context_id="c1"),
    )
    record = registry.get_run("run-history-cap", owner="alice")
    assert record is not None

    task = task_from_run_record(record, history_length=100)

    assert not task.history
