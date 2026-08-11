# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for bounded, process-owned Interop resources."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from mcp.client.stdio import StdioServerParameters
from pydantic import SecretStr

from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.interop.models import (
    A2ATarget,
    InteropTarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)
from mcp_server_phytomni.interop.registry import InteropRegistry
from mcp_server_phytomni.interop.runtime import (
    InteropResourceFactories,
    InteropResourceRuntime,
)
from mcp_server_phytomni.runtime.outbound import (
    OutboundPoolName,
    OutboundPoolRegistry,
)

pytestmark = pytest.mark.unit


def _http_target(
    target_id: str = "peer-http",
) -> MCPStreamableHttpTarget:
    """Build one fixed streamable-HTTP target."""
    return MCPStreamableHttpTarget.model_validate(
        {
            "id": target_id,
            "kind": "mcp",
            "transport": "streamable_http",
            "url": f"https://{target_id}.example.test/v1/mcp",
            "allowed_tools": ["search_genes"],
        }
    )


def _a2a_target(target_id: str = "peer-a2a") -> A2ATarget:
    """Build one fixed A2A target."""
    return A2ATarget.model_validate(
        {
            "id": target_id,
            "kind": "a2a",
            "transport": "a2a",
            "card_base_url": f"https://{target_id}.example.test/card",
            "allowed_interface_origins": [f"https://{target_id}.example.test"],
            "allowed_skills": ["search_genes"],
        }
    )


def _stdio_target(target_id: str = "peer-stdio") -> MCPStdioTarget:
    """Build one absolute-path stdio target."""
    return MCPStdioTarget(
        id=target_id,
        kind="mcp",
        transport="stdio",
        command="/opt/phytomni-peer",
        args=("--mode", "stdio"),
        env_keys=("LANG", "PATH"),
        allowed_tools=("annotate_gene",),
    )


def _registry(*targets: InteropTarget) -> InteropRegistry:
    """Build an enabled immutable target registry."""
    return InteropRegistry(
        enabled=True,
        _targets={target.id: target for target in targets},
    )


def _pools(capacity: int = 0) -> OutboundPoolRegistry:
    """Build all typed pools with one selected Interop capacity."""
    capacities = {name: 0 for name in OutboundPoolName}
    capacities[OutboundPoolName.INTEROP] = capacity
    return OutboundPoolRegistry(capacities, wait_warn_seconds=1.0)


def _sensitive() -> SensitiveConfig:
    """Build a secret-free Interop settings object."""
    return SensitiveConfig(
        DOMAIN_NAME="test-domain",
        USER_NAME="test-user",
        USER_PASSWORD=SecretStr("test-password"),
        ACCESS_KEY_ID=SecretStr("test-access-key"),
        SECRET_ACCESS_KEY=SecretStr("test-secret-key"),
        BASE_URL="https://llm.example.test",
        MODEL_ID="test-model",
        API_KEY=SecretStr("test-api-key"),
        CODER_URL="https://coder.example.test",
        CODER_MODEL="test-coder-model",
        CODER_API_KEY=SecretStr("test-coder-key"),
        GAUSS_DSN=SecretStr("postgresql://user:pass@db.example.test/test"),
        EMBED_URL="https://embed.example.test",
        EMBED_MODEL="test-embed-model",
        EMBED_API_KEY=SecretStr("test-embed-key"),
        INTEROP_CREDENTIALS=SecretStr("{}"),
    )


@pytest.mark.asyncio
async def test_http_client_reused_only_by_validated_target() -> None:
    """One target owns one client while another target gets another."""
    constructed: list[str] = []
    closed: list[str] = []

    def factory(target_id: str, **_: Any) -> httpx.AsyncClient:
        constructed.append(target_id)

        client = httpx.AsyncClient()
        original_close = client.aclose

        async def close() -> None:
            closed.append(target_id)
            await original_close()

        setattr(client, "aclose", close)
        return client

    pools = _pools()
    runtime = InteropResourceRuntime(
        _registry(_http_target("peer-a"), _http_target("peer-b")),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(http=factory),
    )

    async def get(client: httpx.AsyncClient) -> int:
        del client
        return 200

    assert await runtime.run_http("peer-a", get) == 200
    assert await runtime.run_http("peer-a", get) == 200
    assert await runtime.run_http("peer-b", get) == 200
    assert constructed == ["peer-a", "peer-b"]
    assert runtime.http_keys == {"peer-a", "peer-b"}

    with pytest.raises(Exception):
        await runtime.run_http("caller-url.example", get)
    assert pools.snapshot(OutboundPoolName.INTEROP).started == 3

    await runtime.aclose()
    await runtime.aclose()
    assert closed == ["peer-a", "peer-b"]


@pytest.mark.asyncio
async def test_http_failure_evicts_exact_client_and_next_call_rebuilds() -> (
    None
):
    """A transport failure closes one resource and bounds its replacement."""
    constructed = 0
    closed = 0

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        nonlocal constructed
        constructed += 1

        client = httpx.AsyncClient()
        original_close = client.aclose

        async def close() -> None:
            nonlocal closed
            closed += 1
            await original_close()

        setattr(client, "aclose", close)
        return client

    runtime = InteropResourceRuntime(
        _registry(_a2a_target()),
        _sensitive(),
        _pools(),
        factories=InteropResourceFactories(http=factory),
    )

    async def call(_client: httpx.AsyncClient) -> int:
        if constructed == 1:
            raise httpx.ConnectError("closed")
        return 204

    with pytest.raises(httpx.ConnectError):
        await runtime.run_http("peer-a2a", call)
    assert runtime.http_keys == set()
    assert closed == 1
    assert await runtime.run_http("peer-a2a", call) == 204
    assert constructed == 2
    await runtime.aclose()
    assert closed == 2


@pytest.mark.asyncio
async def test_target_validation_happens_before_interop_acquisition() -> None:
    """Unknown target ids cannot consume a logical slot."""
    pools = _pools(1)
    runtime = InteropResourceRuntime(
        _registry(_a2a_target()),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(
            http=lambda *_args, **_kwargs: pytest.fail(
                "unknown target must not construct a client"
            )
        ),
    )

    async def operation(_client: httpx.AsyncClient) -> None:
        pytest.fail("operation must not run")

    with pytest.raises(Exception):
        await runtime.run_http("not-operator-target", operation)
    snapshot = pools.snapshot(OutboundPoolName.INTEROP)
    assert snapshot.started == 0
    assert snapshot.in_use == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_http_resource_creation_happens_before_interop_lease() -> None:
    """Client construction does not consume the lease capacity."""
    observations: list[tuple[str, int]] = []
    pools = _pools(1)

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        observations.append(
            (
                "factory",
                pools.snapshot(OutboundPoolName.INTEROP).in_use,
            )
        )
        return httpx.AsyncClient()

    runtime = InteropResourceRuntime(
        _registry(_a2a_target()),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(http=factory),
    )

    async def operation(_client: httpx.AsyncClient) -> int:
        observations.append(
            (
                "operation",
                pools.snapshot(OutboundPoolName.INTEROP).in_use,
            )
        )
        return 204

    assert await runtime.run_http("peer-a2a", operation) == 204
    assert observations == [("factory", 0), ("operation", 1)]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_stream_http_holds_interop_lease_until_generator_closes() -> (
    None
):
    """A streamed operation retains capacity after its first yielded item."""
    pools = _pools(1)
    runtime = InteropResourceRuntime(
        _registry(_a2a_target()),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(
            http=lambda *_args, **_kwargs: httpx.AsyncClient()
        ),
    )
    release = asyncio.Event()

    async def stream_operation(
        _client: httpx.AsyncClient,
    ) -> AsyncGenerator[str, None]:
        yield "chunk"
        await release.wait()

    stream = cast(
        AsyncGenerator[str, None],
        runtime.stream_http("peer-a2a", stream_operation),
    )
    assert await anext(stream) == "chunk"
    assert pools.snapshot(OutboundPoolName.INTEROP).in_use == 1

    second = asyncio.create_task(
        runtime.run_http("peer-a2a", lambda _client: _async_none())
    )
    for _ in range(100):
        if pools.snapshot(OutboundPoolName.INTEROP).waiting == 1:
            break
        await asyncio.sleep(0)
    else:
        await stream.aclose()
        await second
        await runtime.aclose()
        pytest.fail("second operation did not wait for the open stream")

    await stream.aclose()
    await second
    assert pools.snapshot(OutboundPoolName.INTEROP).in_use == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_first_initializations_share_interop_capacity() -> None:
    """Different first-use MCP sessions cannot initialize concurrently."""
    pools = _pools(1)
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    active_initializations = 0
    max_active_initializations = 0

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient()

    @asynccontextmanager
    async def stream_factory(
        url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncGenerator[tuple[object, object, Callable[[], None]], None]:
        del http_client, terminate_on_close
        target_id = "peer-a" if "peer-a." in url else "peer-b"
        yield SimpleNamespace(target_id=target_id), object(), lambda: None

    @asynccontextmanager
    async def session_context(
        read_stream: Any, *_args: Any, **_kwargs: Any
    ) -> AsyncGenerator[Any, None]:
        session = SimpleNamespace()

        async def initialize() -> None:
            nonlocal active_initializations, max_active_initializations
            active_initializations += 1
            max_active_initializations = max(
                max_active_initializations,
                active_initializations,
            )
            try:
                if read_stream.target_id == "peer-a":
                    first_started.set()
                    await release_first.wait()
                else:
                    second_started.set()
            finally:
                active_initializations -= 1

        session.initialize = initialize
        yield session

    runtime = InteropResourceRuntime(
        _registry(_http_target("peer-a"), _http_target("peer-b")),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(
            http=factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )

    first = asyncio.create_task(
        runtime.run_mcp("peer-a", lambda _session: _async_none())
    )
    await first_started.wait()
    second = asyncio.create_task(
        runtime.run_mcp("peer-b", lambda _session: _async_none())
    )
    for _ in range(100):
        if (
            second_started.is_set()
            or pools.snapshot(OutboundPoolName.INTEROP).waiting == 1
        ):
            break
        await asyncio.sleep(0)

    second_started_before_release = second_started.is_set()
    waiting_before_release = pools.snapshot(OutboundPoolName.INTEROP).waiting
    release_first.set()
    await asyncio.gather(first, second)
    await runtime.aclose()

    assert not second_started_before_release
    assert waiting_before_release == 1
    assert max_active_initializations == 1


@pytest.mark.asyncio
async def test_mcp_initialization_and_operation_use_separate_leases() -> None:
    """First use leases initialization and the later operation separately."""
    pools = _pools(1)
    observations: list[tuple[str, int]] = []

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        observations.append(
            ("factory", pools.snapshot(OutboundPoolName.INTEROP).in_use)
        )
        return httpx.AsyncClient()

    @asynccontextmanager
    async def stream_factory(
        _url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del http_client, terminate_on_close
        yield object(), object(), lambda: None

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        session = SimpleNamespace()

        async def initialize() -> None:
            observations.append(
                (
                    "initialize",
                    pools.snapshot(OutboundPoolName.INTEROP).in_use,
                )
            )

        session.initialize = initialize
        yield session

    runtime = InteropResourceRuntime(
        _registry(_http_target()),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(
            http=factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )

    async def operation(_session: Any) -> int:
        observations.append(
            ("operation", pools.snapshot(OutboundPoolName.INTEROP).in_use)
        )
        return 204

    assert await runtime.run_mcp("peer-http", operation) == 204
    assert await runtime.run_mcp("peer-http", operation) == 204
    assert observations == [
        ("factory", 0),
        ("initialize", 1),
        ("operation", 1),
        ("operation", 1),
    ]
    snapshot = pools.snapshot(OutboundPoolName.INTEROP)
    assert snapshot.started == 3
    assert snapshot.completed == 3
    assert snapshot.in_use == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_initialization_failure_rolls_back_under_lease() -> None:
    """Failed initialization closes every partial resource and its lease."""
    pools = _pools(1)
    events: list[str] = []

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient()
        original_close = client.aclose

        async def close() -> None:
            events.append("client-close")
            await original_close()

        setattr(client, "aclose", close)
        return client

    @asynccontextmanager
    async def stream_factory(
        _url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del http_client, terminate_on_close
        events.append("stream-open")
        try:
            yield object(), object(), lambda: None
        finally:
            events.append("stream-close")

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        events.append("session-open")
        session = SimpleNamespace()

        async def initialize() -> None:
            assert pools.snapshot(OutboundPoolName.INTEROP).in_use == 1
            events.append("initialize")
            raise httpx.ConnectError("initialization failed")

        session.initialize = initialize
        try:
            yield session
        finally:
            events.append("session-close")

    runtime = InteropResourceRuntime(
        _registry(_http_target()),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(
            http=factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )

    with pytest.raises(httpx.ConnectError):
        await runtime.run_mcp("peer-http", lambda _session: _async_none())

    assert events == [
        "stream-open",
        "session-open",
        "initialize",
        "session-close",
        "stream-close",
        "client-close",
    ]
    assert runtime.keys == frozenset()
    assert runtime.http_keys == frozenset()
    snapshot = pools.snapshot(OutboundPoolName.INTEROP)
    assert snapshot.started == 1
    assert snapshot.failed == 1
    assert snapshot.in_use == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_initialization_cancellation_rolls_back_under_lease() -> (
    None
):
    """Cancelled initialization closes state before returning capacity."""
    pools = _pools(1)
    initialized = asyncio.Event()
    closed: list[str] = []

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient()
        original_close = client.aclose

        async def close() -> None:
            closed.append("client")
            await original_close()

        setattr(client, "aclose", close)
        return client

    @asynccontextmanager
    async def stream_factory(
        _url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del http_client, terminate_on_close
        try:
            yield object(), object(), lambda: None
        finally:
            closed.append("stream")

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        session = SimpleNamespace()

        async def initialize() -> None:
            initialized.set()
            await asyncio.Event().wait()

        session.initialize = initialize
        try:
            yield session
        finally:
            closed.append("session")

    runtime = InteropResourceRuntime(
        _registry(_http_target()),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(
            http=factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )
    task = asyncio.create_task(
        runtime.run_mcp("peer-http", lambda _session: _async_none())
    )
    await initialized.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed == ["session", "stream", "client"]
    assert runtime.keys == frozenset()
    assert runtime.http_keys == frozenset()
    snapshot = pools.snapshot(OutboundPoolName.INTEROP)
    assert snapshot.cancelled == 1
    assert snapshot.in_use == 0
    await runtime.aclose()


def _fake_session() -> SimpleNamespace:
    """Build a minimal initialized session accepted by the runtime seam."""
    session = SimpleNamespace(initialized=0)

    async def initialize() -> None:
        """Record successful MCP initialization."""
        session.initialized += 1

    session.initialize = initialize
    return session


@pytest.mark.asyncio
async def test_mcp_session_reused_and_closed_after_transport_context() -> None:
    """MCP HTTP reuses one session and closes it before its HTTP client."""
    stream_opened = 0
    stream_closed = 0
    session_opened = 0
    session_closed = 0
    clients_closed = 0

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, request=request)
            )
        )
        original_close = client.aclose

        async def close() -> None:
            nonlocal clients_closed
            clients_closed += 1
            await original_close()

        setattr(client, "aclose", close)
        return client

    @asynccontextmanager
    async def stream_factory(
        _url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del http_client, terminate_on_close
        nonlocal stream_opened, stream_closed
        stream_opened += 1
        try:
            yield object(), object(), lambda: None
        finally:
            stream_closed += 1

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        nonlocal session_opened, session_closed
        session_opened += 1
        session = _fake_session()
        try:
            yield session
        finally:
            session_closed += 1

    runtime = InteropResourceRuntime(
        _registry(_http_target()),
        _sensitive(),
        _pools(),
        factories=InteropResourceFactories(
            http=factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )

    async def use(session: Any) -> int:
        return session.initialized

    assert await runtime.run_mcp("peer-http", use) == 1
    assert await runtime.run_mcp("peer-http", use) == 1
    assert stream_opened == session_opened == 1
    assert runtime.keys == {("peer-http", "streamable_http")}
    await runtime.aclose()
    assert stream_closed == session_closed == 1
    assert clients_closed == 1


@pytest.mark.asyncio
async def test_mcp_stdio_resource_uses_allowlisted_environment() -> None:
    """Stdio resource construction uses only the target's env allowlist."""
    params_seen: list[StdioServerParameters] = []
    session = _fake_session()

    @asynccontextmanager
    async def stdio_factory(
        params: StdioServerParameters,
    ) -> AsyncIterator[tuple[object, object]]:
        params_seen.append(params)
        yield object(), object()

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        yield session

    runtime = InteropResourceRuntime(
        _registry(_stdio_target()),
        _sensitive(),
        _pools(),
        factories=InteropResourceFactories(
            stdio=stdio_factory,
            session=session_context,
        ),
    )
    await runtime.run_mcp("peer-stdio", lambda _: _async_none())
    assert params_seen[0].command == "/opt/phytomni-peer"
    assert params_seen[0].args == ["--mode", "stdio"]
    assert set(params_seen[0].env or {}) <= {"LANG", "PATH"}
    await runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_transport_failure_evicts_shared_http_client() -> None:
    """A failed MCP open closes its HTTP client before rebuilding."""
    constructed = 0
    closed = 0

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        nonlocal constructed
        constructed += 1
        client = httpx.AsyncClient()
        original_close = client.aclose

        async def close() -> None:
            nonlocal closed
            closed += 1
            await original_close()

        setattr(client, "aclose", close)
        return client

    stream_attempts = 0

    @asynccontextmanager
    async def stream_factory(
        _url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del http_client, terminate_on_close
        nonlocal stream_attempts
        stream_attempts += 1
        if stream_attempts == 1:
            raise httpx.ConnectError("closed")
        yield object(), object(), lambda: None

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        yield _fake_session()

    runtime = InteropResourceRuntime(
        _registry(_http_target()),
        _sensitive(),
        _pools(),
        factories=InteropResourceFactories(
            http=factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )
    with pytest.raises(httpx.ConnectError):
        await runtime.run_mcp("peer-http", lambda _session: _async_none())
    assert runtime.http_keys == frozenset()
    assert closed == 1
    await runtime.run_mcp("peer-http", lambda _session: _async_none())
    assert constructed == 2
    await runtime.aclose()
    assert closed == 2


def _mcp_eviction_runtime(
    constructed: list[httpx.AsyncClient],
) -> InteropResourceRuntime:
    """Build a streamable-MCP runtime for the eviction race test."""

    def factory(_target_id: str, **_: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient()
        original_close = client.aclose

        async def close() -> None:
            await original_close()

        setattr(client, "aclose", close)
        constructed.append(client)
        return client

    @asynccontextmanager
    async def stream_factory(
        _url: str,
        *,
        http_client: httpx.AsyncClient,
        terminate_on_close: bool,
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del http_client, terminate_on_close
        yield object(), object(), lambda: None

    @asynccontextmanager
    async def session_context(
        *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[Any]:
        yield _fake_session()

    runtime = InteropResourceRuntime(
        _registry(_http_target()),
        _sensitive(),
        _pools(1),
        factories=InteropResourceFactories(
            http=factory,
            streamable=stream_factory,
            session=session_context,
        ),
    )
    return runtime


@pytest.mark.asyncio
async def test_mcp_eviction_lock_prevents_reuse_of_closing_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement MCP session waits until its shared client is evicted."""
    constructed: list[httpx.AsyncClient] = []
    runtime = _mcp_eviction_runtime(constructed)
    await runtime.run_mcp("peer-http", lambda _session: _async_none())
    resource = next(
        iter(getattr(getattr(runtime, "_state"), "mcp_resources").values())
    )
    original_evict = getattr(runtime, "_evict_http")
    eviction_started = asyncio.Event()
    release_eviction = asyncio.Event()

    async def controlled_evict(
        target_id: str,
        client: httpx.AsyncClient,
    ) -> None:
        eviction_started.set()
        await release_eviction.wait()
        await original_evict(target_id, client)

    monkeypatch.setattr(runtime, "_evict_http", controlled_evict)
    evict_mcp = getattr(runtime, "_evict_mcp")
    get_mcp_resource = getattr(runtime, "_get_mcp_resource")
    evict_task = asyncio.create_task(evict_mcp("peer-http", resource))
    await eviction_started.wait()
    replacement_task = asyncio.create_task(get_mcp_resource("peer-http"))
    try:
        await asyncio.sleep(0.01)
        assert not replacement_task.done()
    finally:
        release_eviction.set()
        await evict_task
        replacement = await replacement_task
        replacement_client = replacement.http_client
        replacement_was_open = (
            replacement_client is not None and not replacement_client.is_closed
        )
        await runtime.aclose()

    assert len(constructed) == 2
    assert replacement.http_client is not resource.http_client
    assert replacement_client is not None
    assert replacement_was_open


async def _async_none() -> None:
    """Return a completed operation for session fixtures."""


@pytest.mark.asyncio
async def test_interop_capacity_and_cancellation_release() -> None:
    """The typed lease bounds concurrent work and releases on cancel."""
    pools = _pools(1)
    runtime = InteropResourceRuntime(
        _registry(_a2a_target()),
        _sensitive(),
        pools,
        factories=InteropResourceFactories(
            http=lambda *_args, **_kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(204, request=request)
                )
            )
        ),
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking(_client: httpx.AsyncClient) -> None:
        entered.set()
        await release.wait()

    first = asyncio.create_task(runtime.run_http("peer-a2a", blocking))
    await entered.wait()
    second = asyncio.create_task(runtime.run_http("peer-a2a", blocking))
    for _ in range(100):
        if pools.snapshot(OutboundPoolName.INTEROP).waiting == 1:
            break
        await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    snapshot = pools.snapshot(OutboundPoolName.INTEROP)
    assert snapshot.in_use == 1
    assert snapshot.waiting == 0
    release.set()
    await first
    await runtime.aclose()
