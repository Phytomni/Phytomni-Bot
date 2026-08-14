# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS download lifecycle tests through the public child RelayClient."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Self

import httpx
import pytest
from pydantic import SecretStr
from tests.support.outbound_fakes import (
    bounded_await,
    bounded_wait_for_event,
)

from mcp_server_phytomni.common.relay_client import RelayClient
from mcp_server_phytomni.runtime.outbound import OutboundPoolName

pytestmark = pytest.mark.unit


class _LifecycleByteStream(httpx.AsyncByteStream):
    """Expose deterministic entry, cancellation, and close observations."""

    def __init__(
        self,
        *chunks: bytes,
        release: asyncio.Event | None = None,
    ) -> None:
        self._chunks = chunks
        self._release = release
        self.entered = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.entered.set()
        if self._release is not None:
            await self._release.wait()
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        """Record transport-body closure exactly where HTTPX observes it."""
        self.close_count += 1


class _FailingSink:
    """Write one partial chunk, then emulate a destination filesystem error."""

    def __init__(
        self, managed_handle: Any, in_use: list[int], pools: Any
    ) -> None:
        self._managed_handle = managed_handle
        self._handle: BinaryIO | None = None
        self._in_use = in_use
        self._pools = pools

    def __enter__(self) -> Self:
        self._handle = self._managed_handle.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._managed_handle.__exit__(exc_type, exc_value, traceback)

    def write(self, chunk: bytes) -> int:
        """Persist a partial chunk before raising the boundary failure."""
        assert self._handle is not None
        self._handle.write(chunk)
        self._handle.flush()
        self._in_use.append(self._pools.snapshot(OutboundPoolName.OBS).in_use)
        raise OSError("destination write failed")


def _client() -> RelayClient:
    """Return a child relay client with a deterministic policy."""
    return RelayClient(
        base_url="https://relay.test",
        api_key=SecretStr("relay-key"),
        timeout=5.0,
        max_retries=0,
        retriable_codes=(),
    )


async def test_destination_failure_closes_response_and_removes_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
) -> None:
    """A failed local write releases OBS only after response closure."""
    stream = _LifecycleByteStream(b"partial")
    outbound_runtime.transport.enqueue(stream=stream)
    destination = tmp_path / "result.bin"
    pools = outbound_runtime.runtime.pools
    observed_in_use: list[int] = []
    real_open = Path.open

    def open_with_failure(
        path: Path, *args: Any, **kwargs: Any
    ) -> BinaryIO | _FailingSink:
        """Fail only the target file while preserving unrelated file opens."""
        if path != destination:
            return real_open(path, *args, **kwargs)
        return _FailingSink(
            closing(real_open(path, *args, **kwargs)),
            observed_in_use,
            pools,
        )

    monkeypatch.setattr(Path, "open", open_with_failure)

    with pytest.raises(OSError, match="destination write failed"):
        await _client().get_obs_object_to_path(
            "/obs/phytomni/agent_data/user_data/u/r/result.bin",
            destination,
            message="download failed",
        )

    assert observed_in_use == [1]
    assert stream.close_count == 1
    assert pools.snapshot(OutboundPoolName.OBS).in_use == 0
    assert not destination.exists()


async def test_cancellation_closes_response_and_removes_partial_file(
    tmp_path: Path,
    outbound_runtime: Any,
) -> None:
    """Caller cancellation closes the response before returning the lease."""
    release = asyncio.Event()
    stream = _LifecycleByteStream(b"unused", release=release)
    outbound_runtime.transport.enqueue(stream=stream)
    destination = tmp_path / "cancelled.bin"
    pools = outbound_runtime.runtime.pools
    task = asyncio.create_task(
        _client().get_obs_object_to_path(
            "/obs/phytomni/agent_data/user_data/u/r/cancelled.bin",
            destination,
            message="download failed",
        )
    )
    try:
        await bounded_wait_for_event(stream.entered, task=task)
        assert pools.snapshot(OutboundPoolName.OBS).in_use == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bounded_await(task)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await bounded_await(
            asyncio.gather(task, return_exceptions=True),
        )

    snapshot = pools.snapshot(OutboundPoolName.OBS)
    assert snapshot.in_use == 0
    assert snapshot.cancelled == 1
    assert stream.close_count == 1
    assert not destination.exists()
