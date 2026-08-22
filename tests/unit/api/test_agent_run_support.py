# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for native agent-run request and stream helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from fastapi.responses import StreamingResponse

from mcp_server_phytomni.api.agent_run_support import (
    _run_id_from_started_frame,
    request_info_query,
    stream_run_id,
)

pytestmark = pytest.mark.unit

_RUN_STARTED = (
    'event: RunStarted\ndata: {"type": "RunStarted", "run_id": "run-1"}\n'
)


def test_request_info_query_reads_json_when_arguments_omit_query() -> None:
    """The request body is the fallback when tool args have no query."""
    assert request_info_query({}, '{"user_query": "rice"}') == "rice"
    assert request_info_query({}, '{"goal_description": "goal"}') == "goal"


def test_request_info_query_rejects_unusable_json() -> None:
    """Invalid or non-object JSON does not invent a query string."""
    assert request_info_query({}, "{") is None
    assert request_info_query({}, "[1]") is None


def test_run_id_from_started_frame_accepts_bytes_and_memoryview() -> None:
    """The opening frame may arrive as text, bytes, or a memoryview."""
    assert _run_id_from_started_frame(_RUN_STARTED) == "run-1"
    assert _run_id_from_started_frame(_RUN_STARTED.encode()) == "run-1"
    assert (
        _run_id_from_started_frame(memoryview(_RUN_STARTED.encode()))
        == "run-1"
    )


def test_run_id_from_started_frame_rejects_unusable_payloads() -> None:
    """Malformed or non-RunStarted frames do not yield a durable id."""
    assert _run_id_from_started_frame(b"\xff") == ""
    assert _run_id_from_started_frame(cast(Any, 123)) == ""
    assert _run_id_from_started_frame("event: RunStarted\ndata: {\n") == ""
    assert _run_id_from_started_frame("event: RunStarted\ndata: []\n") == ""
    assert _run_id_from_started_frame("event: RunFinished\ndata: {}\n") == ""


async def test_stream_run_id_returns_empty_when_the_body_is_empty() -> None:
    """A stream that ends before the first frame has no run identity."""

    async def empty() -> AsyncIterator[bytes]:
        for _ in ():
            yield b""

    response = StreamingResponse(empty())
    assert await stream_run_id(response) == ""
