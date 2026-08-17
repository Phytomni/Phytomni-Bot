# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Hardened HTTPX boundary for operator-owned interop target ids.

Classes: InteropHTTPError, InteropHTTPTransport.
Functions: httpx_client_factory.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from time import monotonic
from typing import Any

import httpx
from pydantic import SecretStr

from ..config.settings import SensitiveConfig
from .credentials import InteropCredentialError, credential_headers
from .models import A2ATarget, MCPStdioTarget, MCPStreamableHttpTarget
from .registry import InteropRegistry, InteropRegistryError
from .security import (
    AsyncDNSResolver,
    EndpointSecurityError,
    HTTPInteropTarget,
    resolve_host,
    validate_target_request,
)


class InteropHTTPError(httpx.TransportError):
    """Sanitized outbound interop transport failure."""


class _ResponseSizeLimitExceededError(Exception):
    """Internal signal converted to a sanitized public transport error."""


class _CappedResponseStream(httpx.AsyncByteStream):
    """Bound one peer response body and close it on abort or cancellation."""

    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        *,
        target_id: str,
        max_bytes: int,
        deadline: float | None = None,
    ) -> None:
        self._stream = stream
        self._target_id = target_id
        self._max_bytes = max_bytes
        self._deadline = deadline
        self._seen_bytes = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            if self._deadline is None:
                async for chunk in self._stream:
                    self._seen_bytes += len(chunk)
                    if self._seen_bytes > self._max_bytes:
                        raise _ResponseSizeLimitExceededError
                    yield chunk
            else:
                async with asyncio.timeout_at(self._deadline):
                    async for chunk in self._stream:
                        self._seen_bytes += len(chunk)
                        if self._seen_bytes > self._max_bytes:
                            raise _ResponseSizeLimitExceededError
                        yield chunk
        except _ResponseSizeLimitExceededError:
            await self._close_safely()
            raise InteropHTTPError(
                "interop response size limit exceeded for target id "
                f"{self._target_id!r}"
            ) from None
        except TimeoutError:
            await self._close_safely()
            raise InteropHTTPError(
                "interop response timed out for target id "
                f"{self._target_id!r}"
            ) from None
        except asyncio.CancelledError:
            await self._close_safely()
            raise
        except Exception:
            await self._close_safely()
            raise InteropHTTPError(
                "interop response read failed for target id "
                f"{self._target_id!r}"
            ) from None

    async def _close_safely(self) -> None:
        """Close the delegated stream without leaking peer exceptions."""
        with suppress(Exception, asyncio.CancelledError):
            await self._stream.aclose()

    async def aclose(self) -> None:
        """Close the delegated peer response stream."""
        await self._close_safely()


class InteropHTTPTransport(httpx.AsyncBaseTransport):
    """Validate, pin, authenticate, and cap one HTTP interop target."""

    def __init__(
        self,
        *,
        target: HTTPInteropTarget,
        credentials: SecretStr,
        resolver: AsyncDNSResolver = resolve_host,
        delegate: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._target = target
        self._credentials = credentials
        self._resolver = resolver
        self._delegate = delegate or httpx.AsyncHTTPTransport(
            trust_env=False,
            proxy=None,
            retries=0,
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    def __repr__(self) -> str:
        """Return a representation containing only the non-secret target id."""
        return f"{type(self).__name__}(target_id={self._target.id!r})"

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        """Validate and forward one request without exposing peer details."""
        deadline = monotonic() + self._target.total_timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                endpoint = await validate_target_request(
                    self._target,
                    request.url,
                    resolver=self._resolver,
                    dns_timeout_seconds=min(
                        self._target.connect_timeout_seconds,
                        remaining,
                    ),
                )
                headers = httpx.Headers(request.headers)
                for name in _CALLER_CONTROL_HEADERS:
                    headers.pop(name, None)
                headers["Host"] = endpoint.host_header
                if self._target.credential_ref is not None:
                    try:
                        configured_headers = credential_headers(
                            self._credentials,
                            self._target.credential_ref,
                        )
                    except InteropCredentialError:
                        raise InteropHTTPError(
                            "outbound interop credentials rejected for "
                            f"target id {self._target.id!r}"
                        ) from None
                    for name, value in configured_headers.items():
                        headers[name] = value
                # The byte cap wraps the transport stream before HTTPX
                # decoding.  Force identity and reject peers that ignore it,
                # otherwise compressed bodies could expand after the cap.
                headers["Accept-Encoding"] = "identity"

                extensions = dict(request.extensions)
                extensions["sni_hostname"] = endpoint.sni_hostname
                pinned_request = httpx.Request(
                    request.method,
                    endpoint.connection_url,
                    headers=headers,
                    stream=request.stream,
                    extensions=extensions,
                )
                response = await self._delegate.handle_async_request(
                    pinned_request
                )
        except EndpointSecurityError:
            raise InteropHTTPError(
                "outbound interop request rejected for target id "
                f"{self._target.id!r}"
            ) from None
        except TimeoutError:
            raise InteropHTTPError(
                "outbound interop request timed out for target id "
                f"{self._target.id!r}"
            ) from None
        except Exception:
            raise InteropHTTPError(
                "outbound interop request failed for target id "
                f"{self._target.id!r}"
            ) from None
        if not isinstance(response.stream, httpx.AsyncByteStream):
            with suppress(Exception):
                await response.aclose()
            raise InteropHTTPError(
                "outbound interop response rejected for target id "
                f"{self._target.id!r}"
            ) from None
        content_encoding = response.headers.get("content-encoding", "")
        if content_encoding.strip().lower() not in {"", "identity"}:
            with suppress(Exception):
                await response.aclose()
            raise InteropHTTPError(
                "outbound interop response encoding rejected for target id "
                f"{self._target.id!r}"
            ) from None
        response.stream = _CappedResponseStream(
            response.stream,
            target_id=self._target.id,
            max_bytes=self._target.response_max_bytes,
            deadline=deadline,
        )
        return response

    async def aclose(self) -> None:
        """Close the isolated delegated transport and its connection pool."""
        with suppress(Exception):
            await self._delegate.aclose()


_CALLER_CONTROL_HEADERS = frozenset(
    {
        "accept-encoding",
        "authorization",
        "connection",
        "content-length",
        "cookie",
        "host",
        "proxy-authorization",
        "set-cookie",
        "transfer-encoding",
        "x-api-key",
        "x-auth-token",
    }
)


def _http_target(
    registry: InteropRegistry, target_id: str
) -> HTTPInteropTarget:
    """Resolve one HTTP-capable target without accepting an endpoint."""
    try:
        target = registry.require_target(target_id)
    except InteropRegistryError:
        raise InteropHTTPError("unknown outbound interop target id") from None
    if isinstance(target, MCPStdioTarget):
        raise InteropHTTPError(
            f"outbound interop target is not HTTP-capable: {target_id}"
        )
    return target


def _base_url(target: HTTPInteropTarget) -> str:
    """Return the operator-owned initial URL for one HTTP target."""
    if isinstance(target, MCPStreamableHttpTarget):
        return target.url
    if isinstance(target, A2ATarget):
        return target.card_base_url
    raise AssertionError("unreachable HTTP interop target")


class _InteropAsyncClient(httpx.AsyncClient):
    """Async client that maps an empty request to the exact target URL."""

    def __init__(self, *, endpoint_url: str, **kwargs: Any) -> None:
        endpoint = httpx.URL(endpoint_url)
        origin = endpoint.copy_with(path="/", query=None, fragment=None)
        self._interop_endpoint = endpoint
        super().__init__(base_url=origin, **kwargs)

    def build_request(
        self, method: str, url: httpx.URL | str, **kwargs: Any
    ) -> httpx.Request:
        """Preserve the configured endpoint path for ``client.post("")``."""
        parsed = httpx.URL(url)
        if (
            not parsed.is_absolute_url
            and parsed.raw_path in {b"", b"/"}
            and not parsed.query
            and not parsed.fragment
        ):
            url = self._interop_endpoint
        return super().build_request(method, url, **kwargs)


def httpx_client_factory(
    target_id: str,
    *,
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None = None,
    resolver: AsyncDNSResolver = resolve_host,
    delegate_transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build an Interop-only HTTPX client from an operator target id.

    This is not a process-wide HTTP factory. Concurrency is the
    ``OutboundPoolName.INTEROP`` lease taken by the Interop runtime
    before it uses the client.
    """
    target = _http_target(registry, target_id)
    credentials = SecretStr("{}")
    if target.credential_ref is not None:
        resolved_settings = sensitive_config or SensitiveConfig.load()
        credentials = resolved_settings.INTEROP_CREDENTIALS
    transport = InteropHTTPTransport(
        target=target,
        credentials=credentials,
        resolver=resolver,
        delegate=delegate_transport,
    )
    timeout = httpx.Timeout(
        target.total_timeout_seconds,
        connect=target.connect_timeout_seconds,
        read=target.idle_timeout_seconds,
        write=target.idle_timeout_seconds,
        pool=target.connect_timeout_seconds,
    )
    return _InteropAsyncClient(
        endpoint_url=_base_url(target),
        transport=transport,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


__all__ = [
    "InteropHTTPError",
    "InteropHTTPTransport",
    "httpx_client_factory",
]
