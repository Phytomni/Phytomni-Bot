# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared local+remote reconciliation for one task row.

Used by ``GetTaskStatus`` and run-registry status. DeepGenome rows reconcile
from the local snapshot because the in-process coordinator owns remote
polling; other rows may probe the remote analysis platform once.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import Any, Literal

from mcp.shared.exceptions import McpError

from ..agents.analyst.agent import task_log, task_status
from ..config.defaults import AnalystConfig
from .deep_genome_store import (
    DeepGenomeStore,
    DeepGenomeTransitionError,
    snapshot_to_public_dict,
)
from .fingerprint_jobs import mark_job_terminal
from .live_tasks import is_live_running
from .task_manager import TaskManager, resolve_tasks_db_path
from .terminal_report import (
    TerminalReportContext,
    is_terminal_report_agent,
    persist_terminal_report,
    synthesize_terminal_report,
)

__all__ = ["reconcile_task", "reconcile_task_log"]

logger = logging.getLogger(__name__)

_NON_TERMINAL_STATUSES = frozenset({"running", "submitted", "pending"})
_RESTART_ORPHAN_REASON = "workflow interrupted by service restart"


def _platform_log_contents(payload: Mapping[str, Any]) -> list[str]:
    """Return ordered ``logs[].content`` strings from one platform payload."""
    logs = payload.get("logs")
    if not isinstance(logs, list):
        return []
    return [
        item["content"]
        for item in logs
        if isinstance(item, dict) and isinstance(item.get("content"), str)
    ]


def _usable_platform_task_log(payload: Mapping[str, Any]) -> bool:
    """Return whether the payload has at least one non-empty log chunk."""
    return any(content for content in _platform_log_contents(payload))


def _explicit_empty_platform_task_log(payload: Mapping[str, Any]) -> bool:
    """Return whether the payload is an explicit empty ``logs`` list."""
    return isinstance(
        payload.get("logs"), list
    ) and not _usable_platform_task_log(payload)


def _row_is_terminal(row: Mapping[str, Any] | None) -> bool:
    """Return whether the local task row has left the in-flight statuses."""
    if row is None:
        return False
    status = str(row.get("status") or "").strip().lower()
    return bool(status) and status not in _NON_TERMINAL_STATUSES


def _project_task_log_text(payload: dict[str, Any]) -> dict[str, Any]:
    """Add ordered public text for the analyst platform's log chunks."""
    contents = _platform_log_contents(payload)
    if not contents:
        return payload
    return {**payload, "text": "".join(contents)}


_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_LIVE_SUCCESS = "SUCCEEDED"
_LIVE_DEAD = frozenset({"FAILED", "CANCELLED"})


def _project_deep_genome_snapshot(
    result: dict[str, Any], snapshot: Any
) -> dict[str, Any]:
    """Merge one public local DeepGenome snapshot into a task result."""
    result["status"] = snapshot.status
    result.update(snapshot_to_public_dict(snapshot))
    if result["status"] in _NON_TERMINAL_STATUSES and result["final_report"]:
        result["status"] = "succeeded"
    elif result["status"] in _SUCCESS_STATUSES and not result["final_report"]:
        result["status"] = "failed"
    return result


def _reconcile_deep_genome_local(
    result: dict[str, Any], *, manager: TaskManager, task_id: str
) -> dict[str, Any]:
    """Reconcile a DeepGenome row from its local snapshot only.

    The coordinator is the sole owner of concrete remote polling. A read
    path may settle an orphaned umbrella after process restart, but it must
    never turn either the umbrella id or a concrete id into a remote probe.
    """
    try:
        store = DeepGenomeStore(manager.db_path)
        snapshot = store.get_snapshot(task_id)
    except sqlite3.Error:
        logger.warning(
            "reconcile: deep_genome local snapshot unavailable for %s",
            task_id,
        )
        return result

    if snapshot is None:
        return result

    if (
        snapshot.status in _NON_TERMINAL_STATUSES
        and not snapshot.final_report
        and not is_live_running(task_id)
    ):
        try:
            snapshot = store.fail_umbrella(
                task_id,
                reason=_RESTART_ORPHAN_REASON,
            )
        except (DeepGenomeTransitionError, sqlite3.Error):
            # A concurrent coordinator/finalizer may have settled the owner;
            # reread the winner instead of manufacturing a local verdict.
            try:
                snapshot = store.get_snapshot(task_id)
            except sqlite3.Error:
                snapshot = None
            if snapshot is None:
                return result
        else:
            logger.warning(
                "reconcile: deep_genome umbrella %s was settled failed "
                "after losing its in-process coordinator",
                task_id,
            )

    if snapshot is None:
        return result
    return _project_deep_genome_snapshot(result, snapshot)


async def _ensure_report_agent_final_report(
    manager: TaskManager,
    result: dict[str, Any],
    *,
    agent: str | None,
    task_id: str,
) -> dict[str, Any]:
    """Persist assembler output when a report agent succeeds without one."""
    if agent is None or not is_terminal_report_agent(agent):
        return result
    if str(result.get("status", "")).upper() != _LIVE_SUCCESS:
        return result
    existing = result.get("final_report")
    if isinstance(existing, str) and existing.strip():
        return result
    assembled = await synthesize_terminal_report(
        TerminalReportContext(
            agent=agent,
            status="succeeded",
            live=[result],
            artifacts=(),
            query=None,
        )
    )
    persist_terminal_report(
        [{**result, "task_id": task_id}],
        assembled,
        task_manager=manager,
    )
    result["final_report"] = assembled.final_report
    return result


def _persist_live_terminal_row(
    manager: TaskManager,
    *,
    task_id: str,
    analysis_id: str,
    output_dir: str,
    remote_status: str,
) -> None:
    """Write a confirmed remote terminal verdict back onto the local row."""
    if remote_status != _LIVE_SUCCESS and remote_status not in _LIVE_DEAD:
        return
    persisted = "succeeded" if remote_status == _LIVE_SUCCESS else "failed"
    try:
        manager.update_task(task_id, persisted, analysis_id, output_dir)
    except (sqlite3.Error, OSError):
        logger.warning(
            "reconcile: failed to persist live status for %s", task_id
        )


def _fingerprint_ei_id(row: Mapping[str, Any], probe_id: str) -> str:
    """Return the remote EI id used as the fingerprint job key."""
    for key in ("source_task_id", "analysis_id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return probe_id


def _mark_fingerprint_from_live(
    db_path: str, ei_task_id: str, remote_status: str
) -> None:
    """Settle a fingerprint job from a confirmed live terminal verdict."""
    mapped_by_live: dict[str, Literal["succeeded", "failed", "cancelled"]] = {
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
    }
    mapped = mapped_by_live.get(remote_status)
    if mapped is None or not ei_task_id:
        return
    try:
        mark_job_terminal(db_path, ei_task_id, mapped)
    except (sqlite3.Error, OSError, ValueError):
        logger.warning(
            "reconcile: failed to mark fingerprint job terminal"
        )


async def reconcile_task(task_id: str) -> dict[str, Any]:
    """Return one task's locally recorded + live-bridged status.

    Performs a single non-blocking ``SELECT`` on the local registry,
    then — when the row is eligible — exactly one live analysis-platform
    ``task_status`` lookup. Never the ``wait_for_completion`` poll loop,
    so the call cannot re-create the C-1 MCP timeout. A failed or
    unreachable live check degrades to the locally recorded status so
    the lookup stays robust.

    Remote probes are skipped for every DeepGenome row. The coordinator
    owns polling for the umbrella and concrete work items; this read path
    only consumes local snapshots and settles process-restart orphans.
    All other rows probe ``source_task_id`` when set, else ``task_id``.

    Args:
        task_id: The task id to look up.

    Returns:
        Dict containing ``task_id`` / ``status`` / ``output_dir`` /
        ``analysis_id`` / ``live_status`` / ``final_report``. ``status``
        is ``"unknown"`` for an unrecorded id and the other fields are
        empty in that case. ``final_report`` carries the assembled
        markdown DeepGenome persists on the row. Analyst-class agents
        that observe live ``SUCCEEDED`` without a stored report receive
        the existing assembler fallback so GetTaskStatus never returns
        an empty successful scientific run. Other agents and
        non-success rows still surface ``None``. The poll formatter
        and the run-aggregate can then display the report without
        re-running the workflow. DeepGenome report/progress fields are
        merged from the local snapshot when the additive tables exist.
        A DeepGenome row still showing a non-terminal status but carrying
        a ``final_report`` (a lost terminal status write) is surfaced as
        ``succeeded``. A DeepGenome row marked succeeded without a
        ``final_report`` is surfaced as ``failed``. A confirmed live
        terminal (``SUCCEEDED`` / ``FAILED`` / ``CANCELLED``) is written
        back onto the local row so a later probe failure cannot revive
        ``submitted``. ``degraded`` /
        ``degraded_reason`` carry the persisted (already-redacted)
        degradation reason or ``None`` so both poll surfaces can flag a
        degraded report.
    """
    manager = TaskManager(resolve_tasks_db_path())
    row = manager.get_task(task_id)
    if row is None:
        return {
            "task_id": task_id,
            "status": "unknown",
            "output_dir": "",
            "analysis_id": "",
            "live_status": None,
            "final_report": None,
            "degraded": False,
            "degraded_reason": None,
        }
    degraded_reason = manager.get_task_degraded(task_id)
    task_agent = manager.get_task_agent(task_id)
    result: dict[str, Any] = {
        "task_id": task_id,
        "status": row["status"],
        "output_dir": row["output_dir"],
        "analysis_id": row["analysis_id"],
        "live_status": None,
        "final_report": manager.get_task_final_report(task_id),
        "degraded": degraded_reason is not None,
        "degraded_reason": degraded_reason,
    }
    if task_agent == "deep_genome":
        return _reconcile_deep_genome_local(
            result, manager=manager, task_id=task_id
        )
    analyst_config = AnalystConfig()
    probe_id = row["source_task_id"] or task_id
    try:
        live = await task_status(
            probe_id,
            analysis_url=analyst_config.ANALYSIS_URL,
            region=analyst_config.ANALYSIS_REGION,
            timeout=analyst_config.TIMEOUT,
            retriable_codes=analyst_config.RETRIABLE_CODES,
            max_retries=analyst_config.MAX_RETRIES,
        )
    except McpError:
        return result
    result["live_status"] = live
    live_status = live.get("status") if isinstance(live, dict) else None
    if not isinstance(live_status, str) or not live_status.strip():
        return result
    cleaned = live_status.strip()
    result["status"] = cleaned
    live_output = live.get("output_dir") if isinstance(live, dict) else None
    if isinstance(live_output, str) and live_output.strip():
        result["output_dir"] = live_output.strip()
    _persist_live_terminal_row(
        manager,
        task_id=task_id,
        analysis_id=str(row["analysis_id"] or ""),
        output_dir=str(result["output_dir"] or ""),
        remote_status=cleaned.upper(),
    )
    _mark_fingerprint_from_live(
        manager.db_path,
        _fingerprint_ei_id(row, probe_id),
        cleaned.upper(),
    )
    return await _ensure_report_agent_final_report(
        manager,
        result,
        agent=task_agent,
        task_id=task_id,
    )


async def reconcile_task_log(task_id: str) -> dict[str, Any] | None:
    """Return cached log or fetch from remote + cache, best-effort.

    Reads ``tasks.task_log`` first. A cache hit requires at least one
    non-empty ``logs[].content`` string — the same join the platform
    operator uses. An early empty object (``{}``) is a miss so a later
    completed job can still be fetched. After a terminal status, an
    explicit empty ``{"logs": []}`` is cached so a job with no log does
    not refetch forever.
    On a miss, calls ``agents.analyst.task_ops.task_log`` with
    ``source_task_id`` when the caller-owned row points at a
    deduplicated remote task, writes a usable or terminal-empty payload
    via ``TaskManager.set_task_log``, and returns it. Both cached and
    fresh platform payloads gain an additive ``text`` projection when
    they contain ordered string values at ``logs[].content``.
    A remote failure (any ``McpError`` from ``task_log``) logs at
    ``warning`` and returns ``None`` — the caller (HTTP route) treats
    ``None`` as "no log available yet" rather than surfacing the
    backend failure to the user, since a transient analyst-platform
    5xx must not break a ``/v1/runs/.../logs`` poll.

    Args:
        task_id: The task id to reconcile.

    Returns:
        The log dict when available (cached or freshly fetched), or
        ``None`` when the task is unknown and the remote is unreachable.
    """
    mgr = TaskManager(resolve_tasks_db_path())
    row = mgr.get_task(task_id)
    cached = mgr.get_task_log(task_id)
    if isinstance(cached, dict) and _usable_platform_task_log(cached):
        return _project_task_log_text(cached)
    if (
        isinstance(cached, dict)
        and _explicit_empty_platform_task_log(cached)
        and _row_is_terminal(row)
    ):
        return _project_task_log_text(cached)
    probe_id = task_id
    if row is not None and row["source_task_id"]:
        probe_id = row["source_task_id"]
    analyst_config = AnalystConfig()
    try:
        payload = await task_log(
            probe_id,
            analysis_url=analyst_config.ANALYSIS_URL,
            region=analyst_config.ANALYSIS_REGION,
            timeout=analyst_config.TIMEOUT,
            retriable_codes=analyst_config.RETRIABLE_CODES,
            max_retries=analyst_config.MAX_RETRIES,
            compute_resource=analyst_config.COMPUTE_RESOURCE,
        )
    except McpError:
        logger.warning(
            "reconcile_task_log: remote fetch failed for %s", task_id
        )
        return None
    if not isinstance(payload, dict):
        return None
    if _usable_platform_task_log(payload):
        mgr.set_task_log(task_id, payload)
    elif _row_is_terminal(row):
        logs = payload.get("logs")
        mgr.set_task_log(
            task_id,
            {"logs": logs if isinstance(logs, list) else []},
        )
    return _project_task_log_text(payload)
