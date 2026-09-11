# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the finite, thread-bound OBS client runtime."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import anyio
import pytest
from tests.support.logging_helpers import capture_non_propagating_logger

from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    ObsProfileName,
    OutboundPoolName,
)
from mcp_server_phytomni.runtime.outbound.obs import build_obs_client_runtime
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry

pytestmark = pytest.mark.unit


def _pools(*, obs_capacity: int = 1) -> OutboundPoolRegistry:
    """Build a registry with only the OBS capacity under test."""
    return OutboundPoolRegistry(
        {
            name: (obs_capacity if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )


class _RecordingObsClient:
    """Synchronous fake that exposes thread and close behavior."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.thread_ids: list[int] = []
        self.calls = 0
        self.close_count = 0

    def call(self, value: str) -> str:
        """Block one SDK-shaped operation until the test releases it."""
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        self.entered.set()
        if value == "hold":
            self.release.wait(timeout=5)
        return value

    def close(self) -> None:
        """Record exactly one synchronous SDK close."""
        self.close_count += 1


async def _wait_thread_event(event: threading.Event) -> None:
    """Wait for a blocking fake signal without blocking the loop."""
    for _ in range(500):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("OBS worker did not start")


@pytest.mark.asyncio
async def test_runtime_reuses_one_profile_and_serializes_sdk_calls() -> None:
    """One primary client is reused and the OBS pool bounds SDK calls."""
    client = _RecordingObsClient()
    pools = _pools(obs_capacity=1)
    runtime = ObsClientRuntime(pools, client)

    first = asyncio.create_task(
        runtime.run(
            ObsProfileName.PRIMARY,
            lambda owned: owned.call("hold"),
        )
    )
    await _wait_thread_event(client.entered)
    second = asyncio.create_task(
        runtime.run(
            ObsProfileName.PRIMARY,
            lambda owned: owned.call("second"),
        )
    )
    for _ in range(500):
        if pools.snapshot(OutboundPoolName.OBS).waiting == 1:
            break
        await asyncio.sleep(0.01)
    else:
        client.release.set()
        await first
        await second
        raise AssertionError("second OBS operation did not queue")

    snapshot = pools.snapshot(OutboundPoolName.OBS)
    assert snapshot.in_use == 1
    assert snapshot.waiting == 1
    assert client.calls == 1

    client.release.set()
    assert await first == "hold"
    assert await second == "second"
    snapshot = pools.snapshot(OutboundPoolName.OBS)
    assert snapshot.in_use == 0
    assert snapshot.started == 2
    assert snapshot.completed == 2
    assert client.calls == 2
    assert len(client.thread_ids) == 2

    await runtime.aclose()
    await runtime.aclose()
    assert client.close_count == 1


@pytest.mark.asyncio
async def test_cancellation_waits_for_thread_before_releasing_lease() -> None:
    """Cancellation cannot return OBS capacity while the SDK thread runs."""
    client = _RecordingObsClient()
    pools = _pools(obs_capacity=1)
    runtime = ObsClientRuntime(pools, client)
    task = asyncio.create_task(
        runtime.run(
            ObsProfileName.PRIMARY,
            lambda owned: owned.call("hold"),
        )
    )
    await _wait_thread_event(client.entered)

    task.cancel()
    await asyncio.sleep(0)
    snapshot = pools.snapshot(OutboundPoolName.OBS)
    assert snapshot.in_use == 1
    assert snapshot.cancelled == 0

    client.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    snapshot = pools.snapshot(OutboundPoolName.OBS)
    assert snapshot.in_use == 0
    assert snapshot.cancelled == 1
    await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_worker", [False, True])
async def test_repeated_cancellation_owns_worker_until_completion(
    fail_worker: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeated cancellation cannot release an active SDK lease or log data."""
    client = _RecordingObsClient()
    pools = _pools()
    runtime = ObsClientRuntime(pools, client)

    def operation(owned: _RecordingObsClient) -> str:
        result = owned.call("hold")
        if fail_worker:
            raise OSError("synthetic-provider-secret")
        return result

    task = asyncio.create_task(runtime.run(ObsProfileName.PRIMARY, operation))
    try:
        await _wait_thread_event(client.entered)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert pools.snapshot(OutboundPoolName.OBS).in_use == 1
        client.release.set()
        with (
            capture_non_propagating_logger(
                "mcp_server_phytomni.runtime.async_utils", caplog.handler
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await task
        assert pools.snapshot(OutboundPoolName.OBS).in_use == 0
        await asyncio.sleep(0)
        assert "synthetic-provider-secret" not in caplog.text
        messages = [record.getMessage() for record in caplog.records]
        assert messages.count(
            "owned task failed operation=obs_run exception=OSError"
        ) == int(fail_worker)
        assert all(record.exc_info is None for record in caplog.records)
    finally:
        client.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


@pytest.mark.asyncio
async def test_worker_failure_propagates_without_cancellation() -> None:
    """The operation owner still receives ordinary SDK failures."""
    runtime = ObsClientRuntime(_pools(), _RecordingObsClient())

    def fail(_client: _RecordingObsClient) -> str:
        raise OSError("synthetic-provider-secret")

    try:
        with pytest.raises(OSError, match="synthetic-provider-secret"):
            await runtime.run(ObsProfileName.PRIMARY, fail)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_anyio_cancellation_holds_lease_until_worker_finishes() -> None:
    """Level cancellation cannot interrupt the owned worker drain."""
    client = _RecordingObsClient()
    pools = _pools()
    runtime = ObsClientRuntime(pools, client)

    async def consume() -> None:
        with pytest.raises(asyncio.CancelledError):
            await runtime.run(
                ObsProfileName.PRIMARY, lambda owned: owned.call("hold")
            )

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(consume)
            await _wait_thread_event(client.entered)
            group.cancel_scope.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.sleep(0.02)
                assert pools.snapshot(OutboundPoolName.OBS).in_use == 1
                client.release.set()
        assert pools.snapshot(OutboundPoolName.OBS).in_use == 0
    finally:
        client.release.set()
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count", [0, 1, 2])
async def test_close_failure_is_owned_through_cancellation(
    cancel_count: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cancelled close waits for its thread without logging SDK details."""
    client = _RecordingObsClient()
    runtime = ObsClientRuntime(_pools(), client)

    def fail_close() -> None:
        client.close_count += 1
        client.entered.set()
        client.release.wait(timeout=5)
        raise OSError("synthetic-provider-secret")

    monkeypatch.setattr(client, "close", fail_close)
    task = asyncio.create_task(runtime.aclose())
    try:
        await _wait_thread_event(client.entered)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        client.release.set()
        expected = asyncio.CancelledError if cancel_count else OSError
        with (
            capture_non_propagating_logger(
                "mcp_server_phytomni.runtime.async_utils", caplog.handler
            ),
            pytest.raises(expected),
        ):
            await task
        await asyncio.sleep(0)
        assert client.close_count == 1
        assert "synthetic-provider-secret" not in caplog.text
        messages = [record.getMessage() for record in caplog.records]
        assert messages.count(
            "owned task failed operation=obs_close exception=OSError"
        ) == int(bool(cancel_count))
        assert all(record.exc_info is None for record in caplog.records)
    finally:
        client.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_close_waits_for_active_thread_before_closing_client() -> None:
    """Shutdown never closes an OBS client while its worker is using it."""
    client = _RecordingObsClient()
    pools = _pools(obs_capacity=1)
    runtime = ObsClientRuntime(pools, client)
    operation = asyncio.create_task(
        runtime.run(
            ObsProfileName.PRIMARY,
            lambda owned: owned.call("hold"),
        )
    )
    await _wait_thread_event(client.entered)

    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    assert client.close_count == 0
    assert not closing.done()

    client.release.set()
    assert await operation == "hold"
    await closing
    assert client.close_count == 1


def test_startup_factory_receives_only_trusted_primary_profile() -> None:
    """The startup factory is called once with fixed config credentials."""
    captured: list[dict[str, Any]] = []
    client = _RecordingObsClient()

    def factory(**kwargs: Any) -> _RecordingObsClient:
        captured.append(kwargs)
        return client

    runtime = build_obs_client_runtime(
        ServerConfig(),
        _pools(obs_capacity=1),
        factory=factory,
    )
    assert runtime is not None
    assert len(captured) == 1
    assert captured[0]["server"] == ServerConfig().OBS_SERVER
