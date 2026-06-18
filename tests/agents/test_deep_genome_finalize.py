# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""deep_genome's terminal finalization write is best-effort and logged.

``_finalize_workflow`` is the background-task done-callback that stamps
the umbrella row's terminal status. A registry write failure there must
not crash the callback (nothing is left to run) and is logged rather
than silently suppressed, so an operator can see the lost poll-surfacing;
the submit-path ``degraded_tracking`` flag does not extend to it.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.deep_genome import agent as agent_module
from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeAgents

pytestmark = pytest.mark.agent


def _boom_manager(_db_path: str) -> Any:
    """Return a TaskManager stand-in whose terminal write always fails."""

    def _update_task(*_args: Any) -> bool:
        raise sqlite3.Error("database is locked")

    return SimpleNamespace(update_task=_update_task)


def _succeeded_task() -> Any:
    """Return a fake completed task reporting success (no exception)."""
    return SimpleNamespace(cancelled=lambda: False, exception=lambda: None)


class _FinalizeProbe(DeepGenomeAgents):
    """Expose ``_finalize_workflow`` via a public in-hierarchy proxy.

    Mirrors ``_FollowUpProbe`` in ``test_deep_genome_report``: the
    protected call rides ``self`` inside the subclass so it never trips
    ``protected-access``.
    """

    def run_finalize(
        self, task: Any, *, umbrella_id: str, output_dir: str
    ) -> None:
        """Public proxy for ``_finalize_workflow``."""
        self._finalize_workflow(
            task, umbrella_id=umbrella_id, output_dir=output_dir
        )


def test_finalize_workflow_logs_and_swallows_terminal_write_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal-status registry write failure is logged, not raised."""
    monkeypatch.setattr(
        agent_module, "resolve_tasks_db_path", lambda: "ignored.db"
    )
    monkeypatch.setattr(agent_module, "TaskManager", _boom_manager)
    warned: list[Any] = []
    monkeypatch.setattr(
        agent_module.logger,
        "warning",
        lambda *args, **_kwargs: warned.append(args),
    )
    agent = _FinalizeProbe(knowledge_agent=None, analyst_agent=None)

    # Must return normally even though the registry write explodes.
    agent.run_finalize(
        _succeeded_task(), umbrella_id="dg-fin", output_dir="/obs/o"
    )

    assert warned, "a terminal-write failure must be logged"
    assert "dg-fin" in warned[0]
