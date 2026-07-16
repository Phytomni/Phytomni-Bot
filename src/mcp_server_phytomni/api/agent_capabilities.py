# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Immutable public capability descriptors for native API agents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

__all__ = [
    "AGENT_CAPABILITIES",
    "AgentCapability",
    "get_agent_capability",
    "serialize_agent_capability",
]


@dataclass(frozen=True)
class AgentCapability:
    """Consumer-facing facts for one canonical agent slug."""

    streaming: bool = False
    interactive: bool = False
    report_states: tuple[str, ...] = ()
    artifacts: bool = False
    degraded_outcomes: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize with JSON-compatible deterministic values."""
        return {
            "streaming": self.streaming,
            "interactive": self.interactive,
            "report_states": list(self.report_states),
            "artifacts": self.artifacts,
            "degraded_outcomes": self.degraded_outcomes,
        }


_CAPABILITIES: dict[str, AgentCapability] = {
    "chat": AgentCapability(streaming=True, interactive=True),
    "knowledge": AgentCapability(streaming=True),
    "data": AgentCapability(),
    "review": AgentCapability(streaming=True, interactive=True),
    "brief_gene": AgentCapability(streaming=True),
    "analyst": AgentCapability(),
    "deep_genome": AgentCapability(
        report_states=("intermediate", "final"),
        artifacts=True,
        degraded_outcomes=True,
    ),
    "research": AgentCapability(),
    "design": AgentCapability(),
    "network": AgentCapability(),
}

AGENT_CAPABILITIES: Final[Mapping[str, AgentCapability]] = MappingProxyType(
    _CAPABILITIES
)


def get_agent_capability(slug: str) -> AgentCapability:
    """Return the explicit descriptor for ``slug`` or fail closed."""
    try:
        return AGENT_CAPABILITIES[slug]
    except KeyError as exc:
        raise KeyError(f"unknown agent capability slug: {slug}") from exc


def serialize_agent_capability(slug: str) -> dict[str, Any]:
    """Return one JSON-compatible capability descriptor for ``slug``."""
    return get_agent_capability(slug).to_public_dict()
