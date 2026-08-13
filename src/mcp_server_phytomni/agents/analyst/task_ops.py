# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Analyst task-platform wrappers reused by AnalystAgent and operators.

Exports ``task_status`` / ``task_log`` / ``task_delete`` and the polling
helper ``wait_for_completion``. Each wrapper is a thin compatibility seam
over the analysis platform HTTP API: kwargs are merged against
``ANALYST_CONFIG`` defaults, retriable codes are normalized to a fresh
list, and failures raise ``McpError`` with the platform's error string.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...auth.iam import get_token
from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    request_response_with_retries,
)
from ...common.relay_client import RelayRequestOptions, current_relay_client
from ...config.relay_mode import relay_mode_enabled
from ...runtime.outbound import (
    OutboundPoolName,
    current_outbound_http_client,
)
from .defaults import ANALYST_CONFIG


def _common_request_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Resolve the shared analysis-URL / region / timeout / retry kwargs.

    Every analyst task wrapper accepts the same five overrides and
    normalizes ``retriable_codes`` to a list copy. Centralizing the
    extraction keeps the four wrappers below from duplicating the
    boilerplate (pylint R0801) and gives a single seam to update when
    the override surface changes.
    """
    retriable_codes = kwargs.get("retriable_codes")
    if retriable_codes is None:
        retriable_codes = list(ANALYST_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    return {
        "analysis_url": kwargs.get(
            "analysis_url", ANALYST_CONFIG.ANALYSIS_URL
        ),
        "region": kwargs.get("region", ANALYST_CONFIG.ANALYSIS_REGION),
        "timeout": kwargs.get("timeout", ANALYST_CONFIG.TIMEOUT),
        "retriable_codes": retriable_codes,
        "max_retries": kwargs.get("max_retries", ANALYST_CONFIG.MAX_RETRIES),
    }


async def task_status(
    task_id: str,
    **kwargs: Any,
) -> dict:
    """Check the execution status of a specified task.

    Args:
        task_id: The unique identifier of the task to check.
        **kwargs: ``analysis_url`` / ``region`` / ``timeout`` /
            ``retriable_codes`` / ``max_retries`` overrides.

    Returns:
        The platform's task-status payload as a dict.

    Raises:
        McpError: If checking the task status fails after all retries.
    """
    if relay_mode_enabled():
        return await current_relay_client().get_json(
            f"analysis/{task_id}",
            pool=OutboundPoolName.ANALYSIS_STATUS,
            options=RelayRequestOptions(
                message=f"Check task {task_id} status failed"
            ),
        )
    req = _common_request_kwargs(kwargs)
    client = current_outbound_http_client(OutboundPoolName.ANALYSIS_STATUS)
    response = await request_response_with_retries(
        client,
        JsonPostRequest(
            url=f"{req['analysis_url']}/{task_id}",
            method="GET",
            headers={
                "Content-Type": "application/json",
                "X-Auth-Token": await get_token(
                    request_timeout=req["timeout"], region=req["region"]
                ),
            },
        ),
        JsonPostRetry(
            timeout=req["timeout"],
            max_retries=req["max_retries"],
            retriable_codes=req["retriable_codes"],
            message=f"Check task {task_id} status failed",
        ),
    )
    if response is not None and response.status_code == 200:
        return response.json()

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to check task status after all retries",
        )
    )


async def probe_live_status(task_id: str) -> str | None:
    """Return the upper-cased remote status, or None on probe failure.

    A single non-blocking ``task_status`` lookup wired to
    ``ANALYST_CONFIG``, used by the dedup reuse decision. Lives here
    because ``task_ops`` owns analysis-platform access and is a safe
    leaf import for both analyst entry points (the top-level wrapper and
    the dispatch seam) without forming the
    ``runtime.task_dedup`` import cycle. Returns ``None`` when the
    platform is unreachable so callers fail safe (resubmit) rather than
    reuse a task whose live state is unknown.

    Args:
        task_id: Analysis-platform task id to probe.

    Returns:
        Upper-cased status string (e.g. ``"SUCCEEDED"``), or ``None`` on
        an ``McpError`` or a missing status field.
    """
    try:
        live = await task_status(
            task_id,
            analysis_url=ANALYST_CONFIG.ANALYSIS_URL,
            region=ANALYST_CONFIG.ANALYSIS_REGION,
            timeout=ANALYST_CONFIG.TIMEOUT,
            retriable_codes=ANALYST_CONFIG.RETRIABLE_CODES,
            max_retries=ANALYST_CONFIG.MAX_RETRIES,
        )
    except McpError:
        return None
    if isinstance(live, dict):
        return str(live.get("status") or "").upper() or None
    return None


async def task_log(
    task_id: str,
    **kwargs: Any,
) -> dict:
    """Retrieve the execution log for a specified task.

    Args:
        task_id: The unique identifier of the task.
        **kwargs: ``analysis_url`` / ``region`` / ``timeout`` /
            ``retriable_codes`` / ``max_retries`` overrides plus
            ``compute_resource`` for the log job-name suffix.

    Returns:
        The platform's task-log payload as a dict.

    Raises:
        McpError: If fetching the task log fails after all retries.
    """
    compute_resource = kwargs.get(
        "compute_resource", ANALYST_CONFIG.COMPUTE_RESOURCE
    )
    if relay_mode_enabled():
        return await current_relay_client().get_json(
            f"analysis/{task_id}/logs",
            pool=OutboundPoolName.ANALYSIS_STATUS,
            options=RelayRequestOptions(
                message=f"Check task {task_id} log failed"
            ),
            query={"task_name": f"analyst-agents-{compute_resource}"},
        )
    req = _common_request_kwargs(kwargs)
    client = current_outbound_http_client(OutboundPoolName.ANALYSIS_STATUS)
    response = await request_response_with_retries(
        client,
        JsonPostRequest(
            url=(
                f"{req['analysis_url']}/{task_id}/logs"
                f"?task_name=analyst-agents-{compute_resource}"
            ),
            method="GET",
            headers={
                "Content-Type": "application/json",
                "X-Auth-Token": await get_token(
                    request_timeout=req["timeout"], region=req["region"]
                ),
            },
        ),
        JsonPostRetry(
            timeout=req["timeout"],
            max_retries=req["max_retries"],
            retriable_codes=req["retriable_codes"],
            message=f"Check task {task_id} log failed",
        ),
    )
    if response is not None and response.status_code == 200:
        return response.json()

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to check task log after all retries",
        )
    )


async def task_delete(
    task_id: str,
    **kwargs: Any,
) -> str:
    """Terminate and delete a specified task on the analysis platform.

    Args:
        task_id: The unique identifier of the task to delete.
        **kwargs: ``analysis_url`` / ``region`` / ``timeout`` /
            ``retriable_codes`` / ``max_retries`` overrides.

    Returns:
        Success message naming the deleted task id.

    Raises:
        McpError: If the task deletion fails after all retries.
    """
    if relay_mode_enabled():
        await current_relay_client().post_json(
            f"analysis/{task_id}/terminate",
            {"force": True},
            pool=OutboundPoolName.ANALYSIS_CONTROL,
            options=RelayRequestOptions(message="Failed to delete task"),
        )
        return f"Delete task {task_id} success."
    req = _common_request_kwargs(kwargs)
    client = current_outbound_http_client(OutboundPoolName.ANALYSIS_CONTROL)
    response = await request_response_with_retries(
        client,
        JsonPostRequest(
            url=f"{req['analysis_url']}/{task_id}/terminate",
            headers={
                "Content-Type": "application/json",
                "X-Auth-Token": await get_token(
                    request_timeout=req["timeout"], region=req["region"]
                ),
            },
            json_body={"force": True},
        ),
        JsonPostRetry(
            timeout=req["timeout"],
            max_retries=req["max_retries"],
            retriable_codes=req["retriable_codes"],
            message="Failed to delete task",
        ),
    )
    if response is not None and response.status_code == 200:
        return f"Delete task {task_id} success."

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to delete task after all retries",
        )
    )


async def wait_for_completion(
    task_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Poll a submitted task until it reaches a terminal status.

    Args:
        task_id: Analysis platform task id to poll.
        **kwargs: Same overrides as ``task_status`` plus ``poll_interval``
            and ``max_poll``.

    Returns:
        Final task status payload when the task succeeds.

    Raises:
        McpError: If the task is cancelled, fails, or returns an unknown
            status.
        asyncio.TimeoutError: If polling exceeds ``max_poll`` seconds.
    """
    req = _common_request_kwargs(kwargs)
    poll_interval = kwargs.get("poll_interval", ANALYST_CONFIG.POLL_INTERVAL)
    max_poll = kwargs.get("max_poll", ANALYST_CONFIG.MAX_POLL)
    start_time = time.time()
    while (time.time() - start_time) < max_poll:
        status_data = await task_status(task_id, **req)
        match status_data.get("status"):
            case "CANCELLED":
                raise McpError(
                    ErrorData(code=INTERNAL_ERROR, message="Task cancelled")
                )
            case "FAILED":
                raise McpError(
                    ErrorData(code=INTERNAL_ERROR, message="Task failed")
                )
            case "PENDING" | "RUNNING":
                await asyncio.sleep(poll_interval)
            case "SUCCEEDED":
                return status_data
            case _:
                raise McpError(
                    ErrorData(code=INTERNAL_ERROR, message="Task status error")
                )
    raise TimeoutError(f"Exceeded max polling time {max_poll / 60} minutes")


__all__ = [
    "probe_live_status",
    "task_delete",
    "task_log",
    "task_status",
    "wait_for_completion",
]
