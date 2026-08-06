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
from typing import Any

from mcp.shared.exceptions import McpError

from ..agents.analyst.agent import task_log, task_status
from ..config.defaults import AnalystConfig
from .deep_genome_store import (
    DeepGenomeStore,
    DeepGenomeTransitionError,
    snapshot_to_public_dict,
)
from .live_tasks import is_live_running
from .task_manager import TaskManager, resolve_tasks_db_path

__all__ = ["reconcile_task", "reconcile_task_log"]

logger = logging.getLogger(__name__)

_NON_TERMINAL_STATUSES = frozenset({"running", "submitted", "pending"})
_RESTART_ORPHAN_REASON = "workflow interrupted by service restart"


def _project_task_log_text(payload: dict[str, Any]) -> dict[str, Any]:
    """Add ordered public text for the analyst platform's log chunks."""
    logs = payload.get("logs")
    if not isinstance(logs, list):
        return payload
    contents = [
        item["content"]
        for item in logs
        if isinstance(item, dict) and isinstance(item.get("content"), str)
    ]
    if not contents:
        return payload
    return {**payload, "text": "".join(contents)}


def _project_deep_genome_snapshot(
    result: dict[str, Any], snapshot: Any
) -> dict[str, Any]:
    """Merge one public local DeepGenome snapshot into a task result."""
    result["status"] = snapshot.status
    result.update(snapshot_to_public_dict(snapshot))
    if result["status"] in _NON_TERMINAL_STATUSES and result["final_report"]:
        result["status"] = "succeeded"
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
        markdown DeepGenome persists on the row (``None`` for every
        other agent and for rows with no report yet), letting the poll
        formatter and the run-aggregate surface the report without
        re-running the workflow. DeepGenome report/progress fields are
        merged from the local snapshot when the additive tables exist.
        A DeepGenome row still showing a non-terminal status but carrying
        a ``final_report`` (a lost terminal status write) is surfaced as
        ``succeeded``. ``degraded`` /
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
    if live_status:
        result["status"] = live_status
    return result


async def reconcile_task_log(task_id: str) -> dict[str, Any] | None:
    """Return cached log or fetch from remote + cache, best-effort.

    Reads ``tasks.task_log`` first. On a miss, calls
    ``agents.analyst.task_ops.task_log`` with ``source_task_id`` when the
    caller-owned row points at a deduplicated remote task, writes the raw
    response via ``TaskManager.set_task_log``, and returns it. Both cached
    and fresh platform payloads gain an additive ``text`` projection when
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
    cached = mgr.get_task_log(task_id)
    if cached is not None:
        return _project_task_log_text(cached)
    row = mgr.get_task(task_id)
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
    mgr.set_task_log(task_id, payload)
    return _project_task_log_text(payload)
