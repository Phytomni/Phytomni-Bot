# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for per-service API-key scopes and the require_scope gate.

Covers the scopes_satisfy decision (empty = all, relay:* wildcard) and
the route contract: scope-less and agents-scoped keys reach an agent
route, a relay-only key gets 403, and an unknown key gets 401.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mcp_server_phytomni.api.auth import (
    ApiKeyStore,
    relay_scope_satisfied,
    scopes_satisfy,
)

pytestmark = pytest.mark.server


def test_empty_scopes_allow_everything() -> None:
    """An empty granted set means all access (back-compat)."""
    assert scopes_satisfy(frozenset(), ("agents",))
    assert scopes_satisfy(frozenset(), ("relay:llm", "agents"))


def test_relay_scope_denies_empty_grant() -> None:
    """Empty scopes are all-access for agents but DENY on relay.

    Relay routes inject the operator's real upstream credentials, so a
    legacy scope-less key must not silently reach every upstream.
    """
    assert not relay_scope_satisfied(frozenset(), "llm")
    assert not relay_scope_satisfied(frozenset(), "retrieve")


def test_relay_scope_exact_service_match() -> None:
    """relay:<svc> authorizes exactly that relay service."""
    assert relay_scope_satisfied(frozenset({"relay:llm"}), "llm")
    assert not relay_scope_satisfied(frozenset({"relay:llm"}), "retrieve")


def test_relay_scope_keeps_research_input_service_isolated() -> None:
    """Research grant authority needs its own explicit relay service scope."""
    assert relay_scope_satisfied(
        frozenset({"relay:research-input"}), "research-input"
    )
    assert not relay_scope_satisfied(
        frozenset({"relay:obs"}), "research-input"
    )


def test_relay_scope_wildcard_covers_any_service() -> None:
    """relay:* authorizes any relay service."""
    assert relay_scope_satisfied(frozenset({"relay:*"}), "llm")
    assert relay_scope_satisfied(frozenset({"relay:*"}), "database")


def test_relay_scope_ignores_agents_scope() -> None:
    """An agents-only key cannot reach a relay service."""
    assert not relay_scope_satisfied(frozenset({"agents"}), "llm")


def test_exact_scope_match() -> None:
    """A granted scope authorizes that exact need."""
    assert scopes_satisfy(frozenset({"agents"}), ("agents",))
    assert not scopes_satisfy(frozenset({"relay:llm"}), ("agents",))


def test_relay_wildcard_covers_any_relay_service() -> None:
    """relay:* authorizes any relay:<service> need but not agents."""
    assert scopes_satisfy(frozenset({"relay:*"}), ("relay:llm",))
    assert scopes_satisfy(frozenset({"relay:*"}), ("relay:retrieve",))
    assert not scopes_satisfy(frozenset({"relay:*"}), ("agents",))


def test_specific_relay_scope_does_not_cross_services() -> None:
    """A relay:llm scope does not authorize relay:retrieve."""
    assert scopes_satisfy(frozenset({"relay:llm"}), ("relay:llm",))
    assert not scopes_satisfy(frozenset({"relay:llm"}), ("relay:retrieve",))


@pytest.fixture(name="scoped_keys")
def _scoped_keys_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Mint keys with distinct scopes against a throwaway key store."""
    db = tmp_path / "scoped_keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    store = ApiKeyStore(str(db))
    return {
        "all_access": store.create(user_id="admin").api_key,
        "agents": store.create(
            user_id="agent-user", scopes=["agents"]
        ).api_key,
        "relay_only": store.create(
            user_id="customer", scopes=["relay:llm"]
        ).api_key,
    }


async def test_scope_less_key_reaches_agent_route(
    api_client: httpx.AsyncClient,
    scoped_keys: dict[str, str],
) -> None:
    """A scope-less key keeps full access to agent routes."""
    response = await api_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {scoped_keys['all_access']}"},
    )

    assert response.status_code == 200


async def test_agents_scoped_key_reaches_agent_route(
    api_client: httpx.AsyncClient,
    scoped_keys: dict[str, str],
) -> None:
    """A key holding the agents scope reaches agent routes."""
    response = await api_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {scoped_keys['agents']}"},
    )

    assert response.status_code == 200


async def test_relay_only_key_forbidden_on_agent_route(
    api_client: httpx.AsyncClient,
    scoped_keys: dict[str, str],
) -> None:
    """A relay-only key is authenticated but lacks the agents scope."""
    response = await api_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {scoped_keys['relay_only']}"},
    )

    assert response.status_code == 403


async def test_unknown_key_is_unauthorized_not_forbidden(
    api_client: httpx.AsyncClient,
    scoped_keys: dict[str, str],
) -> None:
    """An unknown key returns 401, distinct from the 403 scope failure."""
    del scoped_keys
    response = await api_client.get(
        "/v1/models",
        headers={"Authorization": "Bearer ptm_unknown_key_value"},
    )

    assert response.status_code == 401
