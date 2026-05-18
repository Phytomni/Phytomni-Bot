# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the IAM ``get_token`` retry hardening.

A single transient ``httpx.ConnectError`` previously failed the whole
request because ``get_token`` issued one POST with no retry, unlike every
downstream BI / NL2SQL call. These offline tests pin the new behavior:
transient connect errors are retried via the shared
``request_response_with_retries`` helper, exhausted retries surface an
``McpError`` (not a bare exception), and a success response missing the
``X-Subject-Token`` header is reported as an ``McpError``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.auth import iam


def _resp(status_code: int, headers: dict[str, str]) -> SimpleNamespace:
    """Build a minimal httpx-like response stub.

    Exposes only what ``get_token`` and the shared retry helper read:
    ``status_code``, ``headers``, and a no-op ``raise_for_status`` (every
    canned response in these tests is 2xx).

    Args:
        status_code: HTTP status code for the stub response.
        headers: Response header mapping.

    Returns:
        A ``SimpleNamespace`` quacking like an ``httpx.Response``.
    """
    return SimpleNamespace(
        status_code=status_code,
        headers=dict(headers),
        raise_for_status=lambda: None,
    )


def _client_factory(behaviors: list[Any], calls: dict[str, int]) -> type:
    """Build a fake ``AsyncClient`` whose ``post`` replays ``behaviors``.

    Args:
        behaviors: Per-call script; an exception instance is raised, any
            other value is returned as the response.
        calls: Mutable counter; ``calls["n"]`` is incremented per POST so
            the test can assert how many attempts were made.

    Returns:
        A class for ``monkeypatch.setattr(iam, "AsyncClient", ...)``.
    """
    script = list(behaviors)

    class _FakeClient:
        """Async context-manager HTTP client stub."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Ignore client construction arguments."""
            del args, kwargs

        async def __aenter__(self) -> "_FakeClient":
            """Enter the async context."""
            return self

        async def __aexit__(self, *args: Any) -> None:
            """Exit the async context."""
            del args

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            """Replay the next scripted behavior for one POST."""
            del args, kwargs
            calls["n"] += 1
            behavior = script.pop(0)
            if isinstance(behavior, BaseException):
                raise behavior
            return behavior

    return _FakeClient


@pytest.fixture(autouse=True)
def _instant_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the shared retry backoff instant so tests stay fast.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def _no_sleep(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


async def test_get_token_retries_transient_connect_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient ConnectError is retried; the next 2xx yields the token.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    calls = {"n": 0}
    fake = _client_factory(
        [
            httpx.ConnectError("transient connect blip"),
            _resp(201, {"X-Subject-Token": "tok-abc-123"}),
        ],
        calls,
    )
    monkeypatch.setattr(iam, "AsyncClient", fake)

    token = await iam.get_token()

    assert token == "tok-abc-123"
    assert calls["n"] == 2  # one failure + one success


async def test_get_token_raises_mcperror_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent ConnectError surfaces as McpError, not a raw exception.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    attempts = iam.SERVER_CONFIG.MAX_RETRIES + 1
    calls = {"n": 0}
    fake = _client_factory(
        [httpx.ConnectError("down") for _ in range(attempts)],
        calls,
    )
    monkeypatch.setattr(iam, "AsyncClient", fake)

    with pytest.raises(McpError) as excinfo:
        await iam.get_token()

    assert "Failed to get token" in str(excinfo.value)
    assert calls["n"] == attempts


async def test_get_token_raises_mcperror_when_header_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2xx response without X-Subject-Token is an McpError, not KeyError.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    calls = {"n": 0}
    fake = _client_factory([_resp(200, {})], calls)
    monkeypatch.setattr(iam, "AsyncClient", fake)

    with pytest.raises(McpError) as excinfo:
        await iam.get_token()

    assert "X-Subject-Token" in str(excinfo.value)
