# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the /v1/relay router mount and its enable kill-switch.

The relay router is always mounted; a per-request guard re-reads
RELAY_ENABLED so the relay surface is 404 by default, 200 once enabled,
and stops serving the instant the flag flips back to False.
"""

from __future__ import annotations

import inspect

import httpx
import pytest

from mcp_server_phytomni.api.relay.deps import relay_enabled_guard

pytestmark = pytest.mark.server


def test_relay_kill_switch_dependency_is_native_async() -> None:
    """Avoid FastAPI's worker-thread bridge for the per-request guard."""
    assert inspect.iscoroutinefunction(relay_enabled_guard)


async def test_relay_healthz_404_when_disabled(
    api_client: httpx.AsyncClient,
) -> None:
    """The relay surface is hidden (404) under the default config."""
    response = await api_client.get("/v1/relay/healthz")

    assert response.status_code == 404


async def test_relay_healthz_200_when_enabled(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling the relay exposes the liveness probe."""
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")

    response = await api_client.get("/v1/relay/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_relay_enabled_reread_per_request(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flipping RELAY_ENABLED off stops serving without a rebuild.

    Pins the runtime kill-switch: the guard re-reads the flag on every
    request, so an operator can disable the relay mid-incident without a
    worker restart.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    assert (await api_client.get("/v1/relay/healthz")).status_code == 200

    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "0")
    assert (await api_client.get("/v1/relay/healthz")).status_code == 404


async def test_core_healthz_unaffected_by_relay_mount(
    api_client: httpx.AsyncClient,
) -> None:
    """Mounting the relay router leaves the core liveness probe intact."""
    response = await api_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
