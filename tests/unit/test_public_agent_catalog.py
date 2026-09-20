# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for the canonical public-agent catalog."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.public_agent_catalog_expectations import (
    EXPECTED_EXECUTION_DRIVERS,
    EXPECTED_EXECUTION_TARGET_KINDS,
    EXPECTED_TRACE_OPERATIONS,
    EXPECTED_TRACE_TARGET_AGENT_SLUGS,
)

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.openai_mapping import MODEL_TO_TOOL
from mcp_server_phytomni.graphs.architecture import GRAPH_ARCHITECTURE
from mcp_server_phytomni.mcp.app import TOOL_ARGUMENT_MODELS, TOOL_HANDLERS
from mcp_server_phytomni.public_agent_catalog import (
    PUBLIC_AGENT_CATALOG,
    RuntimeCatalogSnapshot,
    export_public_agent_catalog,
    graph_runtime_metadata,
    result_delivery_agent_slugs,
    validate_runtime_catalog,
)
from mcp_server_phytomni.runtime import (
    execution_drivers_v2,
    execution_entrypoint_v2,
)
from mcp_server_phytomni.runtime.result_run_layout import (
    RESULT_DELIVERY_AGENTS,
)


def test_catalog_defines_exactly_ten_unique_public_agents() -> None:
    """Verify catalog defines exactly ten unique public agents."""
    assert len(PUBLIC_AGENT_CATALOG) == 10
    assert len({item.tool for item in PUBLIC_AGENT_CATALOG}) == 10
    assert len({item.slug for item in PUBLIC_AGENT_CATALOG}) == 10
    assert {item.lifecycle for item in PUBLIC_AGENT_CATALOG} == {
        "synchronous",
        "resumable",
        "asynchronous",
    }
    assert {item.slug: item.driver for item in PUBLIC_AGENT_CATALOG} == {
        "chat": "resumable_graph",
        "knowledge": "local_graph",
        "data": "local_graph",
        "analyst": "remote_task",
        "review": "resumable_graph",
        "brief_gene": "local_graph",
        "deep_genome": "hybrid",
        "research": "hybrid",
        "design": "remote_fanout",
        "network": "remote_fanout",
    }


def test_every_agent_declares_complete_runtime_policy_metadata() -> None:
    """Verify every agent declares complete runtime policy metadata."""
    for item in PUBLIC_AGENT_CATALOG:
        assert item.topology in {"serial", "conditional", "parallel", "hybrid"}
        assert item.checkpoint in {"none", "graph", "coordinator"}
        assert item.resume in {
            "none",
            "action",
            "recovery",
            "action_and_recovery",
        }
        assert item.cancellation in {
            "cooperative",
            "best_effort",
            "requested_confirmed",
        }
        assert item.deadline_seconds > 0
        assert item.retry in {"none", "bounded", "provider_bounded"}
        assert item.max_attempts > 0
        assert item.join in {
            "not_applicable",
            "all",
            "fail_fast",
            "best_effort",
            "quorum",
        }
        assert item.result_mapper
        assert item.event_contract == "execution_journal_v2"
        assert item.transport_views
        if item.artifacts:
            assert item.artifact_roles
        assert len(item.todo_phases) >= 2
        assert len(set(item.todo_phases)) == len(item.todo_phases)
        assert item.public_summary in {"none", "explicit"}
        assert item.trace_producer in {
            "local_semantic",
            "structured_provider",
            "hybrid",
        }
        assert item.trace_operations
        assert len(set(item.trace_operations)) == len(item.trace_operations)
        assert set(item.trace_target_operations) <= set(item.trace_operations)


def test_catalog_truthfully_declares_reachable_public_presenters() -> None:
    """Verify catalog truthfully declares reachable public presenters."""
    plans = {item.slug: item.todo_phases for item in PUBLIC_AGENT_CATALOG}
    assert set(plans) == {
        "chat",
        "knowledge",
        "data",
        "analyst",
        "review",
        "brief_gene",
        "deep_genome",
        "research",
        "design",
        "network",
    }
    assert plans["knowledge"] == ("retrieving", "generating")
    assert {
        item.slug: item.trace_operations for item in PUBLIC_AGENT_CATALOG
    } == EXPECTED_TRACE_OPERATIONS
    assert {
        item.slug: item.trace_producer for item in PUBLIC_AGENT_CATALOG
    } == {
        "chat": "local_semantic",
        "knowledge": "local_semantic",
        "data": "local_semantic",
        "analyst": "structured_provider",
        "review": "local_semantic",
        "brief_gene": "local_semantic",
        "deep_genome": "hybrid",
        "research": "hybrid",
        "design": "structured_provider",
        "network": "structured_provider",
    }
    assert {
        item.slug: item.trace_target_operations
        for item in PUBLIC_AGENT_CATALOG
        if item.trace_target_operations
    } == {
        slug: ("remote.analysis",)
        for slug in EXPECTED_TRACE_TARGET_AGENT_SLUGS
    }
    # Network has two reachable, explicitly authored bounded summaries. Other
    # Agents must not imply access to raw chain-of-thought.
    assert {
        item.slug: item.public_summary for item in PUBLIC_AGENT_CATALOG
    } == {
        slug: "explicit" if slug == "network" else "none"
        for slug in EXPECTED_TRACE_OPERATIONS
    }


def test_runtime_registrations_match_canonical_catalog() -> None:
    """Verify runtime registrations match canonical catalog."""
    snapshot = RuntimeCatalogSnapshot(
        TOOL_ARGUMENT_MODELS,
        TOOL_HANDLERS,
        getattr(api_app, "_AGENT_SLUG_TO_TOOL"),
        MODEL_TO_TOOL,
        getattr(api_app, "_REMOTE_AGENT_SLUGS"),
        getattr(api_app, "_LEGACY_ALIASES"),
    )
    assert not validate_runtime_catalog(snapshot)


def test_runtime_policy_and_graph_metadata_are_catalog_derived() -> None:
    """Verify runtime policy and graph metadata are catalog derived."""

    assert result_delivery_agent_slugs() == RESULT_DELIVERY_AGENTS
    for graph_id, expected in graph_runtime_metadata().items():
        actual = GRAPH_ARCHITECTURE[graph_id]
        assert actual.classification == "public"
        assert actual.public_agent == expected["public_agent"]
        assert actual.lifecycle == expected["lifecycle"]
        assert (
            actual.subgraph_dependencies == expected["subgraph_dependencies"]
        )
        assert actual.remote_providers == expected["remote_providers"]
        assert actual.driver == expected["driver"]
        assert actual.topology == expected["topology"]


def test_catalog_export_is_stable_and_machine_readable() -> None:
    """Verify catalog export is stable and machine readable."""
    payload = export_public_agent_catalog()
    assert payload["schema_version"] == 1
    assert len(payload["agents"]) == 10
    encoded = json.dumps(payload, sort_keys=True)
    assert "handler" in encoded
    assert "schema" in encoded
    assert "secret" not in encoded.lower()


def test_catalog_advertises_execution_event_replay_contract() -> None:
    """Verify catalog advertises execution event replay contract."""
    payload = export_public_agent_catalog()
    capability = payload["capabilities"]["execution_events"]
    assert capability["major_version"] == 1
    assert capability["resumable_history"] is True
    assert capability["custom_event"] == "phyto.run_event"
    assert capability["target_kinds"] == list(EXPECTED_EXECUTION_TARGET_KINDS)
    runtime = payload["capabilities"]["execution_runtime"]
    assert runtime == {
        "major_version": 1,
        "journal_major_version": 2,
        "drivers": sorted(EXPECTED_EXECUTION_DRIVERS),
    }


def test_domain_handlers_remain_canonical_and_runtime_adapters_stay_thin() -> (
    None
):
    """Runtime owns lifecycle only; MCP domain handlers own business rules."""

    assert {
        item.tool: TOOL_HANDLERS[item.tool].__name__
        for item in PUBLIC_AGENT_CATALOG
    } == {item.tool: item.handler for item in PUBLIC_AGENT_CATALOG}

    for module in (execution_drivers_v2, execution_entrypoint_v2):
        module_file = module.__file__
        assert module_file is not None
        source = Path(module_file).read_text(encoding="utf-8")
        assert "from ..agents" not in source
        assert "import ..agents" not in source
        assert "mcp.handlers" not in source
