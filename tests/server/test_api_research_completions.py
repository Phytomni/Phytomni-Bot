# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for Knowledge/Review/BriefGene chat models and model listing.

Covers GET /v1/models, model->agent routing, doc_list preservation, and
the BriefGene obs_file_list rejection.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from mcp_server_phytomni import server

pytestmark = pytest.mark.server


def _stub(
    monkeypatch: pytest.MonkeyPatch, agent: str, payload: dict[str, Any]
) -> None:
    """Stub one MCP handler with a canned payload."""

    async def fake(_args: Any) -> dict[str, Any]:
        """Return the canned payload."""
        return payload

    monkeypatch.setitem(server.TOOL_HANDLERS, agent, fake)


async def test_models_lists_chat_like_agents(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """Verify GET /v1/models lists exactly the four chat models."""
    response = await api_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    ids = {m["id"] for m in body["data"]}
    assert ids == {
        "phyto-chat",
        "phyto-knowledge",
        "phyto-review",
        "phyto-brief-gene",
    }


async def test_models_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify GET /v1/models needs a key."""
    response = await api_client.get("/v1/models")

    assert response.status_code == 401


async def test_knowledge_preserves_doc_list(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify knowledge model routes and keeps top-level doc_list."""
    _stub(
        monkeypatch,
        server.PhytomniAgents.KNOWLEDGE_AGENT.value,
        {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ans"},
                    "finish_reason": "stop",
                }
            ],
            "doc_list": [{"title": "Paper A"}],
        },
    )

    response = await chat_completion(
        api_client, issued_api_key, model="phyto-knowledge", content="q"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["doc_list"] == [{"title": "Paper A"}]
    assert body["model"] == "phyto-knowledge"


async def test_brief_gene_rejects_obs_file_list(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """Verify brief-gene refuses obs_file_list it cannot accept."""
    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-brief-gene",
        content="AT1G01010",
        obs_file_list=["/obs/phytomni/x.pdf"],
    )

    assert response.status_code == 400


async def test_brief_gene_without_obs_succeeds(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify brief-gene runs when no obs_file_list is supplied."""
    _stub(
        monkeypatch,
        server.PhytomniAgents.BRIEF_GENE_AGENT.value,
        {"answer": "gene summary"},
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-brief-gene",
        content="AT1G01010",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "gene summary"
