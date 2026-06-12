# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the customer relay client.

Covers relay URL construction under the fixed ``/v1/relay`` prefix,
bearer-auth header injection, JSON POST and GET response handling, the
``McpError`` mapping that keeps the relay key out of error text, and the
``build_relay_client`` factory reading the relay config + secret.
"""

from __future__ import annotations

import contextlib
from typing import Any, cast

import httpx
import pytest
from mcp.shared.exceptions import McpError
from pydantic import SecretStr

from mcp_server_phytomni.common import relay_client as rc
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.config.settings import (
    SensitiveConfig,
    get_sensitive_config,
)

pytestmark = pytest.mark.unit


def _client(api_key: str = "relay-secret-value") -> rc.RelayClient:
    """Return a RelayClient with a fixed relay base URL and policy."""
    return rc.RelayClient(
        base_url="https://relay.test",
        api_key=SecretStr(api_key),
        timeout=5.0,
        max_retries=2,
        retriable_codes=(503,),
    )


class _CapturingClient:
    """Async-context client stub recording one POST/GET and replaying a
    canned response.

    The real ``httpx.AsyncClient.request`` is blocked offline, so the
    relay client is exercised against this stub (yielded in place of
    ``get_async_client``). It records the URL / headers / body the
    shared retry helper sends so tests can assert the relay contract.
    """

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.captured: dict[str, Any] = {}

    async def __aenter__(self) -> "_CapturingClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Record a POST and replay the canned response."""
        self.captured = {"method": "POST", "url": url, **kwargs}
        return self.response

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Record a GET and replay the canned response."""
        self.captured = {"method": "GET", "url": url, **kwargs}
        return self.response

    async def request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Record a non-GET/POST request (e.g. PUT) and replay it."""
        self.captured = {"method": method, "url": url, **kwargs}
        return self.response


def _patch_client(monkeypatch, response: httpx.Response) -> _CapturingClient:
    """Patch ``relay_client.get_async_client`` to yield a capturing stub."""
    client = _CapturingClient(response)

    @contextlib.asynccontextmanager
    async def fake_get_async_client(*, timeout: Any = None, **_kwargs: Any):
        del timeout, _kwargs
        yield client

    monkeypatch.setattr(rc, "get_async_client", fake_get_async_client)
    return client


def _response(status: int, body: Any, method: str = "POST") -> httpx.Response:
    """Return a canned httpx.Response carrying a request for status raises."""
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request(method, "https://relay.test/v1/relay/x"),
    )


def test_relay_url_builds_v1_relay_path():
    """``relay_url`` joins the base under the fixed ``/v1/relay`` prefix."""
    assert (
        _client().relay_url("retrieve/search")
        == "https://relay.test/v1/relay/retrieve/search"
    )


def test_relay_url_strips_leading_slash_on_path():
    """A leading slash on the relay path does not double the separator."""
    assert (
        _client().relay_url("/analysis/abc")
        == "https://relay.test/v1/relay/analysis/abc"
    )


def test_relay_url_appends_query():
    """Query mappings are URL-encoded onto the relay path."""
    url = _client().relay_url(
        "spa-faq/repo1", {"question": "x y", "page_num": "1"}
    )
    assert url.startswith("https://relay.test/v1/relay/spa-faq/repo1?")
    assert "question=x+y" in url
    assert "page_num=1" in url


def test_repr_masks_api_key():
    """The relay key never appears in the client repr."""
    assert "super-secret" not in repr(_client("super-secret"))


async def test_post_json_sends_bearer_and_body(monkeypatch):
    """``post_json`` POSTs the body with a bearer header and parses JSON."""
    client_stub = _patch_client(monkeypatch, _response(200, {"hits": []}))

    result = await _client("k9").post_json(
        "retrieve/search",
        json_body={"q": "gene"},
        message="relay retrieve failed",
    )

    assert result == {"hits": []}
    assert client_stub.captured["method"] == "POST"
    assert (
        client_stub.captured["url"]
        == "https://relay.test/v1/relay/retrieve/search"
    )
    assert client_stub.captured["headers"]["Authorization"] == "Bearer k9"
    assert client_stub.captured["json"] == {"q": "gene"}


async def test_get_json_uses_get_method(monkeypatch):
    """``get_json`` issues a GET carrying the bearer header."""
    client_stub = _patch_client(
        monkeypatch, _response(200, {"status": "done"}, method="GET")
    )

    result = await _client("k9").get_json(
        "analysis/abc", message="relay status failed"
    )

    assert result == {"status": "done"}
    assert client_stub.captured["method"] == "GET"
    assert (
        client_stub.captured["url"]
        == "https://relay.test/v1/relay/analysis/abc"
    )
    assert client_stub.captured["headers"]["Authorization"] == "Bearer k9"


async def test_non_retriable_status_raises_mcperror_without_key(monkeypatch):
    """A non-retriable upstream status raises McpError, key-free."""
    _patch_client(monkeypatch, _response(500, {"err": "boom"}))

    with pytest.raises(McpError) as excinfo:
        await _client("super-secret").post_json(
            "retrieve/search",
            json_body={},
            message="relay retrieve failed",
        )

    assert "super-secret" not in str(excinfo.value)


def test_build_relay_client_reads_config_and_secret(monkeypatch):
    """``build_relay_client`` pulls base URL, key, and policy from config."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test/api/")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "factory-key")

    config = ServerConfig()
    sensitive = cast(Any, SensitiveConfig)(_env_file=None)

    client = rc.build_relay_client(config, sensitive)

    assert client.base_url == "https://relay.test/api"
    assert client.api_key.get_secret_value() == "factory-key"
    assert client.timeout == config.TIMEOUT
    assert client.max_retries == config.MAX_RETRIES
    assert tuple(client.retriable_codes) == tuple(config.RETRIABLE_CODES)


def test_current_relay_client_reads_live_relay_config(monkeypatch):
    """``current_relay_client`` reads RELAY_BASE_URL / key from the env.

    It is the zero-arg seam S4 platform boundaries call (they have no
    config object in scope); it builds from a fresh ``ServerConfig()`` so
    a relay-mode child Bot's live ``RELAY_BASE_URL`` is reflected.
    """
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    monkeypatch.setenv("PHYTOMNI_RELAY_BASE_URL", "https://relay.test")
    monkeypatch.setenv("PHYTOMNI_RELAY_API_KEY", "live-key")
    get_sensitive_config.cache_clear()

    client = rc.current_relay_client()

    assert client.base_url == "https://relay.test"
    assert client.api_key.get_secret_value() == "live-key"


async def test_put_obs_object_puts_bytes_with_path_query(monkeypatch):
    """``put_obs_object`` PUTs raw bytes to /obs/object with the path query."""
    client_stub = _patch_client(
        monkeypatch, _response(200, {"obs_path": "/obs/phytomni/x"}, "PUT")
    )

    result = await _client("k9").put_obs_object(
        "/obs/phytomni/agent_data/x.pdf",
        b"file-bytes",
        message="relay upload failed",
    )

    assert result == {"obs_path": "/obs/phytomni/x"}
    assert client_stub.captured["method"] == "PUT"
    assert "v1/relay/obs/object?" in client_stub.captured["url"]
    assert "path=" in client_stub.captured["url"]
    assert client_stub.captured["data"] == b"file-bytes"
    assert client_stub.captured["headers"]["Authorization"] == "Bearer k9"


async def test_get_obs_object_returns_raw_bytes(monkeypatch):
    """``get_obs_object`` GETs /obs/object and returns the raw body bytes."""
    client_stub = _patch_client(
        monkeypatch,
        httpx.Response(
            200,
            content=b"ATOM 1 N",
            request=httpx.Request(
                "GET", "https://relay.test/v1/relay/obs/object"
            ),
        ),
    )

    data = await _client("k9").get_obs_object(
        "/obs/phytomni/agent_data/out/r.cif", message="relay download failed"
    )

    assert data == b"ATOM 1 N"
    assert client_stub.captured["method"] == "GET"
    assert "v1/relay/obs/object?" in client_stub.captured["url"]
    assert "path=" in client_stub.captured["url"]


async def test_get_obs_list_returns_keys(monkeypatch):
    """``get_obs_list`` GETs /obs/list with the prefix query, unwraps keys."""
    client_stub = _patch_client(
        monkeypatch,
        _response(200, {"keys": ["pfx/a.png", "pfx/b.md"]}, "GET"),
    )

    keys = await _client("k9").get_obs_list(
        "agent_data/user_data/u/runs/d/r/t/output/",
        message="relay list failed",
    )

    assert keys == ["pfx/a.png", "pfx/b.md"]
    assert "v1/relay/obs/list?" in client_stub.captured["url"]
    assert "prefix=" in client_stub.captured["url"]


async def test_put_obs_dir_puts_marker(monkeypatch):
    """``put_obs_dir`` PUTs /obs/dir with the path query, parses JSON."""
    client_stub = _patch_client(
        monkeypatch, _response(200, {"obs_path": "/obs/phytomni/d/"}, "PUT")
    )

    result = await _client("k9").put_obs_dir(
        "/obs/phytomni/agent_data/d/output/", message="relay mkdir failed"
    )

    assert result == {"obs_path": "/obs/phytomni/d/"}
    assert client_stub.captured["method"] == "PUT"
    assert "v1/relay/obs/dir?" in client_stub.captured["url"]


class _StreamResponse:
    """Streaming response stub yielding canned chunks via aiter_bytes."""

    def __init__(self, status_code: int, chunks: list[bytes]) -> None:
        self.status_code = status_code
        self._chunks = chunks

    async def aiter_bytes(self) -> Any:
        """Yield the canned chunks one at a time."""
        for chunk in self._chunks:
            yield chunk


class _StreamClient:
    """Async-context client stub whose ``stream`` replays canned chunks."""

    captured: dict[str, Any] = {}

    def __init__(self, response: _StreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> "_StreamClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        """Record the stream request and replay the canned response."""
        _StreamClient.captured = {"method": method, "url": url, **kwargs}

        @contextlib.asynccontextmanager
        async def _cm():
            yield self.response

        return _cm()


def _patch_stream_client(monkeypatch, response: _StreamResponse) -> None:
    """Patch ``get_async_client`` to yield a streaming capturing stub."""
    client = _StreamClient(response)

    @contextlib.asynccontextmanager
    async def fake_get_async_client(*, timeout: Any = None, **_kwargs: Any):
        del timeout, _kwargs
        yield client

    monkeypatch.setattr(rc, "get_async_client", fake_get_async_client)


async def test_get_obs_object_to_path_streams_to_disk(tmp_path, monkeypatch):
    """The download streams each chunk straight to the destination file."""
    _patch_stream_client(
        monkeypatch, _StreamResponse(200, [b"AB", b"C", b"D"])
    )
    dest = tmp_path / "r.cif"

    await _client("k9").get_obs_object_to_path(
        "/obs/phytomni/agent_data/user_data/u/r/r.cif",
        dest,
        message="relay download failed",
    )

    assert dest.read_bytes() == b"ABCD"
    assert _StreamClient.captured["method"] == "GET"
    assert "v1/relay/obs/object?" in _StreamClient.captured["url"]
    assert _StreamClient.captured["headers"]["Authorization"] == "Bearer k9"


async def test_get_obs_object_to_path_raises_on_error_status(
    tmp_path, monkeypatch
):
    """A 4xx/5xx upstream status raises McpError, key-free, no file write."""
    _patch_stream_client(monkeypatch, _StreamResponse(404, [b"nope"]))
    dest = tmp_path / "missing.cif"

    with pytest.raises(McpError) as excinfo:
        await _client("super-secret").get_obs_object_to_path(
            "/obs/phytomni/agent_data/user_data/u/r/r.cif",
            dest,
            message="relay download failed",
        )

    assert "super-secret" not in str(excinfo.value)
    assert not dest.exists()
