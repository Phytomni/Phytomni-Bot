# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""End-to-end OBS relay round trip: child RelayClient -> ASGI relay -> ops.

Drives the real ``RelayClient`` against the in-process relay app over an
``ASGITransport`` with the OBS SDK faked at the ``obs_relay_ops`` seam, so
an upload / list / download contract regression across the client, route,
tenant guard, and ops layers is caught (the per-layer unit tests cannot).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp.shared.exceptions import McpError
from pydantic import SecretStr

from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.relay import obs as obs_route_module
from mcp_server_phytomni.api.relay.routes import create_relay_router
from mcp_server_phytomni.common import relay_client as rc
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    OutboundPoolName,
)
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry

pytestmark = pytest.mark.server

# Captured before the autouse ``block_external_http`` fixture swaps
# ``httpx.AsyncClient.request``; restored per test so the in-process ASGI
# transport runs instead of raising the offline guard.
_REAL_REQUEST = httpx.AsyncClient.request

_OWNER_KEY = "agent_data/user_data/customer/runs/x/out.txt"
_OWNER_PREFIX = "agent_data/user_data/customer/runs/x/"
_GENE_MD_KEY = "gene-examples/md/AT1G01010_result.md"
_GENE_IMAGE_KEY = "gene-examples/img/AT1G01010/AT1G01010_network.png"


@pytest.fixture(autouse=True)
async def _install_operator_obs_runtime(
    fake_obs_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Bind route SDK calls to the fixture's one operator-owned fake."""

    pools = OutboundPoolRegistry(
        {
            name: (1 if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )
    obs_runtime = ObsClientRuntime(pools, fake_obs_client())

    monkeypatch.setattr(
        obs_route_module,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=obs_runtime),
    )
    try:
        yield
    finally:
        await obs_runtime.aclose()
        await pools.aclose()


@pytest.fixture(autouse=True)
def _relay_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the relay app at temp key + audit stores with relay enabled."""
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_RELAY_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite")
    )


@pytest.fixture(name="relay_app")
def _relay_app_fixture() -> FastAPI:
    """Return a FastAPI app mounting the relay router."""
    app = FastAPI()
    app.include_router(create_relay_router())
    return app


@pytest.fixture(name="owner_key")
def _owner_key_fixture(tmp_path) -> str:
    """Mint a relay:obs key bound to the ``customer`` tenant."""
    store = ApiKeyStore(str(tmp_path / "keys.sqlite"))
    return store.create(user_id="customer", scopes=["relay:obs"]).api_key


def _relay_client(
    app: FastAPI, api_key: str, monkeypatch: pytest.MonkeyPatch
) -> rc.RelayClient:
    """Return a RelayClient with a runtime-owned ASGI request profile."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_REQUEST)

    class _RequestClient:
        """Drive one buffered or streamed request through ASGI."""

        async def request(
            self, method: str, url: str, **kwargs: Any
        ) -> httpx.Response:
            """Run one buffered ASGI request."""
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app)
            ) as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response

        @contextlib.asynccontextmanager
        async def stream(
            self, method: str, url: str, **kwargs: Any
        ) -> AsyncIterator[httpx.Response]:
            """Open one streamed ASGI request."""
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app)
                ) as client,
                client.stream(method, url, **kwargs) as response,
            ):
                yield response

    class _HttpRuntime:
        """Return the ASGI profile for every child relay service pool."""

        def for_pool(
            self, _pool: object, *, profile: object = None
        ) -> _RequestClient:
            """Return the in-process request profile."""
            del profile
            return _RequestClient()

        @contextlib.asynccontextmanager
        async def stream(
            self,
            _pool: object,
            method: str,
            url: str,
            *,
            profile: object = None,
            **kwargs: Any,
        ) -> AsyncIterator[httpx.Response]:
            """Return one in-process streamed response."""
            del profile
            async with _RequestClient().stream(
                method, url, **kwargs
            ) as response:
                yield response

    runtime = SimpleNamespace(http=_HttpRuntime())
    monkeypatch.setattr(rc, "current_outbound_runtime", lambda: runtime)
    return rc.RelayClient(
        base_url="http://relay.test",
        api_key=SecretStr(api_key),
        timeout=5.0,
        max_retries=0,
        retriable_codes=(),
    )


async def test_obs_relay_round_trip(
    relay_app: FastAPI,
    owner_key: str,
    fake_obs_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload, tenant-scoped list, and download flow through the relay."""
    fake_obs_client.objects[_OWNER_KEY] = b"RESULT-BYTES"
    fake_obs_client.pages = [
        SimpleNamespace(
            contents=[SimpleNamespace(key=_OWNER_KEY)],
            is_truncated=False,
            next_marker=None,
        )
    ]
    client = _relay_client(relay_app, owner_key, monkeypatch)

    uploaded = await client.put_obs_object(
        f"/obs/phytomni/{_OWNER_KEY}", b"RESULT-BYTES", message="up"
    )
    assert uploaded["obs_path"].endswith(_OWNER_KEY)

    keys = await client.get_obs_list(_OWNER_PREFIX, message="ls")
    assert _OWNER_KEY in keys

    data = await client.get_obs_object(
        f"/obs/phytomni/{_OWNER_KEY}", message="dl"
    )
    assert data == b"RESULT-BYTES"


async def test_obs_relay_rejects_foreign_tenant_end_to_end(
    relay_app: FastAPI,
    owner_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A foreign-tenant download is refused end to end (403 -> McpError).

    The tenant guard rejects the path before any OBS op runs, so the
    SDK fake is unnecessary here.
    """
    client = _relay_client(relay_app, owner_key, monkeypatch)

    with pytest.raises(McpError):
        await client.get_obs_object(
            "/obs/phytomni/agent_data/user_data/other/runs/x/r.cif",
            message="dl",
        )


async def test_gene_example_catalog_reads_end_to_end(
    relay_app: FastAPI,
    owner_key: str,
    fake_obs_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Curated list and reads work through the child RelayClient boundary."""
    fake_obs_client.objects[_GENE_MD_KEY] = b"# AT1G01010"
    fake_obs_client.objects[_GENE_IMAGE_KEY] = b"PNG"
    fake_obs_client.pages = [
        SimpleNamespace(
            contents=[SimpleNamespace(key=_GENE_MD_KEY)],
            is_truncated=False,
            next_marker=None,
        )
    ]
    client = _relay_client(relay_app, owner_key, monkeypatch)

    assert await client.get_obs_list(
        "gene-examples/md/", message="gene-list"
    ) == [_GENE_MD_KEY]
    assert (
        await client.get_obs_object(_GENE_MD_KEY, message="gene-md")
        == b"# AT1G01010"
    )
    assert (
        await client.get_obs_object(_GENE_IMAGE_KEY, message="gene-image")
        == b"PNG"
    )

    with pytest.raises(McpError):
        await client.put_obs_object(
            _GENE_MD_KEY, b"forbidden", message="gene-write"
        )
