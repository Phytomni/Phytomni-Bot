# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Async task polling for the live e2e suite.

Tools that submit work to the analysis platform (Analyst, DeepGenome,
DigitalDesign, GeneNetwork, InSilicoResearch) return a task handle and
defer execution to a separate backend. Each poll prefers the public
``GetTaskStatus`` MCP tool on the same client that submitted the task
so the lookup runs inside the server process (where outbound runtime
exists). In-process ``reconcile_task`` is only a fallback for offline
helpers that have no MCP client; it cannot see live ``RUNNING`` from
the pytest process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx
from mcp.shared.exceptions import McpError

from mcp_client_phytomni import McpToolResponse, PhytomniMcpClient
from mcp_server_phytomni.contracts.deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
    DEEP_GENOME_REPORT_FIELDS,
    DeepGenomeReportSnapshot,
    sanitize_nonnegative_int,
)
from mcp_server_phytomni.runtime.task_reconcile import reconcile_task

from .client import call_tool, submit_timeout_seconds

_logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "server_tasks.db"
DEFAULT_TIMEOUT_SECONDS = 3600.0
DEFAULT_POLL_INTERVAL_SECONDS = 10.0
TERMINAL_STATUSES = frozenset(
    {"succeeded", "success", "completed", "done", "failed", "error"}
)
SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
RUNNING_STATUSES = frozenset({"running"})
HTTP_TERMINAL_STATUSES = frozenset({"input_required", "succeeded", "failed"})
HTTP_RUNNING_OR_TERMINAL_STATUSES = HTTP_TERMINAL_STATUSES | RUNNING_STATUSES
_HTTP_RUN_DEADLINE_MESSAGE = (
    "HTTP run did not reach a terminal status before the deadline"
)
_INVALID_POLL_TIMEOUT_MESSAGE = (
    "polling timeout must be finite and greater than zero"
)


@dataclass(frozen=True)
class _TaskIdentity:
    """Submission identity and terminal status shared by task snapshots."""

    task_id: str
    status: str
    analysis_id: str
    output_dir: str


@dataclass(frozen=True)
class _TaskProgress:
    """Progress and degradation fields shared by task snapshots."""

    progress: Mapping[str, int | bool | str] = field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str | None = None
    brief_gene_status: str = "unknown"


@dataclass(frozen=True)
class TaskState(_TaskProgress, DeepGenomeReportSnapshot, _TaskIdentity):
    """One sanitized task snapshot used by live acceptance tests.

    Attributes:
        task_id: Task id originally returned by the submission call.
        status: Current task status string.
        analysis_id: Analysis platform identifier (``"unupdated"`` until
            the agent's poller fills it in).
        output_dir: Remote output directory (``"unupdated"`` until the
            agent's poller fills it in).
        intermediate_report: Best report snapshot currently available;
            it may remain present after a degraded terminal failure.
        final_report: Assembled report markdown persisted by
            deep_genome's report node; ``None`` for every other agent
            and until that node runs.
        report_stage: Stable report lifecycle label.
        report_completeness: ``none``, ``partial``, or ``complete``.
        report_revision: Monotonic report revision observed by the poller.
        report_updated_at: Sanitized UTC timestamp, when available.
        progress: Public progress counters and BriefGene status.
        degraded: Whether the terminal/report state is degraded.
        degraded_reason: Fixed public degradation reason, if any.
        brief_gene_status: Required-profile status copied from progress.
        failures: Sanitized optional-work failure descriptors.
        artifacts: Public artifact descriptors, when a run-level surface
            provides them.
        output_dirs: Output directory paths published by a task result.
    """

    failures: tuple[Mapping[str, str], ...] = ()
    artifacts: tuple[Mapping[str, Any], ...] = ()
    output_dirs: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        """Whether the task reached a success terminal state.

        Normalises case so backend verdicts like ``'SUCCEEDED'`` and
        local-DB rows like ``'succeeded'`` both register as success.
        """
        return self.status.lower() in SUCCESS_STATUSES


class TaskPollingTimeoutError(TimeoutError):
    """Raised when an E2E polling helper exceeds its deadline."""


@dataclass(frozen=True)
class HttpRunTerminal:
    """Terminal HTTP run result plus the revisions observed on the way."""

    status: str
    result: dict[str, Any]
    revisions: tuple[int, ...]


@dataclass(frozen=True)
class _HttpPollSpec:
    """Stop set and budgets for one HTTP run poll."""

    stop_statuses: frozenset[str]
    timeout_seconds: float | None = None
    poll_interval_seconds: float = 10.0


async def poll_http_run_to_terminal(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 10.0,
) -> HttpRunTerminal:
    """Poll one long-lived HTTP run and return its terminal result.

    The helper performs only non-blocking ``GET /v1/runs/{id}`` lookups,
    records each distinct report revision, and rejects revision regression.
    It deliberately returns the JSON result mapping rather than a client
    model so the live test can assert the exact run-level envelope.

    Args:
        client: Authenticated HTTP client bound to the live API.
        run_id: Owner-scoped run id returned by a submit endpoint.
        headers: Authentication headers for the status route.
        timeout_seconds: Finite positive monotonic local polling budget.
            Defaults to the environment-aware
            :func:`resolve_timeout_seconds` value.
        poll_interval_seconds: Delay between non-terminal reads.

    Returns:
        Terminal status, result mapping, and distinct report revisions.

    Raises:
        AssertionError: For malformed status responses or a regressing
            report revision.
        TaskPollingTimeoutError: If the local deadline expires.
        ValueError: If the polling budget is not finite and positive.
    """
    return await _poll_http_run(
        client,
        run_id,
        headers,
        _HttpPollSpec(
            stop_statuses=HTTP_TERMINAL_STATUSES,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        ),
    )


async def poll_http_run_to_running_or_terminal(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 10.0,
) -> HttpRunTerminal:
    """Poll one HTTP run until it is running or already terminal.

    Temporary long-job gate: a live ``running`` status is
    ``ACCEPTED_WITH_GAPS``. Terminal statuses keep the original meaning.

    Args:
        client: Authenticated HTTP client bound to the live API.
        run_id: Owner-scoped run id returned by a submit endpoint.
        headers: Authentication headers for the status route.
        timeout_seconds: Finite positive monotonic local polling budget.
        poll_interval_seconds: Delay between non-stop reads.

    Returns:
        Status, result mapping, and distinct report revisions.

    Raises:
        AssertionError: For malformed status responses or a regressing
            report revision.
        TaskPollingTimeoutError: If the local deadline expires.
        ValueError: If the polling budget is not finite and positive.
    """
    return await _poll_http_run(
        client,
        run_id,
        headers,
        _HttpPollSpec(
            stop_statuses=HTTP_RUNNING_OR_TERMINAL_STATUSES,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        ),
    )


async def _poll_http_run(
    client: httpx.AsyncClient,
    run_id: str,
    headers: Mapping[str, str],
    spec: _HttpPollSpec,
) -> HttpRunTerminal:
    """Drive one HTTP run poll with a prepared stop set and budgets."""
    effective_timeout = (
        resolve_timeout_seconds()
        if spec.timeout_seconds is None
        else _validate_timeout_seconds(spec.timeout_seconds)
    )
    deadline = time.monotonic() + effective_timeout
    revisions: list[int] = []
    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            break
        try:
            async with asyncio.timeout(remaining_seconds):
                response = await client.get(
                    f"/v1/runs/{run_id}",
                    headers=dict(headers),
                    timeout=remaining_seconds,
                )
        except (TimeoutError, httpx.TimeoutException):
            raise TaskPollingTimeoutError(_HTTP_RUN_DEADLINE_MESSAGE) from None
        assert (
            response.status_code == 200
        ), f"run status returned HTTP {response.status_code}"
        body = response.json()
        assert isinstance(body, dict), "run status response was not an object"
        status = str(body.get("status", "")).lower()
        result = body.get("result")
        if not isinstance(result, dict):
            result = {}
        revision = result.get("report_revision")
        if isinstance(revision, int) and not isinstance(revision, bool):
            if revisions and revision < revisions[-1]:
                raise AssertionError("run report revision regressed")
            if not revisions or revision != revisions[-1]:
                revisions.append(revision)
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            break
        if status in spec.stop_statuses:
            _logger.info(
                "live run terminal status=%s revisions=%s artifacts=%s",
                status,
                len(revisions),
                (
                    len(result.get("artifacts", []))
                    if isinstance(result.get("artifacts"), list)
                    else 0
                ),
            )
            return HttpRunTerminal(
                status=status,
                result=result,
                revisions=tuple(revisions),
            )
        await asyncio.sleep(min(spec.poll_interval_seconds, remaining_seconds))
    raise TaskPollingTimeoutError(_HTTP_RUN_DEADLINE_MESSAGE)


def _validate_timeout_seconds(value: str | float) -> float:
    """Return one finite timeout or raise the stable validation error."""
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError(_INVALID_POLL_TIMEOUT_MESSAGE) from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(_INVALID_POLL_TIMEOUT_MESSAGE)
    return timeout


def resolve_timeout_seconds() -> float:
    """Return the polling timeout, honoring environment overrides.

    Override with ``PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS`` to extend the
    default one-hour wait without editing per-test source. A configured
    value must be finite and greater than zero.

    Returns:
        Timeout in seconds.

    Raises:
        ValueError: With a stable message when the configured value is empty,
            malformed, non-finite, zero, or negative.
    """
    raw = os.environ.get("PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS")
    if raw is not None:
        return _validate_timeout_seconds(raw)
    return _validate_timeout_seconds(DEFAULT_TIMEOUT_SECONDS)


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


def _status_in(status: str, allowed: frozenset[str]) -> bool:
    """Return True when ``status`` matches one allowed value, ignoring case."""
    return status.lower() in allowed


async def _status_payload_from_mcp(
    client: PhytomniMcpClient | object,
    task_id: str,
) -> Mapping[str, Any] | None:
    """Return a GetTaskStatus payload from the server that submitted."""
    response = await call_tool(
        cast(PhytomniMcpClient, client),
        "GetTaskStatus",
        {"task_id": task_id},
    )
    if not isinstance(response, McpToolResponse):
        raw = getattr(response, "raw_payload", None)
        formatted = getattr(response, "formatted", None)
    else:
        raw = response.raw_payload
        formatted = response.formatted
    if isinstance(raw, Mapping) and raw.get("status"):
        return raw
    metadata = getattr(formatted, "metadata", None)
    if isinstance(metadata, Mapping) and metadata.get("status"):
        return metadata
    return None


async def _reconciled_task_state(
    task_id: str,
    resolved_db: Path,
    *,
    client: PhytomniMcpClient | object | None = None,
) -> TaskState | None:
    """Return a fresh ``TaskState`` combining MCP status + local DB.

    Prefer ``GetTaskStatus`` on the submitting MCP client so live
    ``RUNNING`` is read inside the server outbound runtime. Fall back
    to in-process ``reconcile_task`` only when no client is available.
    """
    if client is not None:
        try:
            payload = await _status_payload_from_mcp(client, task_id)
        except (
            McpError,
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            _logger.debug(
                "GetTaskStatus(%s) failed: %s; falling back",
                task_id,
                exc,
            )
            payload = None
        if payload is not None:
            status = payload.get("status", "unknown")
            if status != "unknown":
                return task_state_from_mapping(payload, task_id=task_id)
    try:
        reconciled = await reconcile_task(task_id)
    except (
        McpError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
    ) as exc:
        _logger.debug(
            "reconcile_task(%s) failed: %s; falling back to local DB",
            task_id,
            exc,
        )
        return _read_task_state(resolved_db, task_id)
    status = reconciled.get("status", "unknown")
    if status == "unknown":
        return _read_task_state(resolved_db, task_id)
    return task_state_from_mapping(reconciled, task_id=task_id)


def task_state_from_mapping(
    payload: Mapping[str, Any], *, task_id: str
) -> TaskState:
    """Project one public payload into the sanitized E2E state model."""
    progress = _mapping_progress(payload.get("progress"))
    brief_gene_status = (
        _nonblank_text(payload.get("brief_gene_status"))
        or _nonblank_text(progress.get("brief_gene_status"))
        or "unknown"
    )
    output_dir = str(payload.get("output_dir", "") or "")
    output_dirs = _string_tuple(payload.get("output_dirs"))
    if not output_dirs and output_dir not in {"", "unupdated"}:
        output_dirs = (output_dir,)
    return TaskState(
        task_id=task_id,
        status=str(payload.get("status", "unknown")),
        analysis_id=str(payload.get("analysis_id", "") or ""),
        output_dir=output_dir,
        intermediate_report=_optional_text(payload.get("intermediate_report")),
        final_report=_optional_text(payload.get("final_report")),
        report_stage=_choice(
            payload.get("report_stage"),
            {"waiting_for_brief_gene", "intermediate", "final"},
            "waiting_for_brief_gene",
        ),
        report_completeness=_choice(
            payload.get("report_completeness"),
            {"none", "partial", "complete"},
            "none",
        ),
        report_revision=sanitize_nonnegative_int(
            payload.get("report_revision")
        ),
        report_updated_at=_optional_text(payload.get("report_updated_at")),
        progress=progress,
        degraded=bool(payload.get("degraded", False)),
        degraded_reason=_optional_text(payload.get("degraded_reason")),
        brief_gene_status=brief_gene_status,
        failures=_failure_tuple(payload.get("failures")),
        artifacts=_artifact_tuple(payload.get("artifacts")),
        output_dirs=output_dirs,
    )


def _optional_text(value: Any) -> str | None:
    """Return nonblank text without retaining arbitrary payload objects."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _nonblank_text(value: Any) -> str | None:
    """Return normalized nonblank text for one public status field."""
    text = _optional_text(value)
    return text.strip().lower() if text is not None else None


def _choice(value: Any, allowed: set[str], default: str) -> str:
    """Return one value from a closed public vocabulary."""
    return value if isinstance(value, str) and value in allowed else default


def _mapping_progress(value: Any) -> dict[str, int | bool | str]:
    """Copy only scalar progress values from a reconciled mapping."""
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item
        for key in DEEP_GENOME_PROGRESS_FIELDS
        if (item := _progress_value(key, value.get(key))) is not None
    }


def _progress_value(key: str, value: Any) -> int | bool | str | None:
    """Validate one E2E progress value without retaining private keys."""
    if key == "planning_complete":
        return value if isinstance(value, bool) else None
    if key == "brief_gene_status":
        return (
            value.strip().lower()
            if isinstance(value, str) and value.strip()
            else None
        )
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        else None
    )


def _failure_tuple(value: Any) -> tuple[Mapping[str, str], ...]:
    """Retain only string fields in public failure descriptors."""
    if not isinstance(value, (list, tuple)):
        return ()
    projected: list[Mapping[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        fields = {
            str(key): field
            for key, field in item.items()
            if isinstance(key, str) and isinstance(field, str)
        }
        if fields:
            projected.append(fields)
    return tuple(projected)


def _artifact_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Project artifact descriptors without preserving arbitrary objects."""
    if not isinstance(value, (list, tuple)):
        return ()
    projected: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        output_dir = item.get("output_dir")
        paths = item.get("paths")
        descriptor: dict[str, Any] = {}
        if isinstance(output_dir, str) and output_dir.strip():
            descriptor["output_dir"] = output_dir
        descriptor["paths"] = _string_tuple(paths)
        if descriptor["paths"] or "output_dir" in descriptor:
            projected.append(descriptor)
    return tuple(projected)


def _string_tuple(value: Any) -> tuple[str, ...]:
    """Return nonblank string members from a public sequence."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


async def poll_until_done(
    task_id: str,
    *,
    db_path: Path | None = None,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    client: PhytomniMcpClient | object | None = None,
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
    return await _poll_until(
        task_id,
        TERMINAL_STATUSES,
        db_path=db_path,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        client=client,
    )


async def poll_until_remote_running_or_done(
    task_id: str,
    *,
    db_path: Path | None = None,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    client: PhytomniMcpClient | object | None = None,
) -> TaskState:
    """Poll until the task is remotely running or already terminal."""
    return await _poll_until(
        task_id,
        TERMINAL_STATUSES | RUNNING_STATUSES,
        db_path=db_path,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        client=client,
    )


async def _poll_until(
    task_id: str,
    stop_statuses: frozenset[str],
    *,
    db_path: Path | None,
    timeout_seconds: float | None,
    poll_interval_seconds: float,
    client: PhytomniMcpClient | object | None = None,
) -> TaskState:
    """Poll one task until its reconciled status matches ``stop_statuses``."""
    deadline = time.monotonic() + (
        timeout_seconds
        if timeout_seconds is not None
        else resolve_timeout_seconds()
    )
    resolved_db = db_path or resolve_db_path()
    last_state: TaskState | None = None

    while time.monotonic() < deadline:
        state = await _reconciled_task_state(
            task_id, resolved_db, client=client
        )
        if state is not None:
            last_state = state
            if _status_in(state.status, stop_statuses):
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


def _extract_from_mapping(payload: Any) -> str | None:
    """Return a task_id from a dict-shaped payload if present."""
    if not isinstance(payload, dict):
        return None
    for key in _TASK_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_from_text(text: str) -> str | None:
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
    state = await poll_until_done(task_id, client=client)
    if not state.succeeded:
        raise AssertionError(
            f"{tool_name} task {task_id} ended with status "
            f"{state.status!r}; expected a success terminal state."
        )
    return state


async def submit_and_poll_to_remote_running(
    client: PhytomniMcpClient,
    tool_name: str,
    payload: Any,
) -> TaskState:
    """Submit an async tool and accept remote ``RUNNING`` as with-gaps.

    Temporary gate for 1h-48h analysis-platform jobs. A live
    ``RUNNING`` verdict is enough; a success terminal still wins. A
    failed/error terminal fails the test. Local ``submitted`` without
    a live running verdict is not accepted.

    Args:
        client: Session-scoped MCP client.
        tool_name: Public MCP tool name (e.g. ``"AnalystAgent"``).
        payload: JSON-schema-compatible payload for ``tool_name``.

    Returns:
        ``TaskState`` once the reconciled status is running or a
        success terminal.

    Raises:
        AssertionError: If the task ends in a failed/error terminal.
        TaskPollingTimeoutError: If the polling deadline elapses.
        RuntimeError: If no task identifier could be extracted.
    """
    response = await call_tool(
        client,
        tool_name,
        payload,
        timeout_seconds=submit_timeout_seconds(),
    )
    task_id = extract_task_id(response)
    state = await poll_until_remote_running_or_done(task_id, client=client)
    if state.succeeded or _status_in(state.status, RUNNING_STATUSES):
        if not state.succeeded:
            _logger.warning(
                "ACCEPTED_WITH_GAPS: %s task %s is remote RUNNING; "
                "terminal report and artifacts are deferred",
                tool_name,
                task_id,
            )
        return state
    raise AssertionError(
        f"{tool_name} task {task_id} ended with status "
        f"{state.status!r}; expected remote RUNNING or success."
    )


def _read_task_state(
    db_path: Path,
    task_id: str,
) -> TaskState | None:
    """Return one task row by id, or ``None`` if the row is missing."""
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        selected = ["task_id", "status", "analysis_id", "output_dir"]
        optional = tuple(
            field
            for field in DEEP_GENOME_REPORT_FIELDS
            if field not in {"progress", "degraded", "failures"}
        ) + ("progress_json",)
        selected.extend(name for name in optional if name in columns)
        row = conn.execute(
            f"SELECT {', '.join(selected)} FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if row is None:
        return None
    payload: dict[str, Any] = dict(row)
    progress_json = payload.pop("progress_json", None)
    if isinstance(progress_json, str):
        try:
            payload["progress"] = json.loads(progress_json)
        except (TypeError, ValueError):
            payload["progress"] = {}
    if payload.get("degraded_reason"):
        payload["degraded"] = True
    return task_state_from_mapping(payload, task_id=str(payload["task_id"]))
