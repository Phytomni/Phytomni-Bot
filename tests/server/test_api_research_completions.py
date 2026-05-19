# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for Knowledge/Review/BriefGene chat models and model listing.

Covers GET /v1/models, model->agent routing, doc_list preservation, and
the BriefGene obs_file_list rejection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.api.auth import ApiKeyStore

pytestmark = pytest.mark.server


def _issue_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the key store at a temp db and return a fresh key."""
    db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    return ApiKeyStore(db).create(user_id="u1").api_key


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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify GET /v1/models lists exactly the four chat models."""
    key = _issue_key(tmp_path, monkeypatch)

    response = await api_client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key}"}
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify knowledge model routes and keeps top-level doc_list."""
    key = _issue_key(tmp_path, monkeypatch)
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

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "phyto-knowledge",
            "messages": [{"role": "user", "content": "q"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["doc_list"] == [{"title": "Paper A"}]
    assert body["model"] == "phyto-knowledge"


async def test_brief_gene_rejects_obs_file_list(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify brief-gene refuses obs_file_list it cannot accept."""
    key = _issue_key(tmp_path, monkeypatch)

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "phyto-brief-gene",
            "messages": [{"role": "user", "content": "AT1G01010"}],
            "obs_file_list": ["/obs/phytomni/x.pdf"],
        },
    )

    assert response.status_code == 400


async def test_brief_gene_without_obs_succeeds(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify brief-gene runs when no obs_file_list is supplied."""
    key = _issue_key(tmp_path, monkeypatch)
    _stub(
        monkeypatch,
        server.PhytomniAgents.BRIEF_GENE_AGENT.value,
        {"answer": "gene summary"},
    )

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "phyto-brief-gene",
            "messages": [{"role": "user", "content": "AT1G01010"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "gene summary"
