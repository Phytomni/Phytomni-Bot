# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Route tests for the resolve_gene_id flag on design native runs.

DigitalDesign is not exposed under /v1/chat/completions; these tests
pin the /v1/agents/design/runs path. Mirrors the BGA + deep_genome
server-test template.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.design.resolve_query import (
    DigitalDesignIdCandidate,
    DigitalDesignResolveError,
    DigitalDesignResolveResult,
)
from mcp_server_phytomni.api import app as api_app

pytestmark = pytest.mark.server


def _resolved(gene_id: str, raw: str) -> DigitalDesignResolveResult:
    """Return a canned design resolver result."""
    return DigitalDesignResolveResult(
        gene_id=gene_id,
        raw_query=raw,
        candidates=[
            DigitalDesignIdCandidate(gene_id=gene_id, confidence=1.0),
        ],
    )


def _stub_design_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace DigitalDesignAgent handler with a minimal capturing stub."""

    async def fake(args: Any) -> dict[str, Any]:
        captured["species_code"] = args.species_code
        captured["gene_id"] = args.gene_id
        return {"answer": "design submission", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.DIGITAL_DESIGN_AGENT.value,
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
    """flag=true rewrites user_query into gene_id and stamps metadata."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_design_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, design_config: Any, sensitive_config: Any
    ) -> DigitalDesignResolveResult:
        del design_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("AT1G01010", raw_query)

    monkeypatch.setattr(api_app, "resolve_design_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "design",
        {
            "species_code": "ath",
            "obs_file_list": [],
            "user_query": "design AT1G01010 promoter",
            "resolve_gene_id": True,
        },
    )

    assert response.status_code == 202  # remote-submit agent
    body = response.json()
    assert captured["species_code"] == "ath"
    assert captured["gene_id"] == "AT1G01010"
    assert resolver_calls == ["design AT1G01010 promoter"]
    metadata = body["result"]["formatted"].get("metadata") or {}
    assert metadata.get("original_query") == "design AT1G01010 promoter"
    assert metadata.get("resolved_gene_id") == "AT1G01010"
    assert metadata.get("resolve_gene_id") is True


async def test_native_runs_skips_resolver_when_flag_false(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=false leaves gene_id as-is from the structured request."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
    _stub_design_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, design_config: Any, sensitive_config: Any
    ) -> DigitalDesignResolveResult:
        del design_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_design_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "design",
        {
            "species_code": "ath",
            "gene_id": "AT1G01010",
            "obs_file_list": [],
            "resolve_gene_id": False,
        },
    )

    assert response.status_code == 202
    assert captured["gene_id"] == "AT1G01010"
    assert not resolver_calls


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
    _stub_design_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, design_config: Any, sensitive_config: Any
    ) -> DigitalDesignResolveResult:
        del design_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(api_app, "resolve_design_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "design",
        {
            "species_code": "ath",
            "obs_file_list": [],
            "resolve_gene_id": True,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert "user_query" in body["error"]["message"]
    assert not resolver_calls
    assert "gene_id" not in captured


async def test_native_runs_resolver_failure_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """DigitalDesignResolveError surfaces as HTTP 400 with the reason."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    _stub_design_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, design_config: Any, sensitive_config: Any
    ) -> DigitalDesignResolveResult:
        del raw_query, design_config, sensitive_config
        raise DigitalDesignResolveError("no valid candidate")

    monkeypatch.setattr(api_app, "resolve_design_user_query", fake_resolve)

    response = await _post_run(
        api_client,
        issued_api_key,
        "design",
        {
            "species_code": "ath",
            "obs_file_list": [],
            "user_query": "ambiguous query",
            "resolve_gene_id": True,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert "no valid candidate" in body["error"]["message"]
    assert "gene_id" not in captured
