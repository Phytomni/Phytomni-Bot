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

from typing import Any, Dict

from mcp.shared.exceptions import McpError

from ..agents.analyst.agent import task_status
from ..config.defaults import AnalystConfig
from .task_manager import TaskManager, resolve_tasks_db_path

__all__ = ["reconcile_task"]


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
