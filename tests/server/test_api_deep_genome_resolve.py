# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Route tests for the resolve_gene_id flag on deep_genome native runs.

deep_genome is not exposed under /v1/chat/completions (no
MODEL_TO_TOOL entry), so these tests pin the
/v1/agents/deep_genome/runs path only. Mirrors the BGA server-test
template: flag-on rewriting, flag-off passthrough, wrong-agent
rejection, missing user_query rejection, and resolver-error 400.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.deep_genome.resolve_query import (
    DeepGenomeIdCandidate,
    DeepGenomeResolveError,
    DeepGenomeResolveResult,
)
from mcp_server_phytomni.api import app as api_app

pytestmark = pytest.mark.server


def _resolved(gene_id: str, raw: str) -> DeepGenomeResolveResult:
    """Return a canned deep_genome resolver result."""
    return DeepGenomeResolveResult(
        gene_id=gene_id,
        raw_query=raw,
        candidates=[DeepGenomeIdCandidate(gene_id=gene_id, confidence=1.0)],
    )


def _stub_deep_genome_handler(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    """Replace DeepGenomeAgent handler with a minimal capturing stub."""

    async def fake(args: Any) -> dict[str, Any]:
        captured["species_code"] = args.species_code
        captured["gene_id"] = args.gene_id
        return {"answer": "deep genome submission", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.DEEP_GENOME_AGENT.value,
        fake,
    )


async def _post_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    agent_slug: str,
    arguments: dict[str, Any],
) -> httpx.Response:
    """POST one native /v1/agents/{slug}/runs with the supplied arguments."""
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
    _stub_deep_genome_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, deep_genome_config: Any, sensitive_config: Any
    ) -> DeepGenomeResolveResult:
        """Capture the raw query and return a fixed canonical id."""
        del deep_genome_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("Os01g0177400", raw_query)

    monkeypatch.setattr(
        api_app, "resolve_deep_genome_user_query", fake_resolve
    )

    response = await _post_run(
        api_client,
        issued_api_key,
        "deep_genome",
        {
            "species_code": "osa",
            "user_query": "tell me about CAB1 in rice",
            "resolve_gene_id": True,
        },
    )

    assert response.status_code == 202  # remote-submit agent
    body = response.json()
    assert captured["species_code"] == "osa"
    assert captured["gene_id"] == "Os01g0177400"
    assert resolver_calls == ["tell me about CAB1 in rice"]
    metadata = body["result"]["formatted"].get("metadata") or {}
    assert metadata.get("original_query") == "tell me about CAB1 in rice"
    assert metadata.get("resolved_gene_id") == "Os01g0177400"
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
    _stub_deep_genome_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, deep_genome_config: Any, sensitive_config: Any
    ) -> DeepGenomeResolveResult:
        del deep_genome_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(
        api_app, "resolve_deep_genome_user_query", fake_resolve
    )

    response = await _post_run(
        api_client,
        issued_api_key,
        "deep_genome",
        {
            "species_code": "osa",
            "gene_id": "Os01g0177400",
            "resolve_gene_id": False,
        },
    )

    assert response.status_code == 202  # remote-submit agent
    assert captured["gene_id"] == "Os01g0177400"
    assert not resolver_calls
    metadata = response.json()["result"]["formatted"].get("metadata") or {}
    assert "resolved_gene_id" not in metadata


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
    _stub_deep_genome_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, deep_genome_config: Any, sensitive_config: Any
    ) -> DeepGenomeResolveResult:
        del deep_genome_config, sensitive_config
        resolver_calls.append(raw_query)
        return _resolved("UNUSED", raw_query)

    monkeypatch.setattr(
        api_app, "resolve_deep_genome_user_query", fake_resolve
    )

    response = await _post_run(
        api_client,
        issued_api_key,
        "deep_genome",
        {"species_code": "osa", "resolve_gene_id": True},
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
    """DeepGenomeResolveError surfaces as HTTP 400 with the reason."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    _stub_deep_genome_handler(monkeypatch, captured)

    async def fake_resolve(
        raw_query: str, *, deep_genome_config: Any, sensitive_config: Any
    ) -> DeepGenomeResolveResult:
        del raw_query, deep_genome_config, sensitive_config
        raise DeepGenomeResolveError("no valid candidate")

    monkeypatch.setattr(
        api_app, "resolve_deep_genome_user_query", fake_resolve
    )

    response = await _post_run(
        api_client,
        issued_api_key,
        "deep_genome",
        {
            "species_code": "osa",
            "user_query": "ambiguous query",
            "resolve_gene_id": True,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert "no valid candidate" in body["error"]["message"]
    assert "gene_id" not in captured
