# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the service-token FastAPI dependency.

Covers the four states require_service_principal must distinguish:
no token configured (503), no token presented (401), wrong token (401),
and correct token via Bearer or X-Service-Token headers (200).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api.admin_auth import require_service_principal

pytestmark = pytest.mark.server


async def test_returns_503_when_service_token_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a fresh deployment with no token fails closed at 503."""
    monkeypatch.delenv("API_SERVICE_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await require_service_principal(
            authorization="Bearer some-token", x_service_token=None
        )

    assert exc_info.value.status_code == 503
    assert "not enabled" in exc_info.value.detail


async def test_returns_401_when_no_token_presented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a configured server rejects requests with no credentials."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "configured-token")

    with pytest.raises(HTTPException) as exc_info:
        await require_service_principal(
            authorization=None, x_service_token=None
        )

    assert exc_info.value.status_code == 401
    assert "Missing" in exc_info.value.detail


async def test_returns_401_when_token_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a wrong token is rejected even when correctly formatted."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "configured-token")

    with pytest.raises(HTTPException) as exc_info:
        await require_service_principal(
            authorization="Bearer wrong-token", x_service_token=None
        )

    assert exc_info.value.status_code == 401
    assert "Invalid" in exc_info.value.detail


async def test_accepts_correct_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a matching token via Authorization Bearer header is accepted."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "matching-token")

    await require_service_principal(
        authorization="Bearer matching-token", x_service_token=None
    )


async def test_accepts_correct_x_service_token_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a matching token via X-Service-Token header is accepted."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "matching-token")

    await require_service_principal(
        authorization=None, x_service_token="matching-token"
    )


async def test_rejects_non_bearer_authorization_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Basic/other auth schemes are not silently accepted."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "configured-token")

    with pytest.raises(HTTPException) as exc_info:
        await require_service_principal(
            authorization="Basic configured-token", x_service_token=None
        )

    assert exc_info.value.status_code == 401
