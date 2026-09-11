# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Owned shutdown preserves errors without leaking late failure details."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import Iterator

import pytest
from tests.support.logging_helpers import capture_non_propagating_logger
from tests.support.outbound_fakes import RecordingResources

from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.runtime.outbound import (
    OutboundRuntimeStateError,
    aclose_outbound_runtime,
    current_outbound_runtime,
    init_outbound_runtime,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _capture_shutdown_logs(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    """Observe safe shared-task diagnostics without changing root logging."""
    with capture_non_propagating_logger(
        "mcp_server_phytomni.runtime.async_utils", caplog.handler
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["http", "process"])
@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_shutdown_failure_is_owned_and_observed(
    surface: str,
    cancel_waiter: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Propagate close failures without exposing details after cancellation."""
    resources = RecordingResources()
    runtime = await init_outbound_runtime(
        ServerConfig(), factories=resources.factories()
    )
    started = asyncio.Event()
    release = asyncio.Event()
    original_close = runtime.http.direct_upstream.aclose

    async def fail_close() -> None:
        started.set()
        await release.wait()
        await original_close()
        raise OSError("synthetic-provider-secret")

    monkeypatch.setattr(runtime.http.direct_upstream, "aclose", fail_close)
    close = (
        runtime.http.aclose if surface == "http" else aclose_outbound_runtime
    )
    waiter = asyncio.create_task(close())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        if cancel_waiter:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert "trusted" not in resources.closed
        release.set()
        if cancel_waiter:
            for _ in range(100):
                if "trusted" in resources.closed:
                    break
                await asyncio.sleep(0)
            assert "trusted" in resources.closed
        else:
            with pytest.raises(OSError, match="synthetic-provider-secret"):
                await waiter
        await asyncio.sleep(0)
        assert resources.closed.count("trusted") == 1
        assert resources.closed.count("direct_upstream") == 1
        if surface == "process":
            _done, pending = await asyncio.wait(
                {runtime.begin_close()}, timeout=1
            )
            assert not pending
            with pytest.raises(OutboundRuntimeStateError):
                current_outbound_runtime()
        gc.collect()
        await asyncio.sleep(0)
        assert "synthetic-provider-secret" not in caplog.text
        messages = [record.getMessage() for record in caplog.records]
        assert (
            messages.count(
                "owned task failed operation=http_shutdown exception=OSError"
            )
            == 1
        )
        if surface == "process":
            assert (
                messages.count(
                    "owned task failed operation=outbound_shutdown "
                    "exception=OSError"
                )
                == 1
            )
    finally:
        release.set()
        await asyncio.gather(waiter, return_exceptions=True)
        if surface == "http":
            with pytest.raises(OSError, match="synthetic-provider-secret"):
                await aclose_outbound_runtime()
