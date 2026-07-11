# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""A2A correlation coverage for the shared run registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.run_registry import (
    A2ACorrelation,
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.unit


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
