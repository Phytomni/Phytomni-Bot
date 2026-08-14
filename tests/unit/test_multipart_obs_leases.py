# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS lease matrix through the public bounded multipart adapter."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from tests.support.outbound_fakes import bounded_await

from mcp_server_phytomni.runtime.async_utils import wait_for_thread_future
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    OutboundPoolName,
)
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry
from mcp_server_phytomni.storage.multipart import (
    BoundedMultipartStorage,
    MultipartSession,
    MultipartStorageError,
    PartInput,
)
from mcp_server_phytomni.storage.obs_storage import ObsPathError

pytestmark = pytest.mark.unit


class _MultipartSdk:
    """Outer OBS SDK fake for every multipart provider action."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.close_count = 0

    def __getattr__(self, name: str) -> Callable[..., Any]:
        operations = {
            "initiateMultipartUpload": self._begin,
            "uploadPart": self._put_part,
            "completeMultipartUpload": self._complete,
            "abortMultipartUpload": self._abort,
            "getObjectMetadata": self._metadata,
        }
        try:
            return operations[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def _begin(self, **_kwargs: Any) -> Any:
        self.calls.append("begin")
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(uploadId="upload-1"),
        )

    def _put_part(self, **_kwargs: Any) -> Any:
        self.calls.append("part")
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(etag="etag-1"),
        )

    def _complete(self, **_kwargs: Any) -> Any:
        self.calls.append("complete")
        return SimpleNamespace(status=200)

    def _abort(self, **_kwargs: Any) -> Any:
        self.calls.append("abort")
        return SimpleNamespace(status=200)

    def _metadata(self, **_kwargs: Any) -> Any:
        self.calls.append("metadata")
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(contentLength=3),
        )

    def close(self) -> None:
        """Record process-owned SDK teardown."""
        self.close_count += 1


class _UnseekablePart(BytesIO):
    """Reject rewind before the adapter can acquire an OBS lease."""

    def seek(self, *_args: Any, **_kwargs: Any) -> int:
        raise OSError("not seekable")


def _pools() -> OutboundPoolRegistry:
    """Return a registry with one OBS slot and no other capacities."""
    return OutboundPoolRegistry(
        {
            name: (1 if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )


def _started(pools: OutboundPoolRegistry) -> int:
    """Return the OBS pool's monotonic attempt count."""
    return pools.snapshot(OutboundPoolName.OBS).started


async def _run_storage(
    executor: ThreadPoolExecutor,
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run one synchronous storage call through its production thread seam."""
    future = executor.submit(operation, *args, **kwargs)
    return await bounded_await(wait_for_thread_future(future))


async def test_each_multipart_sdk_action_owns_exactly_one_obs_lease() -> None:
    """Begin, part, complete, metadata, and abort each acquire once."""
    sdk = _MultipartSdk()
    pools = _pools()
    runtime = ObsClientRuntime(pools, sdk)
    storage = BoundedMultipartStorage(
        runtime=runtime,
        loop=asyncio.get_running_loop(),
    )
    executor = ThreadPoolExecutor(max_workers=1)
    content = b"abc"
    digest = hashlib.sha256(content).hexdigest()

    try:
        before = _started(pools)
        session = await _run_storage(
            executor,
            storage.begin,
            bucket="bucket",
            object_key="owner/file.bin",
        )
        assert _started(pools) - before == 1

        before = _started(pools)
        part = await _run_storage(
            executor,
            storage.put_part,
            session,
            PartInput(1, BytesIO(content), len(content), digest),
        )
        assert _started(pools) - before == 1

        before = _started(pools)
        completed = await _run_storage(
            executor,
            storage.complete,
            session,
            [part],
        )
        assert completed.byte_size == len(content)
        assert _started(pools) - before == 1

        before = _started(pools)
        reconciled = await _run_storage(
            executor,
            storage.reconcile_complete,
            session,
            [part],
        )
        assert reconciled is not None
        assert reconciled.byte_size == len(content)
        assert _started(pools) - before == 1

        before = _started(pools)
        await _run_storage(executor, storage.abort, session)
        assert _started(pools) - before == 1

        snapshot = pools.snapshot(OutboundPoolName.OBS)
        assert snapshot.in_use == 0
        assert snapshot.max_in_use == 1
        assert snapshot.completed == 5
        assert sdk.calls == [
            "begin",
            "part",
            "complete",
            "metadata",
            "abort",
        ]
    finally:
        executor.shutdown(wait=True)
        await runtime.aclose()
        await pools.aclose()

    assert sdk.close_count == 1


async def test_multipart_local_validation_precedes_obs_acquisition() -> None:
    """Invalid keys and part sources fail without touching the OBS pool."""
    sdk = _MultipartSdk()
    pools = _pools()
    runtime = ObsClientRuntime(pools, sdk)
    storage = BoundedMultipartStorage(
        runtime=runtime,
        loop=asyncio.get_running_loop(),
    )

    try:
        with pytest.raises(ObsPathError):
            storage.begin(
                bucket="bucket",
                object_key="/obs/other-bucket/file.bin",
            )
        with pytest.raises(
            MultipartStorageError,
            match="upload_storage_unavailable",
        ):
            storage.put_part(
                MultipartSession("bucket", "owner/file.bin", "upload-1"),
                PartInput(
                    1,
                    _UnseekablePart(b"abc"),
                    3,
                    hashlib.sha256(b"abc").hexdigest(),
                ),
            )

        snapshot = pools.snapshot(OutboundPoolName.OBS)
        assert snapshot.started == 0
        assert snapshot.in_use == 0
        assert not sdk.calls
    finally:
        await runtime.aclose()
        await pools.aclose()
