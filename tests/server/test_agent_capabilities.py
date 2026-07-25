# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for explicit native-agent capability discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server_phytomni.api.agent_capabilities import (
    AGENT_CAPABILITIES,
    get_agent_capability,
    get_attachment_capability,
    serialize_agent_capability,
)
from mcp_server_phytomni.api.openai_mapping import tool_accepts_obs

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

_EXPECTED_ATTACHMENTS = {
    "chat": (True, False, True),
    "knowledge": (True, False, True),
    "data": (False, False, False),
    "analyst": (True, True, False),
    "review": (True, False, True),
    "research": (True, True, False),
    "brief_gene": (False, False, False),
    "deep_genome": (False, False, False),
    "design": (False, False, False),
    "network": (False, False, False),
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
        isinstance(value, (bool, list, dict)) for value in deep_genome.values()
    )


def test_attachment_matrix_is_exact() -> None:
    """Every canonical slug publishes the approved attachment channels."""
    for slug, expected in _EXPECTED_ATTACHMENTS.items():
        attachments = get_attachment_capability(slug)
        assert (
            attachments.document_context is not None,
            attachments.datasets is not None,
            attachments.expert_forwarding,
        ) == expected


def test_unsupported_attachment_channels_are_json_null() -> None:
    """Unsupported channels are null rather than misleading false objects."""
    attachments = serialize_agent_capability("data")["attachments"]
    assert attachments == {
        "document_context": None,
        "datasets": None,
        "expert_forwarding": False,
    }


def test_capability_golden_is_byte_stable() -> None:
    """The public capability contract is deterministic and reviewable."""
    actual = {
        slug: serialize_agent_capability(slug) for slug in AGENT_CAPABILITIES
    }
    golden_path = (
        Path(__file__).parents[2]
        / "docs"
        / "contracts"
        / "agents"
        / "capabilities.json"
    )
    golden = golden_path.read_text(encoding="utf-8")
    assert json.loads(golden) == actual
    assert golden == json.dumps(actual, ensure_ascii=False, indent=2) + "\n"


def test_obs_policy_reads_attachment_registry() -> None:
    """The OpenAI tool gate follows the registry's document capability."""
    assert tool_accepts_obs("ChatAgent") is True
    assert tool_accepts_obs("KnowledgeAgent") is True
    assert tool_accepts_obs("ReviewAgent") is True
    assert tool_accepts_obs("BriefGeneAgent") is False
    assert tool_accepts_obs("DataAgent") is False
    assert tool_accepts_obs("unknown-tool") is False


def test_unknown_capability_slug_fails_closed() -> None:
    """A typo cannot silently inherit another agent's capability facts."""
    with pytest.raises(KeyError, match="unknown agent capability slug"):
        get_agent_capability("expert")
