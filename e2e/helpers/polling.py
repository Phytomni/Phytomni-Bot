# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Async task polling for the live e2e suite.

Tools that submit work to the analysis platform (Analyst, DeepGenome,
DigitalDesign, GeneNetwork, InSilicoResearch) return a task handle and
defer execution to a separate backend. The MCP surface in
``src/mcp_server_phytomni/mcp/schemas.py`` does not expose a public
task-status tool; this helper drops one layer and combines two
sources on every iteration so remote-submit agents (which never
update the local row themselves) still reach terminal:

- the local ``server_tasks.db`` row (managed by
  ``mcp_server_phytomni/runtime/task_manager.py``), inspected
  through :func:`_read_task_state`;
- the live backend reconcile bridge
  (``mcp_server_phytomni/runtime/task_reconcile.reconcile_task``)
  which issues a single non-blocking ``task_status`` call and
  prefers the live verdict when reachable.

See ``e2e/README.md`` for the rationale behind not routing this
through the MCP client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mcp_client_phytomni import McpToolResponse, PhytomniMcpClient
from mcp_server_phytomni.runtime.task_reconcile import reconcile_task

from .client import call_tool, submit_timeout_seconds

_logger = logging.getLogger(__name__)

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
        final_report: Assembled report markdown persisted by
            deep_genome's report node; ``None`` for every other agent
            and until that node runs.
    """

    task_id: str
    status: str
    analysis_id: str
    output_dir: str
    final_report: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """Whether the task reached a success terminal state.

        Normalises case so backend verdicts like ``'SUCCEEDED'`` and
        local-DB rows like ``'succeeded'`` both register as success.
        """
        return self.status.lower() in SUCCESS_STATUSES


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


def _state_is_terminal(state: TaskState) -> bool:
    """Return True if ``state.status`` falls in :data:`TERMINAL_STATUSES`.

    Backend ``task_status`` calls historically return upper-cased
    verdicts (``'FAILED'`` / ``'SUCCEEDED'``); the local DB rows are
    lower-cased. The comparison normalises so case drift from either
    side cannot mask a terminal state.
    """
    return state.status.lower() in TERMINAL_STATUSES


async def _reconciled_task_state(
    task_id: str,
    resolved_db: Path,
) -> Optional[TaskState]:
    """Return a fresh ``TaskState`` combining local DB + live backend.

    Calls :func:`reconcile_task` which itself issues one local
    ``SELECT`` plus one live backend ``task_status`` lookup; falls
    through to the local-only read if reconcile raises (network
    down, backend 5xx, auth misconfigured). Returns ``None`` when
    even the local row is missing so the caller keeps polling.
    """
    try:
        reconciled = await reconcile_task(task_id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _logger.debug(
            "reconcile_task(%s) failed: %s; falling back to local DB",
            task_id,
            exc,
        )
        return _read_task_state(resolved_db, task_id)
    status = reconciled.get("status", "unknown")
    if status == "unknown":
        return _read_task_state(resolved_db, task_id)
    report = reconciled.get("final_report")
    return TaskState(
        task_id=task_id,
        status=str(status),
        analysis_id=str(reconciled.get("analysis_id", "") or ""),
        output_dir=str(reconciled.get("output_dir", "") or ""),
        final_report=report if isinstance(report, str) else None,
    )


async def poll_until_done(
    task_id: str,
    *,
    db_path: Optional[Path] = None,
    timeout_seconds: Optional[float] = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> TaskState:
    """Poll local DB + live backend until ``task_id`` reaches terminal.

    Each iteration calls
    :func:`mcp_server_phytomni.runtime.task_reconcile.reconcile_task`
    which combines a local DB read with a single live backend
    ``task_status`` lookup. This avoids the silent-failure mode where
    remote-submit agents (network / design / evolution / environment
    / research) never update the local row themselves, so a passive
    local-only poll would forever see ``status='submitted'`` and
    time out even after backend reached terminal. The reconcile call
    degrades to the local-only read when the backend is unreachable.

    Args:
        task_id: Task id originally returned by an async tool call.
        db_path: SQLite database path override. Defaults to
            ``resolve_db_path()``.
        timeout_seconds: Total poll budget. Defaults to
            ``resolve_timeout_seconds()``.
        poll_interval_seconds: Delay between iterations.

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
        state = await _reconciled_task_state(task_id, resolved_db)
        if state is not None:
            last_state = state
            if _state_is_terminal(state):
                return state
        await asyncio.sleep(poll_interval_seconds)

    raise TaskPollingTimeoutError(
        f"Task {task_id} did not reach a terminal status before the "
        f"deadline (last seen state: {last_state})."
    )


_TASK_ID_KEYS = ("task_id", "taskId", "id", "submission_id")
_TASK_ID_PATTERN = re.compile(
    r"\btask[_\- ]?id\s*[:=]\s*([0-9a-fA-F-]{8,})",
    re.IGNORECASE,
)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def extract_task_id(response: McpToolResponse) -> str:
    """Return the submission task_id reported by an async tool.

    Tries the raw JSON payload first (common ``task_id`` keys), then
    falls back to scanning the formatted answer text for either a
    ``task_id: <uuid>`` pattern or a bare UUID.

    Args:
        response: ``McpToolResponse`` returned by ``client.call_tool``.

    Returns:
        The task identifier as a string.

    Raises:
        RuntimeError: If no task identifier can be located.
    """
    payload = response.raw_payload
    candidate = _extract_from_mapping(payload)
    if candidate is not None:
        return candidate
    candidate = _extract_from_text(response.formatted.answer)
    if candidate is not None:
        return candidate
    raise RuntimeError(
        "Could not extract task_id from response: "
        f"raw_payload={payload!r}; answer={response.formatted.answer!r}"
    )


def _extract_from_mapping(payload: Any) -> Optional[str]:
    """Return a task_id from a dict-shaped payload if present."""
    if not isinstance(payload, dict):
        return None
    for key in _TASK_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_from_text(text: str) -> Optional[str]:
    """Return a task_id parsed from formatted answer text if present."""
    explicit = _TASK_ID_PATTERN.search(text)
    if explicit:
        return explicit.group(1)
    uuid_match = _UUID_PATTERN.search(text)
    if uuid_match:
        return uuid_match.group(0)
    return None


async def submit_and_poll_to_success(
    client: PhytomniMcpClient,
    tool_name: str,
    payload: Any,
) -> TaskState:
    """Submit an async tool and poll its task to a success terminal state.

    Timeouts honor the environment overrides documented in
    ``e2e/README.md``: ``PHYTOMNI_E2E_SUBMIT_TIMEOUT_SECONDS`` for the
    submit call and ``PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS`` for the
    polling deadline.

    Args:
        client: Session-scoped MCP client.
        tool_name: Public MCP tool name (e.g. ``"AnalystAgent"``).
        payload: JSON-schema-compatible payload for ``tool_name``.

    Returns:
        Final ``TaskState`` once the task reaches a success terminal
        state.

    Raises:
        AssertionError: If the task ends in a non-success terminal
            status, so the caller's test fails with a clear message.
        TaskPollingTimeoutError: If the polling deadline elapses.
        RuntimeError: If no task identifier could be extracted from
            the submission response.
    """
    response = await call_tool(
        client,
        tool_name,
        payload,
        timeout_seconds=submit_timeout_seconds(),
    )
    task_id = extract_task_id(response)
    state = await poll_until_done(task_id)
    if not state.succeeded:
        raise AssertionError(
            f"{tool_name} task {task_id} ended with status "
            f"{state.status!r}; expected a success terminal state."
        )
    return state


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
