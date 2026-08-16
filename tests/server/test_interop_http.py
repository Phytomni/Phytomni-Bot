# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the flag-gated, sanitized interop capability listing."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.interop.cache import DiscoveryCache
from mcp_server_phytomni.interop.capabilities import (
    DiscoveryError,
    DiscoveryResult,
    InteropCapability,
)
from mcp_server_phytomni.interop.models import A2ATarget, MCPStdioTarget
from mcp_server_phytomni.interop.registry import (
    InteropRegistry,
    InteropRegistryError,
)

pytestmark = pytest.mark.server
_REAL_ASYNC_REQUEST = httpx.AsyncClient.request
_PATH = "/v1/interop/capabilities"


def _registry() -> InteropRegistry:
    """Build targets whose sensitive transport fields must stay private."""
    stdio = MCPStdioTarget.model_validate(
        {
            "id": "stdio-peer",
            "kind": "mcp",
            "transport": "stdio",
            "command": "/opt/phytomni/bin/mcp-peer",
            "args": ["--safe-mode"],
            "allowed_tools": ["lookup"],
            "credential_ref": "operator-token",
        }
    )
    a2a = A2ATarget.model_validate(
        {
            "id": "a2a-peer",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": (
                "https://card.peer.invalid/.well-known/agent-card.json"
            ),
            "allowed_interface_origins": ["https://rpc.peer.invalid"],
            "allowed_skills": ["lookup"],
            "credential_ref": "operator-token",
        }
    )
    return InteropRegistry(
        _targets={stdio.id: stdio, a2a.id: a2a},
    )


def _capability(target_id: str, kind: str) -> InteropCapability:
    """Build one detached capability DTO for a fake discovery result."""
    return InteropCapability(
        target_id=target_id,
        kind=kind,
        remote_name="lookup",
        qualified_name=f"{target_id}__lookup",
        description="safe metadata",
        input_schema={"type": "object", "properties": {}},
    )


def _discoverer(
    kind: str,
    results: dict[str, DiscoveryResult],
    calls: list[str],
) -> Callable[..., Awaitable[DiscoveryResult]]:
    """Build a cache-aware fake for one discovery transport kind."""

    async def discover(
        target_id: str,
        *,
        cache: DiscoveryCache,
        **_: Any,
    ) -> DiscoveryResult:
        """Return a result through the same cache seam as production."""

        async def load(_: str) -> DiscoveryResult:
            calls.append(target_id)
            return results[target_id]

        return await cache.discover(target_id, load, kind=kind)

    return discover


@pytest.fixture(name="client_bundle")
async def _client_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Build an interop-enabled API app and an agents-scoped API key."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    monkeypatch.setenv("PHYTOMNI_INTEROP_TARGETS", "[]")
    db = str(tmp_path / "interop-keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    key = (
        ApiKeyStore(db)
        .create(user_id="interop-user", scopes=["agents"])
        .api_key
    )
    app = create_app()
    client = httpx.AsyncClient(
        base_url="http://api.test",
        transport=httpx.ASGITransport(app=app),
    )
    try:
        yield client, key
    finally:
        await client.aclose()


async def test_listing_requires_authentication_and_has_no_query_overrides(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Authentication is enforced and URL-style discovery overrides reject."""
    client, key = client_bundle
    unauthenticated = await client.get(_PATH)
    assert unauthenticated.status_code == 401

    response = await client.get(
        _PATH,
        params={"url": "https://attacker.invalid/override"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 400
    assert "attacker.invalid" not in response.text


async def test_listing_is_sanitized_partial_and_cached(
    client_bundle: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One target may fail while safe DTOs remain deterministic and cached."""
    client, key = client_bundle
    registry = _registry()
    results = {
        "stdio-peer": DiscoveryResult(
            data=(_capability("stdio-peer", "mcp"),)
        ),
        "a2a-peer": DiscoveryResult(
            errors=(DiscoveryError("a2a-peer", "a2a", "discovery_failed"),)
        ),
    }
    calls: list[str] = []
    monkeypatch.setattr(
        api_app_module,
        "load_interop_registry",
        lambda *_args: registry,
    )
    monkeypatch.setattr(
        api_app_module,
        "discover_external_mcp_capabilities",
        _discoverer("mcp", results, calls),
    )
    monkeypatch.setattr(
        api_app_module,
        "discover_external_a2a_capabilities",
        _discoverer("a2a", results, calls),
    )

    headers = {"Authorization": f"Bearer {key}"}
    first = await client.get(_PATH, headers=headers)
    second = await client.get(_PATH, headers=headers)

    assert first.status_code == second.status_code == 200
    body = first.json()
    assert body == {
        "object": "list",
        "data": [_capability("stdio-peer", "mcp").model_dump()],
        "errors": [
            {
                "target_id": "a2a-peer",
                "kind": "a2a",
                "code": "discovery_failed",
            }
        ],
    }
    assert calls.count("stdio-peer") == 1
    assert calls.count("a2a-peer") == 2
    serialized = json.dumps(body, sort_keys=True)
    for forbidden in (
        "card.peer.invalid",
        "/opt/phytomni/bin/mcp-peer",
        "operator-token",
        "credential_ref",
        "command",
        "args",
        "header",
        "token",
    ):
        assert forbidden not in serialized


async def test_registry_failure_is_stable_and_does_not_leak_config(
    client_bundle: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid operator config becomes a stable 503 without raw details."""
    client, key = client_bundle

    def fail(*_: Any) -> InteropRegistry:
        raise InteropRegistryError("secret credential_ref=operator-token")

    monkeypatch.setattr(api_app_module, "load_interop_registry", fail)
    response = await client.get(
        _PATH,
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == (
        "interop registry unavailable"
    )
    assert "operator-token" not in response.text
