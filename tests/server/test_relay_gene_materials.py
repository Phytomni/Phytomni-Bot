# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Authenticated HTTP acceptance for report-bound curated materials."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.types import Message
from tests.support.curated_gene import protocol_association
from tests.support.relay_fakes import (
    build_relay_app,
    make_relay_client_fixture,
    make_relay_key_fixture,
    make_relay_reset_fixture,
)

from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay import obs as obs_routes
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    OutboundPoolName,
)
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry

pytestmark = pytest.mark.server
_relay_key_fixture = make_relay_key_fixture(user_id="customer")
_client_fixture = make_relay_client_fixture(build_relay_app)
_reset_inflight = make_relay_reset_fixture(forward_module)

_GENE = "AT1G01010"
_REPORT = b"# Approved report\n"
_REVISION = hashlib.sha256(_REPORT).hexdigest()
_MANIFEST_KEY = f"gene-examples/manifests/{_GENE}_result.json"
_MATERIAL_KEY = f"gene-examples/materials/{_GENE}/{_REVISION}/protocol.md"
_CONTENT = b"# Experiment\nOriginal protocol.\n"


class _BlockingSource:
    """Expose observable source lifetime to the ASGI cancellation tests."""

    def __init__(self) -> None:
        """Bridge worker readiness to the running request event loop."""
        self.reading = threading.Event()
        self.closed = threading.Event()
        self.started = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.close_count = 0

    def read(self, _size: int) -> bytes:
        """Wait until cleanup actively interrupts the SDK body."""
        self.reading.set()
        self.loop.call_soon_threadsafe(self.started.set)
        assert self.closed.wait(3), "source was not closed on disconnect"
        return b""

    def close(self) -> None:
        """Record idempotent closure before the worker completes."""
        self.close_count += 1
        self.closed.set()


@pytest.fixture(name="catalog")
async def _catalog_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, bytes]]:
    """Exercise the real reader over a thread-bound operator SDK double."""
    manifest = {
        "schema_version": 1,
        "gene_id": _GENE,
        "report_file": f"{_GENE}_result.md",
        "report_sha256": _REVISION,
        "reference_count": 0,
        "reference_materials": [],
        "resources": [
            {
                **protocol_association(),
                "object_key": _MATERIAL_KEY,
                "size_bytes": len(_CONTENT),
                "sha256": hashlib.sha256(_CONTENT).hexdigest(),
            }
        ],
    }
    objects = {
        _MANIFEST_KEY: json.dumps(manifest).encode(),
        f"gene-examples/md/{_GENE}_result.md": _REPORT,
        _MATERIAL_KEY: _CONTENT,
    }

    class _Sdk:
        def metadata(self, **kwargs: Any) -> Any:
            """Only return metadata for explicitly installed objects."""
            raw = objects.get(kwargs["objectKey"])
            return SimpleNamespace(
                status=404 if raw is None else 200,
                body=SimpleNamespace(contentLength=len(raw or b"")),
            )

        def download(self, **kwargs: Any) -> Any:
            """Supply actual fixture bytes, not mocked HTTP responses."""
            raw = objects.get(kwargs["objectKey"])
            return SimpleNamespace(
                status=404 if raw is None else 200,
                body=SimpleNamespace(response=io.BytesIO(raw or b"")),
            )

    setattr(_Sdk, "getObjectMetadata", _Sdk.metadata)
    setattr(_Sdk, "getObject", _Sdk.download)
    pools = OutboundPoolRegistry(
        {
            name: (1 if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )
    runtime = ObsClientRuntime(pools, _Sdk())

    monkeypatch.setattr(
        obs_routes,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=runtime),
    )
    monkeypatch.setenv(
        "PHYTOMNI_RELAY_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite")
    )
    try:
        yield objects
    finally:
        await runtime.aclose()
        await pools.aclose()


@pytest.mark.parametrize(
    "key,media_type",
    [
        (_MANIFEST_KEY, "application/json"),
        (_MATERIAL_KEY, "text/markdown"),
    ],
)
async def test_authenticated_get_returns_verified_curated_bytes(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    catalog: dict[str, bytes],
    key: str,
    media_type: str,
) -> None:
    """The existing authenticated read route serves exactly declared bytes."""
    response = await client.get(
        "/v1/relay/obs/object",
        params={"path": key},
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )
    assert response.status_code == 200
    assert response.content == catalog[key]
    assert response.headers["content-type"].split(";")[0] == media_type
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "operation,path",
    [
        ("PUT", _MATERIAL_KEY),
        ("PUT", _MANIFEST_KEY),
        ("LIST", "gene-examples/materials/"),
        ("LIST", "gene-examples/manifests/"),
    ],
)
async def test_curated_read_grant_does_not_expand_other_operations(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    catalog: dict[str, bytes],
    operation: str,
    path: str,
) -> None:
    """A curated read never grants material enumeration or publication."""
    assert catalog
    route = "list" if operation == "LIST" else "object"
    field = "prefix" if operation == "LIST" else "path"
    response = await client.request(
        "GET" if operation == "LIST" else "PUT",
        f"/v1/relay/obs/{route}",
        params={field: path},
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )
    assert response.status_code == 403


async def test_material_errors_fail_closed_without_locator_leaks(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    catalog: dict[str, bytes],
) -> None:
    """Changed source bytes are an explicit conflict, not a successful body."""
    catalog[_MATERIAL_KEY] += b"wrong revision"
    response = await client.get(
        "/v1/relay/obs/object",
        params={"path": _MATERIAL_KEY},
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )
    assert response.status_code == 409
    assert _GENE not in response.text
    assert "gene-examples" not in response.text
    assert "wrong revision" not in response.text


async def test_curated_object_requires_relay_obs_scope(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    catalog: dict[str, bytes],
) -> None:
    """Shared catalog contents are not anonymous relay downloads."""
    assert catalog
    relay_key("llm")
    response = await client.get(
        "/v1/relay/obs/object", params={"path": _MATERIAL_KEY}
    )
    assert response.status_code == 401


@pytest.mark.parametrize("cancel_task", [False, True])
async def test_curated_disconnect_closes_source_before_runtime_release(
    monkeypatch: pytest.MonkeyPatch,
    relay_key: Callable[[str], str],
    cancel_task: bool,
) -> None:
    """Both HTTP disconnect and task cancellation drain the real OBS lease."""
    source = _BlockingSource()

    class _Sdk:
        def metadata(self, **_kwargs: Any) -> Any:
            """Keep the small fixture below every body bound."""
            return SimpleNamespace(
                status=200, body=SimpleNamespace(contentLength=1)
            )

        def download(self, **_kwargs: Any) -> Any:
            """Leave the initial manifest body blocked until cancellation."""
            return SimpleNamespace(
                status=200, body=SimpleNamespace(response=source)
            )

    setattr(_Sdk, "getObjectMetadata", _Sdk.metadata)
    setattr(_Sdk, "getObject", _Sdk.download)
    pools = OutboundPoolRegistry(
        {
            name: (1 if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )
    runtime = ObsClientRuntime(pools, _Sdk())
    monkeypatch.setattr(
        obs_routes,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=runtime),
    )

    async def receive() -> dict[str, str]:
        """Deliver an actual ASGI disconnect once the body is open."""
        if not cancel_task and source.reading.is_set():
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    statuses: list[int] = []

    async def send(message: Message) -> None:
        """Observe public ASGI status without recording authentication data."""
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    app = build_relay_app()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": "/v1/relay/obs/object",
        "root_path": "",
        "headers": [(b"authorization", f"Bearer {relay_key('obs')}".encode())],
        "query_string": f"path={_MANIFEST_KEY}".encode(),
        "client": ("127.0.0.1", 43210),
        "server": ("relay.test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        async with asyncio.timeout(3):
            await source.started.wait()
            if cancel_task:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
                assert statuses == [499]
        assert source.closed.is_set()
        assert source.close_count == 1
        assert (
            await runtime.run(
                obs_routes.ObsProfileName.PRIMARY,
                lambda _sdk: "lease available",
            )
            == "lease available"
        )
    finally:
        source.closed.set()
        if not task.done():
            task.cancel()
        await runtime.aclose()
        await pools.aclose()
