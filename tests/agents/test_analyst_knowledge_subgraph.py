# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``AnalystAgent`` knowledge retrieval.

The ``method_retrieve`` site always routes through a prep + post pair
surrounding a per-instance compiled KnowledgeAgent app. Also covers
the cross-product with the always-mounted chat subgraph.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.analyst.state import AnalystState
from mcp_server_phytomni.config.defaults import AnalystConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.support.subgraph_fakes import (
    RecordingKnowledgeApp,
    assert_subgraph_prefixes,
    install_knowledge_app,
    knowledge_output,
    knowledge_state,
)

pytestmark = pytest.mark.agent

_CORE_MODULE = "mcp_server_phytomni.agents.analyst.core"


async def test_knowledge_fakes_capture_output_and_isolate_state() -> None:
    """Shared knowledge fakes capture calls and return independent values."""
    fake = RecordingKnowledgeApp(output=knowledge_output("captured"))
    state = knowledge_state(user_query="query")
    sibling = knowledge_state()

    result = await fake.compiled.ainvoke(state)
    state["retrieved_docs"].append({"title": "mutated"})

    expected = knowledge_output("captured")
    assert result["retrieved_docs"] == expected["retrieved_docs"]
    assert result["final_response"] == expected["final_response"]
    assert fake.calls == [{"user_query": "query", "retrieved_docs": []}]
    assert sibling["retrieved_docs"] == []


async def test_knowledge_fake_propagates_configured_error() -> None:
    """Shared knowledge fakes do not swallow configured failures."""
    fake = RecordingKnowledgeApp(error=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await fake.compiled.ainvoke(knowledge_state())


def _install_fake_knowledge_app(
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Patch ``build_knowledge_app`` to return a deterministic compiled stub.

    ``AnalystAgent.__init__`` always constructs the per-instance
    compiled KA subgraph; tests substitute a tiny compiled
    subgraph so the structural xray walk discovers it through the
    wrapper's closure free-vars while keeping the test fully offline
    (no real KnowledgeAgent compile, no real retrieve).
    """
    return install_knowledge_app(
        monkeypatch,
        f"{_CORE_MODULE}.build_knowledge_app",
    ).compiled


def _build_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> AnalystAgent:
    """Construct an ``AnalystAgent`` with an offline knowledge app.

    The ``method_retrieve`` site always mounts the prep + post pair
    surrounding the per-instance compiled KnowledgeAgent app, and the
    chat subgraph is always mounted too, so the cross-product wire is
    exercised on every construction. The helper installs the fake
    knowledge app via :func:`_install_fake_knowledge_app` so the
    construction stays offline (no real KnowledgeAgent compile, no
    real retrieve).
    """
    _install_fake_knowledge_app(monkeypatch)
    return AnalystAgent(
        analyst_config=AnalystConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Direct call: ``method_retrieve_node`` still awaits ``multi_retrieve``.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Prep node stages ``knowledge_payload`` + ``pending_post_knowledge``.
# ---------------------------------------------------------------------------


async def test_method_retrieve_prep_node_stages_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prep node stages ``knowledge_payload`` and post sentinel."""
    agent = _build_agent(monkeypatch)
    state = cast(
        AnalystState,
        {"goal_description": "assemble transcriptome"},
    )
    result = await agent.method_retrieve_prep_node(state)

    assert result["pending_post_knowledge"] == "method_retrieve_post_node"
    payload = result["knowledge_payload"]
    assert payload["user_query"] == "assemble transcriptome"
    assert payload["is_generate"] is False
    assert payload["is_follow_up"] is False
    assert payload["repo_id_dict"] == dict(agent.analyst_config.REPO_ID_DICT)
    assert "obs_file_list" not in payload


# ---------------------------------------------------------------------------
# Post node parses ``knowledge_response`` into ``method_context``.
# ---------------------------------------------------------------------------


async def test_method_retrieve_post_node_parses_knowledge_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post node lifts retrieved docs into the legacy ``method_context``."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.analyst.graph_knowledge_subgraph"
        ".download_upload_context",
        AsyncMock(return_value=("uploaded", 9)),
    )
    agent = _build_agent(monkeypatch)
    state = cast(
        AnalystState,
        {
            "obs_file_list": [],
            "knowledge_response": {
                "retrieved_docs": [
                    {
                        "chunk_id": "doc-a",
                        "title": "Doc A",
                        "content": "doc-A",
                    }
                ],
                "retrieval_outcome": "complete",
                "final_response": {},
            },
        },
    )
    result = await agent.method_retrieve_post_node(state)

    method_context = result["method_context"]
    assert method_context["upload_context"] == "uploaded"
    assert "doc-A" in method_context["retrieve_context"]


async def test_method_retrieve_post_node_accepts_explicit_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post node accepts a validated no-match response without docs."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.analyst.graph_knowledge_subgraph"
        ".download_upload_context",
        AsyncMock(return_value=("", 0)),
    )
    agent = _build_agent(monkeypatch)
    state = cast(
        AnalystState,
        {
            "obs_file_list": [],
            "knowledge_response": {
                "retrieved_docs": [],
                "retrieval_outcome": "no_match",
                "final_response": {},
            },
        },
    )
    result = await agent.method_retrieve_post_node(state)

    method_context = result["method_context"]
    assert method_context["upload_context"] == ""
    assert method_context["retrieve_context"] == ""


# ---------------------------------------------------------------------------
# Structural: the method_retrieve site mounts the prep+post pair.
# ---------------------------------------------------------------------------


def test_compiled_graph_xray_expands_knowledge_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled graph exposes the shared knowledge subgraph to ``xray``.

    Structural check: ``StateGraph.get_graph(xray=True)`` walks the
    compiled graph and inlines any node whose body closes over a
    ``CompiledStateGraph``. Mounting knowledge via
    ``make_knowledge_node_wrapper(knowledge_app=...)`` keeps the
    compiled subgraph at the wrapper's closure free-vars, so
    ``find_subgraph_pregel`` discovers it and the xray render carries
    node keys prefixed with ``knowledge:``. A flat ``knowledge`` key
    with no child prefix would mean the wrapper hid the subgraph and
    the render reverted to an opaque box.
    """
    agent = _build_agent(monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(key.startswith("knowledge:") for key in node_keys), sorted(
        node_keys
    )
    assert "method_retrieve_prep_node" in node_keys
    assert "method_retrieve_post_node" in node_keys
    assert "method_retrieve_node" not in node_keys


# ---------------------------------------------------------------------------
# Cross-product: both chat AND knowledge subgraphs are mounted.
# ---------------------------------------------------------------------------


def test_compiled_graph_xray_expands_both_subgraphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xray surfaces both ``chat:`` AND ``knowledge:`` keys."""
    agent = _build_agent(monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert_subgraph_prefixes(node_keys, "chat:", "knowledge:")
    # Cross-product still substitutes the method_retrieve site.
    assert "method_retrieve_prep_node" in node_keys
    assert "method_retrieve_post_node" in node_keys
    assert "method_retrieve_node" not in node_keys
