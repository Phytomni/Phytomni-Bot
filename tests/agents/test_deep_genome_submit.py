# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Submit-style behavior tests for ``DeepGenomeAgents.arun``.

Pin the B.3 contract: ``arun`` mints an umbrella ``task_id``
synchronously, spawns the LangGraph workflow on the running loop,
and returns the submit envelope. The background coroutine must
eventually stamp a terminal umbrella-row status even when the
workflow raises.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.deep_genome import agent as agent_module
from mcp_server_phytomni.agents.deep_genome.agent import (
    DeepGenomeAgents,
    DeepGenomeSubmissionError,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.runtime.deep_genome_store import DeepGenomeStore
from mcp_server_phytomni.runtime.request_context import (
    bind_pre_recorded_task_id,
    bind_run_id,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec
from mcp_server_phytomni.runtime.submit_recorder import record_submitted_task
from mcp_server_phytomni.runtime.task_manager import (
    Submission,
    TaskManager,
)
from tests.support.sqlite import closed_sqlite_connection

pytestmark = pytest.mark.agent


class _FakeApp:
    """Stub LangGraph app whose ``ainvoke`` is fully controlled by the test."""

    def __init__(self, result: Any = None, raises: Exception | None = None):
        """Capture the desired ainvoke outcome.

        Args:
            result: Value to return from ``ainvoke``.
            raises: Exception to raise from ``ainvoke`` instead of returning.
        """
        self._result = result
        self._raises = raises
        self.invocations: list[dict[str, Any]] = []

    async def ainvoke(self, initial_state: Any, config: Any = None) -> Any:
        """Mimic LangGraph ``ainvoke`` with a controlled outcome."""
        _ = config
        self.invocations.append(initial_state)
        if self._raises is not None:
            raise self._raises
        return self._result


def _build_agent(app: _FakeApp, tmp_path: Path) -> DeepGenomeAgents:
    """Construct a DeepGenomeAgents with the fake graph wired in.

    Args:
        app: Stub app placed on ``self.app`` to skip the real graph
            compilation cost.
        tmp_path: Pytest temp dir used as the umbrella ``output_dir``
            root through ``deep_genome_config.DEEPGENOME_OUT``.

    Returns:
        A ready-to-call agent instance with the fake graph installed.
    """
    agent = DeepGenomeAgents.__new__(DeepGenomeAgents)
    # ``self.app`` is annotated as the LangGraph ``CompiledStateGraph``
    # subscripted with DeepGenomeState; ``_FakeApp`` only mimics the
    # ``ainvoke`` shape the background coroutine actually calls, so
    # widen to ``Any`` at the assignment to keep pyright honest about
    # the runtime substitution.
    agent.app = cast(Any, app)
    config = DeepGenomeConfig()
    setattr(config, "DEEPGENOME_OUT", str(tmp_path))
    agent.deep_genome_config = config
    return agent


def _patch_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Redirect the agent's task registry writes at the temp SQLite file."""
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.deep_genome.agent.resolve_tasks_db_path",
        lambda: db_path,
    )
    return db_path


async def _drain_background_tasks() -> None:
    """Yield control until every pending background task completes.

    The arun seam uses fire-and-forget ``asyncio.create_task``; without
    explicit awaits these tasks never get to run on the event loop.
    """
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def test_arun_returns_immediately_with_submit_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``arun`` returns a submit envelope synchronously.

    The submit envelope must carry a populated ``task_id`` (so the
    chokepoint can persist the umbrella row) and an ``output_dir`` that
    falls under the configured ``DEEPGENOME_OUT`` root. The background
    coroutine continues afterwards; the test waits for it so the row
    can be inspected at terminal status.
    """
    _patch_db(monkeypatch, tmp_path)

    class _FixedIdFactory:
        """Return stable identities so the raw submit payload is exact."""

        def new_id(self, kind: str, *_parts: str) -> str:
            """Return the fixed ID for the requested submission identity."""
            return {
                "run": "run-deep_genome-submit",
                "task": "task-deep_genome-submit",
            }[kind]

    monkeypatch.setattr(agent_module, "IdFactory", _FixedIdFactory)
    fake_app = _FakeApp(result={"final_report": "ok"})
    agent = _build_agent(fake_app, tmp_path)

    envelope = await agent.arun(species_code="osa", gene_id="Os01g0177400")

    assert envelope == {
        "task_id": "task-deep_genome-submit",
        "output_dir": f"{tmp_path}/task-deep_genome-submit",
        "compute_resource": "deep-genome",
    }
    assert "run_id" not in envelope
    await _drain_background_tasks()


async def test_arun_reserves_before_binding_and_launching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durable reservation precedes every coordinator launch side effect."""
    _patch_db(monkeypatch, tmp_path)
    fake_app = _FakeApp(result={"final_report": "ok"})
    agent = _build_agent(fake_app, tmp_path)
    events: list[str] = []

    original_reserve = DeepGenomeStore.reserve_run
    original_bind = agent_module.bind_run_id
    original_create_task = agent_module.asyncio.create_task
    original_register = agent_module.register_live_task

    def reserve(*args: Any, **kwargs: Any) -> Any:
        result = original_reserve(*args, **kwargs)
        events.append("reserve_commit")
        return result

    def bind(run_id: str | None) -> Any:
        events.append("bind_run")
        return original_bind(run_id)

    def create_task(coroutine: Any) -> asyncio.Task[Any]:
        events.append("create_task")
        return original_create_task(coroutine)

    def register(task_id: str, task: asyncio.Task[Any]) -> None:
        events.append("register_live")
        original_register(task_id, task)

    monkeypatch.setattr(DeepGenomeStore, "reserve_run", reserve)
    monkeypatch.setattr(agent_module, "bind_run_id", bind)
    monkeypatch.setattr(agent_module.asyncio, "create_task", create_task)
    monkeypatch.setattr(agent_module, "register_live_task", register)

    await agent.arun(
        species_code="osa",
        gene_id="Os01g0177400",
        user_id="alice",
    )

    assert events == [
        "reserve_commit",
        "bind_run",
        "create_task",
        "register_live",
    ]
    await _drain_background_tasks()


async def test_create_task_failure_is_compensated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A coordinator launch failure settles the reservation.

    No task id is returned to the caller.
    """
    db_path = _patch_db(monkeypatch, tmp_path)
    fake_app = _FakeApp(result={"final_report": "unreachable"})
    agent = _build_agent(fake_app, tmp_path)

    def raising_create_task(coroutine: Any) -> asyncio.Task[Any]:
        """Reject local scheduling before any graph node can run."""
        coroutine.close()
        raise RuntimeError("event loop launch failed")

    monkeypatch.setattr(
        agent_module.asyncio, "create_task", raising_create_task
    )

    with pytest.raises(
        DeepGenomeSubmissionError,
        match="submission tracking failed",
    ):
        await agent.arun(
            species_code="osa",
            gene_id="Os01g0100100",
            user_id="alice",
        )

    with closed_sqlite_connection(db_path) as conn:
        run = conn.execute(
            "SELECT status, error FROM runs",
        ).fetchone()
        task = conn.execute(
            "SELECT status, degraded_reason FROM tasks",
        ).fetchone()
        section_count = conn.execute(
            "SELECT COUNT(*) FROM deep_genome_sections",
        ).fetchone()[0]

    assert run == ("failed", "local coordinator failed to start")
    assert task == ("failed", "local coordinator failed to start")
    assert section_count == 0
    assert not fake_app.invocations


def test_pre_recorded_deep_genome_submission_does_not_mint_second_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic recorder recognizes the reserved DeepGenome row."""
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.submit_recorder.resolve_tasks_db_path",
        lambda: db_path,
    )
    registry = RunRegistry(db_path)
    registry.create_run(RunSpec("run-1", "anonymous", "deep_genome", "remote"))

    bind_run_id("run-1")
    bind_pre_recorded_task_id("task-1")
    record_submitted_task(
        {"task_id": "task-1", "output_dir": "/tmp/x"},
        agent="deep_genome",
    )

    assert [
        record.spec.run_id for record in registry.list_runs(owner="anonymous")
    ] == ["run-1"]


async def test_arun_background_writes_succeeded_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Background workflow completion stamps ``status="succeeded"``.

    The MCP dispatch chokepoint (``_record_submitted_task``) writes
    the umbrella ``tasks`` row from ``arun``'s submit envelope before
    the background coroutine eventually flips ``status``. The test
    mimics that prerequisite write so the background's
    ``update_task`` lands on an existing row.
    """
    db_path = _patch_db(monkeypatch, tmp_path)
    fake_app = _FakeApp(result={"final_report": "ok"})
    agent = _build_agent(fake_app, tmp_path)

    envelope = await agent.arun(species_code="osa", gene_id="Os01g0177400")
    TaskManager(db_path).record(
        Submission(
            task_id=envelope["task_id"],
            status="submitted",
            output_dir=envelope["output_dir"],
        )
    )
    await _drain_background_tasks()

    with closed_sqlite_connection(db_path) as conn:
        row = conn.execute(
            "SELECT status, output_dir FROM tasks WHERE task_id = ?",
            (envelope["task_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "succeeded"
    assert row[1] == envelope["output_dir"]


async def test_arun_background_writes_failed_on_workflow_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LangGraph exception MUST mark the umbrella row ``"failed"``.

    Without this terminal write a polling client would hang forever on
    a dead background; the chokepoint's submit row alone never reaches
    a terminal status. The bare ``"failed"`` matches the live-status
    bridge's lowercase contract used by the live-status reader.
    """
    db_path = _patch_db(monkeypatch, tmp_path)
    fake_app = _FakeApp(raises=RuntimeError("boom"))
    agent = _build_agent(fake_app, tmp_path)

    envelope = await agent.arun(species_code="osa", gene_id="Os01g0177400")
    TaskManager(db_path).record(
        Submission(
            task_id=envelope["task_id"],
            status="submitted",
            output_dir=envelope["output_dir"],
        )
    )
    await _drain_background_tasks()

    with closed_sqlite_connection(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE task_id = ?",
            (envelope["task_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "failed"


async def test_succeeded_workflow_keeps_status_when_db_write_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SQLite hiccup on the success path must not flip status to "failed".

    Pins AF-002 from the post-B.3 audit. Earlier the success-side
    ``update_task`` sat inside the same try as ``ainvoke_graph``, so
    a SQLite write exception there got caught by the outer except
    and rewrote the umbrella row to ``"failed"`` — a successful
    workflow misclassified as failed. The new try/except/else +
    ``contextlib.suppress`` shape decides the status before the
    terminal write, so an update failure leaves the row at its
    prior status (``"submitted"`` from the chokepoint write) rather
    than fabricating a misleading ``"failed"``.
    """
    db_path = _patch_db(monkeypatch, tmp_path)
    fake_app = _FakeApp(result={"final_report": "ok"})
    agent = _build_agent(fake_app, tmp_path)
    envelope = await agent.arun(species_code="osa", gene_id="Os01g0177400")
    TaskManager(db_path).record(
        Submission(
            task_id=envelope["task_id"],
            status="submitted",
            output_dir=envelope["output_dir"],
        )
    )

    def boom(*args: Any, **kwargs: Any) -> None:
        """Simulate a registry write failing under contention."""
        _ = (args, kwargs)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(TaskManager, "update_task", boom)
    await _drain_background_tasks()

    with closed_sqlite_connection(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE task_id = ?",
            (envelope["task_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "submitted"
