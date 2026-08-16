# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for fixed outbound HTTP profiles and attempt-scoped leases."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.support.logging_helpers import capture_non_propagating_logger
from tests.support.outbound_fakes import (
    ControlledByteStream,
    QueueTransport,
    RecordingResources,
    recording_outbound_runtime,
)

from mcp_server_phytomni.common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
)
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.runtime import cleanup as cleanup_runtime
from mcp_server_phytomni.runtime.outbound import (
    OutboundHttpProfile,
    OutboundPoolName,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _attach_cleanup_log_handler(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    """Capture cleanup logs after package logging disables propagation."""
    with capture_non_propagating_logger(
        cleanup_runtime.__name__, caplog.handler
    ):
        yield


_CLOSE_FAILURE_DETAIL = "synthetic-provider-secret"


def _raise_secret_on_close() -> None:
    """Raise the redacted close-path secret for the cleanup warning test."""
    raise RuntimeError(_CLOSE_FAILURE_DETAIL)


class _CancellationResistantCloseStream(ControlledByteStream):
    """Ignore cancellation until a test-controlled release arrives."""

    def __init__(
        self,
        *chunks: bytes,
        failure: Exception | None = None,
    ) -> None:
        super().__init__(*chunks, failure=failure)
        self._close_started = asyncio.Event()
        self._release_close = asyncio.Event()

    async def wait_until_close_started(self) -> None:
        """Block until response close has entered the cancellation loop."""
        await self._close_started.wait()

    def release_close(self) -> None:
        """Allow the pending close handshake to finish."""
        self._release_close.set()

    async def wait_until_close_released(self) -> None:
        """Block until the test has released the close handshake."""
        await self._release_close.wait()

    async def aclose(self) -> None:
        self._close_started.set()
        while not self._release_close.is_set():
            try:
                await asyncio.wait_for(
                    self._release_close.wait(),
                    timeout=0.01,
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                continue
        await super().aclose()


def _config(**overrides: Any) -> ServerConfig:
    """Build the configured server with selected HTTP settings."""
    return ServerConfig(**overrides)


@pytest.mark.asyncio
async def test_profiles_preserve_tls_proxy_and_connection_limits() -> None:
    """Construct exactly the trusted and direct-upstream client profiles."""
    resources = RecordingResources()
    config = _config(
        TLS_VERIFY=False,
        HTTP_MAX_CONNECTIONS=37,
        HTTP_MAX_KEEPALIVE=19,
    )

    async with recording_outbound_runtime(
        config=config, resources=resources
    ) as runtime:
        assert runtime.http.trusted is not runtime.http.direct_upstream

    assert set(resources.constructed) == {
        "trusted",
        "direct_upstream",
        "obs",
    }
    trusted = resources.constructed["trusted"]
    direct = resources.constructed["direct_upstream"]
    assert trusted["verify"] is False
    assert "trust_env" not in trusted
    assert direct["verify"] is False
    assert direct["trust_env"] is False
    for kwargs in (trusted, direct):
        assert kwargs["timeout"] is None
        assert kwargs["limits"].max_connections == 37
        assert kwargs["limits"].max_keepalive_connections == 19


@pytest.mark.asyncio
async def test_typed_profiles_reuse_the_exact_owned_clients() -> None:
    """Profile selection never constructs a per-request HTTP client."""
    transport = QueueTransport()
    transport.enqueue(content=b"{}")
    transport.enqueue(content=b"{}")
    resources = RecordingResources(transport=transport)

    async with recording_outbound_runtime(
        config=_config(), resources=resources
    ) as runtime:
        trusted = runtime.http.for_pool(OutboundPoolName.RETRIEVAL)
        direct = runtime.http.for_pool(
            OutboundPoolName.SPA_FAQ,
            profile=OutboundHttpProfile.DIRECT_UPSTREAM,
        )
        assert trusted.profile_client is runtime.http.trusted
        assert direct.profile_client is runtime.http.direct_upstream
        await trusted.request("GET", "https://trusted.invalid")
        await direct.request("GET", "https://direct.invalid")

    assert set(resources.constructed) == {
        "trusted",
        "direct_upstream",
        "obs",
    }


@pytest.mark.asyncio
async def test_buffered_retry_releases_before_backoff_and_reuses_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every retry attempt leases independently on one shared client."""
    transport = QueueTransport()
    first_closed = asyncio.Event()
    transport.enqueue(
        status=503,
        stream=ControlledByteStream(b"busy", on_close=first_closed.set),
    )
    transport.enqueue(content=b'{"ok": true}')
    resources = RecordingResources(transport=transport)
    slept = asyncio.Event()
    continue_retry = asyncio.Event()

    async def backoff(_delay: float) -> None:
        slept.set()
        await continue_retry.wait()

    monkeypatch.setattr(
        "mcp_server_phytomni.common.http.asyncio",
        SimpleNamespace(sleep=backoff),
    )
    async with recording_outbound_runtime(
        config=_config(OUTBOUND_RETRIEVAL_CONCURRENCY=1),
        resources=resources,
    ) as runtime:
        client = runtime.http.for_pool(OutboundPoolName.RETRIEVAL)
        task = asyncio.create_task(
            post_json_with_retries(
                client,
                JsonPostRequest(url="https://upstream.invalid/query"),
                JsonPostRetry(
                    timeout=1.0,
                    max_retries=1,
                    retriable_codes=(503,),
                    message="failed",
                ),
            )
        )
        await asyncio.wait_for(first_closed.wait(), 1.0)
        await asyncio.wait_for(slept.wait(), 1.0)
        assert runtime.pools.snapshot(OutboundPoolName.RETRIEVAL).in_use == 0
        continue_retry.set()
        assert await task == {"ok": True}
        assert runtime.pools.snapshot(OutboundPoolName.RETRIEVAL).started == 2
        assert client.profile_client is runtime.http.trusted


@pytest.mark.asyncio
async def test_buffered_body_failure_holds_then_releases_lease() -> None:
    """A read failure cannot release a slot before body consumption ends."""
    entered = asyncio.Event()
    release = asyncio.Event()
    stream = ControlledByteStream(
        entered=entered,
        release=release,
        failure=httpx.ReadError("broken body"),
    )
    transport = QueueTransport()
    transport.enqueue(stream=stream)
    resources = RecordingResources(transport=transport)
    async with recording_outbound_runtime(
        config=_config(OUTBOUND_RETRIEVAL_CONCURRENCY=1),
        resources=resources,
    ) as runtime:
        client = runtime.http.for_pool(OutboundPoolName.RETRIEVAL)
        task = asyncio.create_task(
            client.request("GET", "https://upstream.invalid/body")
        )
        await entered.wait()
        assert runtime.pools.snapshot(OutboundPoolName.RETRIEVAL).in_use == 1
        release.set()
        with pytest.raises(httpx.ReadError, match="broken body"):
            await task
        assert runtime.pools.snapshot(OutboundPoolName.RETRIEVAL).in_use == 0
        assert stream.closed is True


@pytest.mark.asyncio
async def test_response_close_failure_preserves_body_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A response-close exception cannot replace the request body failure."""
    transport = QueueTransport()
    transport.enqueue(
        stream=ControlledByteStream(
            failure=httpx.ReadError("broken body"),
            on_close=_raise_secret_on_close,
        )
    )
    resources = RecordingResources(transport=transport)

    caplog.set_level(logging.WARNING, logger=cleanup_runtime.__name__)
    async with recording_outbound_runtime(
        config=_config(), resources=resources
    ) as runtime:
        client = runtime.http.for_pool(OutboundPoolName.RETRIEVAL)
        with pytest.raises(httpx.ReadError, match="broken body"):
            await client.request("GET", "https://upstream.invalid/body")

    assert "stream cleanup failed operation=response_close" in caplog.text
    assert "RuntimeError" in caplog.text
    assert _CLOSE_FAILURE_DETAIL not in caplog.text


@pytest.mark.asyncio
async def test_response_close_has_a_finite_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation-resistant response close cannot hang request cleanup."""
    stream = _CancellationResistantCloseStream(
        failure=httpx.ReadError("broken body")
    )
    transport = QueueTransport()
    transport.enqueue(stream=stream)
    resources = RecordingResources(transport=transport)

    monkeypatch.setattr(cleanup_runtime, "CLEANUP_TIMEOUT_SECONDS", 0.05)
    async with recording_outbound_runtime(
        config=_config(), resources=resources
    ) as runtime:
        client = runtime.http.for_pool(OutboundPoolName.RETRIEVAL)
        task = asyncio.create_task(
            client.request("GET", "https://upstream.invalid/body")
        )
        await stream.wait_until_close_started()
        asyncio.get_running_loop().call_later(0.2, stream.release_close)

        started_at = time.monotonic()
        with pytest.raises(httpx.ReadError, match="broken body"):
            await task
        assert time.monotonic() - started_at < 0.15

        await stream.wait_until_close_released()
        for _ in range(100):
            if stream.closed:
                break
            await asyncio.sleep(0)
        assert stream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["eof", "error", "cancel", "close"])
async def test_stream_lease_covers_every_termination_path(ending: str) -> None:
    """Streaming holds one slot until EOF, error, cancel, or explicit close."""
    entered = asyncio.Event()
    release = asyncio.Event()
    failure = httpx.ReadError("stream failed") if ending == "error" else None
    stream = ControlledByteStream(
        b"chunk",
        entered=entered,
        release=release,
        failure=failure,
    )
    transport = QueueTransport()
    transport.enqueue(stream=stream)
    resources = RecordingResources(transport=transport)

    async with recording_outbound_runtime(
        config=_config(OUTBOUND_OBS_CONCURRENCY=1), resources=resources
    ) as runtime:

        async def consume() -> None:
            async with runtime.http.stream(
                OutboundPoolName.OBS,
                "GET",
                "https://upstream.invalid/object",
            ) as response:
                if ending == "close":
                    await response.aclose()
                    return
                iterator = response.aiter_bytes()
                async for _chunk in iterator:
                    pass

        task = asyncio.create_task(consume())
        if ending == "close":
            for _ in range(100):
                if runtime.pools.snapshot(OutboundPoolName.OBS).in_use == 1:
                    break
                await asyncio.sleep(0)
            assert runtime.pools.snapshot(OutboundPoolName.OBS).in_use == 1
            await task
        else:
            await entered.wait()
            assert runtime.pools.snapshot(OutboundPoolName.OBS).in_use == 1
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif ending != "close":
            release.set()
            if ending == "error":
                with pytest.raises(httpx.ReadError, match="stream failed"):
                    await task
            else:
                await task
        assert runtime.pools.snapshot(OutboundPoolName.OBS).in_use == 0
        assert stream.closed is True


@pytest.mark.asyncio
async def test_request_after_runtime_close_fails_before_transport() -> None:
    """A previously bound client rejects attempts after runtime close."""
    transport = QueueTransport()
    transport.enqueue()
    resources = RecordingResources(transport=transport)
    async with recording_outbound_runtime(
        config=_config(), resources=resources
    ) as runtime:
        client = runtime.http.for_pool(OutboundPoolName.IAM)

    with pytest.raises(RuntimeError, match="closing"):
        await client.request("GET", "https://upstream.invalid/token")
    assert not transport.requests
