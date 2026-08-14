# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""OBS lifecycle matrix through the public operator relay routes."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from tests.support.http_fakes import open_asgi_client
from tests.support.outbound_fakes import (
    bounded_await,
    bounded_wait_for_event,
)
from tests.support.relay_fakes import build_relay_app, relay_key_factory

from mcp_server_phytomni.api.relay import obs as obs_route_module
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    OutboundPoolName,
)
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry

pytestmark = pytest.mark.server

_OWNER_ROOT = "agent_data/user_data/customer/runs/lifecycle/"
_OWNER_OBJECT = f"{_OWNER_ROOT}result.bin"


class _SdkReader:
    """Synchronous OBS body with deterministic EOF, error, and blocking."""

    def __init__(
        self,
        *chunks: bytes,
        failure: OSError | None = None,
        block_after_chunks: bool = False,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self._failure = failure
        self._block_after_chunks = block_after_chunks
        self._on_close = on_close
        self._release = threading.Event()
        self.close_count = 0

    def read(self, _size: int) -> bytes:
        """Return a chunk, then optionally fail or block until closed."""
        if self._chunks:
            return self._chunks.pop(0)
        if self._failure is not None:
            failure = self._failure
            self._failure = None
            raise failure
        if self._block_after_chunks:
            self._release.wait(timeout=5)
        return b""

    def close(self) -> None:
        """Release a blocked read and record exactly-once source closure."""
        self.close_count += 1
        self._release.set()
        if self._on_close is not None:
            self._on_close()


class _OperatorObsSdk:
    """Outer OBS SDK fake used behind the real owned runtime and routes."""

    def __init__(
        self,
        *,
        reader: _SdkReader | None = None,
        list_pages: list[Any] | None = None,
    ) -> None:
        self.reader = reader or _SdkReader(b"payload")
        self.list_pages = list(list_pages or [])
        self.calls: list[str] = []
        self.close_count = 0

    def __getattr__(self, name: str) -> Callable[..., Any]:
        operations = {
            "putContent": self._put_content,
            "getObjectMetadata": self._get_object_metadata,
            "getObject": self._get_object,
            "listObjects": self._list_objects,
        }
        try:
            return operations[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def _put_content(self, **kwargs: Any) -> Any:
        self.calls.append(
            "directory" if kwargs.get("content") is None else "upload"
        )
        return SimpleNamespace(status=200, requestId="request")

    def _get_object_metadata(self, **_kwargs: Any) -> Any:
        self.calls.append("metadata")
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(contentLength=7),
            requestId="request",
        )

    def _get_object(self, **_kwargs: Any) -> Any:
        self.calls.append("download")
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(response=self.reader),
            requestId="request",
        )

    def _list_objects(self, **_kwargs: Any) -> Any:
        self.calls.append("list")
        return SimpleNamespace(
            status=200,
            body=self.list_pages.pop(0),
            requestId="request",
        )

    def close(self) -> None:
        """Record process-owned SDK teardown."""
        self.close_count += 1


def _pools() -> OutboundPoolRegistry:
    """Return a registry with only one operator OBS slot."""
    return OutboundPoolRegistry(
        {
            name: (1 if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )


@asynccontextmanager
async def _installed_obs_runtime(
    monkeypatch: pytest.MonkeyPatch,
    sdk: _OperatorObsSdk,
) -> AsyncIterator[tuple[ObsClientRuntime, OutboundPoolRegistry]]:
    """Install one real OBS runtime behind the production route adapter."""
    pools = _pools()
    runtime = ObsClientRuntime(pools, sdk)
    monkeypatch.setattr(
        obs_route_module,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=runtime),
    )
    try:
        yield runtime, pools
    finally:
        await runtime.aclose()
        await pools.aclose()


def _relay_app_and_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FastAPI, str]:
    """Build the real relay router and one tenant-scoped OBS key."""
    monkeypatch.setenv(
        "PHYTOMNI_RELAY_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite")
    )
    key_factory = relay_key_factory(
        tmp_path,
        monkeypatch,
        user_id="customer",
    )
    return build_relay_app(), key_factory("obs")


def _started(pools: OutboundPoolRegistry) -> int:
    """Return the OBS pool's monotonic attempt count."""
    return pools.snapshot(OutboundPoolName.OBS).started


def _fast_eof_sdk() -> tuple[_OperatorObsSdk, asyncio.Event]:
    """Return a one-chunk SDK plus an event fired after source EOF closes."""
    source_closed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def signal_source_closed() -> None:
        loop.call_soon_threadsafe(source_closed.set)

    return (
        _OperatorObsSdk(
            reader=_SdkReader(b"first", on_close=signal_source_closed)
        ),
        source_closed,
    )


def _assert_fast_eof_completed(
    pools: OutboundPoolRegistry,
    before: Any,
    sdk: _OperatorObsSdk,
) -> None:
    """Assert the unchanged fast-EOF lease and source-close contract."""
    after = pools.snapshot(OutboundPoolName.OBS)
    assert after.started - before.started == 2
    assert after.completed - before.completed == 2
    assert after.failed - before.failed == 0
    assert after.in_use == 0
    assert sdk.reader.close_count == 1


async def test_each_operator_sdk_action_owns_exactly_one_obs_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload, directory, list pages, metadata, and download lease per call."""
    sdk = _OperatorObsSdk(
        reader=_SdkReader(b"payload"),
        list_pages=[
            SimpleNamespace(
                contents=[SimpleNamespace(key=f"{_OWNER_ROOT}a.txt")],
                is_truncated=True,
                next_marker=f"{_OWNER_ROOT}a.txt",
            ),
            SimpleNamespace(
                contents=[SimpleNamespace(key=f"{_OWNER_ROOT}b.txt")],
                is_truncated=False,
                next_marker=None,
            ),
        ],
    )
    app, key = _relay_app_and_key(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {key}"}

    async with (
        _installed_obs_runtime(monkeypatch, sdk) as (_runtime, pools),
        open_asgi_client(
            monkeypatch,
            app,
            base_url="http://relay.test",
        ) as client,
    ):
        before = _started(pools)
        upload = await client.put(
            "/v1/relay/obs/object",
            params={"path": _OWNER_OBJECT},
            headers=headers,
            content=b"payload",
        )
        assert upload.status_code == 200
        assert _started(pools) - before == 1

        before = _started(pools)
        directory = await client.put(
            "/v1/relay/obs/dir",
            params={"path": _OWNER_ROOT},
            headers=headers,
        )
        assert directory.status_code == 200
        assert _started(pools) - before == 1

        before = _started(pools)
        listing = await client.get(
            "/v1/relay/obs/list",
            params={"prefix": _OWNER_ROOT},
            headers=headers,
        )
        assert listing.status_code == 200
        assert listing.json()["keys"] == [
            f"{_OWNER_ROOT}a.txt",
            f"{_OWNER_ROOT}b.txt",
        ]
        assert _started(pools) - before == 2

        before = _started(pools)
        download = await client.get(
            "/v1/relay/obs/object",
            params={"path": _OWNER_OBJECT},
            headers=headers,
        )
        assert download.status_code == 200
        assert download.content == b"payload"
        assert _started(pools) - before == 2

        snapshot = pools.snapshot(OutboundPoolName.OBS)
        assert snapshot.in_use == 0
        assert snapshot.max_in_use == 1

    assert sdk.calls == [
        "upload",
        "directory",
        "list",
        "list",
        "metadata",
        "download",
    ]
    assert sdk.reader.close_count == 1
    assert sdk.close_count == 1


async def test_tenant_validation_rejects_before_any_operator_obs_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every operator OBS route validates tenant input before acquisition."""
    sdk = _OperatorObsSdk()
    app, key = _relay_app_and_key(tmp_path, monkeypatch)
    headers = {"Authorization": f"Bearer {key}"}
    foreign = "agent_data/user_data/other/runs/x/result.bin"

    async with (
        _installed_obs_runtime(monkeypatch, sdk) as (_runtime, pools),
        open_asgi_client(
            monkeypatch,
            app,
            base_url="http://relay.test",
        ) as client,
    ):
        responses = [
            await client.put(
                "/v1/relay/obs/object",
                params={"path": foreign},
                headers=headers,
                content=b"payload",
            ),
            await client.get(
                "/v1/relay/obs/object",
                params={"path": foreign},
                headers=headers,
            ),
            await client.get(
                "/v1/relay/obs/list",
                params={"prefix": "agent_data/user_data/other/runs/"},
                headers=headers,
            ),
            await client.put(
                "/v1/relay/obs/dir",
                params={"path": "agent_data/user_data/other/runs/x/"},
                headers=headers,
            ),
        ]

        assert [response.status_code for response in responses] == [
            403,
            403,
            403,
            403,
        ]
        assert _started(pools) == 0
        assert pools.snapshot(OutboundPoolName.OBS).in_use == 0
        assert not sdk.calls


async def _drive_download_asgi(
    app: FastAPI,
    key: str,
    pools: OutboundPoolRegistry,
    *,
    disconnect_after_first: bool = False,
    first_send_release: asyncio.Event | None = None,
) -> tuple[asyncio.Task[None], asyncio.Event, list[int]]:
    """Start one real ASGI download with controllable client termination."""
    request_sent = False
    disconnect = asyncio.Event()
    first_body = asyncio.Event()
    lease_samples: list[int] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/relay/obs/object",
        "raw_path": b"/v1/relay/obs/object",
        "query_string": urlencode({"path": _OWNER_OBJECT}).encode(),
        "root_path": "",
        "headers": [
            (b"host", b"relay.test"),
            (b"authorization", f"Bearer {key}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("relay.test", 80),
    }

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] != "http.response.body" or not message.get("body"):
            return
        lease_samples.append(pools.snapshot(OutboundPoolName.OBS).in_use)
        first_body.set()
        if disconnect_after_first:
            disconnect.set()
        if first_send_release is not None:
            await first_send_release.wait()

    task = asyncio.create_task(app(scope, receive, send))
    return task, first_body, lease_samples


async def test_operator_source_error_closes_once_and_records_failed_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider read error closes its source and fails the stream lease."""
    reader = _SdkReader(b"first", failure=OSError("provider read failed"))
    sdk = _OperatorObsSdk(reader=reader)
    app, key = _relay_app_and_key(tmp_path, monkeypatch)
    first_send_release = asyncio.Event()

    async with _installed_obs_runtime(monkeypatch, sdk) as (_runtime, pools):
        before = pools.snapshot(OutboundPoolName.OBS)
        task, first_body, samples = await _drive_download_asgi(
            app,
            key,
            pools,
            first_send_release=first_send_release,
        )
        try:
            await bounded_wait_for_event(first_body, task=task)
            assert samples == [1]
        finally:
            first_send_release.set()
            (result,) = await bounded_await(
                asyncio.gather(task, return_exceptions=True)
            )
        assert isinstance(result, BaseException)
        after = pools.snapshot(OutboundPoolName.OBS)

        assert after.started - before.started == 2
        assert after.completed - before.completed == 1
        assert after.failed - before.failed == 1
        assert after.in_use == 0
        assert reader.close_count == 1


async def test_operator_fast_eof_holds_lease_until_response_send_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fast source EOF cannot release OBS while downstream send is open."""
    sdk, source_closed = _fast_eof_sdk()
    first_send_release = asyncio.Event()

    async with _installed_obs_runtime(monkeypatch, sdk) as (_runtime, pools):
        before = pools.snapshot(OutboundPoolName.OBS)
        task, first_body, samples = await _drive_download_asgi(
            *_relay_app_and_key(tmp_path, monkeypatch),
            pools,
            first_send_release=first_send_release,
        )

        try:
            await bounded_wait_for_event(first_body, task=task)
            await bounded_wait_for_event(source_closed, task=task)
            await asyncio.sleep(0.05)
            assert samples == [1]
            assert pools.snapshot(OutboundPoolName.OBS).in_use == 1
        finally:
            first_send_release.set()
            (result,) = await bounded_await(
                asyncio.gather(task, return_exceptions=True)
            )
        assert result is None

        _assert_fast_eof_completed(pools, before, sdk)


@pytest.mark.parametrize("termination", ["disconnect", "cancel"])
async def test_operator_consumer_termination_closes_source_once(
    termination: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect and caller cancellation close upstream before release."""
    reader = _SdkReader(b"first", block_after_chunks=True)
    sdk = _OperatorObsSdk(reader=reader)
    app, key = _relay_app_and_key(tmp_path, monkeypatch)

    async with _installed_obs_runtime(monkeypatch, sdk) as (_runtime, pools):
        before = pools.snapshot(OutboundPoolName.OBS)
        task, first_body, samples = await _drive_download_asgi(
            app,
            key,
            pools,
            disconnect_after_first=termination == "disconnect",
        )
        await bounded_wait_for_event(first_body, task=task)
        assert samples == [1]
        assert pools.snapshot(OutboundPoolName.OBS).in_use == 1
        if termination == "cancel":
            task.cancel()
        (result,) = await bounded_await(
            asyncio.gather(task, return_exceptions=True)
        )
        if termination == "cancel":
            assert isinstance(result, asyncio.CancelledError)
        else:
            assert result is None

        after = pools.snapshot(OutboundPoolName.OBS)
        assert after.started - before.started == 2
        assert after.completed - before.completed == 1
        assert after.failed - before.failed == 0
        assert after.cancelled - before.cancelled == 1
        assert (
            after.completed
            + after.failed
            + after.cancelled
            - before.completed
            - before.failed
            - before.cancelled
            == after.started - before.started
        )
        assert after.in_use == 0
        assert after.waiting == 0
        assert reader.close_count == 1
