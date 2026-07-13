# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for outbound interop endpoint and address validation."""

from collections.abc import Sequence
from ipaddress import ip_address

import httpx
import pytest
from pydantic import ValidationError

from mcp_server_phytomni.interop.models import (
    A2ATarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)
from mcp_server_phytomni.interop.security import (
    AsyncDNSResolver,
    EndpointSecurityError,
    validate_target_request,
)

pytestmark = pytest.mark.unit


def _http_target(**overrides: object) -> MCPStreamableHttpTarget:
    """Return one fixed HTTPS MCP target with optional policy overrides."""
    payload: dict[str, object] = {
        "id": "mcp-http",
        "kind": "mcp",
        "transport": "streamable_http",
        "url": "https://mcp.example.test:8443/v1/mcp",
        "allowed_tools": ["search_genes"],
    }
    payload.update(overrides)
    return MCPStreamableHttpTarget.model_validate(payload)


def _a2a_target(**overrides: object) -> A2ATarget:
    """Return one A2A target with an operator-approved interface origin."""
    payload: dict[str, object] = {
        "id": "a2a-peer",
        "kind": "a2a",
        "transport": "a2a",
        "card_base_url": "https://card.example.test/base",
        "allowed_interface_origins": ["https://rpc.example.test:9443"],
        "allowed_skills": ["annotate_gene"],
    }
    payload.update(overrides)
    return A2ATarget.model_validate(payload)


def _resolver_for(
    *addresses: str,
) -> AsyncDNSResolver:
    """Return an async resolver that yields the supplied literal addresses."""

    async def resolve(_hostname: str, _port: int) -> Sequence[str]:
        return addresses

    return resolve


async def test_validated_endpoint_pins_ip_and_preserves_host_and_sni() -> None:
    """A public DNS result becomes the connection host, not the TLS name."""
    target = _http_target()

    endpoint = await validate_target_request(
        target,
        httpx.URL(target.url),
        resolver=_resolver_for("8.8.8.8"),
    )

    assert str(endpoint.connection_url) == "https://8.8.8.8:8443/v1/mcp"
    assert endpoint.host_header == "mcp.example.test:8443"
    assert endpoint.sni_hostname == "mcp.example.test"
    assert "mcp.example.test" not in repr(endpoint)
    assert "8.8.8.8" not in repr(endpoint)


async def test_ipv6_endpoint_uses_bracketed_connection_url() -> None:
    """A public IPv6 result remains a valid bracketed HTTP connection URL."""
    target = _http_target(url="https://mcp.example.test/v1/mcp")

    endpoint = await validate_target_request(
        target,
        httpx.URL(target.url),
        resolver=_resolver_for("2606:4700:4700::1111"),
    )

    assert str(endpoint.connection_url) == (
        "https://[2606:4700:4700::1111]/v1/mcp"
    )
    assert endpoint.host_header == "mcp.example.test"
    assert endpoint.sni_hostname == "mcp.example.test"


async def test_http_is_rejected_without_explicit_target_opt_in() -> None:
    """Plain HTTP remains disabled even when it points at a public address."""
    target = _http_target(url="http://mcp.example.test/v1/mcp")

    with pytest.raises(EndpointSecurityError, match="HTTPS"):
        await validate_target_request(
            target,
            httpx.URL(target.url),
            resolver=_resolver_for("8.8.8.8"),
        )


async def test_http_is_allowed_only_with_explicit_target_opt_in() -> None:
    """An operator may explicitly permit HTTP for one configured target."""
    target = _http_target(
        url="http://mcp.example.test/v1/mcp",
        allow_insecure_http=True,
    )

    endpoint = await validate_target_request(
        target,
        httpx.URL(target.url),
        resolver=_resolver_for("8.8.8.8"),
    )

    assert str(endpoint.connection_url) == "http://8.8.8.8/v1/mcp"
    assert endpoint.sni_hostname == "mcp.example.test"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.20.1.4",
        "169.254.1.2",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "100.64.0.1",
        "::1",
        "fe80::1",
        "fec0::1",
        "::",
        "ff02::1",
    ],
)
async def test_non_public_addresses_are_rejected_by_default(
    address: str,
) -> None:
    """Unsafe address categories require an exact target CIDR policy."""
    target = _http_target()

    with pytest.raises(EndpointSecurityError, match="address policy"):
        await validate_target_request(
            target,
            httpx.URL(target.url),
            resolver=_resolver_for(address),
        )


async def test_private_address_is_allowed_by_target_cidr_policy() -> None:
    """A private endpoint is accepted only inside the target allowlist."""
    target = _http_target(private_cidr_allowlist=["10.20.0.0/16"])

    endpoint = await validate_target_request(
        target,
        httpx.URL(target.url),
        resolver=_resolver_for("10.20.1.4"),
    )

    assert endpoint.connection_url.host == "10.20.1.4"


@pytest.mark.parametrize(
    ("address", "network"),
    [
        ("100.64.0.1", "100.64.0.0/10"),
        ("fec0::1", "fec0::/10"),
    ],
)
async def test_special_use_address_requires_exact_target_cidr(
    address: str, network: str
) -> None:
    """CGNAT and IPv6 site-local space need explicit operator policy."""
    target = _http_target(private_cidr_allowlist=[network])

    endpoint = await validate_target_request(
        target,
        httpx.URL(target.url),
        resolver=_resolver_for(address),
    )

    assert ip_address(endpoint.connection_url.host) == ip_address(address)


async def test_ipv4_mapped_ipv6_requires_explicit_cidr_policy() -> None:
    """Mapped IPv6 cannot bypass the IPv4 private-address policy."""
    mapped = "::ffff:10.20.1.4"

    with pytest.raises(EndpointSecurityError, match="address policy"):
        await validate_target_request(
            _http_target(),
            httpx.URL(_http_target().url),
            resolver=_resolver_for(mapped),
        )

    allowed_target = _http_target(private_cidr_allowlist=["10.20.0.0/16"])
    endpoint = await validate_target_request(
        allowed_target,
        httpx.URL(allowed_target.url),
        resolver=_resolver_for(mapped),
    )

    assert ip_address(endpoint.connection_url.host) == ip_address(mapped)


async def test_mixed_public_and_non_public_dns_set_is_rejected() -> None:
    """A rebinding-shaped mixed answer is rejected as one atomic set."""
    target = _http_target(private_cidr_allowlist=["10.20.0.0/16"])

    with pytest.raises(EndpointSecurityError, match="mixed"):
        await validate_target_request(
            target,
            httpx.URL(target.url),
            resolver=_resolver_for("8.8.8.8", "10.20.1.4"),
        )


async def test_each_request_re_resolves_and_revalidates_dns() -> None:
    """No validation result is cached across outbound requests."""
    calls = 0

    async def resolve(_hostname: str, _port: int) -> Sequence[str]:
        nonlocal calls
        calls += 1
        return ("8.8.8.8",)

    target = _http_target()
    for _ in range(2):
        await validate_target_request(
            target,
            httpx.URL(target.url),
            resolver=resolve,
        )

    assert calls == 2


async def test_request_must_stay_on_an_operator_configured_origin() -> None:
    """A caller cannot choose an arbitrary outbound hostname or scheme."""
    target = _http_target()

    with pytest.raises(EndpointSecurityError, match="origin"):
        await validate_target_request(
            target,
            httpx.URL("https://attacker.example.test/steal"),
            resolver=_resolver_for("8.8.8.8"),
        )


async def test_mcp_request_must_use_the_exact_configured_path() -> None:
    """MCP credentials cannot be sent to another same-origin endpoint."""
    target = _http_target()

    with pytest.raises(EndpointSecurityError, match="path"):
        await validate_target_request(
            target,
            httpx.URL("https://mcp.example.test:8443/admin"),
            resolver=_resolver_for("8.8.8.8"),
        )


async def test_a2a_card_must_use_the_exact_configured_path() -> None:
    """A card credential cannot be reused on another card-origin path."""
    target = _a2a_target()

    with pytest.raises(EndpointSecurityError, match="path"):
        await validate_target_request(
            target,
            httpx.URL("https://card.example.test/base/admin"),
            resolver=_resolver_for("8.8.8.8"),
        )


async def test_a2a_interface_path_may_use_an_explicit_card_origin() -> None:
    """An operator-approved card origin may expose its RPC path separately."""
    target = _a2a_target(
        allowed_interface_origins=[
            "https://card.example.test",
            "https://rpc.example.test:9443",
        ]
    )

    endpoint = await validate_target_request(
        target,
        httpx.URL("https://card.example.test/a2a"),
        resolver=_resolver_for("8.8.8.8"),
    )

    assert endpoint.connection_url.path == "/a2a"


async def test_a2a_interface_origin_is_operator_approved() -> None:
    """A2A may use a listed interface origin in addition to its card origin."""
    target = _a2a_target()

    endpoint = await validate_target_request(
        target,
        httpx.URL("https://rpc.example.test:9443/rpc"),
        resolver=_resolver_for("8.8.4.4"),
    )

    assert str(endpoint.connection_url) == "https://8.8.4.4:9443/rpc"
    assert endpoint.host_header == "rpc.example.test:9443"


@pytest.mark.parametrize(
    "request_url",
    [
        "https://mcp.example.test:8443/v1/mcp?token=hidden",
        "https://mcp.example.test:8443/v1/mcp#hidden",
        "ftp://mcp.example.test:8443/v1/mcp",
    ],
)
async def test_request_rejects_unsafe_url_components(
    request_url: str,
) -> None:
    """Callers cannot reintroduce URL components forbidden by the model."""
    target = _http_target()

    with pytest.raises(EndpointSecurityError):
        await validate_target_request(
            target,
            httpx.URL(request_url),
            resolver=_resolver_for("8.8.8.8"),
        )


async def test_dns_errors_are_redacted_without_exception_chaining() -> None:
    """Resolver failures do not echo configured hosts or nested messages."""

    async def resolve(_hostname: str, _port: int) -> Sequence[str]:
        raise RuntimeError(
            "resolver leaked mcp.example.test Authorization Bearer token"
        )

    target = _http_target()
    with pytest.raises(EndpointSecurityError) as excinfo:
        await validate_target_request(
            target,
            httpx.URL(target.url),
            resolver=resolve,
        )

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert "mcp.example.test" not in rendered
    assert "Authorization" not in rendered
    assert "Bearer" not in rendered
    assert excinfo.value.__cause__ is None


def test_http_opt_in_is_not_available_to_stdio_targets() -> None:
    """The insecure-HTTP flag exists only on network target variants."""
    payload = {
        "id": "stdio-peer",
        "kind": "mcp",
        "transport": "stdio",
        "command": "/opt/phytomni/bin/peer",
        "allowed_tools": ["search_genes"],
        "allow_insecure_http": True,
    }

    with pytest.raises(ValidationError, match="allow_insecure_http"):
        MCPStdioTarget.model_validate(payload)
