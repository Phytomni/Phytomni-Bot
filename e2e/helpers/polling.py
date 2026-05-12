# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Async task polling for the live e2e suite.

Tools that submit work to the analysis platform (Analyst, DeepGenome,
DigitalDesign, GeneNetwork, InSilicoResearch) return a task handle and
defer execution to a separate backend. The MCP surface in
``src/mcp_server_phytomni/mcp/schemas.py`` does not expose a public
task-status tool, so this helper drops one layer: it inspects the local
``server_tasks.db`` (managed by
``mcp_server_phytomni/runtime/task_manager.py``) and polls until the
task reaches a terminal status. See ``e2e/README.md`` for the rationale
behind not routing this through the MCP client.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = "server_tasks.db"
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 10.0
TERMINAL_STATUSES = frozenset(
    {"succeeded", "success", "completed", "done", "failed", "error"}
)
SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})


@dataclass(frozen=True)
class TaskState:
    """One row from the local ``tasks`` table.

    Attributes:
        task_id: Task id originally returned by the submission call.
        status: Current task status string.
        analysis_id: Analysis platform identifier (``"unupdated"`` until
            the agent's poller fills it in).
        output_dir: Remote output directory (``"unupdated"`` until the
            agent's poller fills it in).
    """

    task_id: str
    status: str
    analysis_id: str
    output_dir: str

    @property
    def succeeded(self) -> bool:
        """Whether the task reached a success terminal state."""
        return self.status in SUCCESS_STATUSES


class TaskPollingTimeoutError(TimeoutError):
    """Raised when ``poll_until_done`` exceeds its deadline."""


def resolve_timeout_seconds() -> float:
    """Return the polling timeout, honoring environment overrides.

    Override with ``PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS`` to extend the
    default ten-minute wait without editing per-test source.

    Returns:
        Timeout in seconds.
    """
    raw = os.environ.get("PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS")
    if raw:
        return float(raw)
    return DEFAULT_TIMEOUT_SECONDS


def resolve_db_path() -> Path:
    """Return the path to ``server_tasks.db`` for polling.

    Override with ``PHYTOMNI_E2E_TASKS_DB`` when the MCP server runs
    from a non-default working directory.

    Returns:
        Absolute path to the SQLite tasks database.
    """
    raw = os.environ.get("PHYTOMNI_E2E_TASKS_DB")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd() / DEFAULT_DB_PATH


async def poll_until_done(
    task_id: str,
    *,
    db_path: Optional[Path] = None,
    timeout_seconds: Optional[float] = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> TaskState:
    """Poll the local task DB until ``task_id`` reaches terminal status.

    Args:
        task_id: Task id originally returned by an async tool call.
        db_path: SQLite database path override. Defaults to
            ``resolve_db_path()``.
        timeout_seconds: Total poll budget. Defaults to
            ``resolve_timeout_seconds()``.
        poll_interval_seconds: Delay between DB reads.

    Returns:
        Final ``TaskState`` once the task reaches a terminal status.

    Raises:
        TaskPollingTimeoutError: If the deadline elapses before the
            task reaches a terminal state.
    """
    deadline = time.monotonic() + (
        timeout_seconds
        if timeout_seconds is not None
        else resolve_timeout_seconds()
    )
    resolved_db = db_path or resolve_db_path()
    last_state: Optional[TaskState] = None

    while time.monotonic() < deadline:
        state = _read_task_state(resolved_db, task_id)
        if state is not None:
            last_state = state
            if state.status in TERMINAL_STATUSES:
                return state
        await asyncio.sleep(poll_interval_seconds)

    raise TaskPollingTimeoutError(
        f"Task {task_id} did not reach a terminal status before the "
        f"deadline (last seen state: {last_state})."
    )


def _read_task_state(
    db_path: Path,
    task_id: str,
) -> Optional[TaskState]:
    """Return one task row by id, or ``None`` if the row is missing."""
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT task_id, status, analysis_id, output_dir "
            "FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if row is None:
        return None
    return TaskState(
        task_id=row[0],
        status=row[1],
        analysis_id=row[2],
        output_dir=row[3],
    )
