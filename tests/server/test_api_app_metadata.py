# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the HTTP API skeleton.

Covers liveness/readiness probes and the unified error envelope produced by
the application-wide exception handlers.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.server


async def test_healthz_is_unauthenticated_ok(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify /healthz is a dependency-free liveness probe."""
    response = await api_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_ready(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify /readyz reports readiness after opening local stores."""
    response = await api_client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_unknown_route_uses_unified_error_envelope(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify unmatched routes return the unified error envelope."""
    response = await api_client.get("/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "request_id", "retryable"}
    assert error["code"] == "not_found"
    assert error["message"] == "resource not found"
    assert error["retryable"] is False
