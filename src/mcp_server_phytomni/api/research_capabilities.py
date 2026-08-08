# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Runtime capability facts for Research input resolution."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from ..config.defaults import ApiConfig, ServerConfig
from ..config.relay_mode import relay_mode_enabled
from ..storage.obs_relay_ops import operator_obs_client
from ..storage.research_objects import DirectResearchObjectMetadataPort

if TYPE_CHECKING:
    from ..common.relay_client import RelayClient

__all__ = [
    "RESEARCH_RELAY_CAPABILITY_TTL_SECONDS",
    "RESEARCH_RELAY_REFRESH_COOLDOWN_SECONDS",
    "ResearchInputRuntimeCapability",
    "ResearchRelayCapabilities",
    "ResearchRelayCapabilityCache",
    "research_input_runtime_capability",
]

RESEARCH_RELAY_CAPABILITY_TTL_SECONDS = 300
RESEARCH_RELAY_REFRESH_COOLDOWN_SECONDS = 30
_RESEARCH_PROTOCOL = "research_object_grant_v1"
_RESEARCH_PROTOCOL_VERSION = 1
_PUBLIC_PROTOCOL = "research_input_resolution_v1"
_UNAVAILABLE: Literal["research_input_protocol_unavailable"] = (
    "research_input_protocol_unavailable"
)
_MAX_RESEARCH_OBJECTS = 256


@dataclass(frozen=True, slots=True)
class ResearchRelayCapabilities:
    """Authenticated, bounded operator capability evidence."""

    protocol_versions: tuple[int, ...]
    max_objects: int
    authorized_scope: Literal["relay:research-input", "relay:*"]
    obtained_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchInputRuntimeCapability:
    """Pure public readiness projection for the Research input protocol."""

    ready: bool
    protocols: MappingProxyType[str, tuple[int, ...]]
    descriptor: MappingProxyType[str, int] | None
    unavailable_code: Literal["research_input_protocol_unavailable"] | None


class ResearchRelayCapabilityCache:
    """Single-flight, fail-closed cache for the operator handshake."""

    def __init__(self) -> None:
        self._snapshot: ResearchRelayCapabilities | None = None
        self._refresh_task: (
            asyncio.Task[ResearchRelayCapabilities | None] | None
        ) = None
        self._last_failure_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def refresh_once(
        self, client: RelayClient, now: datetime
    ) -> ResearchRelayCapabilities | None:
        """Run one shared handshake and clear truth on every failure."""
        _aware_utc(now)
        async with self._lock:
            task = self._refresh_task
            if task is None or task.done():
                task = asyncio.create_task(self._refresh(client, now))
                self._refresh_task = task
                task.add_done_callback(_observe_refresh_task)
        return await asyncio.shield(task)

    def fresh_snapshot(
        self, now: datetime
    ) -> ResearchRelayCapabilities | None:
        """Return only unexpired, structurally compatible truth."""
        now = _aware_utc(now)
        snapshot = self._snapshot
        if not _compatible(snapshot, now):
            return None
        return snapshot

    def schedule_refresh(self, client: RelayClient, now: datetime) -> bool:
        """Schedule a refresh unless the failure cooldown is live."""
        now = _aware_utc(now)
        if self.fresh_snapshot(now) is not None:
            return False
        if (
            self._last_failure_at is not None
            and now - self._last_failure_at
            < timedelta(seconds=RESEARCH_RELAY_REFRESH_COOLDOWN_SECONDS)
        ):
            return False
        task = self._refresh_task
        if task is not None and not task.done():
            return False
        try:
            task = asyncio.create_task(self._refresh(client, now))
        except RuntimeError:
            return False
        self._refresh_task = task
        task.add_done_callback(_observe_refresh_task)
        return True

    async def _refresh(
        self, client: RelayClient, now: datetime
    ) -> ResearchRelayCapabilities | None:
        """Perform and normalize one authenticated handshake."""
        received: ResearchRelayCapabilities | None = None
        with suppress(Exception):
            received = await client.get_research_capabilities()
            if not _compatible_shape(received):
                received = None
        if received is None:
            self._snapshot = None
            self._last_failure_at = now
            return None
        refreshed = ResearchRelayCapabilities(
            protocol_versions=received.protocol_versions,
            max_objects=received.max_objects,
            authorized_scope=received.authorized_scope,
            obtained_at=now,
            expires_at=now
            + timedelta(seconds=RESEARCH_RELAY_CAPABILITY_TTL_SECONDS),
        )
        self._snapshot = refreshed
        self._last_failure_at = None
        return refreshed


def _observe_refresh_task(
    task: asyncio.Task[ResearchRelayCapabilities | None],
) -> None:
    """Consume a detached refresh exception without swallowing cancellation."""
    if not task.cancelled():
        task.exception()


def research_input_runtime_capability(
    config: ApiConfig,
    relay_snapshot: ResearchRelayCapabilities | None,
) -> ResearchInputRuntimeCapability:
    """Return ready only for constructible direct or compatible relay mode."""
    descriptor = _descriptor(config)
    if descriptor is None:
        return _unavailable()
    relay = bool(getattr(config, "RELAY_MODE", relay_mode_enabled()))
    if relay:
        if not _compatible(relay_snapshot, datetime.now(UTC)):
            return _unavailable()
        assert relay_snapshot is not None
        if (
            relay_snapshot.max_objects < config.API_MAX_RESEARCH_DATASET_PATHS
            or 1 not in relay_snapshot.protocol_versions
        ):
            return _unavailable()
    elif not _direct_constructible(config):
        return _unavailable()
    return ResearchInputRuntimeCapability(
        ready=True,
        protocols=MappingProxyType({_PUBLIC_PROTOCOL: (1,)}),
        descriptor=descriptor,
        unavailable_code=None,
    )


def _descriptor(config: ApiConfig) -> MappingProxyType[str, int] | None:
    """Copy only the effective numeric limits into an immutable projection."""
    names = (
        "API_MAX_USER_QUERY_CHARS",
        "API_MAX_ATTACHMENTS_PER_REQUEST",
        "API_MAX_RESEARCH_DATASET_PATHS",
        "API_MAX_RESEARCH_INPUT_REFERENCES",
    )
    try:
        values = {name.lower(): int(getattr(config, name)) for name in names}
    except (AttributeError, TypeError, ValueError):
        return None
    if any(value <= 0 for value in values.values()):
        return None
    return MappingProxyType(
        {
            "max_user_query_chars": values["api_max_user_query_chars"],
            "max_attachments_per_request": values[
                "api_max_attachments_per_request"
            ],
            "max_research_dataset_paths": values[
                "api_max_research_dataset_paths"
            ],
            "max_research_input_references": values[
                "api_max_research_input_references"
            ],
        }
    )


def _direct_constructible(config: ApiConfig) -> bool:
    """Construct the direct metadata adapter without making network calls."""
    try:
        source: Any = config
        if not all(
            isinstance(getattr(source, name, None), str)
            and bool(getattr(source, name, None))
            for name in ("BUCKET_NAME", "OBS_SERVER")
        ):
            source = ServerConfig()
        DirectResearchObjectMetadataPort(
            bucket=source.BUCKET_NAME,
            client_factory=partial(operator_obs_client, source.OBS_SERVER),
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return True


def _compatible(
    snapshot: ResearchRelayCapabilities | None, now: datetime
) -> bool:
    """Check authenticated capability shape, protocol, scope, and expiry."""
    if not _compatible_shape(snapshot):
        return False
    assert snapshot is not None
    return snapshot.expires_at > now


def _compatible_shape(snapshot: object) -> bool:
    """Validate capability values before they can influence readiness."""
    if not isinstance(snapshot, ResearchRelayCapabilities):
        return False
    valid = _valid_protocol_versions(snapshot.protocol_versions)
    valid = valid and _RESEARCH_PROTOCOL_VERSION in snapshot.protocol_versions
    valid = valid and _valid_max_objects(snapshot.max_objects)
    valid = valid and snapshot.authorized_scope in {
        "relay:research-input",
        "relay:*",
    }
    try:
        obtained_at = _aware_utc(snapshot.obtained_at)
        expires_at = _aware_utc(snapshot.expires_at)
    except (TypeError, ValueError):
        return False
    return (
        valid
        and expires_at > obtained_at
        and expires_at - obtained_at
        <= (timedelta(seconds=RESEARCH_RELAY_CAPABILITY_TTL_SECONDS))
    )


def _valid_protocol_versions(value: object) -> bool:
    """Validate the bounded, duplicate-free protocol version tuple."""
    if not isinstance(value, tuple) or not value:
        return False
    if any(
        not isinstance(version, int) or isinstance(version, bool)
        for version in value
    ):
        return False
    return len(set(value)) == len(value)


def _valid_max_objects(value: object) -> bool:
    """Validate the operator's bounded object maximum."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= _MAX_RESEARCH_OBJECTS
    )


def _aware_utc(value: datetime) -> datetime:
    """Require an aware timestamp and normalize it for comparisons."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _unavailable() -> ResearchInputRuntimeCapability:
    """Build the stable fail-closed unavailable projection."""
    return ResearchInputRuntimeCapability(
        ready=False,
        protocols=MappingProxyType({}),
        descriptor=None,
        unavailable_code=_UNAVAILABLE,
    )
