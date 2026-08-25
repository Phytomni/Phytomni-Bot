# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Non-regressing semantic, provider, and client-stream liveness clocks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ExecutionLivenessClocks:
    """Three clocks whose meanings must never be collapsed into activity."""

    last_execution_fact_at: str | None = None
    last_provider_contact_at: str | None = None
    last_stream_contact_at: str | None = None

    def observe_execution_fact(
        self, occurred_at: str
    ) -> ExecutionLivenessClocks:
        """Advance only the semantic execution-fact clock."""
        return replace(
            self,
            last_execution_fact_at=_latest(
                self.last_execution_fact_at,
                occurred_at,
            ),
        )

    def observe_provider_contact(
        self,
        occurred_at: str,
        *,
        semantic_changed: bool,
    ) -> ExecutionLivenessClocks:
        """Advance provider contact and optionally a genuine semantic fact."""
        updated = replace(
            self,
            last_provider_contact_at=_latest(
                self.last_provider_contact_at,
                occurred_at,
            ),
        )
        return (
            updated.observe_execution_fact(occurred_at)
            if semantic_changed
            else updated
        )

    def observe_stream_contact(
        self, occurred_at: str
    ) -> ExecutionLivenessClocks:
        """Advance only transient transport connectivity."""
        return replace(
            self,
            last_stream_contact_at=_latest(
                self.last_stream_contact_at,
                occurred_at,
            ),
        )


def _latest(current: str | None, candidate: str) -> str:
    candidate_time = _parse(candidate)
    if current is None or candidate_time > _parse(current):
        return candidate
    return current


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("liveness clock must include a timezone")
    return parsed
