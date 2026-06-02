# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the relay request-body byte-budget reader.

The reader streams the inbound body so a chunked request without (or
with a falsified) Content-Length cannot exhaust worker memory: it bails
the moment the cumulative read exceeds the budget instead of buffering
the whole body, and rejects with 413.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from mcp_server_phytomni.api.relay.routes import read_relay_body

pytestmark = pytest.mark.server


def _streaming_request(
    chunks: list[bytes],
) -> tuple[Request, Callable[[], int]]:
    """Build a POST request whose body streams the given chunks.

    Returns the request and a callable reporting how many receive events
    were pulled, so a test can prove the reader bails early.
    """
    queue = list(chunks)
    pulls = {"count": 0}

    async def receive() -> dict[str, object]:
        pulls["count"] += 1
        if queue:
            return {
                "type": "http.request",
                "body": queue.pop(0),
                "more_body": bool(queue),
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/relay/llm/chat/completions",
        "headers": [],
        "query_string": b"",
    }
    return Request(scope, receive=receive), lambda: pulls["count"]


async def test_read_relay_body_returns_body_within_budget() -> None:
    """A body within the budget is returned whole."""
    request, _ = _streaming_request([b"ab", b"cd"])

    body = await read_relay_body(request, 100)

    assert body == b"abcd"


async def test_read_relay_body_rejects_over_budget() -> None:
    """A body exceeding the budget is rejected with 413."""
    request, _ = _streaming_request([b"a" * 60, b"b" * 60])

    with pytest.raises(HTTPException) as excinfo:
        await read_relay_body(request, 100)

    assert excinfo.value.status_code == 413


async def test_read_relay_body_bails_before_draining_everything() -> None:
    """An oversized chunked body is rejected without reading all of it."""
    chunks = [b"x" * 50 for _ in range(1000)]
    request, pulls = _streaming_request(chunks)

    with pytest.raises(HTTPException):
        await read_relay_body(request, 100)

    # 100-byte budget over 50-byte chunks: bail after ~3 reads, not 1000.
    assert pulls() < 10
