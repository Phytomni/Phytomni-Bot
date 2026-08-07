# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for explicit native-agent capability discovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mcp_server_phytomni.api.advertised_protocols import (
    RESULT_ARCHIVE_PROTOCOL,
    RESULT_ARCHIVE_PROTOCOL_VERSION,
    serialize_protocols,
)
from mcp_server_phytomni.api.agent_capabilities import (
    AGENT_CAPABILITIES,
    ExpertAttachmentRequirement,
    agent_supports_attachment_channels,
    filter_tools_for_attachment_channels,
    filter_tools_for_expert_attachments,
    get_agent_capability,
    get_agent_slug_for_tool,
    get_attachment_capability,
    required_attachment_channels,
    serialize_agent_capability,
)
from mcp_server_phytomni.api.openai_mapping import (
    tool_accepts_obs,
    tool_accepts_stream,
)
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.attachment_assets import (
    ResolvedAsset,
    ResolvedAttachmentBundle,
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

_EXPECTED_ATTACHMENTS = {
    "chat": (True, False, True),
    "knowledge": (True, False, True),
    "data": (False, False, False),
    "analyst": (True, True, False),
    "review": (True, False, True),
    "research": (True, True, False),
    "brief_gene": (False, False, False),
    "deep_genome": (False, False, False),
    "design": (True, False, False),
    "network": (True, False, False),
}

_ALL_PUBLIC_TOOLS = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)


def _tools_for_slugs(slugs: set[str]) -> tuple[str, ...]:
    """Return public tool names for the requested canonical slug set."""
    return tuple(
        tool
        for tool in _ALL_PUBLIC_TOOLS
        if get_agent_slug_for_tool(tool) in slugs
    )


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
    for slug in ("analyst", "research", "design", "network"):
        capability = serialize_agent_capability(slug)
        assert capability["report_states"] == ["final"]
        assert capability["artifacts"] is True
        assert capability["degraded_outcomes"] is True


def test_result_archive_protocol_requires_direct_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive protocol is advertised only outside relay-only mode."""
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)

    direct = serialize_protocols(lambda: False)

    assert direct[RESULT_ARCHIVE_PROTOCOL] == [RESULT_ARCHIVE_PROTOCOL_VERSION]
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    assert RESULT_ARCHIVE_PROTOCOL not in serialize_protocols(lambda: False)


def test_attachment_matrix_is_exact() -> None:
    """Every canonical slug publishes the approved attachment channels."""
    for slug, expected in _EXPECTED_ATTACHMENTS.items():
        attachments = get_attachment_capability(slug)
        assert (
            attachments.document_context is not None,
            attachments.datasets is not None,
            attachments.expert_forwarding,
        ) == expected


@pytest.mark.parametrize(
    ("channels", "supported_tools"),
    [
        (
            frozenset(),
            _ALL_PUBLIC_TOOLS,
        ),
        (
            frozenset({"documents"}),
            _tools_for_slugs(
                {
                    "chat",
                    "knowledge",
                    "review",
                    "analyst",
                    "research",
                    "design",
                    "network",
                }
            ),
        ),
        (
            frozenset({"datasets"}),
            _tools_for_slugs({"analyst", "research"}),
        ),
        (
            frozenset({"documents", "datasets"}),
            _tools_for_slugs({"analyst", "research"}),
        ),
    ],
)
def test_attachment_channel_predicates_and_tool_filtering(
    channels: frozenset[str],
    supported_tools: tuple[str, ...],
) -> None:
    """Capability-derived predicates retain only authorized input tools."""
    allowed_tools = _ALL_PUBLIC_TOOLS

    assert (
        tuple(
            tool
            for tool in allowed_tools
            if agent_supports_attachment_channels(tool, channels)
        )
        == supported_tools
    )
    assert (
        filter_tools_for_attachment_channels(
            allowed_tools=allowed_tools,
            channels=channels,
        )
        == supported_tools
    )


def test_attachment_channel_filter_discards_unknown_without_synthesis() -> (
    None
):
    """An unrecognized tool cannot be introduced by channel filtering."""
    assert filter_tools_for_attachment_channels(
        allowed_tools=("unknown-tool", "AnalystAgent"),
        channels=frozenset({"datasets"}),
    ) == ("AnalystAgent",)


def test_expert_attachment_filter_intersects_capability() -> None:
    """Managed and legacy Expert requirements remain independent facts."""
    allowed = (
        "DataAgent",
        "DigitalDesignAgent",
        "AnalystAgent",
        "BriefGeneAgent",
        "ChatAgent",
    )

    assert filter_tools_for_expert_attachments(
        allowed_tools=allowed,
        requirement=ExpertAttachmentRequirement(managed_assets=True),
    ) == ("DigitalDesignAgent", "AnalystAgent", "ChatAgent")
    assert filter_tools_for_expert_attachments(
        allowed_tools=("unknown-tool", *allowed),
        requirement=ExpertAttachmentRequirement(
            managed_assets=True,
            legacy_documents=True,
        ),
    ) == ("DigitalDesignAgent", "AnalystAgent", "ChatAgent")


def test_required_attachment_channels_follow_bundle_partitions() -> None:
    """A resolved bundle becomes the exact channel requirement set."""
    document = ResolvedAsset(
        asset_id="file_document",
        reference="obs://document",
        filename="document.pdf",
        content_type="application/pdf",
        size_bytes=1,
        purpose="document",
    )
    dataset = ResolvedAsset(
        asset_id="file_dataset",
        reference="obs://dataset",
        filename="dataset.csv",
        content_type="text/csv",
        size_bytes=1,
        purpose="dataset",
    )

    assert (
        required_attachment_channels(ResolvedAttachmentBundle(assets=()))
        == frozenset()
    )
    assert required_attachment_channels(
        ResolvedAttachmentBundle(assets=(document,))
    ) == frozenset({"documents"})
    assert required_attachment_channels(
        ResolvedAttachmentBundle(assets=(dataset,))
    ) == frozenset({"datasets"})
    assert required_attachment_channels(
        ResolvedAttachmentBundle(assets=(document, dataset))
    ) == frozenset({"documents", "datasets"})


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
    assert hashlib.sha256(golden.encode("utf-8")).hexdigest() == (
        "66452e129e2c0746330fb8bb15b67713765d793aa7e7987737c2c435d255d2b8"
    )


def test_attachment_limits_are_public_and_exact() -> None:
    """The published limits match the invocation validator contract."""
    for slug, channel in (
        ("chat", "document_context"),
        ("analyst", "datasets"),
        ("design", "document_context"),
        ("network", "document_context"),
    ):
        limits = serialize_agent_capability(slug)["attachments"][channel]
        assert limits["max_file_bytes"] == 26_214_400
        assert limits["max_files"] == 10
        assert limits["max_total_bytes"] == 52_428_800


@pytest.mark.parametrize("slug", ["design", "network"])
def test_design_and_network_accept_only_document_context(slug: str) -> None:
    """The two added channels do not enable datasets or Expert forwarding."""
    attachments = serialize_agent_capability(slug)["attachments"]
    assert attachments["document_context"] is not None
    assert attachments["datasets"] is None
    assert attachments["expert_forwarding"] is False


def test_obs_policy_reads_attachment_registry() -> None:
    """The OpenAI tool gate follows the registry's document capability."""
    assert tool_accepts_obs("ChatAgent") is True
    assert tool_accepts_obs("KnowledgeAgent") is True
    assert tool_accepts_obs("ReviewAgent") is True
    assert tool_accepts_obs("BriefGeneAgent") is False
    assert tool_accepts_obs("DataAgent") is False
    assert tool_accepts_obs("unknown-tool") is False


def test_stream_policy_matches_the_public_capability_contract() -> None:
    """The HTTP stream gate matches the published agent capabilities."""
    expected = {
        "ChatAgent": True,
        "KnowledgeAgent": True,
        "DataAgent": False,
        "ReviewAgent": True,
        "BriefGeneAgent": True,
        "AnalystAgent": False,
        "DeepGenomeAgent": False,
        "InSilicoResearchAgent": False,
        "DigitalDesignAgent": False,
        "GeneNetworkAgent": False,
    }
    for tool_name, supports_stream in expected.items():
        assert tool_accepts_stream(tool_name) is supports_stream
    assert tool_accepts_stream("unknown-tool") is False


def test_unknown_capability_slug_fails_closed() -> None:
    """A typo cannot silently inherit another agent's capability facts."""
    with pytest.raises(KeyError, match="unknown agent capability slug"):
        get_agent_capability("expert")
