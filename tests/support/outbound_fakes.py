# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Recording helpers for outbound runtime and HTTP lifecycle tests."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
from openai import AsyncOpenAI

from mcp_server_phytomni.runtime.outbound import (
    ObsProfileName,
    OutboundHttpFactories,
    OutboundPoolName,
    OutboundResourceFactories,
    aclose_outbound_runtime,
    init_outbound_runtime,
)


async def bounded_await[ResultT](
    awaitable: Awaitable[ResultT],
    *,
    timeout_seconds: float = 5.0,
) -> ResultT:
    """Await one test synchronization point under a finite deadline."""
    async with asyncio.timeout(timeout_seconds):
        return await awaitable


async def bounded_wait_for_event(
    event: asyncio.Event,
    *,
    task: asyncio.Task[Any],
    timeout_seconds: float = 5.0,
) -> None:
    """Wait for an event or surface premature owned-task completion."""
    if event.is_set():
        return
    event_waiter = asyncio.create_task(event.wait())
    try:
        async with asyncio.timeout(timeout_seconds):
            completed, _pending = await asyncio.wait(
                (event_waiter, task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if event_waiter in completed:
                await event_waiter
                return
            await task
            raise AssertionError("task completed before expected event")
    finally:
        event_waiter.cancel()
        await bounded_await(
            asyncio.gather(event_waiter, return_exceptions=True),
            timeout_seconds=timeout_seconds,
        )


@asynccontextmanager
async def managed_async_task[ResultT](
    coroutine: Coroutine[Any, Any, ResultT],
    *,
    release_events: Iterable[asyncio.Event] = (),
    timeout_seconds: float = 5.0,
) -> AsyncIterator[asyncio.Task[ResultT]]:
    """Own one spawned test task and guarantee bounded cleanup."""
    task = asyncio.create_task(coroutine)
    try:
        yield task
    finally:
        body_error = sys.exception()

        async def clean_up() -> None:
            """Release gates and consume the owned task under its deadline."""
            for event in release_events:
                event.set()
            task.cancel()
            await bounded_await(
                asyncio.gather(task, return_exceptions=True),
                timeout_seconds=timeout_seconds,
            )

        cleanup_task = asyncio.create_task(clean_up())
        cleanup_error = (
            await asyncio.gather(
                cleanup_task,
                return_exceptions=True,
            )
        )[0]
        if isinstance(cleanup_error, BaseException):
            if body_error is None:
                raise cleanup_error
            body_error.add_note(
                "managed_async_task cleanup failed; body error preserved"
            )


class _ImmediateLease:
    """No-wait lease used when service tests inject a recording client."""

    async def __aenter__(self) -> None:
        """Enter without delaying the provider fake."""
        return None

    async def __aexit__(self, *_args: Any) -> None:
        """Release the no-op lease."""
        return None


class InlineObsRuntime:
    """Run one synchronous OBS operation against an injected fake client."""

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        """Store a factory so tests can exercise startup failures too."""
        self._client_factory = client_factory

    async def run(self, profile: Any, operation: Callable[[Any], Any]) -> Any:
        """Lend one fake client without creating a real event-loop resource."""
        del profile
        return operation(self._client_factory())

    async def aclose(self) -> None:
        """Match the process-owned runtime lifecycle without owning resources.

        The fake does not own a real SDK resource, so closing is a no-op.
        """


class CountingObsRuntime:
    """Run OBS operations while recording each independent lease."""

    def __init__(self, client: Any) -> None:
        """Keep the caller-supplied fake client behind the runtime seam."""
        self.calls = 0
        self.client = client

    async def run(
        self, profile: ObsProfileName, operation: Callable[[Any], Any]
    ) -> Any:
        """Execute one OBS operation and count its runtime lease."""
        assert profile is ObsProfileName.PRIMARY
        self.calls += 1
        return operation(self.client)

    async def aclose(self) -> None:
        """Match the process-owned lifecycle without owning resources."""


def patch_openai_runtime(
    monkeypatch: Any,
    module: ModuleType,
    openai: Any,
) -> None:
    """Patch a service module onto one injected runtime-owned client."""

    def lease(_pool: OutboundPoolName) -> _ImmediateLease:
        """Return one immediate context-managed lease."""
        return _ImmediateLease()

    runtime = SimpleNamespace(
        openai=openai,
        pools=SimpleNamespace(lease=lease),
    )
    monkeypatch.setattr(module, "current_outbound_runtime", lambda: runtime)


def all_capacities(
    **overrides: int,
) -> dict[OutboundPoolName, int]:
    """Return all fixed pool capacities with selected values replaced."""
    capacities = {name: 0 for name in OutboundPoolName}
    capacities.update(
        {
            OutboundPoolName(name): capacity
            for name, capacity in overrides.items()
        }
    )
    return capacities


class ControlledByteStream(httpx.AsyncByteStream):
    """Yield configured chunks around optional gates and failures."""

    def __init__(
        self,
        *chunks: bytes,
        entered: Any = None,
        release: Any = None,
        failure: Exception | None = None,
        on_close: Any = None,
    ) -> None:
        self._chunks = chunks
        self._entered = entered
        self._release = release
        self._failure = failure
        self._on_close = on_close
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._entered is not None:
            self._entered.set()
        if self._release is not None:
            await self._release.wait()
        for chunk in self._chunks:
            yield chunk
        if self._failure is not None:
            raise self._failure

    async def aclose(self) -> None:
        self.closed = True
        if self._on_close is not None:
            self._on_close()


@dataclass
class RecordingResources:
    """Construct two HTTP clients and record close order and kwargs."""

    transport: (
        httpx.AsyncBaseTransport
        | Mapping[str, httpx.AsyncBaseTransport]
        | None
    ) = None
    fail_on: str | None = None
    openai_factory: Callable[..., AsyncOpenAI] | None = None
    obs_client_factory: Callable[..., Any] | None = None
    closed: list[str] = field(default_factory=list)
    constructed: dict[str, dict[str, Any]] = field(default_factory=dict)
    clients: dict[str, httpx.AsyncClient] = field(default_factory=dict)

    def factory(self, name: str) -> Any:
        """Return one named factory accepted by OutboundHttpFactories."""

        def create(**kwargs: Any) -> httpx.AsyncClient:
            if self.fail_on == name:
                raise RuntimeError(name)
            self.constructed[name] = dict(kwargs)
            transport = (
                self.transport[name]
                if isinstance(self.transport, Mapping)
                else self.transport
            )
            client = httpx.AsyncClient(transport=transport, **kwargs)
            self.clients[name] = client
            original_close = client.aclose

            async def close() -> None:
                self.closed.append(name)
                await original_close()

            setattr(client, "aclose", close)
            return client

        return create

    def factories(self) -> OutboundResourceFactories:
        """Build all process-resource factories for lifecycle tests."""

        def build_obs(**kwargs: Any) -> Any:
            self.constructed["obs"] = dict(kwargs)
            if self.obs_client_factory is not None:
                return self.obs_client_factory(**kwargs)

            def close() -> None:
                """Record the process-owned client close."""
                self.closed.append("obs")

            return SimpleNamespace(close=close)

        return OutboundResourceFactories(
            http=OutboundHttpFactories(
                trusted=self.factory("trusted"),
                direct_upstream=self.factory("direct_upstream"),
            ),
            openai=self.openai_factory or AsyncOpenAI,
            obs=build_obs,
        )


def recording_openai_resources(
    create: Callable[..., Awaitable[Any]],
) -> RecordingResources:
    """Build runtime resources with only the outer OpenAI call scripted."""

    def factory(**kwargs: Any) -> Any:
        http_client = kwargs["http_client"]
        client = SimpleNamespace(
            base_url="https://example.invalid/v1",
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create),
            ),
        )

        async def close() -> None:
            """Close the runtime-owned SDK HTTP transport."""
            await http_client.aclose()

        client.close = close
        return client

    return RecordingResources(openai_factory=factory)


def assert_started_pool_attempts(
    runtime: Any,
    expected: Mapping[OutboundPoolName, int],
) -> None:
    """Assert exact started-attempt counters and zero for omitted pools."""
    assert {
        name: runtime.pools.snapshot(name).started for name in OutboundPoolName
    } == {name: expected.get(name, 0) for name in OutboundPoolName}


class QueueTransport(httpx.AsyncBaseTransport):
    """Return queued responses while recording transport attempts."""

    def __init__(self) -> None:
        self.responses: list[httpx.Response | Exception] = []
        self.requests: list[httpx.Request] = []

    def enqueue(
        self,
        *,
        status: int = 200,
        content: bytes = b"{}",
        stream: httpx.AsyncByteStream | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Queue one response for the next request."""
        kwargs: dict[str, Any] = {
            "status_code": status,
            "headers": headers,
        }
        if stream is None:
            kwargs["content"] = content
        else:
            kwargs["stream"] = stream
        self.responses.append(httpx.Response(**kwargs))

    def enqueue_error(self, error: Exception) -> None:
        """Queue one transport exception for the next request."""
        self.responses.append(error)

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        response.request = request
        return response


@asynccontextmanager
async def recording_outbound_runtime(
    *,
    config: Any,
    resources: RecordingResources,
) -> AsyncIterator[Any]:
    """Publish a recording runtime and always clear its process slot."""
    runtime = await init_outbound_runtime(
        config,
        factories=resources.factories(),
    )
    try:
        yield runtime
    finally:
        await aclose_outbound_runtime()
