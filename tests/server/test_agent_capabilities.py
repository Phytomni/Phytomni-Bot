# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for explicit native-agent capability discovery."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api.agent_capabilities import (
    AGENT_CAPABILITIES,
    get_agent_capability,
    serialize_agent_capability,
)

pytestmark = pytest.mark.server

_EXPECTED_SLUG_ORDER = {
    0: "chat",
    1: "knowledge",
    2: "data",
    3: "review",
    4: "brief_gene",
    5: "analyst",
    6: "deep_genome",
    7: "research",
    8: "design",
    9: "network",
}


def test_capability_descriptors_are_explicit_and_json_compatible() -> None:
    """Every supported slug has a frozen, deterministic descriptor."""
    assert tuple(AGENT_CAPABILITIES) == tuple(
        _EXPECTED_SLUG_ORDER[index]
        for index in range(len(_EXPECTED_SLUG_ORDER))
    )
    assert get_agent_capability("chat").streaming is True
    assert get_agent_capability("knowledge").streaming is True
    assert get_agent_capability("review").streaming is True
    assert get_agent_capability("brief_gene").streaming is True
    assert get_agent_capability("chat").interactive is True
    assert get_agent_capability("review").interactive is True
    assert get_agent_capability("data").streaming is False

    deep_genome = serialize_agent_capability("deep_genome")
    assert deep_genome["report_states"] == ["intermediate", "final"]
    assert deep_genome["artifacts"] is True
    assert deep_genome["degraded_outcomes"] is True
    assert all(
        isinstance(value, (bool, list)) for value in deep_genome.values()
    )


def test_unknown_capability_slug_fails_closed() -> None:
    """A typo cannot silently inherit another agent's capability facts."""
    with pytest.raises(KeyError, match="unknown agent capability slug"):
        get_agent_capability("expert")
