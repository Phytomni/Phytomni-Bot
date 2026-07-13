# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Endpoint, DNS, and IP policy for untrusted interop HTTP peers.

Classes: EndpointSecurityError, ValidatedEndpoint.
Functions: resolve_host, validate_target_request.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)

import httpx

from .models import A2ATarget, MCPStreamableHttpTarget

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
DNSAddress = str | IPAddress
AsyncDNSResolver = Callable[[str, int], Awaitable[Sequence[DNSAddress]]]
HTTPInteropTarget = MCPStreamableHttpTarget | A2ATarget

# ``ipaddress.is_global`` changed its treatment of a few special-use ranges
# between supported Python releases.  Keep the interop policy explicit so a
# DNS answer cannot become reachable merely because the interpreter changed.
_SPECIAL_USE_NETWORKS: tuple[IPNetwork, ...] = (
    IPv4Network("0.0.0.0/8"),
    IPv4Network("100.64.0.0/10"),  # RFC 6598 shared address space
    IPv4Network("192.0.0.0/24"),
    IPv4Network("192.0.2.0/24"),  # TEST-NET-1
    IPv4Network("198.18.0.0/15"),  # benchmarking
    IPv4Network("198.51.100.0/24"),  # TEST-NET-2
    IPv4Network("203.0.113.0/24"),  # TEST-NET-3
    IPv4Network("240.0.0.0/4"),
    IPv6Network("::/128"),
    IPv6Network("::1/128"),
    IPv6Network("100::/64"),  # discard-only prefix
    IPv6Network("2001:db8::/32"),  # documentation
    IPv6Network("fc00::/7"),  # unique-local
    IPv6Network("fec0::/10"),  # site-local, retained for old peers
    IPv6Network("fe80::/10"),  # link-local
    IPv6Network("ff00::/8"),  # multicast
)


class EndpointSecurityError(ValueError):
    """Raised when an outbound endpoint fails the interop security policy."""


@dataclass(frozen=True, slots=True)
class ValidatedEndpoint:
    """One request-scoped pinned endpoint with redacted representation."""

    target_id: str
    connection_url: httpx.URL = field(repr=False)
    host_header: str = field(repr=False)
    sni_hostname: str = field(repr=False)


async def resolve_host(hostname: str, port: int) -> Sequence[str]:
    """Resolve one hostname through the event loop's async DNS interface."""
    loop = asyncio.get_running_loop()
    results = await loop.getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    addresses: list[str] = []
    for result in results:
        address = str(result[4][0])
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def _effective_port(url: httpx.URL) -> int:
    """Return the explicit or scheme-default endpoint port."""
    if url.port is not None:
        return url.port
    return 443 if url.scheme == "https" else 80


def _origin(url: httpx.URL) -> tuple[str, bytes, int]:
    """Return a normalized origin tuple for exact policy comparison."""
    return (url.scheme.lower(), url.raw_host.lower(), _effective_port(url))


def _configured_origins(
    target: HTTPInteropTarget,
) -> frozenset[tuple[str, bytes, int]]:
    """Return every operator-owned origin available to one target."""
    urls: tuple[str, ...]
    if isinstance(target, MCPStreamableHttpTarget):
        urls = (target.url,)
    else:
        urls = (target.card_base_url, *target.allowed_interface_origins)
    return frozenset(_origin(httpx.URL(value)) for value in urls)


def _configured_path_allowed(
    target: HTTPInteropTarget, request_url: httpx.URL
) -> bool:
    """Keep MCP and Agent Card requests on their configured paths.

    MCP credentials are valid for exactly one endpoint path.  An A2A card is
    likewise a fixed resource, while a configured A2A interface origin may
    expose a discovered RPC path beneath that origin.
    """
    if isinstance(target, MCPStreamableHttpTarget):
        configured = httpx.URL(target.url)
        return request_url.raw_path == configured.raw_path
    card = httpx.URL(target.card_base_url)
    if _origin(request_url) == _origin(card):
        return request_url.raw_path == card.raw_path
    return True


def _request_structure_allowed(url: httpx.URL) -> bool:
    """Return whether a request URL retains the model-level safe shape."""
    return bool(
        url.scheme in {"http", "https"}
        and url.host
        and not url.userinfo
        and not url.query
        and not url.fragment
    )


def _requires_cidr_policy(address: IPAddress) -> bool:
    """Return whether an address belongs to any blocked category."""
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return True
    return (
        any(address in network for network in _SPECIAL_USE_NETWORKS)
        or not address.is_global
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or (isinstance(address, IPv6Address) and address.is_site_local)
    )


def _is_allowlisted(address: IPAddress, networks: Sequence[IPNetwork]) -> bool:
    """Return whether a literal or its mapped IPv4 value matches policy."""
    candidates: tuple[IPAddress, ...] = (address,)
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        candidates = (address, address.ipv4_mapped)
    return any(
        candidate.version == network.version and candidate in network
        for candidate in candidates
        for network in networks
    )


def _parse_addresses(values: Sequence[DNSAddress]) -> tuple[IPAddress, ...]:
    """Normalize a DNS answer without retaining invalid resolver values."""
    addresses: list[IPAddress] = []
    for value in values:
        try:
            address = (
                value
                if isinstance(value, (IPv4Address, IPv6Address))
                else ip_address(value)
            )
        except ValueError:
            raise EndpointSecurityError(
                "interop endpoint DNS returned an invalid address"
            ) from None
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise EndpointSecurityError(
            "interop endpoint DNS returned no addresses"
        )
    return tuple(addresses)


def _select_address(
    addresses: Sequence[IPAddress], networks: Sequence[IPNetwork]
) -> IPAddress:
    """Validate an atomic DNS set and select its first safe address."""
    restricted = tuple(_requires_cidr_policy(item) for item in addresses)
    if any(restricted) and not all(restricted):
        raise EndpointSecurityError(
            "interop endpoint DNS returned a mixed public/non-public set"
        )
    if any(restricted) and not all(
        _is_allowlisted(item, networks) for item in addresses
    ):
        raise EndpointSecurityError(
            "interop endpoint address policy rejected the DNS result"
        )
    return addresses[0]


async def _resolve_addresses(
    hostname: str,
    port: int,
    resolver: AsyncDNSResolver,
    *,
    timeout_seconds: float | None = None,
) -> tuple[IPAddress, ...]:
    """Resolve a hostname or validate a configured literal directly."""
    try:
        return (ip_address(hostname),)
    except ValueError:
        pass
    try:
        if timeout_seconds is None:
            values = await resolver(hostname, port)
        else:
            async with asyncio.timeout(timeout_seconds):
                values = await resolver(hostname, port)
    except TimeoutError:
        raise EndpointSecurityError(
            "interop endpoint DNS resolution timed out"
        ) from None
    except Exception:
        raise EndpointSecurityError(
            "interop endpoint DNS resolution failed"
        ) from None
    return _parse_addresses(values)


async def validate_target_request(
    target: HTTPInteropTarget,
    request_url: httpx.URL,
    *,
    resolver: AsyncDNSResolver = resolve_host,
    dns_timeout_seconds: float | None = None,
) -> ValidatedEndpoint:
    """Validate and pin one request to an operator-configured target origin."""
    if not _request_structure_allowed(request_url):
        raise EndpointSecurityError(
            "interop request URL has an unsafe structure"
        )
    if _origin(request_url) not in _configured_origins(target):
        raise EndpointSecurityError(
            "interop request origin is not configured for the target"
        )
    if not _configured_path_allowed(target, request_url):
        raise EndpointSecurityError(
            "interop request path is not configured for the target"
        )
    if request_url.scheme == "http" and not target.allow_insecure_http:
        raise EndpointSecurityError(
            "interop endpoint requires HTTPS unless explicitly enabled"
        )

    hostname = request_url.raw_host.decode("ascii")
    port = _effective_port(request_url)
    addresses = await _resolve_addresses(
        hostname,
        port,
        resolver,
        timeout_seconds=dns_timeout_seconds,
    )
    selected = _select_address(addresses, target.private_cidr_allowlist)
    connection_url = request_url.copy_with(host=selected.compressed)
    return ValidatedEndpoint(
        target_id=target.id,
        connection_url=connection_url,
        host_header=request_url.netloc.decode("ascii"),
        # httpcore 1.0.9 passes this extension directly to
        # ``start_tls(server_hostname: str | None)``.
        sni_hostname=hostname,
    )


__all__ = [
    "AsyncDNSResolver",
    "EndpointSecurityError",
    "ValidatedEndpoint",
    "resolve_host",
    "validate_target_request",
]
