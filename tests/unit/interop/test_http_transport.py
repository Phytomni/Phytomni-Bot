# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the hardened outbound interop HTTP transport."""

import asyncio
import gzip
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.interop.http_transport import (
    InteropHTTPError,
    InteropHTTPTransport,
    httpx_client_factory,
)
from mcp_server_phytomni.interop.models import MCPStreamableHttpTarget
from mcp_server_phytomni.interop.registry import InteropRegistry

pytestmark = pytest.mark.unit
_REAL_ASYNC_REQUEST = httpx.AsyncClient.request


@pytest.fixture(autouse=True)
def _allow_mock_transport_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore HTTPX dispatch; every client below uses MockTransport."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)


def _target(**overrides: object) -> MCPStreamableHttpTarget:
    """Return one credentialed HTTPS target for transport tests."""
    payload: dict[str, object] = {
        "credential_ref": "peer-auth",
        "response_max_bytes": 1024,
        "id": "mcp-http",
        "url": "https://mcp.example.test:8443/v1/mcp",
        "allowed_tools": ["search_genes"],
        "kind": "mcp",
        "transport": "streamable_http",
    }
    payload.update(overrides)
    return MCPStreamableHttpTarget.model_validate(payload)


def _registry(target: MCPStreamableHttpTarget) -> InteropRegistry:
    """Return an enabled immutable registry containing one target."""
    return InteropRegistry(_targets={target.id: target})


def _sensitive(credentials: object) -> SensitiveConfig:
    """Build test settings whose interop secret uses the documented shape."""
    config_cls = cast(Any, SensitiveConfig)
    return config_cls(
        _env_file=None,
        INTEROP_CREDENTIALS=SecretStr(json.dumps(credentials)),
    )


async def _public_resolver(_hostname: str, _port: int) -> Sequence[str]:
    """Resolve every fixture hostname to one public test address."""
    return ("8.8.8.8",)


def _credentials(value: str = "Bearer operator-secret") -> dict[str, object]:
    """Return the documented credential-ref to headers JSON shape."""
    return {
        "peer-auth": {
            "headers": {
                "Authorization": value,
                "X-Peer-Key": "operator-key",
            }
        }
    }


async def test_factory_pins_url_and_injects_credentials_after_validation() -> (
    None
):
    """The delegate sees only the pinned URL and configured credentials."""
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/mcp"
        return httpx.Response(200, json={"ok": True})

    target = _target()
    async with httpx_client_factory(
        "mcp-http",
        registry=_registry(target),
        sensitive_config=_sensitive(_credentials()),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.post(
            "",
            headers={"Authorization": "Bearer caller-value"},
            json={"method": "tools/list"},
        )

    assert response.status_code == 200
    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == "8.8.8.8"
    assert request.url.port == 8443
    assert request.headers["host"] == "mcp.example.test:8443"
    assert request.headers["authorization"] == "Bearer operator-secret"
    assert request.headers["x-peer-key"] == "operator-key"
    assert request.extensions["sni_hostname"] == "mcp.example.test"
    assert isinstance(request.extensions["sni_hostname"], str)


async def test_mcp_transport_rejects_same_origin_path_escape() -> None:
    """The configured MCP credential applies to one exact endpoint path."""
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    target = _target()
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        sensitive_config=_sensitive(_credentials()),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError):
            await client.get("https://mcp.example.test:8443/admin")

    assert called is False


async def test_factory_revalidates_dns_for_every_request() -> None:
    """The transport does not cache a formerly safe DNS answer."""
    answers = iter(("8.8.8.8", "1.1.1.1"))
    resolved: list[str] = []
    seen_hosts: list[str] = []

    async def resolver(_hostname: str, _port: int) -> Sequence[str]:
        address = next(answers)
        resolved.append(address)
        return (address,)

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return httpx.Response(204)

    target = _target(credential_ref=None)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        await client.get("")
        await client.get("")

    assert resolved == ["8.8.8.8", "1.1.1.1"]
    assert seen_hosts == resolved


async def test_factory_strips_caller_credentials_without_target_ref() -> None:
    """Caller-supplied auth headers never cross the operator boundary."""
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    target = _target(credential_ref=None)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        await client.get(
            "",
            headers={
                "Authorization": "Bearer caller-secret",
                "Cookie": "session=caller-secret",
                "X-Api-Key": "caller-secret",
            },
        )

    assert len(seen) == 1
    forwarded = seen[0].headers
    assert "authorization" not in forwarded
    assert "cookie" not in forwarded
    assert "x-api-key" not in forwarded


async def test_dns_resolution_is_bounded_by_connect_timeout() -> None:
    """A resolver that never answers cannot hold an interop request forever."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging_resolver(_hostname: str, _port: int) -> Sequence[str]:
        started.set()
        await release.wait()
        return ("8.8.8.8",)

    target = _target(connect_timeout_seconds=0.01, total_timeout_seconds=1.0)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=hanging_resolver,
        delegate_transport=httpx.MockTransport(
            lambda _request: httpx.Response(204)
        ),
    ) as client:
        with pytest.raises(InteropHTTPError, match="request rejected"):
            await client.get("")

    assert started.is_set()


async def test_total_timeout_covers_response_body_and_closes_stream() -> None:
    """The wall-clock timeout continues while a peer streams its body."""
    stream = _BlockingStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    target = _target(
        credential_ref=None,
        total_timeout_seconds=0.02,
        idle_timeout_seconds=1.0,
    )
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError, match="response timed out"):
            await client.get("")

    assert stream.closed is True


async def test_factory_rejects_arbitrary_origin_before_delegate_call() -> None:
    """A caller-supplied absolute URL cannot escape target policy."""
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    target = _target()
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        sensitive_config=_sensitive(_credentials()),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError) as excinfo:
            await client.get("https://attacker.example.test/steal")

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert called is False
    assert "attacker.example.test" not in rendered
    assert "mcp.example.test" not in rendered
    assert "operator-secret" not in rendered
    assert excinfo.value.__cause__ is None


async def test_delegate_exception_is_redacted_without_chaining() -> None:
    """A delegated transport cannot smuggle endpoint or token text upward."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise InteropHTTPError(
            "https://peer.test Authorization Bearer delegate-secret"
        )

    target = _target(credential_ref=None)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError) as excinfo:
            await client.get("")

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert "peer.test" not in rendered
    assert "Authorization" not in rendered
    assert "delegate-secret" not in rendered
    assert excinfo.value.__cause__ is None


async def test_credentials_are_not_processed_before_endpoint_validation() -> (
    None
):
    """A rejected endpoint cannot reach malformed or secret header material."""
    called = False

    async def private_resolver(_hostname: str, _port: int) -> Sequence[str]:
        return ("127.0.0.1",)

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    malformed = {
        "peer-auth": {"headers": {"Authorization": "Bearer hidden\ninvalid"}}
    }
    target = _target()
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        sensitive_config=_sensitive(malformed),
        resolver=private_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError) as excinfo:
            await client.get("")

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert called is False
    assert "request rejected" in rendered
    assert "credentials rejected" not in rendered
    assert "hidden" not in rendered
    assert "Authorization" not in rendered


@pytest.mark.parametrize(
    "credentials",
    [
        {"peer-auth": {"Authorization": "Bearer hidden"}},
        {"peer-auth": {"headers": {"Bad Header": "hidden"}}},
        {"peer-auth": {"headers": {"X-Key": "hidden\nvalue"}}},
        {"peer-auth": {"headers": {"X-Key": " hidden"}}},
        {"peer-auth": {"headers": {"X-Key": "hidden\t"}}},
        {"peer-auth": {"headers": ["Authorization", "hidden"]}},
    ],
)
async def test_invalid_credential_shape_is_redacted(
    credentials: object,
) -> None:
    """Credential-name/value validation never echoes the rejected material."""
    target = _target()
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        sensitive_config=_sensitive(credentials),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(
            lambda _request: httpx.Response(200)
        ),
    ) as client:
        with pytest.raises(InteropHTTPError) as excinfo:
            await client.get("")

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert "hidden" not in rendered
    assert "Authorization" not in rendered
    assert "Bad Header" not in rendered
    assert excinfo.value.__cause__ is None


async def test_redirects_and_environment_proxies_remain_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirect is returned untouched and proxy environment is irrelevant."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "8.8.8.8"
        return httpx.Response(
            302,
            headers={"Location": "https://attacker.example.test/steal"},
        )

    target = _target(credential_ref=None)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        assert client.follow_redirects is False
        assert client.trust_env is False
        response = await client.get("")

    assert response.status_code == 302
    assert calls == 1


class _SpyStream(httpx.AsyncByteStream):
    """Async response stream that records transport-driven closure."""

    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _LeakyCloseStream(_SpyStream):
    """Overflow stream whose close failure contains unsafe peer text."""

    @property
    def close_error(self) -> str:
        """Return the unsafe fixture text raised by ``aclose``."""
        return "https://peer.test Authorization Bearer close-secret"

    async def aclose(self) -> None:
        self.closed = True
        raise RuntimeError(self.close_error)


class _LeakyReadStream(httpx.AsyncByteStream):
    """Response stream whose read failure contains unsafe peer text."""

    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.closed:  # pragma: no cover - marks this as an async generator
            yield b""
        raise RuntimeError(
            "https://peer.test Authorization Bearer read-secret"
        )

    async def aclose(self) -> None:
        self.closed = True


async def test_response_stream_cap_closes_delegate_on_overflow() -> None:
    """Reading beyond target.response_max_bytes aborts and closes the peer."""
    stream = _SpyStream((b"a" * 700, b"b" * 700))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    target = _target(credential_ref=None, response_max_bytes=1024)
    async with (
        httpx_client_factory(
            target.id,
            registry=_registry(target),
            resolver=_public_resolver,
            delegate_transport=httpx.MockTransport(handler),
        ) as client,
        client.stream("GET", "") as response,
    ):
        with pytest.raises(InteropHTTPError, match="response size"):
            await response.aread()

    assert stream.closed is True


async def test_response_overflow_suppresses_delegate_close_error() -> None:
    """A close failure cannot replace the sanitized response-size error."""
    stream = _LeakyCloseStream((b"a" * 2048,))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    target = _target(credential_ref=None, response_max_bytes=1024)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError) as excinfo:
            await client.get("")

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert stream.closed is True
    assert "response size" in rendered
    assert "peer.test" not in rendered
    assert "Authorization" not in rendered
    assert "close-secret" not in rendered
    assert excinfo.value.__cause__ is None


async def test_compressed_response_cannot_bypass_decoded_size_cap() -> None:
    """Encoded peer bodies are rejected before HTTPX can expand them."""
    stream = _SpyStream((gzip.compress(b"x" * 100_000),))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
        )

    target = _target(credential_ref=None, response_max_bytes=1024)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError) as excinfo:
            await client.get(target.url)

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert stream.closed is True
    assert "response encoding" in rendered
    assert "gzip" not in rendered


async def test_response_stream_exception_is_redacted_and_closed() -> None:
    """A peer read failure cannot escape with endpoint or token text."""
    stream = _LeakyReadStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    target = _target(credential_ref=None)
    async with httpx_client_factory(
        target.id,
        registry=_registry(target),
        resolver=_public_resolver,
        delegate_transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(InteropHTTPError) as excinfo:
            await client.get(target.url)

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert stream.closed is True
    assert "response read" in rendered
    assert "peer.test" not in rendered
    assert "Authorization" not in rendered
    assert "read-secret" not in rendered
    assert excinfo.value.__cause__ is None


class _BlockingStream(httpx.AsyncByteStream):
    """Response stream that blocks until its reader is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        await self.release.wait()
        yield b"done"

    async def aclose(self) -> None:
        self.closed = True


async def test_response_stream_closes_delegate_on_cancellation() -> None:
    """Cancelling a response consumer releases the delegated connection."""
    stream = _BlockingStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    target = _target(credential_ref=None)
    async with (
        httpx_client_factory(
            target.id,
            registry=_registry(target),
            resolver=_public_resolver,
            delegate_transport=httpx.MockTransport(handler),
        ) as client,
        client.stream("GET", "") as response,
    ):
        read_task = asyncio.create_task(response.aread())
        await stream.started.wait()
        read_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read_task

    assert stream.closed is True


def test_transport_repr_redacts_endpoint_and_credentials() -> None:
    """Debug repr contains only the non-secret target identifier."""
    target = _target()
    transport = InteropHTTPTransport(
        target=target,
        credentials=SecretStr(json.dumps(_credentials())),
        resolver=_public_resolver,
        delegate=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )

    rendered = repr(transport)
    assert target.id in rendered
    assert "mcp.example.test" not in rendered
    assert "Authorization" not in rendered
    assert "operator-secret" not in rendered


def test_unknown_target_error_does_not_echo_untrusted_identifier() -> None:
    """A caller cannot smuggle URL or token text into a lookup error."""
    untrusted = "https://attacker.test/?Authorization=Bearer-hidden"
    registry = InteropRegistry(_targets={})

    with pytest.raises(InteropHTTPError) as excinfo:
        httpx_client_factory(untrusted, registry=registry)

    rendered = f"{excinfo.value!s} {excinfo.value!r}"
    assert "attacker.test" not in rendered
    assert "Authorization" not in rendered
    assert "Bearer-hidden" not in rendered
    assert excinfo.value.__cause__ is None
