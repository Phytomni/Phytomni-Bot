# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Route tests for the resolve_to_id flag on network native runs.

GeneNetwork is not exposed under /v1/chat/completions; these tests
pin the /v1/agents/network/runs path. Uses the ``resolve_to_id``
flag and TO-id metadata key, not ``resolve_gene_id``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.network.resolve_query import (
    GeneNetworkResolveError,
    GeneNetworkResolveResult,
    GeneNetworkToIdCandidate,
)
from mcp_server_phytomni.api import app as api_app

pytestmark = pytest.mark.server


def _resolved(to_id: str, raw: str) -> GeneNetworkResolveResult:
    """Return a canned network resolver result."""
    return GeneNetworkResolveResult(
        to_id=to_id,
        raw_query=raw,
        candidates=[GeneNetworkToIdCandidate(to_id=to_id, confidence=1.0)],
    )


def _stub_network_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace GeneNetworkAgent handler with a minimal capturing stub."""

    async def fake(args: Any) -> dict[str, Any]:
        captured["species_code"] = args.species_code
        captured["to_id"] = args.to_id
        return {"answer": "network submission", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.GENE_NETWORK_AGENT.value,
        fake,
    )


async def _post_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    agent_slug: str,
    arguments: dict[str, Any],
) -> httpx.Response:
    """POST one native /v1/agents/{slug}/runs with arguments."""
    auth_header = {"Authorization": f"Bearer {issued_api_key}"}
    payload = {"arguments": arguments}
    return await api_client.post(
        f"/v1/agents/{agent_slug}/runs", headers=auth_header, json=payload
    )


async def test_native_runs_resolves_when_flag_true(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true rewrites user_query into to_id and stamps metadata."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("TO:0000207", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "species_code": "osa",
            "obs_file_list": [],
            "user_query": "rice plant height trait",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 202  # remote-submit agent
    body = response.json()
    assert captured["species_code"] == "osa"
    assert captured["to_id"] == "TO:0000207"
    assert resolver_calls == ["rice plant height trait"]
    metadata = body["result"]["formatted"].get("metadata") or {}
    assert metadata.get("original_query") == "rice plant height trait"
    assert metadata.get("resolved_to_id") == "TO:0000207"
    assert metadata.get("resolve_to_id") is True


async def test_native_runs_skips_resolver_when_flag_false(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=false leaves to_id as-is from the structured request."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "species_code": "osa",
            "to_id": "TO:0000207",
            "obs_file_list": [],
            "resolve_to_id": False,
        },
    )

    assert response.status_code == 202
    assert captured["to_id"] == "TO:0000207"
    assert not resolver_calls


async def test_native_runs_rejects_resolve_to_id_on_non_network_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """resolve_to_id on /v1/agents/chat/runs returns 400."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []

    async def fake_chat(args: Any) -> dict[str, Any]:
        captured["user_query"] = args.user_query
        return {"answer": "chat answer", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake_chat,
    )

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "chat",
        {
            "user_query": "rice height",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert "GeneNetwork" in body["error"]["message"]
    assert not resolver_calls
    assert "user_query" not in captured


async def test_native_runs_rejects_missing_user_query(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true without user_query is 400 before the resolver runs."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del network_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "species_code": "osa",
            "obs_file_list": [],
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert "user_query" in body["error"]["message"]
    assert "resolve_to_id" in body["error"]["message"]
    assert not resolver_calls
    assert "to_id" not in captured


async def test_native_runs_resolver_failure_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """GeneNetworkResolveError surfaces as HTTP 400 with the reason."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    _stub_network_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, network_config: Any, sensitive_config: Any
    ) -> GeneNetworkResolveResult:
        del raw_query, network_config, sensitive_config
        raise GeneNetworkResolveError(
            "no valid candidate (LLM proposed ids not in the catalog)"
        )

    monkeypatch.setattr(api_app, "resolve_network_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "network",
        {
            "species_code": "osa",
            "obs_file_list": [],
            "user_query": "ambiguous trait",
            "resolve_to_id": True,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert "catalog" in body["error"]["message"]
    assert "to_id" not in captured
