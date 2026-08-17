# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Edge coverage for Research relay capability handshake helpers."""

# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

import mcp_server_phytomni.api.research_capabilities as module
from mcp_server_phytomni.api.research_capabilities import (
    ResearchRelayCapabilities,
    ResearchRelayCapabilityCache,
    research_input_runtime_capability,
)
from mcp_server_phytomni.common.relay_client import RelayClient
from mcp_server_phytomni.config.defaults import ApiConfig

pytestmark = pytest.mark.unit


def _capability(
    now: datetime,
    *,
    versions: tuple[int, ...] = (1,),
    max_objects: int = 256,
) -> ResearchRelayCapabilities:
    """Build one compatible relay capability snapshot."""
    return ResearchRelayCapabilities(
        protocol_versions=versions,
        max_objects=max_objects,
        authorized_scope="relay:research-input",
        obtained_at=now,
        expires_at=now + timedelta(seconds=300),
    )


def _limits(**overrides: Any) -> ApiConfig:
    """Return numeric Research limits used by the public descriptor."""
    values = {
        "RELAY_MODE": False,
        "API_MAX_USER_QUERY_CHARS": 131072,
        "API_MAX_ATTACHMENTS_PER_REQUEST": 64,
        "API_MAX_RESEARCH_DATASET_PATHS": 64,
        "API_MAX_RESEARCH_INPUT_REFERENCES": 128,
    }
    values.update(overrides)
    return cast(ApiConfig, SimpleNamespace(**values))


class _FakeRelayClient:
    """Handshake fake that can block or return a configured payload."""

    def __init__(self, result: object = None) -> None:
        self.result = result
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def get_research_capabilities(self) -> object:
        """Optionally block until the test releases the handshake."""
        self.calls += 1
        self.started.set()
        if not self.release.is_set():
            await self.release.wait()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_schedule_refresh_skips_fresh_snapshot() -> None:
    """A still-valid snapshot does not start another handshake."""
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = ResearchRelayCapabilityCache()
    cache._snapshot = _capability(now)
    assert cache.schedule_refresh(cast(RelayClient, object()), now) is False


async def test_schedule_refresh_skips_in_flight_and_create_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-flight task or create_task failure refuses another refresh."""
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = ResearchRelayCapabilityCache()
    client = _FakeRelayClient(_capability(now))
    assert cache.schedule_refresh(cast(RelayClient, client), now) is True
    assert cache.schedule_refresh(cast(RelayClient, client), now) is False
    client.release.set()
    await client.started.wait()
    if cache._refresh_task is not None:
        await cache._refresh_task

    def _boom(coro: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        coro.close()
        raise RuntimeError("no event loop")

    monkeypatch.setattr(asyncio, "create_task", _boom)
    later = now + timedelta(seconds=400)
    assert cache.schedule_refresh(cast(RelayClient, client), later) is False


async def test_abort_refresh_cancels_in_flight_task() -> None:
    """Abort cancels the pending handshake and clears cached truth."""
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = ResearchRelayCapabilityCache()
    cache._snapshot = _capability(now)
    client = _FakeRelayClient(_capability(now))
    assert cache.schedule_refresh(cast(RelayClient, client), now) is False
    cache.abort_refresh()
    assert cache._refresh_task is None
    assert cache._snapshot is None

    later = now + timedelta(seconds=400)
    assert cache.schedule_refresh(cast(RelayClient, client), later) is True
    pending = cache._refresh_task
    cache.abort_refresh()
    assert cache._refresh_task is None
    client.release.set()
    if pending is not None:
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending


async def test_refresh_rejects_incompatible_handshake_shape() -> None:
    """A typed-but-invalid handshake payload is treated as failure."""
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = ResearchRelayCapabilityCache()
    client = _FakeRelayClient(result=object())
    client.release.set()
    assert await cache.refresh_once(cast(RelayClient, client), now) is None
    assert cache._snapshot is None


async def test_current_snapshot_schedules_and_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale relay truth schedules a refresh and fail-closes on errors."""
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = ResearchRelayCapabilityCache()
    monkeypatch.setattr(module, "_RELAY_CAPABILITY_CACHE", cache)
    monkeypatch.setattr(module, "_relay_mode", lambda _config: True)
    client = _FakeRelayClient(_capability(now))
    monkeypatch.setattr(module, "_current_relay_client", lambda: client)
    assert module.current_research_relay_snapshot(ApiConfig(), now) is None
    client.release.set()
    if cache._refresh_task is not None:
        await cache._refresh_task

    monkeypatch.setattr(
        cache, "schedule_refresh", Mock(side_effect=RuntimeError("offline"))
    )
    assert (
        module.current_research_relay_snapshot(
            ApiConfig(), now + timedelta(seconds=400)
        )
        is None
    )


async def test_refresh_capability_timeout_and_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handshake timeout aborts the task; other errors return None."""
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = ResearchRelayCapabilityCache()
    monkeypatch.setattr(module, "_RELAY_CAPABILITY_CACHE", cache)
    monkeypatch.setattr(module, "_relay_mode", lambda _config: True)
    monkeypatch.setattr(module, "_current_relay_client", lambda: object())

    async def _hang(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        await asyncio.Event().wait()

    monkeypatch.setattr(cache, "refresh_once", _hang)
    monkeypatch.setattr(module, "_RELAY_REFRESH_TIMEOUT_SECONDS", 0.01)
    assert (
        await module.refresh_research_relay_capability(ApiConfig(), now)
        is None
    )

    async def _boom(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("handshake")

    monkeypatch.setattr(cache, "refresh_once", _boom)
    assert (
        await module.refresh_research_relay_capability(ApiConfig(), now)
        is None
    )


def test_current_relay_client_imports_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capability module loads the relay client without a cycle."""

    class _RelayModule:
        @staticmethod
        def current_relay_client() -> str:
            """Return a sentinel client."""
            return "relay-client"

    monkeypatch.setattr(module, "import_module", lambda _name: _RelayModule)
    assert module._current_relay_client() == "relay-client"


def test_runtime_capability_descriptor_and_constructible_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness fails closed for bad limits, relay caps, and OBS gaps."""
    missing = research_input_runtime_capability(
        cast(ApiConfig, SimpleNamespace()), None
    )
    assert missing.ready is False

    now = datetime.now(UTC)
    small = _capability(now, max_objects=2)
    relay_config = _limits(RELAY_MODE=True)
    relay = research_input_runtime_capability(relay_config, small)
    assert relay.ready is False

    monkeypatch.setattr(
        module,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=None),
    )
    direct = research_input_runtime_capability(_limits(), None)
    assert direct.ready is False

    monkeypatch.setattr(
        module,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=object()),
    )
    monkeypatch.setattr(
        module,
        "DirectResearchObjectMetadataPort",
        Mock(side_effect=RuntimeError("adapter")),
    )
    broken = research_input_runtime_capability(
        _limits(BUCKET_NAME="b", OBS_SERVER="https://obs"), None
    )
    assert broken.ready is False


def test_descriptor_and_shape_validation_edges() -> None:
    """Descriptor construction and capability shape checks fail closed."""
    assert module._descriptor(cast(ApiConfig, SimpleNamespace())) is None
    assert module._descriptor(_limits(API_MAX_USER_QUERY_CHARS=0)) is None
    assert module._valid_protocol_versions(()) is False
    assert module._valid_protocol_versions((True,)) is False
    naive = ResearchRelayCapabilities(
        protocol_versions=(1,),
        max_objects=10,
        authorized_scope="relay:*",
        obtained_at=datetime(2026, 1, 1),
        expires_at=datetime(2026, 1, 1, 0, 5),
    )
    assert module._compatible_shape(naive) is False
    with pytest.raises(ValueError, match="timezone-aware"):
        module._aware_utc(datetime(2026, 1, 1))
