# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for explicit native-agent capability discovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from tests.support.attachment_fakes import EXPERT_ATTACHMENT_ALLOWED_TOOLS
from tests.support.execution_event_fixtures import (
    expected_trace_detail_limits,
    expected_unknown_operation_presenter,
)
from tests.support.public_agent_catalog_expectations import (
    EXPECTED_EXECUTION_DRIVERS,
    EXPECTED_EXECUTION_RUNTIME_FEATURES,
    EXPECTED_EXECUTION_TARGET_KINDS,
    EXPECTED_PRESENTER_OPERATIONS,
)

from mcp_server_phytomni.agents.research.scientific_formats import (
    advertised_research_formats,
)
from mcp_server_phytomni.api.advertised_protocols import (
    RESEARCH_INPUT_PROTOCOL,
    RESEARCH_INPUT_PROTOCOL_VERSION,
    RESULT_ARCHIVE_PROTOCOL,
    RESULT_ARCHIVE_PROTOCOL_VERSION,
    serialize_protocols,
    serialize_research_input_descriptor,
)
from mcp_server_phytomni.api.agent_capabilities import (
    AGENT_CAPABILITIES,
    ExpertAttachmentRequirement,
    agent_supports_attachment_channels,
    build_research_input_descriptor,
    filter_tools_for_attachment_channels,
    filter_tools_for_expert_attachments,
    get_agent_capability,
    get_agent_slug_for_tool,
    get_attachment_capability,
    required_attachment_channels,
    serialize_agent_capability,
    serialize_execution_runtime_capability,
)
from mcp_server_phytomni.api.openai_mapping import (
    tool_accepts_obs,
    tool_accepts_stream,
)
from mcp_server_phytomni.api.routes import agents as agent_routes
from mcp_server_phytomni.api.routes.agent_dependencies import (
    AgentRouteDependencies,
)
from mcp_server_phytomni.api.routes.agents import _register_native_routes
from mcp_server_phytomni.config.api_limits import ApiLimitsConfig
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.attachment_assets import (
    ResolvedAsset,
    ResolvedAttachmentBundle,
)
from mcp_server_phytomni.runtime.resumable_uploads import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
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
    "design": (False, False, False),
    "network": (False, False, False),
}

_PUBLIC_CHANNEL_KEYS = {
    "argument",
    "max_file_bytes",
    "max_files",
    "max_total_bytes",
}
_OBSOLETE_PUBLIC_CHANNEL_FIELDS = {
    "extensions",
    "formats",
    "encoding",
    "delimiter",
    "requires_description",
    "compressed",
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
    execution_events = serialize_agent_capability("chat")["execution_events"]
    assert execution_events == {
        "major_version": 1,
        "resumable_history": True,
        "custom_event": "phyto.run_event",
        "target_kinds": list(EXPECTED_EXECUTION_TARGET_KINDS),
    }

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


def test_execution_event_capability_follows_production_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Bot-first rollback stops both production and capability discovery."""
    monkeypatch.setenv("PHYTOMNI_EXECUTION_EVENTS_ENABLED", "false")

    assert serialize_agent_capability("chat")["execution_events"] == {}


def test_execution_runtime_capability_is_complete_and_bounded() -> None:
    """Web can negotiate the V2 runtime without duplicating Bot constants."""
    capability = serialize_execution_runtime_capability()
    capability.pop("operation_records")

    assert capability == {
        **EXPECTED_EXECUTION_RUNTIME_FEATURES,
        "drivers": list(EXPECTED_EXECUTION_DRIVERS),
        "target_kinds": list(EXPECTED_EXECUTION_TARGET_KINDS),
        "limits": {
            "default_event_page": 50,
            "max_event_page": 200,
            "max_events_per_execution": 10_000,
            "max_live_backlog": 1_000,
            "max_event_bytes": 16_384,
            "max_content_delta_bytes": 16_384,
            "max_todo_items": 100,
            "heartbeat_seconds": 15,
        },
        "compatibility": {
            "state": "read_only",
            "v1_read_projection": True,
            "v1_write_authority": False,
        },
    }


def test_execution_runtime_capability_advertises_operation_records() -> None:
    """Verify execution runtime capability advertises operation records."""
    capability = serialize_execution_runtime_capability()["operation_records"]

    assert capability["major_version"] == 1
    assert capability["grouping_key"] == "work_unit_id"
    assert capability["attempt_history"] is True
    assert (
        capability["unknown_presenter"]
        == expected_unknown_operation_presenter()
    )
    assert capability["execution_log_artifact_role"] == "execution_log"
    assert capability["liveness_clocks"] == [
        "last_execution_fact_at",
        "last_provider_contact_at",
        "last_stream_contact_at",
    ]
    assert capability["limits"] == expected_trace_detail_limits()
    assert {
        presenter["operation_key"] for presenter in capability["presenters"]
    } == EXPECTED_PRESENTER_OPERATIONS


def test_agent_work_trace_capability_is_truthful_for_every_agent() -> None:
    """Verify agent work trace capability is truthful for every agent."""
    network = serialize_agent_capability("network")["work_trace"]
    assert network == {
        "major_version": 1,
        "state": "supported",
        "features": {
            "lifecycle": "supported",
            "semantic_phases": "supported",
            "semantic_tools": "supported",
            "public_reasoning": "supported",
            "trace_target": "supported",
        },
        "target": {"kind": "trace", "major_version": 1},
        "detail_endpoint": (
            "/v2/executions/{execution_id}/targets/trace/{target_id}"
        ),
    }
    for slug in ("analyst", "deep_genome", "research", "design"):
        work_trace = serialize_agent_capability(slug)["work_trace"]
        assert work_trace["state"] == "supported"
        assert work_trace["features"] == {
            "lifecycle": "supported",
            "semantic_phases": "supported",
            "semantic_tools": "supported",
            "public_reasoning": "unsupported",
            "trace_target": "supported",
        }
        assert work_trace["target"] == {
            "kind": "trace",
            "major_version": 1,
        }
        assert work_trace["detail_endpoint"].endswith(
            "/targets/trace/{target_id}"
        )

    for slug in ("chat", "knowledge", "data", "review", "brief_gene"):
        work_trace = serialize_agent_capability(slug)["work_trace"]
        assert work_trace == {
            "major_version": 1,
            "state": "supported",
            "features": {
                "lifecycle": "supported",
                "semantic_phases": "supported",
                "semantic_tools": "supported",
                "public_reasoning": "unsupported",
                "trace_target": "unsupported",
            },
        }


def test_result_archive_protocol_requires_direct_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive protocol is advertised only outside relay-only mode."""
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)

    direct = serialize_protocols()

    assert direct[RESULT_ARCHIVE_PROTOCOL] == [RESULT_ARCHIVE_PROTOCOL_VERSION]
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    assert RESULT_ARCHIVE_PROTOCOL not in serialize_protocols()


def test_research_input_descriptor_projects_effective_limits_and_formats() -> (
    None
):
    """The detached descriptor shares config limits and registry formats."""
    config = ApiLimitsConfig()

    descriptor = build_research_input_descriptor(config)

    assert descriptor.max_user_query_chars == config.API_MAX_USER_QUERY_CHARS
    assert (
        descriptor.max_attachments_per_request
        == config.API_MAX_ATTACHMENTS_PER_REQUEST
    )
    assert (
        descriptor.max_research_dataset_paths
        == config.API_MAX_RESEARCH_DATASET_PATHS
    )
    assert (
        descriptor.max_research_input_references
        == config.API_MAX_RESEARCH_INPUT_REFERENCES
    )
    assert descriptor.dataset_formats == advertised_research_formats()
    assert serialize_research_input_descriptor(config) == {
        "max_user_query_chars": config.API_MAX_USER_QUERY_CHARS,
        "max_attachments_per_request": config.API_MAX_ATTACHMENTS_PER_REQUEST,
        "max_research_dataset_paths": config.API_MAX_RESEARCH_DATASET_PATHS,
        "max_research_input_references": (
            config.API_MAX_RESEARCH_INPUT_REFERENCES
        ),
        "dataset_formats": list(advertised_research_formats()),
    }


def test_research_protocol_is_not_registered_before_readiness() -> None:
    """Pure descriptor support does not prematurely change the catalog."""
    protocols = serialize_protocols()

    assert RESEARCH_INPUT_PROTOCOL not in protocols
    assert RESEARCH_INPUT_PROTOCOL_VERSION == 1


def test_attachment_matrix_is_exact() -> None:
    """Every canonical slug publishes the approved attachment channels."""
    for slug, expected in _EXPECTED_ATTACHMENTS.items():
        attachments = get_attachment_capability(slug)
        assert (
            attachments.document_context is not None,
            attachments.datasets is not None,
            attachments.expert_forwarding,
        ) == expected

        public_attachments = serialize_agent_capability(slug)["attachments"]
        for channel, argument, supported in (
            ("document_context", "obs_file_list", expected[0]),
            ("datasets", "data_list", expected[1]),
        ):
            descriptor = public_attachments[channel]
            if not supported:
                assert descriptor is None
                continue
            assert descriptor is not None
            assert set(descriptor) == _PUBLIC_CHANNEL_KEYS
            assert descriptor["argument"] == argument
            assert not _OBSOLETE_PUBLIC_CHANNEL_FIELDS.intersection(descriptor)


def test_generic_research_capability_is_format_agnostic() -> None:
    """Research runtime details stay in its gated protocol descriptor."""
    research = serialize_agent_capability("research")["attachments"]

    assert research["document_context"]["max_files"] == 10
    assert research["datasets"]["max_files"] == 10
    assert set(research["datasets"]) == _PUBLIC_CHANNEL_KEYS


@pytest.mark.asyncio
async def test_agent_catalog_keeps_generic_capabilities_config_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime config changes only the gated Research protocol descriptor."""
    first_config = ApiLimitsConfig(
        API_MAX_ATTACHMENTS_PER_REQUEST=7,
        API_MAX_RESEARCH_DATASET_PATHS=3,
        API_MAX_RESEARCH_INPUT_REFERENCES=8,
    )
    second_config = ApiLimitsConfig(
        API_MAX_ATTACHMENTS_PER_REQUEST=9,
        API_MAX_RESEARCH_DATASET_PATHS=4,
        API_MAX_RESEARCH_INPUT_REFERENCES=12,
    )
    monkeypatch.setattr(
        agent_routes,
        "research_input_root_worker_ready",
        lambda: False,
    )
    dependencies = cast(
        AgentRouteDependencies,
        SimpleNamespace(
            auth=SimpleNamespace(
                require_agents=lambda: None,
                schedule_run_gc=lambda: None,
            ),
            catalog=SimpleNamespace(
                agent_slug_to_tool={
                    "analyst": "AnalystAgent",
                    "research": "InSilicoResearchAgent",
                },
                remote_agent_slugs=frozenset(),
                legacy_aliases={},
                serialize_capability=serialize_agent_capability,
                serialize_execution_runtime=(
                    serialize_execution_runtime_capability
                ),
            ),
            upload=SimpleNamespace(
                serialize_file_upload_capability=lambda: {},
                schedule_cleanup=lambda: None,
            ),
        ),
    )
    app = FastAPI()
    _register_native_routes(app, dependencies)
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/v1/agents"
    )

    monkeypatch.setattr(agent_routes, "ApiConfig", lambda: first_config)
    first = await route.endpoint(principal=object())
    monkeypatch.setattr(agent_routes, "ApiConfig", lambda: second_config)
    second = await route.endpoint(principal=object())

    assert first.status_code == second.status_code == 200
    assert json.loads(first.body)["data"] == json.loads(second.body)["data"]
    assert (
        json.loads(first.body)["execution_runtime"]["execution_journal_major"]
        == 2
    )


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
    allowed = EXPERT_ATTACHMENT_ALLOWED_TOOLS

    assert filter_tools_for_expert_attachments(
        allowed_tools=allowed,
        requirement=ExpertAttachmentRequirement(managed_assets=True),
    ) == ("AnalystAgent", "ChatAgent")
    assert filter_tools_for_expert_attachments(
        allowed_tools=("unknown-tool", *allowed),
        requirement=ExpertAttachmentRequirement(
            managed_assets=True,
            legacy_documents=True,
        ),
    ) == ("AnalystAgent", "ChatAgent")


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
        "723113e4394695f082fbb899ea3873b24743fda6b758ca58e6de6868d6deb4d5"
    )


def test_attachment_limits_are_public_and_exact() -> None:
    """The published limits match the invocation validator contract."""
    for slug, channel in (
        ("chat", "document_context"),
        ("analyst", "datasets"),
    ):
        limits = serialize_agent_capability(slug)["attachments"][channel]
        assert limits["max_file_bytes"] == MAX_UPLOAD_BYTES
        assert limits["max_files"] == MAX_UPLOAD_FILES
        assert limits["max_total_bytes"] == MAX_UPLOAD_TOTAL_BYTES


@pytest.mark.parametrize("slug", ["design", "network"])
def test_design_and_network_accept_no_attachment_channels(slug: str) -> None:
    """Design and Network do not advertise document or dataset inputs."""
    attachments = serialize_agent_capability(slug)["attachments"]
    assert attachments["document_context"] is None
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
