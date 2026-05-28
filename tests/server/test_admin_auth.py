# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the service-token FastAPI dependency.

Covers the four states require_service_principal must distinguish:
no token configured (503), no token presented (401), wrong token (401),
and correct token via Bearer or X-Service-Token headers (200). Also
covers ``ApiConfig.API_SERVICE_TOKEN`` env-name acceptance so the
unprefixed ``API_SERVICE_TOKEN`` and the ``PHYTOMNI_API_SERVICE_TOKEN``
alias both resolve to the same configured token.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api.admin_auth import require_service_principal
from mcp_server_phytomni.config.defaults import ApiConfig

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


def test_service_token_picked_up_from_unprefixed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``API_SERVICE_TOKEN`` (unprefixed) resolves into ``ApiConfig``.

    Locks the canonical env-name pickup so existing deployments using
    the unprefixed spelling keep working after the alias landed.
    """
    monkeypatch.delenv("PHYTOMNI_API_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("API_SERVICE_TOKEN", "svc-unprefixed")

    config = ApiConfig()

    assert config.API_SERVICE_TOKEN is not None
    assert config.API_SERVICE_TOKEN.get_secret_value() == "svc-unprefixed"


def test_service_token_picked_up_from_prefixed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PHYTOMNI_API_SERVICE_TOKEN`` alias resolves into ``ApiConfig``.

    Regression guard for the AF-001 finding: docs / e2e helpers /
    runbook all use the ``PHYTOMNI_``-prefixed name, so the alias is
    required for them to actually unlock ``/v1/api-keys/*`` rather
    than silently leaving the routes at 503.
    """
    monkeypatch.delenv("API_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("PHYTOMNI_API_SERVICE_TOKEN", "svc-prefixed")

    config = ApiConfig()

    assert config.API_SERVICE_TOKEN is not None
    assert config.API_SERVICE_TOKEN.get_secret_value() == "svc-prefixed"
