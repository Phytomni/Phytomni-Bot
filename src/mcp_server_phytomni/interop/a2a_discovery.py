# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Discover external A2A v1 cards through the hardened interop boundary.

The discovery seam accepts only an operator registry target id.  A peer card
is treated as untrusted metadata: it is fetched without redirects, parsed by
the official SDK, reduced to operator-approved JSON-RPC 1.0 interfaces and
skills, and returned without any signature-verification claim.  Later client
code may pass the reduced card to :class:`a2a.client.ClientFactory`; no client,
transport, or credential is retained by this module.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from a2a.client.card_resolver import parse_agent_card
from a2a.types import AgentCard

from ..config.settings import SensitiveConfig
from .cache import DiscoveryCache
from .capabilities import (
    MAX_CAPABILITIES,
    MAX_DESCRIPTION_BYTES,
    DiscoveryError,
    DiscoveryResult,
    InteropCapability,
    InteropCapabilityError,
)
from .http_transport import InteropHTTPError, httpx_client_factory
from .models import A2ATarget
from .registry import InteropRegistry, InteropRegistryError
from .security import (
    AsyncDNSResolver,
    EndpointSecurityError,
    resolve_host,
    validate_target_request,
)

MAX_CARD_BYTES = 256 * 1024
_JSONRPC_BINDING = "JSONRPC"
_A2A_PROTOCOL_VERSION = "1.0"


class InteropA2AError(RuntimeError):
    """Sanitized failure at the external A2A discovery boundary."""

    def __init__(self, code: str, target_id: str) -> None:
        super().__init__(f"external A2A {code}")
        self.code = code
        self.target_id = target_id


def _discovery_kind(registry: InteropRegistry, target_id: str) -> str:
    """Return a target kind without exposing registry or peer details."""
    if not registry.enabled:
        return "a2a"
    try:
        return registry.require_target(target_id).kind
    except InteropRegistryError:
        return "a2a"


def _resolve_a2a_target(
    target_id: str, registry: InteropRegistry
) -> A2ATarget:
    """Resolve one enabled A2A target from the immutable operator registry."""
    if not registry.enabled:
        raise InteropA2AError("disabled", target_id)
    try:
        target = registry.require_target(target_id)
    except InteropRegistryError:
        raise InteropA2AError("unknown_target", target_id) from None
    if not isinstance(target, A2ATarget):
        raise InteropA2AError("unsupported_transport", target_id)
    return target


def _factory_kwargs(
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None,
    resolver: AsyncDNSResolver,
) -> dict[str, Any]:
    """Build only operator-owned arguments for the hardened client factory."""
    kwargs: dict[str, Any] = {
        "registry": registry,
        "resolver": resolver,
    }
    if sensitive_config is not None:
        kwargs["sensitive_config"] = sensitive_config
    return kwargs


async def _read_card_payload(
    target: A2ATarget,
    *,
    target_id: str,
    factory_kwargs: dict[str, Any],
    client_factory: Callable[..., httpx.AsyncClient],
) -> dict[str, Any]:
    """Fetch and decode one bounded JSON Agent Card payload."""
    try:
        await validate_target_request(
            target,
            httpx.URL(target.card_base_url),
            resolver=factory_kwargs["resolver"],
            dns_timeout_seconds=target.connect_timeout_seconds,
        )
        async with client_factory(
            target_id,
            **factory_kwargs,
        ) as client:
            response = await client.get("")
            if 300 <= response.status_code < 400:
                raise InteropA2AError("redirect_rejected", target_id)
            if not 200 <= response.status_code < 300:
                raise InteropA2AError("http_error", target_id)
            media_type = response.headers.get("content-type", "")
            media_type = media_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json" and not media_type.endswith(
                "+json"
            ):
                raise InteropA2AError("content_type_rejected", target_id)
            payload = await response.aread()
    except InteropA2AError:
        raise
    except (EndpointSecurityError, InteropHTTPError, httpx.HTTPError):
        raise InteropA2AError("transport_error", target_id) from None
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TimeoutError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise InteropA2AError("transport_error", target_id) from None

    if len(payload) > MAX_CARD_BYTES:
        raise InteropA2AError("card_too_large", target_id)
    try:
        decoded = json.loads(payload)
    except (TypeError, UnicodeDecodeError, ValueError):
        raise InteropA2AError("invalid_card", target_id) from None
    if not isinstance(decoded, dict):
        raise InteropA2AError("invalid_card", target_id)
    return decoded


def _parse_card(payload: dict[str, Any], target_id: str) -> AgentCard:
    """Parse one peer payload with the official SDK's v1 compatibility path."""
    try:
        card = parse_agent_card(payload)
    except Exception:
        raise InteropA2AError("invalid_card", target_id) from None
    if not isinstance(card, AgentCard):
        raise InteropA2AError("invalid_card", target_id)
    return card


def _bounded_text(value: str, code: str, target_id: str) -> None:
    """Reject oversized untrusted card descriptions before projection."""
    if len(value.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        raise InteropA2AError(code, target_id)


async def _approved_interfaces(
    card: AgentCard,
    target: A2ATarget,
    *,
    target_id: str,
    resolver: AsyncDNSResolver,
) -> list[Any]:
    """Revalidate every card interface and retain only JSON-RPC 1.0 ones."""
    approved: list[Any] = []
    for interface in card.supported_interfaces:
        try:
            interface_url = httpx.URL(interface.url)
            await validate_target_request(
                target,
                interface_url,
                resolver=resolver,
                dns_timeout_seconds=target.connect_timeout_seconds,
            )
        except (EndpointSecurityError, ValueError, TypeError):
            continue
        if interface.protocol_binding != _JSONRPC_BINDING:
            continue
        if interface.protocol_version != _A2A_PROTOCOL_VERSION:
            continue
        if interface.protocol_binding not in target.allowed_interface_bindings:
            continue
        approved.append(interface)
    if not approved:
        raise InteropA2AError("interface_rejected", target_id)
    return approved


def _rebuild_card(
    card: AgentCard,
    interfaces: list[Any],
    target: A2ATarget,
    *,
    target_id: str,
) -> AgentCard:
    """Copy a card while removing unapproved interfaces, skills, and JWS."""
    _bounded_text(card.description, "card_description_too_large", target_id)
    if len(card.skills) > MAX_CAPABILITIES:
        raise InteropA2AError("too_many_skills", target_id)
    allowed_skill_ids = frozenset(target.allowed_skills)
    allowed_skills = sorted(
        (skill for skill in card.skills if skill.id in allowed_skill_ids),
        key=lambda skill: skill.id,
    )
    for skill in allowed_skills:
        _bounded_text(
            skill.description,
            "skill_description_too_large",
            target_id,
        )

    reduced = AgentCard()
    reduced.CopyFrom(card)
    reduced.supported_interfaces.clear()
    reduced.skills.clear()
    reduced.signatures.clear()
    for interface in interfaces:
        reduced.supported_interfaces.add().CopyFrom(interface)
    for skill in allowed_skills:
        reduced.skills.add().CopyFrom(skill)
    return reduced


async def fetch_external_a2a_card(
    target_id: str,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None = None,
    resolver: AsyncDNSResolver = resolve_host,
    _client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> AgentCard:
    """Fetch and reduce one operator-approved external A2A Agent Card.

    The returned card has been checked by the SDK parser and the C3.2 TLS,
    origin, path, DNS, and IP policy.  It deliberately does not claim that
    Agent Card JWS signatures were verified because no trusted key registry is
    configured in this phase.
    """
    target = _resolve_a2a_target(target_id, registry)
    factory = _client_factory or httpx_client_factory
    factory_kwargs = _factory_kwargs(registry, sensitive_config, resolver)
    payload = await _read_card_payload(
        target,
        target_id=target_id,
        factory_kwargs=factory_kwargs,
        client_factory=factory,
    )
    card = _parse_card(payload, target_id)
    interfaces = await _approved_interfaces(
        card,
        target,
        target_id=target_id,
        resolver=resolver,
    )
    return _rebuild_card(
        card,
        interfaces,
        target,
        target_id=target_id,
    )


def _normalize_skills(
    target_id: str, target: A2ATarget, card: AgentCard
) -> tuple[InteropCapability, ...]:
    """Project allowlisted card skills into C3.4 immutable capabilities."""
    allowed = frozenset(target.allowed_skills)
    capabilities: list[InteropCapability] = []
    seen: set[str] = set()
    for skill in card.skills:
        if skill.id not in allowed:
            continue
        qualified_name = f"{target_id}__{skill.id}"
        if qualified_name in seen:
            raise InteropCapabilityError("qualified_name_conflict")
        seen.add(qualified_name)
        capabilities.append(
            InteropCapability(
                target_id=target_id,
                kind="a2a",
                remote_name=skill.id,
                qualified_name=qualified_name,
                description=skill.description,
                input_schema={},
            )
        )
    return tuple(sorted(capabilities, key=lambda item: item.qualified_name))


async def _discover_once(
    target_id: str,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None,
    resolver: AsyncDNSResolver,
    client_factory: Callable[..., httpx.AsyncClient] | None,
) -> DiscoveryResult:
    """Run one card fetch and return only shared sanitized result fields."""
    kind = _discovery_kind(registry, target_id)
    try:
        target = _resolve_a2a_target(target_id, registry)
        card = await fetch_external_a2a_card(
            target_id,
            registry=registry,
            sensitive_config=sensitive_config,
            resolver=resolver,
            _client_factory=client_factory,
        )
        return DiscoveryResult(data=_normalize_skills(target_id, target, card))
    except (InteropA2AError, InteropCapabilityError) as exc:
        return DiscoveryResult(
            errors=(DiscoveryError(target_id, kind, exc.code),)
        )
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        RuntimeError,
        TimeoutError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return DiscoveryResult(
            errors=(DiscoveryError(target_id, kind, "discovery_failed"),)
        )


async def discover_external_a2a_capabilities(
    target_id: str,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None = None,
    resolver: AsyncDNSResolver = resolve_host,
    cache: DiscoveryCache | None = None,
    _client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> DiscoveryResult:
    """Discover allowlisted A2A skills as cacheable metadata-only DTOs."""

    async def loader(_: str) -> DiscoveryResult:
        return await _discover_once(
            target_id,
            registry=registry,
            sensitive_config=sensitive_config,
            resolver=resolver,
            client_factory=_client_factory,
        )

    if cache is not None:
        return await cache.discover(
            target_id,
            loader,
            kind=_discovery_kind(registry, target_id),
        )
    return await loader(target_id)


__all__ = [
    "InteropA2AError",
    "MAX_CARD_BYTES",
    "discover_external_a2a_capabilities",
    "fetch_external_a2a_card",
]
