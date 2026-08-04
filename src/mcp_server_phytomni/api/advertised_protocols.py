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
from dataclasses import dataclass

from ..runtime.conversation_context.models import (
    CONVERSATION_CONTEXT_PROTOCOL_VERSION,
)
from ..runtime.resumable_uploads import (
    UPLOAD_PROTOCOL,
    UPLOAD_PROTOCOL_VERSION,
)

# Conversation-context enablement is read at call time from the live catalog
# config, so the route passes the predicate in. Upload is unconditionally
# available once its routes are registered (config presence is validated at
# startup), so its predicate is a constant True.


def _upload_enabled() -> bool:
    """Upload routes are always available once registered."""
    return True


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
