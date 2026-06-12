# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the OBS object relay routes (server-side SDK termination).

The OBS routes do not proxy an HTTP upstream; the parent runs its own
``ObsClient`` through ``storage/obs_relay_ops`` (mocked here). The routes
re-validate the client path, confine list to the server-owned output
root, audit metadata (never the binary body), and gate on ``relay:obs``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay.audit import RelayAuditStore
from mcp_server_phytomni.api.relay.routes import create_relay_router
from mcp_server_phytomni.storage import obs_relay_ops as ops_module
from mcp_server_phytomni.storage.obs_storage import ObsPathError

pytestmark = pytest.mark.server

_REAL_REQUEST = httpx.AsyncClient.request
_AUDIT_DB_ENV = "PHYTOMNI_RELAY_AUDIT_DB_PATH"


@pytest.fixture(autouse=True)
def _redirect_relay_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirect relay audit writes to a temp DB for every OBS route test."""
    monkeypatch.setenv(_AUDIT_DB_ENV, str(tmp_path / "relay_audit.sqlite"))


@pytest.fixture(name="relay_key")
def _relay_key_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str], str]:
    """Return a factory minting a key scoped for one relay service."""
    db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    store = ApiKeyStore(str(db))
    return lambda svc: store.create(
        user_id="customer", scopes=[f"relay:{svc}"]
    ).api_key


@pytest.fixture(name="client")
async def _client_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield an httpx client bound to the relay app over ASGI."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_REQUEST)
    app = FastAPI()
    app.include_router(create_relay_router())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://relay.test"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the per-key in-flight relay counter across tests."""
    monkeypatch.setattr(forward_module, "_INFLIGHT", {})


_OUTPUT_PREFIX = "agent_data/user_data/customer/runs/d/run_x/task/output/"


async def test_obs_put_object_writes_and_returns_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT writes the body at the validated key and returns the obs path."""
    fake = Mock(return_value="agent_data/uploads/customer/r/up/x.pdf")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/uploads/customer/r/up/x.pdf",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"file-bytes",
    )

    assert response.status_code == 200
    assert response.json()["obs_path"].startswith("/obs/")
    assert fake.call_args.args[2] == b"file-bytes"
    assert fake.call_args.kwargs["obs_server"]


async def test_obs_get_object_streams_under_budget(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET streams the object content in chunks as an octet-stream."""
    monkeypatch.setattr(ops_module, "object_size", Mock(return_value=8))
    monkeypatch.setattr(
        ops_module,
        "iter_object_chunks",
        Mock(return_value=iter([b"ATOM", b" 1 N"])),
    )

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/user_data/customer/runs/d/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.content == b"ATOM 1 N"
    assert response.headers["content-type"] == "application/octet-stream"


async def test_obs_get_object_rejects_over_budget(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An object larger than the response budget is a 413, never streamed."""
    monkeypatch.setenv("PHYTOMNI_RELAY_RESPONSE_MAX_BYTES", "16")
    monkeypatch.setattr(ops_module, "object_size", Mock(return_value=10**6))
    streamed = Mock(return_value=iter([b"x"]))
    monkeypatch.setattr(ops_module, "iter_object_chunks", streamed)

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/user_data/customer/runs/d/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 413
    assert not streamed.called


async def test_obs_list_returns_keys_under_output_root(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list returns keys for a prefix under the server-owned output root."""
    fake = Mock(
        return_value=[f"{_OUTPUT_PREFIX}a.png", f"{_OUTPUT_PREFIX}b.md"]
    )
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        f"/v1/relay/obs/list?prefix={_OUTPUT_PREFIX}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.json()["keys"] == [
        f"{_OUTPUT_PREFIX}a.png",
        f"{_OUTPUT_PREFIX}b.md",
    ]


async def test_obs_list_rejects_prefix_outside_output_root(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prefix not under the server output root is a 403 (no enumeration)."""
    fake = Mock(return_value=[])
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        "/v1/relay/obs/list?prefix=agent_data/secrets/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_dir_marker_creates_zero_byte_object(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT dir delegates to the dir-marker op and returns the path."""
    monkeypatch.setattr(
        ops_module, "put_dir_marker", Mock(return_value=_OUTPUT_PREFIX)
    )

    response = await client.put(
        f"/v1/relay/obs/dir?path={_OUTPUT_PREFIX}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.json()["obs_path"].startswith("/obs/")


async def test_obs_put_rejects_out_of_bucket_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An out-of-bucket path surfaces as a 400, not a 500."""
    monkeypatch.setattr(
        ops_module,
        "put_object_bytes",
        Mock(side_effect=ObsPathError("outside bucket")),
    )

    response = await client.put(
        "/v1/relay/obs/object?path=/obs/other-bucket/x",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"x",
    )

    assert response.status_code == 400


async def test_obs_route_requires_obs_scope(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key without relay:obs is rejected with 403 before any OBS op."""
    fake = Mock(return_value="k")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object?path=/obs/phytomni/agent_data/x",
        headers={"Authorization": f"Bearer {relay_key('llm')}"},
        content=b"x",
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_get_rejects_foreign_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-bucket path under another tenant's namespace is a 403."""
    fake = Mock(return_value=8)
    monkeypatch.setattr(ops_module, "object_size", fake)

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=agent_data/user_data/other-tenant/runs/x/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_put_accepts_own_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path under the caller's own tenant namespace is accepted."""
    fake = Mock(return_value="agent_data/user_data/customer/runs/x/out.txt")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=agent_data/user_data/customer/runs/x/out.txt",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"hi",
    )

    assert response.status_code == 200
    assert fake.called


async def test_obs_put_rejects_foreign_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing under another tenant's namespace is a 403, no op call."""
    fake = Mock(return_value="k")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=agent_data/user_data/other-tenant/runs/x/out.txt",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"hi",
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_dir_rejects_foreign_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Making a dir marker under another tenant's namespace is a 403."""
    fake = Mock(return_value="k")
    monkeypatch.setattr(ops_module, "put_dir_marker", fake)

    response = await client.put(
        "/v1/relay/obs/dir?path=agent_data/user_data/other-tenant/runs/x/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_list_rejects_foreign_tenant_prefix(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list prefix under another tenant's output root is a 403."""
    fake = Mock(return_value=[])
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        "/v1/relay/obs/list?prefix=agent_data/user_data/other-tenant/runs/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_upload_audit_records_metadata_not_binary(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit row carries path/size metadata but never the raw bytes."""
    monkeypatch.setattr(
        ops_module, "put_object_bytes", Mock(return_value="agent_data/x")
    )
    secret = b"TOP-SECRET-PLASMID-SEQUENCE"

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/user_data/customer/x",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=secret,
    )

    assert response.status_code == 200
    rows = RelayAuditStore(os.environ[_AUDIT_DB_ENV]).query()
    obs_rows = [row for row in rows if row.service == "obs"]
    assert obs_rows
    blob = (obs_rows[0].request_body or "") + (obs_rows[0].response_body or "")
    assert secret.decode() not in blob
    assert str(len(secret)) in blob
