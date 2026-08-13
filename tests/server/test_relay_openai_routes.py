# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the OpenAI-family relay routes (llm / coder / embed).

Each route is scope-gated, reads its body under the byte budget, strips
the caller credential and injects the operator's Bearer key, and forwards
to a config-resolved upstream URL with the client query dropped.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from tests.support.relay_fakes import (
    build_relay_app,
    make_relay_client_fixture,
    make_relay_key_fixture,
    make_relay_reset_fixture,
    patch_mock_transport,
)

from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay import routes as routes_module

pytestmark = pytest.mark.server


_relay_key_fixture = make_relay_key_fixture(user_id="c")
_client_fixture = make_relay_client_fixture(build_relay_app)
_reset_inflight = make_relay_reset_fixture(forward_module)


def _patch_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide fake operator LLM/coder/embed secrets to the routes."""
    fake = SimpleNamespace(
        BASE_URL="https://llm.test/v1",
        API_KEY=SecretStr("sk-llm-op"),
        MODEL_ID="operator-llm-model",
        CODER_URL="https://coder.test/api/v2",
        CODER_API_KEY=SecretStr("sk-coder-op"),
        CODER_MODEL="operator-coder-model",
        EMBED_URL="https://embed.test/v1/",
        EMBED_API_KEY=SecretStr("sk-embed-op"),
        EMBED_MODEL="operator-embed-model",
    )
    monkeypatch.setattr(routes_module, "get_sensitive_config", lambda: fake)


@pytest.mark.parametrize(
    ("service", "operator_model"),
    (
        ("llm", "operator-llm-model"),
        ("coder", "operator-coder-model"),
        ("embed", "operator-embed-model"),
    ),
)
async def test_openai_routes_replace_customer_model_with_operator_model(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    operator_model: str,
) -> None:
    """A relay child never needs or selects an operator provider model."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"{}",
        )

    patch_mock_transport(monkeypatch, forward_module, handler)
    _patch_secrets(monkeypatch)
    path = "embeddings" if service == "embed" else "chat/completions"

    response = await client.post(
        f"/v1/relay/{service}/{path}",
        headers={"Authorization": f"Bearer {relay_key(service)}"},
        json={"model": "customer-selected-model", "input": "sentinel"},
    )

    assert response.status_code == 200
    forwarded = json.loads(seen[0].content)
    assert forwarded == {"model": operator_model, "input": "sentinel"}


@pytest.mark.parametrize("body", (b"{", b"[]"))
async def test_openai_routes_reject_invalid_json_before_forwarding(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    """Only a JSON object can cross the operator-credentialed boundary."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        pytest.fail("invalid relay JSON reached the upstream")

    patch_mock_transport(monkeypatch, forward_module, handler)
    _patch_secrets(monkeypatch)

    response = await client.post(
        "/v1/relay/llm/chat/completions",
        headers={
            "Authorization": f"Bearer {relay_key('llm')}",
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert response.status_code == 400


async def test_llm_route_injects_bearer_and_drops_query(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The llm route strips the caller key, injects the operator Bearer,
    and forwards to the config URL without the client query string."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{}"
        )

    patch_mock_transport(monkeypatch, forward_module, handler)
    _patch_secrets(monkeypatch)

    response = await client.post(
        "/v1/relay/llm/chat/completions?inject=evil",
        headers={"Authorization": f"Bearer {relay_key('llm')}"},
        content=b'{"q":1}',
    )

    assert response.status_code == 200
    assert seen[0].headers["authorization"] == "Bearer sk-llm-op"
    # Upstream URL is config-resolved; the client query is dropped ([12]).
    assert str(seen[0].url) == "https://llm.test/v1/chat/completions"


async def test_embed_route_uses_embeddings_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The embed route targets EMBED_URL + /embeddings with its key."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{}"
        )

    patch_mock_transport(monkeypatch, forward_module, handler)
    _patch_secrets(monkeypatch)

    response = await client.post(
        "/v1/relay/embed/embeddings",
        headers={"Authorization": f"Bearer {relay_key('embed')}"},
        content=b'{"input":"x"}',
    )

    assert response.status_code == 200
    assert seen[0].headers["authorization"] == "Bearer sk-embed-op"
    assert str(seen[0].url) == "https://embed.test/v1/embeddings"


async def test_llm_route_rejects_wrong_scope(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An embed-scoped key cannot reach the llm route."""
    _patch_secrets(monkeypatch)

    response = await client.post(
        "/v1/relay/llm/chat/completions",
        headers={"Authorization": f"Bearer {relay_key('embed')}"},
        content=b"{}",
    )

    assert response.status_code == 403
