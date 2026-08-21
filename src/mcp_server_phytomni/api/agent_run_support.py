# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Small request and response helpers for native agent runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi.responses import StreamingResponse

from ..mcp.streaming_phases import close_async_iterator
from .lifecycle_contract import build_agent_run_response, empty_agent_result


def request_info_query(
    arguments: Mapping[str, Any], request_json: str | None
) -> str | None:
    """Resolve the original query without trusting attachment maps."""
    for key in ("user_query", "goal_description"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    try:
        payload = json.loads(request_json or "{}")
    except (AttributeError, TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    for key in ("user_query", "goal_description"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def running_agent_run_response(
    *, run_id: str, agent: str
) -> tuple[dict[str, Any], int]:
    """Build the canonical durable response for a newly started run."""
    return (
        build_agent_run_response(
            run_id=run_id,
            agent=agent,
            status="running",
            task_ids=(),
            result=empty_agent_result(),
            persisted=True,
        ),
        202,
    )


def _run_id_from_started_frame(frame: str | bytes | memoryview) -> str:
    """Read the durable run identity from one AG-UI opening frame."""
    if isinstance(frame, memoryview):
        frame = frame.tobytes()
    if isinstance(frame, bytes):
        try:
            text = frame.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    elif isinstance(frame, str):
        text = frame
    else:
        return ""

    event_name = ""
    data_lines: list[str] = []
    for line in text.splitlines():
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.removeprefix(" ")
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if event_name != "RunStarted" or not data_lines:
        return ""
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, Mapping) or payload.get("type") != "RunStarted":
        return ""
    run_id = payload.get("run_id")
    return run_id.strip() if isinstance(run_id, str) else ""


async def stream_run_id(response: StreamingResponse) -> str:
    """Consume the opening stream frame, then detach this subscriber."""
    stream = aiter(response.body_iterator)
    try:
        frame = await anext(stream)
    except StopAsyncIteration:
        return ""
    finally:
        await close_async_iterator(stream)
    return _run_id_from_started_frame(frame)
