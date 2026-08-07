# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Single source of truth for protocols advertised in ``GET /v1/agents``.

Each entry binds a protocol name to its version and a runtime enablement
check, so the top-level ``protocols`` map of the agent catalog is assembled
in one place instead of being hand-stitched in the route handler. Add a new
protocol here and it flows into the catalog automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from ..config.api_limits import ApiLimitsConfig
from ..config.relay_mode import relay_mode_enabled
from ..runtime.conversation_context.models import (
    CONVERSATION_CONTEXT_PROTOCOL_VERSION,
)
from ..runtime.resumable_uploads import (
    UPLOAD_PROTOCOL,
    UPLOAD_PROTOCOL_VERSION,
)
from .agent_capabilities import build_research_input_descriptor

RESULT_ARCHIVE_PROTOCOL = "result_archive_v1"
RESULT_ARCHIVE_PROTOCOL_VERSION = 1
RESEARCH_INPUT_PROTOCOL = "research_input_resolution_v1"
RESEARCH_INPUT_PROTOCOL_VERSION = 1

__all__ = [
    "RESULT_ARCHIVE_PROTOCOL",
    "RESULT_ARCHIVE_PROTOCOL_VERSION",
    "RESEARCH_INPUT_PROTOCOL",
    "RESEARCH_INPUT_PROTOCOL_VERSION",
    "result_archive_backend_available",
    "advertised_protocols",
    "serialize_protocols",
    "serialize_research_input_descriptor",
]

# Conversation-context enablement is read at call time from the live catalog
# config, so the route passes the predicate in. Upload is unconditionally
# available once its routes are registered (config presence is validated at
# startup), so its predicate is a constant True.


def _upload_enabled() -> bool:
    """Upload routes are always available once registered."""
    return True


def result_archive_backend_available() -> bool:
    """Return whether direct file-backed storage is available."""
    return not relay_mode_enabled()


@dataclass(frozen=True, slots=True)
class AdvertisedProtocol:
    """One entry in the public protocol advertisement."""

    name: str
    version: int
    enabled: Callable[[], bool]


def advertised_protocols(
    context_enabled: Callable[[], bool],
) -> tuple[AdvertisedProtocol, ...]:
    """Return every protocol the catalog should publish right now.

    ``context_enabled`` is supplied by the caller (the route handler) because
    it reads from the live catalog dependency; upload enablement is static.
    """
    return (
        AdvertisedProtocol(
            name=UPLOAD_PROTOCOL,
            version=UPLOAD_PROTOCOL_VERSION,
            enabled=_upload_enabled,
        ),
        AdvertisedProtocol(
            name="conversation_context",
            version=CONVERSATION_CONTEXT_PROTOCOL_VERSION,
            enabled=context_enabled,
        ),
        AdvertisedProtocol(
            name=RESULT_ARCHIVE_PROTOCOL,
            version=RESULT_ARCHIVE_PROTOCOL_VERSION,
            enabled=result_archive_backend_available,
        ),
    )


def serialize_protocols(
    context_enabled: Callable[[], bool],
) -> dict[str, list[int]]:
    """Build the top-level ``protocols`` map for ``GET /v1/agents``."""
    return {
        entry.name: [entry.version]
        for entry in advertised_protocols(context_enabled)
        if entry.enabled()
    }


def serialize_research_input_descriptor(
    config: ApiLimitsConfig,
) -> dict[str, object]:
    """Serialize the detached Research descriptor without catalog wiring."""
    descriptor = asdict(build_research_input_descriptor(config))
    descriptor["dataset_formats"] = list(descriptor["dataset_formats"])
    return descriptor
