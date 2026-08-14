# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Final zero/positive-capacity cancellation and privacy matrix."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.responses import StreamingResponse
from mcp.shared.exceptions import McpError
from openai import AsyncOpenAI
from pydantic import SecretStr
from starlette.requests import Request
from tests.support.outbound_fakes import (
    bounded_await,
    bounded_wait_for_event,
    managed_async_task,
)
from tests.support.relay_request import relay_request_scope

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.api.auth import ApiPrincipal
from mcp_server_phytomni.api.relay import forward as relay_forward
from mcp_server_phytomni.api.relay.audit import RelayAuditStore
from mcp_server_phytomni.api.relay.forward import (
    RelayErrorMode,
    RelayUpstream,
    forward_relay_request,
)
from mcp_server_phytomni.common import relay_client as relay_client_module
from mcp_server_phytomni.common.relay_client import (
    RelayClient,
    RelayRequestOptions,
)
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.interop.models import A2ATarget
from mcp_server_phytomni.interop.registry import InteropRegistry
from mcp_server_phytomni.interop.runtime import (
    InteropResourceFactories,
    InteropResourceRuntime,
)
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    ObsProfileName,
    OutboundPoolName,
    OutboundPoolRegistry,
    OutboundResourceFactories,
    aclose_outbound_runtime,
    init_outbound_runtime,
)
from mcp_server_phytomni.runtime.outbound.http import (
    BoundAsyncRequestClient,
    OutboundHttpFactories,
    OutboundHttpRuntime,
)

pytestmark = pytest.mark.unit

_CHAT_STREAM_OPTIONS: dict[str, Any] = {
    "prompt_file": "unused",
    "prompt_path": "unused",
    "model": "pytest-model",
    "response_format": {"type": "text"},
    "timeout": 1.0,
    "max_retries": 0,
}


def _pools(
    name: OutboundPoolName,
    capacity: int,
) -> OutboundPoolRegistry:
    """Build a registry with one selected finite or unlimited pool."""
    return OutboundPoolRegistry(
        {
            candidate: capacity if candidate is name else 0
            for candidate in OutboundPoolName
        },
        wait_warn_seconds=0.000001,
    )


def _config(**overrides: Any) -> ServerConfig:
    """Build a server configuration with selected outbound capacities."""
    return ServerConfig(**overrides)


@pytest.fixture(autouse=True)
async def _clear_process_runtime() -> AsyncIterator[None]:
    """Bound process-runtime cleanup even when a matrix assertion fails."""
    await bounded_await(aclose_outbound_runtime())
    yield
    await bounded_await(aclose_outbound_runtime())


class _BlockingByteStream(httpx.AsyncByteStream):
    """HTTP body that blocks iteration and records exact source closure."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.entered.set()
        await bounded_await(self.release.wait())
        yield b"payload"

    async def aclose(self) -> None:
        self.close_count += 1
        self.release.set()


def _streaming_client(stream: _BlockingByteStream) -> httpx.AsyncClient:
    """Return one real HTTPX client backed by a blocking response body."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=stream)

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def _no_content_transport() -> httpx.MockTransport:
    """Return one deterministic successful transport for lifecycle tests."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, request=request)

    return httpx.MockTransport(respond)


def _chat_provider_stream() -> AsyncIterator[dict[str, Any]]:
    """Open the standard blocking-chat stream used by this matrix."""
    return chat_service.stream_phyto_chat_chunks(
        "query",
        [],
        **_CHAT_STREAM_OPTIONS,
    )


class _BlockingLlmStream:
    """Provider iterator that blocks one LLM chunk until cancellation."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.close_count = 0

    def __aiter__(self) -> _BlockingLlmStream:
        return self

    async def __anext__(self) -> Any:
        self.entered.set()
        await bounded_await(self.release.wait())
        return SimpleNamespace(model_dump=lambda: {"choices": []})

    async def close(self) -> None:
        """Release the blocked provider iterator exactly once."""
        self.close_count += 1
        self.release.set()


def _blocking_openai(stream: _BlockingLlmStream) -> SimpleNamespace:
    """Return an OpenAI-shaped owner for one controlled stream."""

    async def create(**kwargs: Any) -> _BlockingLlmStream:
        """Return the controlled stream after verifying stream mode."""
        assert kwargs["stream"] is True
        return stream

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


class _BlockingObsStreamClient:
    """Synchronous OBS-shaped stream consumer with exact close counters."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.source_close_count = 0
        self.close_count = 0

    def consume_stream(self) -> None:
        """Block one SDK stream read and close its source on termination."""
        self.entered.set()
        try:
            self.release.wait(timeout=5)
        finally:
            self.source_close_count += 1

    def close(self) -> None:
        """Record closure of the process-owned OBS client."""
        self.close_count += 1


class _CountingAsyncClient(httpx.AsyncClient):
    """HTTPX client that records calls to its persistent close owner."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.close_count = 0

    async def aclose(self) -> None:
        self.close_count += 1
        await super().aclose()


class _RuntimeOpenAI:
    """Startup-owned OpenAI fake that closes its dedicated HTTP client."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client
        self.close_count = 0

    async def close(self) -> None:
        """Close the OpenAI owner and its dedicated HTTP transport."""
        self.close_count += 1
        await self.http_client.aclose()

    @property
    def is_closed(self) -> bool:
        """Return whether the resource has completed process cleanup."""
        return self.close_count > 0 and self.http_client.is_closed


class _RuntimeObs:
    """Startup-owned OBS fake with an exact synchronous close count."""

    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        """Record closure of the process-owned OBS client."""
        self.close_count += 1

    @property
    def is_closed(self) -> bool:
        """Return whether process cleanup closed the OBS client."""
        return self.close_count > 0


class _MarkedFailureStream(httpx.AsyncByteStream):
    """Response body that raises one request-specific private marker."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.close_count = 0

    async def _raise_marker(self) -> bytes:
        """Raise the private failure when the response body is consumed."""
        raise httpx.ReadError(self.marker)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield await self._raise_marker()

    async def aclose(self) -> None:
        self.close_count += 1


async def _wait_for_thread_event(
    event: threading.Event,
    *,
    task: asyncio.Task[Any],
) -> None:
    """Bound a worker signal and surface premature task completion."""
    async with asyncio.timeout(5.0):
        while not event.is_set():
            if task.done():
                await bounded_await(task)
                raise AssertionError("task completed before provider entered")
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("capacity", [0, 1])
async def test_cancel_before_acquisition_leaves_no_pool_state(
    capacity: int,
) -> None:
    """Pre-acquisition cancellation creates no borrower or waiter."""
    pools = _pools(OutboundPoolName.LLM, capacity)
    start = asyncio.Event()

    async def borrow() -> None:
        await bounded_await(start.wait())
        async with pools.lease(OutboundPoolName.LLM):
            pytest.fail("cancelled task acquired a lease")

    try:
        async with managed_async_task(
            borrow(),
            release_events=(start,),
        ) as task:
            await asyncio.sleep(0)
            task.cancel()
            start.set()
            with pytest.raises(asyncio.CancelledError):
                await bounded_await(task)

            snapshot = pools.snapshot(OutboundPoolName.LLM)
            assert snapshot.in_use == 0
            assert snapshot.waiting == 0
            assert snapshot.started == 0
            assert snapshot.cancelled == 0
    finally:
        await bounded_await(pools.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
async def test_cancel_after_acquisition_releases_the_exact_pool(
    capacity: int,
) -> None:
    """Post-acquisition cancellation records once and returns capacity."""
    pools = _pools(OutboundPoolName.LLM, capacity)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def borrow() -> None:
        async with pools.lease(OutboundPoolName.LLM):
            entered.set()
            await bounded_await(release.wait())

    try:
        async with managed_async_task(
            borrow(),
            release_events=(release,),
        ) as task:
            await bounded_wait_for_event(entered, task=task)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bounded_await(task)

            snapshot = pools.snapshot(OutboundPoolName.LLM)
            assert snapshot.in_use == 0
            assert snapshot.waiting == 0
            assert snapshot.started == 1
            assert snapshot.cancelled == 1
    finally:
        await bounded_await(pools.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
async def test_buffered_body_cancellation_closes_before_release(
    capacity: int,
) -> None:
    """Buffered HTTP cancellation closes its body and returns the lease."""
    pools = _pools(OutboundPoolName.RETRIEVAL, capacity)
    stream = _BlockingByteStream()
    profile = _streaming_client(stream)
    client = BoundAsyncRequestClient(
        pools,
        OutboundPoolName.RETRIEVAL,
        profile,
    )
    try:
        async with managed_async_task(
            client.request("GET", "https://buffered-marker.invalid/body"),
            release_events=(stream.release,),
        ) as task:
            await bounded_wait_for_event(stream.entered, task=task)
            assert pools.snapshot(OutboundPoolName.RETRIEVAL).in_use == 1

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bounded_await(task)

            snapshot = pools.snapshot(OutboundPoolName.RETRIEVAL)
            assert snapshot.in_use == 0
            assert snapshot.waiting == 0
            assert snapshot.cancelled == 1
            assert stream.close_count == 1
    finally:
        await bounded_await(profile.aclose())
        await bounded_await(pools.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
async def test_relay_stream_cancellation_closes_before_release(
    capacity: int,
) -> None:
    """The HTTP stream seam used by both relay roles retains its lease."""
    pools = _pools(OutboundPoolName.RELAY_CONTROL, capacity)
    stream = _BlockingByteStream()
    trusted = _streaming_client(stream)
    direct = httpx.AsyncClient(transport=_no_content_transport())
    runtime = OutboundHttpRuntime(
        pools,
        trusted=trusted,
        direct_upstream=direct,
    )

    async def consume() -> None:
        async with runtime.stream(
            OutboundPoolName.RELAY_CONTROL,
            "GET",
            "https://relay-stream-marker.invalid/body",
        ) as response:
            async for _chunk in response.aiter_bytes():
                pass

    try:
        async with managed_async_task(
            consume(),
            release_events=(stream.release,),
        ) as task:
            await bounded_wait_for_event(stream.entered, task=task)
            assert pools.snapshot(OutboundPoolName.RELAY_CONTROL).in_use == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bounded_await(task)

            snapshot = pools.snapshot(OutboundPoolName.RELAY_CONTROL)
            assert snapshot.in_use == 0
            assert snapshot.waiting == 0
            assert snapshot.cancelled == 1
            assert stream.close_count == 1
    finally:
        await bounded_await(runtime.aclose())
        await bounded_await(pools.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
async def test_llm_stream_cancellation_closes_before_release(
    capacity: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat streaming closes the provider iterator under the LLM lease."""
    pools = _pools(OutboundPoolName.LLM, capacity)
    provider_stream = _BlockingLlmStream()
    runtime = SimpleNamespace(
        pools=pools,
        openai=_blocking_openai(provider_stream),
    )
    monkeypatch.setattr(
        chat_service, "current_outbound_runtime", lambda: runtime
    )
    monkeypatch.setattr(chat_service, "get_prompt", lambda *_args: "prompt")
    stream = _chat_provider_stream()

    async def next_chunk() -> dict[str, Any]:
        """Read one provider chunk through the production generator."""
        return await anext(stream)

    try:
        async with managed_async_task(
            next_chunk(),
            release_events=(provider_stream.release,),
        ) as task:
            await bounded_wait_for_event(provider_stream.entered, task=task)
            assert pools.snapshot(OutboundPoolName.LLM).in_use == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bounded_await(task)

            snapshot = pools.snapshot(OutboundPoolName.LLM)
            assert snapshot.in_use == 0
            assert snapshot.waiting == 0
            assert snapshot.cancelled == 1
            assert provider_stream.close_count == 1
    finally:
        await bounded_await(getattr(stream, "aclose")())
        await bounded_await(pools.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
async def test_obs_stream_cancellation_waits_for_source_close(
    capacity: int,
) -> None:
    """OBS cancellation retains its lease through blocking SDK iteration."""
    pools = _pools(OutboundPoolName.OBS, capacity)
    client = _BlockingObsStreamClient()
    runtime = ObsClientRuntime(pools, client)
    try:
        async with managed_async_task(
            runtime.run(
                ObsProfileName.PRIMARY,
                lambda owned: owned.consume_stream(),
            )
        ) as task:
            try:
                await _wait_for_thread_event(client.entered, task=task)
                task.cancel()
                await asyncio.sleep(0)
                assert pools.snapshot(OutboundPoolName.OBS).in_use == 1

                client.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await bounded_await(task)

                snapshot = pools.snapshot(OutboundPoolName.OBS)
                assert snapshot.in_use == 0
                assert snapshot.waiting == 0
                assert snapshot.cancelled == 1
                assert client.source_close_count == 1
            finally:
                client.release.set()
        await bounded_await(runtime.aclose())
        await bounded_await(runtime.aclose())
        assert client.close_count == 1
    finally:
        client.release.set()
        await bounded_await(runtime.aclose())
        await bounded_await(pools.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
async def test_interop_operation_cancellation_releases_and_reuses_owner(
    capacity: int,
) -> None:
    """Interop cancellation releases capacity without leaking its client."""
    pools = _pools(OutboundPoolName.INTEROP, capacity)
    target = A2ATarget.model_validate(
        {
            "id": "peer-matrix",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": "https://peer-matrix.example.test/card",
            "allowed_interface_origins": ["https://peer-matrix.example.test"],
            "allowed_skills": ["search_genes"],
        }
    )
    registry = InteropRegistry(
        enabled=True,
        _targets={target.id: target},
    )
    clients: list[_CountingAsyncClient] = []

    def factory(*_args: Any, **_kwargs: Any) -> _CountingAsyncClient:
        client = _CountingAsyncClient(transport=_no_content_transport())
        clients.append(client)
        return client

    runtime = InteropResourceRuntime(
        registry,
        cast(SensitiveConfig, SimpleNamespace()),
        pools,
        factories=InteropResourceFactories(http=factory),
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def operation(_client: httpx.AsyncClient) -> None:
        entered.set()
        await bounded_await(release.wait())

    try:
        async with managed_async_task(
            runtime.run_http(target.id, operation),
            release_events=(release,),
        ) as task:
            await bounded_wait_for_event(entered, task=task)
            assert pools.snapshot(OutboundPoolName.INTEROP).in_use == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bounded_await(task)

            snapshot = pools.snapshot(OutboundPoolName.INTEROP)
            assert snapshot.in_use == 0
            assert snapshot.waiting == 0
            assert snapshot.cancelled == 1
            assert len(clients) == 1
            assert not clients[0].is_closed
        await bounded_await(runtime.aclose())
        await bounded_await(runtime.aclose())
        assert clients[0].is_closed
        assert clients[0].close_count == 1
    finally:
        await bounded_await(runtime.aclose())
        await bounded_await(pools.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
async def test_runtime_shutdown_drains_then_closes_each_resource_once(
    capacity: int,
) -> None:
    """Process shutdown drains active work before exact reverse cleanup."""
    profiles: list[_CountingAsyncClient] = []
    openai_resources: list[_RuntimeOpenAI] = []
    obs = _RuntimeObs()

    def profile_factory(**_kwargs: Any) -> _CountingAsyncClient:
        client = _CountingAsyncClient(transport=_no_content_transport())
        profiles.append(client)
        return client

    def openai_factory(**kwargs: Any) -> _RuntimeOpenAI:
        resource = _RuntimeOpenAI(kwargs["http_client"])
        openai_resources.append(resource)
        return resource

    factories = OutboundResourceFactories(
        http=OutboundHttpFactories(
            trusted=profile_factory,
            direct_upstream=profile_factory,
        ),
        openai=cast(Callable[..., AsyncOpenAI], openai_factory),
        obs=lambda **_kwargs: obs,
        interop=lambda _pools: None,
    )
    runtime = await init_outbound_runtime(
        _config(OUTBOUND_LLM_CONCURRENCY=capacity),
        factories=factories,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def borrow() -> None:
        async with runtime.pools.lease(OutboundPoolName.LLM):
            entered.set()
            await bounded_await(release.wait())

    async with managed_async_task(
        borrow(),
        release_events=(release,),
    ) as borrower:
        await bounded_wait_for_event(entered, task=borrower)
        async with managed_async_task(
            aclose_outbound_runtime(),
            release_events=(release,),
        ) as closing:
            await asyncio.sleep(0)
            assert not closing.done()
            assert all(client.close_count == 0 for client in profiles)
            assert openai_resources[0].close_count == 0
            assert obs.close_count == 0

            release.set()
            await bounded_await(borrower)
            await bounded_await(closing)
    await bounded_await(aclose_outbound_runtime())

    assert runtime.pools.snapshot(OutboundPoolName.LLM).in_use == 0
    assert len(profiles) == 2
    assert all(client.close_count == 1 for client in profiles)
    assert openai_resources[0].close_count == 1
    assert openai_resources[0].is_closed
    assert obs.is_closed


def _private_markers(prefix: str) -> dict[str, str]:
    """Build one unique marker for each prohibited observation category."""
    return {
        category: f"{prefix}-{category}-private"
        for category in (
            "body",
            "credential",
            "exception",
            "header",
            "identity",
            "path",
            "query",
            "target",
            "url",
        )
    }


def _request_evidence(
    request: httpx.Request,
    stream: _MarkedFailureStream,
) -> str:
    """Return proof that every private marker reached the request seam."""
    return "\n".join(
        (
            str(request.url),
            repr(dict(request.headers)),
            request.content.decode("utf-8"),
            stream.marker,
        )
    )


def _marked_url(markers: dict[str, str]) -> str:
    """Build one URL carrying every URL-shaped private marker."""
    return (
        f"https://private.invalid/{markers['url']}/{markers['path']}"
        f"?q={markers['query']}&target={markers['target']}"
    )


def _marked_headers(markers: dict[str, str]) -> dict[str, str]:
    """Build request headers carrying credential and identity markers."""
    return {
        "Authorization": f"Bearer {markers['credential']}",
        "X-Private-Header": markers["header"],
        "X-Private-Identity": markers["identity"],
    }


async def _run_direct_privacy_attempt(
    *,
    pool: OutboundPoolName,
    pools: OutboundPoolRegistry,
    profile: httpx.AsyncClient,
    markers: dict[str, str],
) -> None:
    """Exercise the direct HTTP adapter or Interop's owned HTTP seam."""
    if pool is not OutboundPoolName.INTEROP:
        client = BoundAsyncRequestClient(pools, pool, profile)
        with pytest.raises(httpx.ReadError):
            await bounded_await(
                client.request(
                    "POST",
                    _marked_url(markers),
                    headers=_marked_headers(markers),
                    content=markers["body"].encode("utf-8"),
                )
            )
        return

    target = A2ATarget.model_validate(
        {
            "id": "privacy-interop-peer",
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": "https://private.invalid/card",
            "allowed_interface_origins": ["https://private.invalid"],
            "allowed_skills": ["privacy_probe"],
        }
    )
    runtime = InteropResourceRuntime(
        InteropRegistry(enabled=True, _targets={target.id: target}),
        cast(SensitiveConfig, SimpleNamespace()),
        pools,
        factories=InteropResourceFactories(
            http=lambda *_args, **_kwargs: profile
        ),
    )

    async def request(client: httpx.AsyncClient) -> None:
        request = httpx.Request(
            "POST",
            _marked_url(markers),
            headers=_marked_headers(markers),
            content=markers["body"].encode("utf-8"),
        )
        await client.send(request)

    try:
        with pytest.raises(httpx.ReadError):
            await bounded_await(runtime.run_http(target.id, request))
    finally:
        await bounded_await(runtime.aclose())


async def _run_relay_child_privacy_attempt(
    *,
    pool: OutboundPoolName,
    http_runtime: OutboundHttpRuntime,
    markers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the production child-side RelayClient request seam."""
    monkeypatch.setattr(
        relay_client_module,
        "current_outbound_runtime",
        lambda: SimpleNamespace(http=http_runtime),
    )
    client = RelayClient(
        base_url="https://private.invalid",
        api_key=SecretStr(markers["credential"]),
        timeout=1.0,
        max_retries=0,
        retriable_codes=(),
    )
    relay_path = (
        f"{markers['url']}/{markers['path']}"
        f"?q={markers['query']}&target={markers['target']}"
    )
    with pytest.raises(McpError):
        await bounded_await(
            client.post_json(
                relay_path,
                {"body": markers["body"]},
                pool=pool,
                options=RelayRequestOptions(
                    message="safe relay privacy probe",
                    extra_headers={
                        "X-Private-Header": markers["header"],
                        "X-Private-Identity": markers["identity"],
                    },
                ),
            )
        )


async def _run_operator_privacy_attempt(
    *,
    pool: OutboundPoolName,
    http_runtime: OutboundHttpRuntime,
    markers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the operator's real forwarding and streamed-body seam."""
    monkeypatch.setattr(
        relay_forward,
        "current_outbound_runtime",
        lambda: SimpleNamespace(http=http_runtime),
    )

    async def inject_headers() -> dict[str, str]:
        return _marked_headers(markers)

    response = await bounded_await(
        forward_relay_request(
            request=Request(relay_request_scope()),
            body=markers["body"].encode("utf-8"),
            upstream=RelayUpstream(
                url=_marked_url(markers),
                error_mode=RelayErrorMode.TRANSPARENT,
                service=pool.value,
                inject_headers=inject_headers,
                pool=pool,
                operation=markers["target"],
            ),
            principal=ApiPrincipal(
                user_id=markers["identity"],
                key_prefix=markers["header"],
            ),
            audit_store=cast(
                RelayAuditStore,
                SimpleNamespace(record=lambda _entry: None),
            ),
        )
    )
    assert isinstance(response, StreamingResponse)
    iterator = cast(Any, response.body_iterator)
    try:
        try:
            async for _chunk in iterator:
                pass
        except httpx.ReadError:
            pass
    finally:
        await bounded_await(iterator.aclose())


@pytest.mark.parametrize("capacity", [0, 1])
@pytest.mark.parametrize(
    "role",
    ["direct", "relay_child", "operator_relay"],
)
@pytest.mark.parametrize("pool", list(OutboundPoolName))
async def test_every_pool_role_observation_redacts_private_markers(
    pool: OutboundPoolName,
    role: str,
    capacity: int,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshots and pool logs retain no service-family request values."""
    markers = _private_markers(f"{role}-{pool.value}")
    stream = _MarkedFailureStream(markers["exception"])
    requests: list[httpx.Request] = []

    def fail(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, stream=stream)

    profile = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    pools = _pools(pool, capacity)
    http_runtime = OutboundHttpRuntime(
        pools,
        trusted=profile,
        direct_upstream=httpx.AsyncClient(transport=_no_content_transport()),
    )
    caplog.set_level(
        logging.INFO,
        logger="mcp_server_phytomni.runtime.outbound.registry",
    )
    monkeypatch.setattr(
        logging.getLogger("mcp_server_phytomni"),
        "propagate",
        True,
    )
    try:
        if role == "direct":
            await _run_direct_privacy_attempt(
                pool=pool,
                pools=pools,
                profile=profile,
                markers=markers,
            )
        elif role == "relay_child":
            await _run_relay_child_privacy_attempt(
                pool=pool,
                http_runtime=http_runtime,
                markers=markers,
                monkeypatch=monkeypatch,
            )
        else:
            assert role == "operator_relay"
            await _run_operator_privacy_attempt(
                pool=pool,
                http_runtime=http_runtime,
                markers=markers,
                monkeypatch=monkeypatch,
            )

        assert len(requests) == 1
        assert all(
            marker in _request_evidence(requests[0], stream)
            for marker in markers.values()
        )
        pool_messages = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name.startswith("mcp_server_phytomni.runtime.outbound")
        )
        snapshot_text = repr(pools.snapshot(pool))
        assert f"pool={pool.value}" in pool_messages
        assert "outcome=failed" in pool_messages
        for marker in markers.values():
            assert marker not in pool_messages
            assert marker not in snapshot_text
        assert stream.close_count == 1
    finally:
        await bounded_await(http_runtime.aclose())
        await bounded_await(pools.aclose())


async def test_relay_child_privacy_role_enters_the_relay_client_seam(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relay-child role must execute the production RelayClient seam."""
    entered: list[None] = []
    original = RelayClient.post_json

    async def observed(
        self: RelayClient,
        relay_path: str,
        json_body: Any,
        *,
        pool: OutboundPoolName,
        options: RelayRequestOptions,
    ) -> Any:
        entered.append(None)
        return await original(
            self,
            relay_path,
            json_body,
            pool=pool,
            options=options,
        )

    monkeypatch.setattr(RelayClient, "post_json", observed)

    await test_every_pool_role_observation_redacts_private_markers(
        OutboundPoolName.RETRIEVAL,
        "relay_child",
        1,
        caplog,
        monkeypatch,
    )

    assert entered == [None]
