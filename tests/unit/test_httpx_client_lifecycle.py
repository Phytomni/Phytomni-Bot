# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the lifecycle-managed shared ``AsyncClient`` slot.

``init_shared_client`` / ``aclose_shared_client`` are owned by the API
``lifespan`` and the MCP ``serve`` loop; ``get_async_client`` yields
the shared client (keep-alive pool) only when the caller passes no
extra kwargs. Any other kwarg (e.g. ``trust_env``) forces an ephemeral
client so the pool's TLS / proxy posture is never mutated mid-request.
The shared slot is also bypassed when no lifespan has initialised it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from mcp_server_phytomni.common import httpx_client as module
from mcp_server_phytomni.common.httpx_client import (
    aclose_shared_client,
    get_async_client,
    init_shared_client,
    shared_client_initialised,
)
from mcp_server_phytomni.config.defaults import ServerConfig

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
async def _isolate_shared_client() -> AsyncIterator[None]:
    """Tear the shared client down between tests so slots never leak.

    Yields:
        ``None``; teardown clears the module-level slot.
    """
    yield
    await aclose_shared_client()


async def test_init_shared_client_is_idempotent() -> None:
    """A second ``init`` returns the existing client unchanged."""
    first = init_shared_client(config=ServerConfig())
    second = init_shared_client(config=ServerConfig())

    assert first is second
    assert shared_client_initialised() is True


async def test_aclose_clears_slot_and_is_idempotent() -> None:
    """``aclose`` clears the slot; a second call is a no-op."""
    init_shared_client(config=ServerConfig())

    assert shared_client_initialised() is True

    await aclose_shared_client()

    assert shared_client_initialised() is False

    await aclose_shared_client()

    assert shared_client_initialised() is False


async def test_get_async_client_yields_shared_when_initialised() -> None:
    """With shared init'd and no extra kwargs, the shared client is yielded."""
    shared = init_shared_client(config=ServerConfig())

    async with (
        get_async_client(timeout=5.0) as first,
        get_async_client(timeout=10.0) as second,
    ):
        assert first is shared
        assert second is shared

    # The shared client must NOT be closed on context exit (lifespan owns it).
    assert shared_client_initialised() is True
    assert shared.is_closed is False


async def test_get_async_client_falls_back_to_ephemeral_for_extra_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-``timeout`` / ``config`` kwarg opts into an ephemeral client.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap AsyncClient
            so we can assert ephemeral construction without TLS calls.
    """
    shared = init_shared_client(config=ServerConfig())
    constructed: list[dict[str, Any]] = []

    class _RecordingClient(AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module, "AsyncClient", _RecordingClient)

    async with get_async_client(timeout=1.0, trust_env=False) as client:
        assert isinstance(client, AsyncClient)
        assert client is not shared

    assert len(constructed) == 1
    assert constructed[0]["trust_env"] is False
    assert constructed[0]["timeout"] == 1.0


async def test_get_async_client_ephemeral_when_shared_uninitialised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``init_shared_client``, the factory uses an ephemeral client.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap AsyncClient
            so we can assert ephemeral construction without TLS calls.
    """
    assert shared_client_initialised() is False
    constructed: list[dict[str, Any]] = []

    class _RecordingClient(AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(module, "AsyncClient", _RecordingClient)

    async with get_async_client(timeout=2.0) as client:
        assert isinstance(client, AsyncClient)

    assert len(constructed) == 1
    assert constructed[0]["timeout"] == 2.0


async def test_get_async_client_still_rejects_explicit_verify() -> None:
    """``verify`` remains banned across both the shared and ephemeral paths."""
    init_shared_client(config=ServerConfig())

    with pytest.raises(TypeError):
        async with get_async_client(timeout=1.0, verify=False) as client:
            del client


async def test_shared_client_uses_no_implicit_timeout() -> None:
    """Shared client carries ``timeout=None`` so per-request timeouts rule."""
    shared = init_shared_client(config=ServerConfig())

    # httpx's ``Timeout(None)`` keeps every component (connect/read/write/pool)
    # as ``None`` so callers must pass timeouts per-request explicitly.
    assert shared.timeout.connect is None
    assert shared.timeout.read is None
    assert shared.timeout.write is None
    assert shared.timeout.pool is None
