# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for in-process per-key request rate limiting.

Covers allowing requests under the per-minute budget and rejecting the
next one with 429 plus a Retry-After header and the unified envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server

pytestmark = pytest.mark.server


def _stub_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ChatAgent so requests are offline and fast."""

    async def fake(_args: Any) -> dict[str, Any]:
        """Return a minimal completion."""
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )


async def test_requests_under_budget_pass(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify requests within the per-minute budget are allowed."""
    monkeypatch.setenv("API_RATE_LIMIT_PER_MIN", "2")
    _stub_chat(monkeypatch)

    first = await chat_completion(api_client, issued_api_key)
    second = await chat_completion(api_client, issued_api_key)

    assert first.status_code == 200
    assert second.status_code == 200


async def test_over_budget_returns_429_with_retry_after(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the over-budget request is refused with 429."""
    monkeypatch.setenv("API_RATE_LIMIT_PER_MIN", "2")
    _stub_chat(monkeypatch)

    await chat_completion(api_client, issued_api_key)
    await chat_completion(api_client, issued_api_key)
    blocked = await chat_completion(api_client, issued_api_key)

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    error = blocked.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["message"] == "request rate limit exceeded"
    assert error["retryable"] is False


async def test_zero_limit_disables_rate_limiting(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured limit of 0 disables enforcement entirely.

    Pins the limit-disabled branch in ratelimit.check: when
    API_RATE_LIMIT_PER_MIN <= 0, every request must pass through
    regardless of the per-key history so operators can quickly turn
    enforcement off without touching the rest of the auth pipeline.
    """
    monkeypatch.setenv("API_RATE_LIMIT_PER_MIN", "0")
    _stub_chat(monkeypatch)

    first = await chat_completion(api_client, issued_api_key)
    second = await chat_completion(api_client, issued_api_key)
    third = await chat_completion(api_client, issued_api_key)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
