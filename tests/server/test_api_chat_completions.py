# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the OpenAI-compatible chat completions endpoint.

Covers auth enforcement, message flattening, ChatAgent passthrough with
follow_up_questions preserved, stream rejection, and unknown model.
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


def _stub_chat(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    """Replace the ChatAgent handler with a canned ChatCompletion."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a fixed OpenAI-shaped completion."""
        captured["user_query"] = args.user_query
        return {
            "id": "chatcmpl-canned",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "photosynthesis converts light",
                        "follow_up_questions": ["what is C4?"],
                    },
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )


async def test_chat_completions_passthrough(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a valid call returns the ChatCompletion with extras."""
    key = _issue_key(tmp_path, monkeypatch)
    captured: dict[str, Any] = {}
    _stub_chat(monkeypatch, captured)

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "phyto-chat",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "what is photosynthesis?"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "phyto-chat"
    assert body["id"]
    message = body["choices"][0]["message"]
    assert message["content"] == "photosynthesis converts light"
    assert message["follow_up_questions"] == ["what is C4?"]
    assert "what is photosynthesis?" in captured["user_query"]


async def test_chat_completions_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify a missing key yields the unified 401 envelope."""
    response = await api_client.post(
        "/v1/chat/completions",
        json={"model": "phyto-chat", "messages": []},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == 401


async def test_chat_completions_rejects_stream(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify stream=true is refused with 400."""
    key = _issue_key(tmp_path, monkeypatch)

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "phyto-chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    assert response.status_code == 400


async def test_chat_completions_unknown_model(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an unknown model id yields 404."""
    key = _issue_key(tmp_path, monkeypatch)

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-imaginary",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 404


async def test_chat_completions_requires_user_message(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an empty message list is rejected with 400."""
    key = _issue_key(tmp_path, monkeypatch)

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "phyto-chat", "messages": []},
    )

    assert response.status_code == 400
