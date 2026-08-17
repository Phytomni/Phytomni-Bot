# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the Research relay handshake and fail-closed readiness seam."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

import mcp_server_phytomni.api.research_capabilities as module
from mcp_server_phytomni.api.research_capabilities import (
    RESEARCH_RELAY_CAPABILITY_TTL_SECONDS,
    RESEARCH_RELAY_REFRESH_COOLDOWN_SECONDS,
    ResearchInputRuntimeCapability,
    ResearchRelayCapabilities,
    ResearchRelayCapabilityCache,
    research_input_runtime_capability,
)
from mcp_server_phytomni.common.relay_client import RelayClient
from mcp_server_phytomni.config.defaults import ApiConfig
from mcp_server_phytomni.storage.research_objects import (
    RelayResearchObjectMetadataPort,
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectMetadataError,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
)

pytestmark = pytest.mark.unit


def _capability(
    now: datetime,
    *,
    versions: tuple[int, ...] = (1,),
    max_objects: int = 256,
    scope: Literal["relay:research-input", "relay:*"] = (
        "relay:research-input"
    ),
    expires_at: datetime | None = None,
) -> ResearchRelayCapabilities:
    """Build a bounded relay capability for cache tests."""
    return ResearchRelayCapabilities(
        protocol_versions=versions,
        max_objects=max_objects,
        authorized_scope=scope,
        obtained_at=now,
        expires_at=expires_at or now + timedelta(seconds=300),
    )


def test_runtime_capability_is_constructible_in_direct_mode(monkeypatch):
    """Direct readiness checks the adapter seam without network I/O."""
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.setattr(
        module,
        "DirectResearchObjectMetadataPort",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        module,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=object()),
    )
    result = research_input_runtime_capability(ApiConfig(), None)

    assert isinstance(result, ResearchInputRuntimeCapability)
    assert result.ready is True
    assert result.unavailable_code is None
    assert result.protocols["research_input_resolution_v1"] == (1,)


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        _capability(datetime(2026, 1, 1, tzinfo=UTC), versions=(2,)),
        _capability(
            datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        ),
        _capability(
            datetime(2026, 1, 1, tzinfo=UTC),
            max_objects=63,
        ),
    ],
)
def test_relay_readiness_fails_closed_for_incompatible_snapshot(snapshot):
    """Missing or incompatible relay truth is unavailable."""
    config = cast(
        ApiConfig,
        SimpleNamespace(
            RELAY_MODE=True,
            API_MAX_RESEARCH_DATASET_PATHS=64,
            API_MAX_RESEARCH_INPUT_REFERENCES=128,
            API_MAX_ATTACHMENTS_PER_REQUEST=64,
            API_MAX_USER_QUERY_CHARS=131072,
        ),
    )
    result = module.research_input_runtime_capability(config, snapshot)
    assert result.ready is False
    assert result.protocols == {}
    assert result.descriptor is None
    assert result.unavailable_code == "research_input_protocol_unavailable"


@pytest.mark.parametrize("scope", ["relay:research-input", "relay:*"])
def test_relay_readiness_accepts_exact_or_wildcard_scope(scope):
    """Only the scoped or wildcard operator authorization is accepted."""
    now = datetime.now(UTC)
    config = cast(
        ApiConfig,
        SimpleNamespace(
            RELAY_MODE=True,
            API_MAX_RESEARCH_DATASET_PATHS=64,
            API_MAX_RESEARCH_INPUT_REFERENCES=128,
            API_MAX_ATTACHMENTS_PER_REQUEST=64,
            API_MAX_USER_QUERY_CHARS=131072,
        ),
    )
    result = research_input_runtime_capability(
        config, _capability(now, scope=scope)
    )
    assert result.ready is True
    assert result.protocols == {"research_input_resolution_v1": (1,)}
    assert result.descriptor is not None
    assert result.descriptor["max_research_input_references"] == 128


class _FakeRelayClient:
    """Handshake fake used to exercise cache single-flight behavior."""

    def __init__(self, result=None, error: Exception | None = None):
        self.calls = 0
        self.result = result
        self.error = error
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def get_research_capabilities(self):
        """Block until the test releases the fake handshake."""
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return self.result

    def reset(self):
        """Reset the call counter for a subsequent handshake assertion."""
        self.calls = 0


@pytest.mark.asyncio
async def test_cache_refresh_is_single_flight_and_ttl_is_injected():
    """Concurrent stale readers share one handshake and use 300-second TTL."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    client = _FakeRelayClient(_capability(now, max_objects=256))
    cache = ResearchRelayCapabilityCache()
    first = asyncio.create_task(
        cache.refresh_once(cast(RelayClient, client), now)
    )
    await client.started.wait()
    second = asyncio.create_task(
        cache.refresh_once(cast(RelayClient, client), now)
    )
    assert client.calls == 1
    client.release.set()
    assert await first == await second
    snapshot = cache.fresh_snapshot(
        now + timedelta(seconds=RESEARCH_RELAY_CAPABILITY_TTL_SECONDS - 1)
    )
    assert snapshot is not None
    assert (
        cache.fresh_snapshot(
            now + timedelta(seconds=RESEARCH_RELAY_CAPABILITY_TTL_SECONDS)
        )
        is None
    )


@pytest.mark.asyncio
async def test_cache_failed_refresh_clears_truth_and_obeys_cooldown():
    """A failed refresh removes readiness and blocks only 30 seconds."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = ResearchRelayCapabilityCache()
    client = _FakeRelayClient(error=RuntimeError("secret endpoint"))
    task = asyncio.create_task(
        cache.refresh_once(cast(RelayClient, client), now)
    )
    await client.started.wait()
    client.release.set()
    assert await task is None
    assert cache.fresh_snapshot(now) is None
    assert cache.schedule_refresh(cast(RelayClient, client), now) is False
    assert (
        cache.schedule_refresh(
            cast(RelayClient, client),
            now
            + timedelta(seconds=RESEARCH_RELAY_REFRESH_COOLDOWN_SECONDS + 1),
        )
        is True
    )


@pytest.mark.asyncio
async def test_cache_unexpected_refresh_failure_clears_previous_truth():
    """Unexpected handshake errors clear readiness and start cooldown."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    client = _FakeRelayClient(_capability(now))
    client.release.set()
    cache = ResearchRelayCapabilityCache()
    assert await cache.refresh_once(cast(RelayClient, client), now)

    client.result = None
    client.error = Exception("unexpected handshake failure")
    failed_at = now + timedelta(seconds=301)
    assert (
        await cache.refresh_once(cast(RelayClient, client), failed_at) is None
    )
    assert cache.fresh_snapshot(failed_at) is None
    assert (
        cache.schedule_refresh(cast(RelayClient, client), failed_at) is False
    )


@pytest.mark.asyncio
async def test_cache_background_unexpected_failure_is_observed():
    """Background handshake errors are sanitized instead of becoming noise."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    client = _FakeRelayClient(error=Exception("unexpected background failure"))
    cache = ResearchRelayCapabilityCache()

    assert cache.schedule_refresh(cast(RelayClient, client), now) is True
    await client.started.wait()
    client.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert cache.fresh_snapshot(now) is None
    assert cache.schedule_refresh(cast(RelayClient, client), now) is False


def _metadata_request() -> ResearchObjectResolveRequest:
    """Build a one-object request for relay metadata-port tests."""
    return ResearchObjectResolveRequest(
        parent_run_id="run-1",
        execution_fingerprint="execution-1",
        objects=(ResearchObjectCandidate("d1", "obs://bucket/a.vcf", ".vcf"),),
    )


def _metadata_authority(
    snapshot: ResearchObjectSnapshot,
) -> ResearchObjectAuthority:
    """Build a valid authority for relay metadata-port tests."""
    return ResearchObjectAuthority("d1", "grant-1", snapshot)


def _metadata_snapshot() -> ResearchObjectSnapshot:
    """Return one complete safe snapshot for metadata-port tests."""
    return ResearchObjectSnapshot(
        "d1",
        17,
        "etag-17",
        "version-1",
        "2026-08-08T00:00:00Z",
        False,
        "digest-d1",
    )


class _FakeMetadataRelayClient:
    """Return typed but intentionally malformed metadata relay results."""

    def __init__(
        self, authorities: tuple[ResearchObjectAuthority, ...]
    ) -> None:
        self.authorities = authorities
        self.revoke_result: object = None

    async def resolve_research_objects(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return the configured resolve result."""
        del request
        return self.authorities

    async def verify_research_objects(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return the configured verify result."""
        del request
        return self.authorities

    async def revoke_research_objects(self, request: object) -> object:
        """Provide the complete typed-client surface for the port fake."""
        del request
        return self.revoke_result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("size_bytes", -1),
        ("size_bytes", True),
        ("snapshot_digest", ""),
        ("snapshot_digest", "digest\x00"),
        ("etag", 17),
        ("version_id", "v" * 513),
        ("last_modified", "2026-08-08\x00"),
        ("dataset_id", "d\n1"),
    ],
)
@pytest.mark.parametrize("operation", ["resolve", "verify"])
@pytest.mark.asyncio
async def test_relay_metadata_port_rejects_malformed_snapshot(
    field: str, value: object, operation: str
):
    """Resolve and verify map every malformed snapshot to a safe error."""
    snapshot_values = asdict(_metadata_snapshot())
    snapshot_values[field] = value
    malformed = ResearchObjectSnapshot(**cast(Any, snapshot_values))
    fake = _FakeMetadataRelayClient((_metadata_authority(malformed),))
    port = RelayResearchObjectMetadataPort(cast(RelayClient, fake))

    if operation == "resolve":
        with pytest.raises(ResearchObjectMetadataError):
            await port.resolve(_metadata_request())
        return

    valid = _metadata_snapshot()
    request = ResearchObjectVerifyRequest(
        parent_run_id="run-1",
        execution_fingerprint="execution-1",
        authorities=(_metadata_authority(valid),),
    )
    with pytest.raises(ResearchObjectMetadataError):
        await port.verify(request)


@pytest.mark.asyncio
async def test_relay_metadata_port_rejects_unsafe_authority_ids():
    """Relay authority and dataset IDs remain bounded non-control text."""
    snapshot = _metadata_snapshot()
    malformed = ResearchObjectAuthority("d1", "g" * 513, snapshot)
    fake = _FakeMetadataRelayClient((malformed,))
    port = RelayResearchObjectMetadataPort(cast(RelayClient, fake))

    with pytest.raises(ResearchObjectMetadataError):
        await port.resolve(_metadata_request())


@pytest.mark.asyncio
async def test_relay_metadata_port_rejects_malformed_revoke_result():
    """A malformed typed revoke result is not accepted as successful."""
    fake = _FakeMetadataRelayClient(())
    fake.revoke_result = {"revoked": "yes"}
    port = RelayResearchObjectMetadataPort(cast(RelayClient, fake))
    request = ResearchObjectRevokeRequest("run-1", "execution-1", ("grant-1",))

    with pytest.raises(ResearchObjectMetadataError):
        await port.revoke(request)
