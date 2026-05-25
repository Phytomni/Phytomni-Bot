# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared local+remote reconciliation for one task row.

The MCP ``GetTaskStatus`` tool and the upcoming run-registry status
endpoint both need the same non-blocking "read the local ``tasks`` row
then perform exactly one remote ``task_status`` lookup" semantics.
Factoring it here keeps the two consumers byte-equivalent and removes
the only point at which their poll logic could drift.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from mcp.shared.exceptions import McpError

from ..agents.analyst.agent import task_log, task_status
from ..config.defaults import AnalystConfig
from .task_manager import TaskManager, resolve_tasks_db_path

__all__ = ["reconcile_task", "reconcile_task_log"]

logger = logging.getLogger(__name__)


async def reconcile_task(task_id: str) -> Dict[str, Any]:
    """Return one task's locally recorded + live-bridged status.

    Performs a single non-blocking ``SELECT`` on the local registry,
    then exactly one live analysis-platform ``task_status`` lookup —
    never the ``wait_for_completion`` poll loop, so the call cannot
    re-create the C-1 MCP timeout. A failed or unreachable live check
    degrades to the locally recorded status so the lookup stays robust.

    Args:
        task_id: The task id to look up.

    Returns:
        Dict containing ``task_id`` / ``status`` / ``output_dir`` /
        ``analysis_id`` / ``live_status``. ``status`` is ``"unknown"``
        for an unrecorded id and the other fields are empty in that
        case.
    """
    row = TaskManager(resolve_tasks_db_path()).get_task(task_id)
    if row is None:
        return {
            "task_id": task_id,
            "status": "unknown",
            "output_dir": "",
            "analysis_id": "",
            "live_status": None,
        }
    analyst_config = AnalystConfig()
    result: Dict[str, Any] = {
        "task_id": task_id,
        "status": row["status"],
        "output_dir": row["output_dir"],
        "analysis_id": row["analysis_id"],
        "live_status": None,
    }
    try:
        live = await task_status(
            task_id,
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


async def reconcile_task_log(task_id: str) -> Optional[Dict[str, Any]]:
    """Return cached log or fetch from remote + cache, best-effort.

    Reads ``tasks.task_log`` first; on hit, returns the cached dict
    immediately. On miss, calls ``agents.analyst.task_ops.task_log``
    against the remote analysis platform, writes the response into the
    local column via ``TaskManager.set_task_log``, and returns it.
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
        return cached
    analyst_config = AnalystConfig()
    try:
        payload = await task_log(
            task_id,
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
    return payload
