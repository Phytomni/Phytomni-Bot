# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for in-process per-key request rate limiting.

Covers allowing requests under the per-minute budget and rejecting the
next one with 429 plus a Retry-After header and the unified envelope.
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
    """Set a tiny rate limit and a temp key store, return a key."""
    db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    monkeypatch.setenv("API_RATE_LIMIT_PER_MIN", "2")
    return ApiKeyStore(db).create(user_id="u1").api_key


def _stub_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ChatAgent so requests are offline and fast."""

    async def fake(_args: Any) -> dict[str, Any]:
        """Return a minimal completion."""
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )


async def _post(client: httpx.AsyncClient, key: str) -> httpx.Response:
    """Issue one chat completion request."""
    return await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "phyto-chat",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )


async def test_requests_under_budget_pass(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify requests within the per-minute budget are allowed."""
    key = _issue_key(tmp_path, monkeypatch)
    _stub_chat(monkeypatch)

    assert (await _post(api_client, key)).status_code == 200
    assert (await _post(api_client, key)).status_code == 200


async def test_over_budget_returns_429_with_retry_after(
    api_client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the over-budget request is refused with 429."""
    key = _issue_key(tmp_path, monkeypatch)
    _stub_chat(monkeypatch)

    await _post(api_client, key)
    await _post(api_client, key)
    blocked = await _post(api_client, key)

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.json()["error"]["code"] == 429
