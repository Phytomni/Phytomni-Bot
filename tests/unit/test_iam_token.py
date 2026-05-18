# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the IAM ``get_token`` retry hardening.

``get_token`` previously issued one POST with no retry, so a single
transient ``httpx.ConnectError`` failed the whole request. These
offline tests pin: transient connect errors retry then succeed,
exhausted retries raise ``McpError``, and a 2xx response missing the
``X-Subject-Token`` header raises ``McpError``. Shared fake client and
instant-retry sleep live in ``tests/unit/conftest.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.auth import iam

_ClientFactory = Callable[[list[Any], dict[str, int]], type]


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


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_get_token_retries_transient_connect_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    fake_client_factory: _ClientFactory,
) -> None:
    """A transient ConnectError is retried; the next 2xx yields the token.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_client_factory: Scripted fake-client builder.
    """
    calls = {"n": 0}
    fake = fake_client_factory(
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


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_get_token_raises_mcperror_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
    fake_client_factory: _ClientFactory,
) -> None:
    """Persistent ConnectError surfaces as McpError, not a raw exception.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_client_factory: Scripted fake-client builder.
    """
    attempts = iam.SERVER_CONFIG.MAX_RETRIES + 1
    calls = {"n": 0}
    fake = fake_client_factory(
        [httpx.ConnectError("down") for _ in range(attempts)],
        calls,
    )
    monkeypatch.setattr(iam, "AsyncClient", fake)

    with pytest.raises(McpError) as excinfo:
        await iam.get_token()

    assert "Failed to get token" in str(excinfo.value)
    assert calls["n"] == attempts


@pytest.mark.usefixtures("instant_retry_sleep")
async def test_get_token_raises_mcperror_when_header_missing(
    monkeypatch: pytest.MonkeyPatch,
    fake_client_factory: _ClientFactory,
) -> None:
    """A 2xx response without X-Subject-Token is an McpError, not KeyError.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake_client_factory: Scripted fake-client builder.
    """
    calls = {"n": 0}
    fake = fake_client_factory([_resp(200, {})], calls)
    monkeypatch.setattr(iam, "AsyncClient", fake)

    with pytest.raises(McpError) as excinfo:
        await iam.get_token()

    assert "X-Subject-Token" in str(excinfo.value)
