# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared async HTTP client factory and verify resolver.

The resolver maps ``ServerConfig.TLS_VERIFY`` and ``ServerConfig.CA_BUNDLE``
to the ``verify`` argument httpx expects (bool or CA-bundle path). The
factory wraps ``httpx.AsyncClient`` so every production caller picks up
those settings without re-reading os.environ at the call-site.
"""

from __future__ import annotations

import ssl
from typing import Any

import pytest
from httpx import AsyncClient

from mcp_server_phytomni.common import httpx_client as factory_module
from mcp_server_phytomni.common.httpx_client import (
    get_async_client,
    resolve_verify,
)
from mcp_server_phytomni.config.defaults import ServerConfig

pytestmark = pytest.mark.unit


def test_resolve_verify_defaults_to_true() -> None:
    """Default ServerConfig produces the safe ``verify=True`` posture."""
    assert resolve_verify(ServerConfig()) is True


def test_resolve_verify_false_when_tls_off() -> None:
    """TLS_VERIFY=False disables verification regardless of bundle."""
    config = ServerConfig(TLS_VERIFY=False, CA_BUNDLE="/etc/pki/ca.pem")

    assert resolve_verify(config) is False


def test_resolve_verify_wraps_ca_bundle_in_ssl_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured bundle path is materialised as an SSLContext.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to spy on the
            standard-library SSLContext factory so the test never
            touches a real on-disk PEM file.
    """
    sentinel = ssl.create_default_context()
    captured: dict[str, Any] = {}

    def _fake_create(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(
        factory_module.ssl, "create_default_context", _fake_create
    )
    config = ServerConfig(TLS_VERIFY=True, CA_BUNDLE="/etc/pki/ca.pem")

    result = resolve_verify(config)

    assert result is sentinel
    assert captured["kwargs"]["cafile"] == "/etc/pki/ca.pem"


def test_resolve_verify_honours_phytomni_env_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PHYTOMNI_TLS_VERIFY / PHYTOMNI_CA_BUNDLE env aliases bind too.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to set env vars.
    """
    monkeypatch.setenv("PHYTOMNI_TLS_VERIFY", "false")
    monkeypatch.setenv("PHYTOMNI_CA_BUNDLE", "/etc/pki/should-be-ignored")

    assert resolve_verify(ServerConfig()) is False


async def test_get_async_client_passes_resolved_verify_to_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory hands the resolved verify value to ``AsyncClient``.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap AsyncClient
            and the SSLContext factory so no real CA file is required.
    """
    sentinel = ssl.create_default_context()
    monkeypatch.setattr(
        factory_module.ssl,
        "create_default_context",
        lambda *_args, **_kwargs: sentinel,
    )
    captured: dict[str, Any] = {}

    class _RecordingClient(AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["init_kwargs"] = kwargs
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(factory_module, "AsyncClient", _RecordingClient)
    config = ServerConfig(TLS_VERIFY=True, CA_BUNDLE="/etc/pki/ca.pem")

    async with get_async_client(timeout=5.0, config=config) as client:
        assert isinstance(client, AsyncClient)

    assert captured["init_kwargs"]["verify"] is sentinel
    assert captured["init_kwargs"]["timeout"] == 5.0


async def test_get_async_client_forwards_extra_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Other httpx keywords (e.g. ``trust_env``) flow through unchanged.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap AsyncClient.
    """
    captured: dict[str, Any] = {}

    class _RecordingClient(AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["init_kwargs"] = kwargs
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(factory_module, "AsyncClient", _RecordingClient)

    async with get_async_client(
        timeout=1.0,
        config=ServerConfig(),
        trust_env=False,
        headers={"X-Test": "yes"},
    ) as client:
        assert isinstance(client, AsyncClient)

    init_kwargs = captured["init_kwargs"]
    assert init_kwargs["trust_env"] is False
    assert init_kwargs["headers"] == {"X-Test": "yes"}


async def test_get_async_client_rejects_explicit_verify() -> None:
    """A caller-supplied ``verify`` must raise to keep the invariant."""
    with pytest.raises(TypeError):
        async with get_async_client(timeout=1.0, verify=False) as client:
            del client
