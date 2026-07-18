# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``DataAgent`` knowledge retrieval.

The retrieve site always routes through a prep + post pair
surrounding a per-instance compiled KnowledgeAgent app. Also covers
the cross-product with the always-mounted chat subgraph.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.data.agent import DataAgent
from mcp_server_phytomni.agents.data.state import DataAgentState
from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_DATA_MODULE = "mcp_server_phytomni.agents.data.agent"


class _FakeKnowledgeState(TypedDict, total=False):
    """Minimal state shape for the offline knowledge-subgraph stub."""

    retrieved_docs: list[dict[str, Any]]


def _build_fake_knowledge_app() -> CompiledStateGraph:
    """Compile a one-node ``StateGraph`` to stand in for the KA subgraph.

    ``find_subgraph_pregel`` (the walker behind ``get_graph(xray=True)``)
    recognises ``CompiledStateGraph`` instances by isinstance, not by
    duck typing, so a ``SimpleNamespace`` cannot satisfy the xray
    expansion. Compiling a trivial ``StateGraph`` that returns an empty
    ``retrieved_docs`` list keeps the test fully offline while still
    presenting a real compiled subgraph for the wrapper's closure to
    capture.
    """

    async def _noop(state: _FakeKnowledgeState) -> dict[str, Any]:
        del state
        return {"retrieved_docs": []}

    workflow: StateGraph = StateGraph(_FakeKnowledgeState)
    workflow.add_node("noop", _noop)
    workflow.add_edge(START, "noop")
    workflow.add_edge("noop", END)
    return workflow.compile()


def _install_fake_knowledge_app(
    monkeypatch: pytest.MonkeyPatch,
) -> CompiledStateGraph:
    """Patch ``build_knowledge_app`` to return a deterministic compiled stub.

    ``DataAgent.__init__`` constructs the per-instance compiled KA
    subgraph unconditionally; tests substitute a tiny compiled
    subgraph so the structural xray walk discovers it through the
    wrapper's closure free-vars while keeping the test fully offline
    (no real KnowledgeAgent compile, no real retrieve).
    """
    fake_app = _build_fake_knowledge_app()
    monkeypatch.setattr(
        f"{_DATA_MODULE}.build_knowledge_app", lambda **_kwargs: fake_app
    )
    return fake_app


def _build_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> DataAgent:
    """Construct a ``DataAgent`` with an offline knowledge app.

    The retrieve site always mounts the prep + post pair surrounding
    the per-instance compiled KnowledgeAgent app, and the chat
    subgraph is always mounted too, so the cross-product wire is
    exercised on every construction. The helper installs the fake
    knowledge app via :func:`_install_fake_knowledge_app` so the
    construction stays offline (no real KnowledgeAgent compile, no
    real retrieve).
    """
    _install_fake_knowledge_app(monkeypatch)
    return DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Prep node stages ``knowledge_payload`` + ``pending_post_knowledge``.
# ---------------------------------------------------------------------------


async def test_retrieve_prep_node_stages_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prep node stages ``knowledge_payload`` and post sentinel.

    The data retrieve site targets a single repo via
    ``DATA_REPO_ID``; the legacy retrieve passes ``DATA_PAGE_SIZE``
    as the per-repo page size. The prep node expresses the same
    retrieval as a one-entry ``repo_id_dict`` so ``multi_retrieve``
    inside the KA subgraph threads ``DATA_PAGE_SIZE`` through to
    the underlying single-repo ``retrieve`` call.
    """
    agent = _build_agent(monkeypatch)
    state = cast(
        DataAgentState,
        {"user_query": "list every transcript per sample"},
    )
    result = await agent.retrieve_prep_node(state)

    assert result["pending_post_knowledge"] == "retrieve_post_node"
    payload = result["knowledge_payload"]
    assert payload["user_query"] == "list every transcript per sample"
    assert payload["is_generate"] is False
    assert payload["is_follow_up"] is False
    assert payload["repo_id_dict"] == {
        agent.data_config.DATA_REPO_ID: agent.data_config.DATA_PAGE_SIZE
    }
    assert "obs_file_list" not in payload


# ---------------------------------------------------------------------------
# Post node parses ``knowledge_response`` into ``retrieve_prompt``.
# ---------------------------------------------------------------------------


async def test_retrieve_post_node_parses_knowledge_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post node lifts retrieved docs into the ``retrieve_prompt``."""
    agent = _build_agent(monkeypatch)
    state = cast(
        DataAgentState,
        {
            "user_query": "list every transcript per sample",
            "knowledge_response": {
                "retrieved_docs": [
                    {"title": "Scenario A", "content": "scenario-A"},
                ],
            },
        },
    )
    result = await agent.retrieve_post_node(state)

    assert "scenario-A" in result["retrieve_prompt"]
    assert "list every transcript per sample" in result["retrieve_prompt"]


async def test_retrieve_post_node_defaults_empty_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post node tolerates a knowledge response without docs.

    Pins the no-docs edge case: an empty ``knowledge_response`` and a
    missing key both default the doc list to ``[]`` so the fragment
    loop simply produces no scenario context. The stitched prompt
    still carries the ``user_query`` so the rewrite stage gets a
    valid template.
    """
    agent = _build_agent(monkeypatch)
    state = cast(
        DataAgentState,
        {
            "user_query": "list every transcript per sample",
            "knowledge_response": None,
        },
    )
    result = await agent.retrieve_post_node(state)

    assert "list every transcript per sample" in result["retrieve_prompt"]
    # Empty doc list yields no scenario fragment.
    assert "scenario" not in result["retrieve_prompt"].lower().split("user")[0]


# ---------------------------------------------------------------------------
# Structural: the retrieve site mounts the prep + post pair.
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
    assert "retrieve_prep_node" in node_keys
    assert "retrieve_post_node" in node_keys
    assert "retrieve_node" not in node_keys


# ---------------------------------------------------------------------------
# Cross-product: chat AND knowledge subgraphs mounted.
# ---------------------------------------------------------------------------


def test_compiled_graph_xray_expands_both_subgraphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Xray surfaces ``chat:`` AND ``knowledge:`` keys.

    Both the chat subgraph and the knowledge subgraph are always
    mounted, so both appear in the xray render together.
    """
    agent = _build_agent(monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)
    assert any(key.startswith("knowledge:") for key in node_keys), sorted(
        node_keys
    )
    # Cross-product still substitutes the retrieve site.
    assert "retrieve_prep_node" in node_keys
    assert "retrieve_post_node" in node_keys
    assert "retrieve_node" not in node_keys
