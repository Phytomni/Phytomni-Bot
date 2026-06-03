# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the platform-family relay routes (envelope mode).

retrieve/rerank/task inject no operator credential; database/analysis
inject an IAM X-Auth-Token; bi injects a static token header. All forward
the body verbatim to a config-resolved URL and map an upstream error to
the unified envelope.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay import routes as routes_module
from mcp_server_phytomni.api.relay.routes import create_relay_router
from mcp_server_phytomni.config.defaults import DeepGenomeConfig

pytestmark = pytest.mark.server


def test_platform_config_aggregates_every_endpoint() -> None:
    """The real config the platform routes read exposes every URL.

    The route tests mock DeepGenomeConfig, so this pins that the actual
    multiple-inheritance config exposes all seven platform URLs plus the
    analysis region the routes resolve by attribute name.
    """
    config = DeepGenomeConfig()

    for attr in (
        "RETRIEVE_URL",
        "RERANK_URL",
        "DATABASE_URL",
        "ANALYSIS_URL",
        "BI_URL",
        "CREATE_TASK_URL",
        "UPDATE_TASK_URL",
        "ANALYSIS_REGION",
    ):
        assert getattr(config, attr)


_REAL_REQUEST = httpx.AsyncClient.request

_PLATFORM_URLS = SimpleNamespace(
    RETRIEVE_URL="https://retrieve.test/search",
    RERANK_URL="https://rerank.test/rank",
    DATABASE_URL="https://db.test/nl2sql",
    ANALYSIS_URL="https://analysis.test/tasks",
    ANALYSIS_REGION="cn-analysis",
    BI_URL="https://bi.test/query",
    CREATE_TASK_URL="https://task.test/create",
    UPDATE_TASK_URL="https://task.test/update",
)


def _patch_platform(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Patch the upstream client, the platform config, secrets, and IAM."""

    @contextlib.asynccontextmanager
    async def _factory(
        **_kwargs: object,
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(forward_module, "get_async_client", _factory)
    monkeypatch.setattr(
        routes_module, "DeepGenomeConfig", lambda: _PLATFORM_URLS
    )
    monkeypatch.setattr(
        routes_module,
        "get_sensitive_config",
        lambda: SimpleNamespace(BI_TOKEN=SecretStr("bi-secret")),
    )

    async def _fake_token(region: object = None) -> str:
        return f"iam-token:{region}"

    monkeypatch.setattr(routes_module, "get_token", _fake_token)


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
        user_id="c", scopes=[f"relay:{svc}"]
    ).api_key


@pytest.fixture(name="client")
async def _client_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield an httpx client bound to the relay app over ASGI."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_REQUEST)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://relay.test"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the per-key in-flight relay counter across tests."""
    monkeypatch.setattr(forward_module, "_INFLIGHT", {})


def _build_app() -> FastAPI:
    """Mount the relay router on a bare app for route testing."""
    app = FastAPI()
    app.include_router(create_relay_router())
    return app


def _ok(_req: httpx.Request) -> httpx.Response:
    """Return a 200 JSON upstream response."""
    return httpx.Response(
        200, headers={"content-type": "application/json"}, content=b"{}"
    )


async def test_retrieve_route_injects_no_credential(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retrieve forwards verbatim to RETRIEVE_URL with no auth header."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok(req)

    _patch_platform(monkeypatch, handler)

    response = await client.post(
        "/v1/relay/retrieve/search",
        headers={"Authorization": f"Bearer {relay_key('retrieve')}"},
        content=b'{"q":"x"}',
    )

    assert response.status_code == 200
    assert str(seen[0].url) == "https://retrieve.test/search"
    forwarded = {k.lower() for k in seen[0].headers}
    assert "authorization" not in forwarded
    assert "x-auth-token" not in forwarded
    assert "token" not in forwarded


async def test_database_route_injects_iam_token(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """database injects the IAM X-Auth-Token (default region)."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok(req)

    _patch_platform(monkeypatch, handler)

    response = await client.post(
        "/v1/relay/database/nl2sql",
        headers={"Authorization": f"Bearer {relay_key('database')}"},
        content=b"{}",
    )

    assert response.status_code == 200
    assert seen[0].headers["x-auth-token"] == "iam-token:None"


async def test_analysis_route_passes_its_region(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """analysis mints its IAM token with the analysis region."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok(req)

    _patch_platform(monkeypatch, handler)

    response = await client.post(
        "/v1/relay/analysis/tasks",
        headers={"Authorization": f"Bearer {relay_key('analysis')}"},
        content=b"{}",
    )

    assert response.status_code == 200
    assert seen[0].headers["x-auth-token"] == "iam-token:cn-analysis"


async def test_bi_route_injects_static_token(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bi injects the static BI_TOKEN under the lowercase token header."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok(req)

    _patch_platform(monkeypatch, handler)

    response = await client.post(
        "/v1/relay/bi/query",
        headers={"Authorization": f"Bearer {relay_key('bi')}"},
        content=b"{}",
    )

    assert response.status_code == 200
    assert seen[0].headers["token"] == "bi-secret"


async def test_platform_upstream_error_maps_to_status(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An envelope-family upstream error surfaces the upstream status."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b'{"detail":"down"}')

    _patch_platform(monkeypatch, handler)

    response = await client.post(
        "/v1/relay/retrieve/search",
        headers={"Authorization": f"Bearer {relay_key('retrieve')}"},
        content=b"{}",
    )

    assert response.status_code == 503


async def test_analysis_status_route_builds_task_url_with_iam(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET status appends the task id to ANALYSIS_URL and injects IAM."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok(req)

    _patch_platform(monkeypatch, handler)

    response = await client.get(
        "/v1/relay/analysis/task-abc123",
        headers={"Authorization": f"Bearer {relay_key('analysis')}"},
    )

    assert response.status_code == 200
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://analysis.test/tasks/task-abc123"
    assert seen[0].headers["x-auth-token"] == "iam-token:cn-analysis"


async def test_analysis_logs_route_appends_logs_and_allowlists_query(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET logs appends /logs and keeps only the task_name query key."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok(req)

    _patch_platform(monkeypatch, handler)

    response = await client.get(
        "/v1/relay/analysis/task-1/logs"
        "?task_name=analyst-agents-medium&evil=hack",
        headers={"Authorization": f"Bearer {relay_key('analysis')}"},
    )

    assert response.status_code == 200
    assert str(seen[0].url) == (
        "https://analysis.test/tasks/task-1/logs"
        "?task_name=analyst-agents-medium"
    )


async def test_analysis_terminate_route_is_post_with_iam(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST terminate appends /terminate to the task URL and injects IAM."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return _ok(req)

    _patch_platform(monkeypatch, handler)

    response = await client.post(
        "/v1/relay/analysis/task-1/terminate",
        headers={"Authorization": f"Bearer {relay_key('analysis')}"},
        content=b"{}",
    )

    assert response.status_code == 200
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://analysis.test/tasks/task-1/terminate"
    assert seen[0].headers["x-auth-token"] == "iam-token:cn-analysis"


async def test_analysis_lifecycle_rejects_path_injection(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task id outside the safe charset is a 400 before any upstream call."""
    _patch_platform(monkeypatch, _ok)

    response = await client.get(
        "/v1/relay/analysis/a@evil.test",
        headers={"Authorization": f"Bearer {relay_key('analysis')}"},
    )

    assert response.status_code == 400
