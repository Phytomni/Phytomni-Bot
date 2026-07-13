# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for bounded, policy-bound external A2A card discovery."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from google.protobuf import json_format

from mcp_server_phytomni.interop import a2a_discovery as discovery_module
from mcp_server_phytomni.interop.a2a_discovery import (
    discover_external_a2a_capabilities,
    fetch_external_a2a_card,
)
from mcp_server_phytomni.interop.cache import DiscoveryCache
from mcp_server_phytomni.interop.capabilities import DiscoveryError
from mcp_server_phytomni.interop.models import (
    A2ATarget,
    MCPStreamableHttpTarget,
)
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.unit
_REAL_ASYNC_REQUEST = httpx.AsyncClient.request


@pytest.fixture(autouse=True)
def _allow_mock_transport_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore HTTPX dispatch so MockTransport remains fully offline."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)


def _target(**overrides: object) -> A2ATarget:
    """Return an A2A target with separate card and RPC origins."""
    payload: dict[str, object] = {
        "id": "a2a-card",
        "kind": "a2a",
        "transport": "a2a",
        "card_base_url": (
            "https://card.example.test/.well-known/agent-card.json"
        ),
        "allowed_interface_origins": [
            "https://rpc.example.test:9443",
            "https://private.example.test:9443",
        ],
        "allowed_interface_bindings": ["JSONRPC"],
        "allowed_skills": ["annotate", "lookup"],
    }
    payload.update(overrides)
    return A2ATarget.model_validate(payload)


def _registry(target: A2ATarget) -> InteropRegistry:
    """Return an enabled registry containing one A2A target."""
    return InteropRegistry(enabled=True, _targets={target.id: target})


def _card() -> AgentCard:
    """Build a card with valid, unsupported, and malicious metadata."""
    card = AgentCard(
        name="External researcher",
        description="safe card",
        supported_interfaces=[
            AgentInterface(
                url="https://rpc.example.test:9443/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
            AgentInterface(
                url="https://rpc.example.test:9443/http",
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
            ),
            AgentInterface(
                url="https://rpc.example.test:9443/old",
                protocol_binding="JSONRPC",
                protocol_version="0.2",
            ),
            AgentInterface(
                url="https://attacker.example.test/steal",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
            AgentInterface(
                url="https://private.example.test:9443/internal",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(id="lookup", name="Lookup", description="lookup"),
            AgentSkill(id="annotate", name="Annotate", description="annotate"),
            AgentSkill(id="unlisted", name="Hidden", description="hidden"),
        ],
    )
    signature = card.signatures.add()
    signature.protected = "unverified-header"
    signature.signature = "unverified-signature"
    signature.header.update({"kid": "unverified-jws"})
    return card


def _factory_for(
    payload: bytes | dict[str, Any],
    *,
    status_code: int = 200,
    content_type: str = "application/json",
    calls: list[str] | None = None,
) -> Any:
    """Build a fake factory that still uses an isolated HTTPX client."""
    body = (
        json.dumps(payload, separators=(",", ":")).encode()
        if isinstance(payload, dict)
        else payload
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(
            status_code,
            headers={"content-type": content_type},
            content=body,
            request=request,
        )

    def factory(
        _target_id: str,
        *,
        registry: InteropRegistry,
        resolver: Any,
        sensitive_config: Any = None,
    ) -> httpx.AsyncClient:
        del registry, resolver, sensitive_config
        return httpx.AsyncClient(
            base_url="https://card.example.test",
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        )

    return factory


async def _resolver(hostname: str, _port: int) -> Sequence[str]:
    """Resolve fixture hosts to public addresses except the private peer."""
    if hostname == "private.example.test":
        return ("10.20.1.4",)
    return ("8.8.8.8",)


async def test_card_is_reduced_before_client_use_and_skills_are_sorted() -> (
    None
):
    """Only approved JSON-RPC 1.0 interfaces and skills survive projection."""
    target = _target()
    card = await fetch_external_a2a_card(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        _client_factory=_factory_for(json_format.MessageToDict(_card())),
    )

    assert [item.url for item in card.supported_interfaces] == [
        "https://rpc.example.test:9443/a2a"
    ]
    assert [item.id for item in card.skills] == ["annotate", "lookup"]
    assert len(card.signatures) == 0
    assert card.capabilities.streaming is True

    result = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        _client_factory=_factory_for(json_format.MessageToDict(_card())),
    )

    assert [item.qualified_name for item in result.data] == [
        "a2a-card__annotate",
        "a2a-card__lookup",
    ]
    assert result.errors == ()
    assert result.data[0].input_schema == {}


async def test_every_interface_is_revalidated_for_origin_and_ip() -> None:
    """A card cannot redirect a later client call to a new origin or IP."""
    target = _target()
    resolved: list[str] = []

    async def resolver(hostname: str, port: int) -> Sequence[str]:
        del port
        resolved.append(hostname)
        return await _resolver(hostname, 443)

    result = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=resolver,
        _client_factory=_factory_for(json_format.MessageToDict(_card())),
    )

    assert result.errors == ()
    assert "card.example.test" in resolved
    assert "rpc.example.test" in resolved
    assert "private.example.test" in resolved
    assert "attacker.example.test" not in resolved


@pytest.mark.parametrize(
    ("status_code", "content_type", "body", "code"),
    [
        (302, "application/json", b"{}", "redirect_rejected"),
        (503, "application/json", b"{}", "http_error"),
        (200, "text/html", b"{}", "content_type_rejected"),
        (200, "application/json", b"not-json", "invalid_card"),
    ],
)
async def test_card_transport_and_parse_failures_are_sanitized(
    status_code: int,
    content_type: str,
    body: bytes,
    code: str,
) -> None:
    """Peer status, content, and parser details never escape discovery."""
    target = _target()
    result = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        _client_factory=_factory_for(
            body,
            status_code=status_code,
            content_type=content_type,
        ),
    )

    assert result.data == ()
    assert result.errors == (DiscoveryError(target.id, "a2a", code),)
    assert "card.example.test" not in repr(result)
    assert "not-json" not in repr(result)


async def test_card_and_skill_limits_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Card bytes, card text, and skill counts remain bounded."""
    target = _target()
    payload = json_format.MessageToDict(_card())

    monkeypatch.setattr(discovery_module, "MAX_CARD_BYTES", 16)
    result = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        _client_factory=_factory_for(payload),
    )
    assert result.errors == (
        DiscoveryError(target.id, "a2a", "card_too_large"),
    )

    monkeypatch.setattr(discovery_module, "MAX_CARD_BYTES", 256 * 1024)
    oversized = _card()
    oversized.description = "x" * (discovery_module.MAX_DESCRIPTION_BYTES + 1)
    result = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        _client_factory=_factory_for(json_format.MessageToDict(oversized)),
    )
    assert result.errors == (
        DiscoveryError(target.id, "a2a", "card_description_too_large"),
    )

    monkeypatch.setattr(discovery_module, "MAX_CAPABILITIES", 1)
    result = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        _client_factory=_factory_for(payload),
    )
    assert result.errors == (
        DiscoveryError(target.id, "a2a", "too_many_skills"),
    )


async def test_discovery_cache_retains_only_successful_skill_dtos() -> None:
    """Repeated card discovery uses the shared metadata-only cache."""
    target = _target()
    calls: list[str] = []
    cache = DiscoveryCache(ttl_seconds=30)
    factory = _factory_for(
        json_format.MessageToDict(_card()),
        calls=calls,
    )

    first = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        cache=cache,
        _client_factory=factory,
    )
    second = await discover_external_a2a_capabilities(
        target.id,
        registry=_registry(target),
        resolver=_resolver,
        cache=cache,
        _client_factory=factory,
    )

    assert first is second
    assert len(calls) == 1
    assert first.errors == ()
    assert not any("https://" in repr(item) for item in first.data)


@pytest.mark.parametrize(
    ("registry", "code", "kind"),
    [
        (InteropRegistry.disabled(), "disabled", "a2a"),
        (InteropRegistry(enabled=True, _targets={}), "unknown_target", "a2a"),
    ],
)
async def test_registry_failures_are_target_level_only(
    registry: InteropRegistry,
    code: str,
    kind: str,
) -> None:
    """Feature-gate and lookup failures expose no endpoint or secret detail."""
    result = await discover_external_a2a_capabilities(
        "a2a-card",
        registry=registry,
        resolver=_resolver,
        _client_factory=_factory_for({}),
    )

    assert result.data == ()
    assert result.errors == (DiscoveryError("a2a-card", kind, code),)


async def test_wrong_target_kind_is_not_used_as_an_a2a_peer() -> None:
    """An MCP registry entry cannot be reinterpreted as an A2A card."""
    target = MCPStreamableHttpTarget.model_validate(
        {
            "id": "a2a-card",
            "kind": "mcp",
            "transport": "streamable_http",
            "url": "https://mcp.example.test/mcp",
            "allowed_tools": ["lookup"],
        }
    )
    registry = InteropRegistry(
        enabled=True,
        _targets={target.id: target},
    )
    result = await discover_external_a2a_capabilities(
        target.id,
        registry=registry,
        resolver=_resolver,
        _client_factory=_factory_for({}),
    )
    assert result.errors == (
        DiscoveryError(target.id, "mcp", "unsupported_transport"),
    )
