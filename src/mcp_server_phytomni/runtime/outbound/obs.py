# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Process-owned, thread-bound OBS SDK resources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from enum import StrEnum
from threading import Event, Thread
from typing import Any, TypeVar

from ...config.defaults import ServerConfig
from ...config.relay_mode import relay_mode_enabled
from ...config.settings import get_sensitive_config
from ...runtime.async_utils import wait_for_thread_future
from ...storage.obs_client import ObsClient
from .models import OutboundPoolName
from .registry import OutboundPoolRegistry

__all__ = [
    "ObsClientFactory",
    "ObsClientRuntime",
    "ObsProfileName",
    "build_obs_client_runtime",
]

type ObsClientFactory = Callable[..., Any]
T = TypeVar("T")


class ObsProfileName(StrEnum):
    """Finite OBS credential profiles owned by this process."""

    PRIMARY = "primary"


class ObsClientRuntime:
    """Lend one owned OBS client to one synchronous SDK operation."""

    def __init__(self, pools: OutboundPoolRegistry, client: Any) -> None:
        """Store the process-owned client and its logical pool registry."""
        self._pools = pools
        self._client = client
        # Keep synchronous SDK work off the event loop and out of the default
        # executor.  The latter is shared with unrelated application work and
        # is not a reliable completion boundary in every supported runner.
        self._executor = ThreadPoolExecutor()
        self._closing = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def run(
        self,
        profile: ObsProfileName,
        operation: Callable[[Any], T],
    ) -> T:
        """Run one SDK operation in a worker thread under the OBS lease.

        The worker future is polled through an asyncio task and awaited after
        caller cancellation so the logical lease is not returned while the
        synchronous SDK call is still using the shared client.
        """
        if profile is not ObsProfileName.PRIMARY:
            raise ValueError(f"unsupported OBS profile: {profile}")
        if self._closing:
            raise RuntimeError("OBS client runtime is closing")
        async with self._pools.lease(OutboundPoolName.OBS):
            worker = self._executor.submit(operation, self._client)

            async def await_worker() -> T:
                """Bridge a synchronous future without cancelling it."""
                return await wait_for_thread_future(worker)

            waiter = asyncio.create_task(await_worker())
            try:
                return await asyncio.shield(waiter)
            except asyncio.CancelledError:
                with suppress(BaseException):
                    await asyncio.shield(waiter)
                raise

    async def aclose(self) -> None:
        """Close the owned SDK client exactly once after pool drain."""
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            done = Event()
            errors: list[Exception] = []

            def close_in_thread() -> None:
                """Wait for SDK work, then close the client off-loop."""
                try:
                    self._executor.shutdown(wait=True)
                    self._close_sync()
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    errors.append(exc)
                finally:
                    done.set()

            Thread(target=close_in_thread, daemon=True).start()

            async def finish_close() -> None:
                """Publish completion after the worker thread has drained."""
                while not done.is_set():
                    try:
                        await asyncio.wait_for(
                            asyncio.Event().wait(), timeout=0.01
                        )
                    except TimeoutError:
                        continue
                if errors:
                    raise errors[0]
                self._closed = True

            waiter = asyncio.create_task(finish_close())
            try:
                await asyncio.shield(waiter)
            except asyncio.CancelledError:
                with suppress(BaseException):
                    await asyncio.shield(waiter)
                raise

    def _close_sync(self) -> None:
        """Close the synchronous SDK client when it exposes close."""
        close = getattr(self._client, "close", None)
        if close is not None:
            close()


def build_obs_client_runtime(
    config: ServerConfig,
    pools: OutboundPoolRegistry,
    *,
    factory: ObsClientFactory = ObsClient,
) -> ObsClientRuntime | None:
    """Build the finite primary OBS profile at trusted process startup."""
    if relay_mode_enabled():
        return None
    access_key, secret_key = get_sensitive_config().obs_credentials()
    client = factory(
        access_key_id=access_key,
        secret_access_key=secret_key,
        server=config.OBS_SERVER,
    )
    return ObsClientRuntime(pools, client)
