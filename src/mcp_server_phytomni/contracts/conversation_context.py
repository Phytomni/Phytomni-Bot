# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Stable conversation-context stage field identities.

This module is intentionally neutral: it imports no server runtime, client,
storage, network, or formatting code. Public formatters and conversation
services share one frozen snapshot so the wire frame and in-process
metadata cannot drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class ContextStageRoute:
    """Agent identity and why this turn was routed to it."""

    selected_agent_id: str
    route_source: str
    route_reason_code: str


@dataclass(frozen=True)
class ContextStageProgress:
    """Projection and rebuild flags for one staged context update."""

    base_business_context_version: int
    proposed_business_context_version: int
    last_applied_ledger_cursor: int
    context_truncated: bool
    context_rebuilt: bool
    context_degraded: bool


@dataclass(frozen=True)
class ContextStageMetadata:
    """Routing and projection metadata for one staged context update."""

    route: ContextStageRoute
    progress: ContextStageProgress

    @classmethod
    def from_public(
        cls, public_metadata: Mapping[str, Any]
    ) -> ContextStageMetadata:
        """Rebuild the snapshot from the flat public wire/store mapping."""
        return cls(
            route=ContextStageRoute(
                selected_agent_id=public_metadata["selected_agent_id"],
                route_source=public_metadata["route_source"],
                route_reason_code=public_metadata["route_reason_code"],
            ),
            progress=ContextStageProgress(
                base_business_context_version=public_metadata[
                    "base_business_context_version"
                ],
                proposed_business_context_version=public_metadata[
                    "proposed_business_context_version"
                ],
                last_applied_ledger_cursor=public_metadata[
                    "last_applied_ledger_cursor"
                ],
                context_truncated=public_metadata["context_truncated"],
                context_rebuilt=public_metadata["context_rebuilt"],
                context_degraded=public_metadata["context_degraded"],
            ),
        )

    def as_public_dict(self) -> dict[str, Any]:
        """Flatten route and progress into the public wire field set."""
        return {**asdict(self.route), **asdict(self.progress)}

    @property
    def selected_agent_id(self) -> str:
        """Agent chosen for this staged turn."""
        return self.route.selected_agent_id

    @property
    def route_source(self) -> str:
        """How the agent was selected for this turn."""
        return self.route.route_source

    @property
    def route_reason_code(self) -> str:
        """Stable reason code that explains the route."""
        return self.route.route_reason_code

    @property
    def base_business_context_version(self) -> int:
        """Business-context version observed before this stage."""
        return self.progress.base_business_context_version

    @property
    def proposed_business_context_version(self) -> int:
        """Business-context version this stage proposes to commit."""
        return self.progress.proposed_business_context_version

    @property
    def last_applied_ledger_cursor(self) -> int:
        """Ledger cursor last applied when this stage was written."""
        return self.progress.last_applied_ledger_cursor

    @property
    def context_truncated(self) -> bool:
        """Whether projection truncated the visible context."""
        return self.progress.context_truncated

    @property
    def context_rebuilt(self) -> bool:
        """Whether this stage rebuilt context instead of appending."""
        return self.progress.context_rebuilt

    @property
    def context_degraded(self) -> bool:
        """Whether persistence or projection ran in a degraded mode."""
        return self.progress.context_degraded


CONTEXT_STAGE_FIELDS = frozenset(
    item.name
    for item in (*fields(ContextStageRoute), *fields(ContextStageProgress))
)
