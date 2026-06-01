# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for ``AnalystAgent`` knowledge retrieval.

Pins ``USE_KNOWLEDGE_SUBGRAPH``: flag-off keeps the legacy
``method_retrieve_node``; flag-on routes through a prep + post pair
surrounding a per-instance compiled KnowledgeAgent app. Also covers
the cross-product when both ``USE_CHAT_SUBGRAPH`` and
``USE_KNOWLEDGE_SUBGRAPH`` are on.
"""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any, TypedDict, cast
from unittest.mock import AsyncMock

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.analyst.state import AnalystState
from mcp_server_phytomni.config.defaults import AnalystConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_ANALYST_MODULE = "mcp_server_phytomni.agents.analyst.graph"
_CORE_MODULE = "mcp_server_phytomni.agents.analyst.core"


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

    The flag-on path constructs the per-instance compiled KA subgraph
    inside ``AnalystAgent.__init__``; tests substitute a tiny compiled
    subgraph so the structural xray walk discovers it through the
    wrapper's closure free-vars while keeping the test fully offline
    (no real KnowledgeAgent compile, no real retrieve).
    """
    fake_app = _build_fake_knowledge_app()
    monkeypatch.setattr(
        f"{_CORE_MODULE}.build_knowledge_app", lambda **_kwargs: fake_app
    )
    return fake_app


def _build_agent(
    use_subgraph: bool,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    use_chat_subgraph: bool = False,
) -> AnalystAgent:
    """Construct an ``AnalystAgent`` with the knowledge flag set.

    Uses ``model_copy`` to flip the flag on the inherited
    ``ServerConfig`` field without tripping pylint ``C0103`` on a
    direct UPPERCASE attribute assignment. The optional
    ``use_chat_subgraph`` argument exercises the cross-product wire
    when both flags are on. When ``monkeypatch`` is supplied, the
    helper installs the fake knowledge app via
    :func:`_install_fake_knowledge_app` so the flag-on construction
    stays offline.
    """
    if use_subgraph and monkeypatch is not None:
        _install_fake_knowledge_app(monkeypatch)
    config = AnalystConfig().model_copy(
        update={
            "USE_KNOWLEDGE_SUBGRAPH": use_subgraph,
            "USE_CHAT_SUBGRAPH": use_chat_subgraph,
        }
    )
    return AnalystAgent(
        analyst_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Flag-off legacy: ``method_retrieve_node`` still awaits ``multi_retrieve``.
# ---------------------------------------------------------------------------


async def test_method_retrieve_node_flag_off_awaits_multi_retrieve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``method_retrieve_node`` calls ``multi_retrieve`` directly."""
    legacy_mock = AsyncMock(
        return_value={"doc_list": [{"title": "Doc A", "content": "doc-A"}]}
    )
    monkeypatch.setattr(f"{_ANALYST_MODULE}.multi_retrieve", legacy_mock)
    monkeypatch.setattr(
        f"{_ANALYST_MODULE}.download_upload_context",
        AsyncMock(return_value=("uploaded", 9)),
    )

    agent = _build_agent(use_subgraph=False)
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "obs_file_list": [],
        },
    )
    result = await agent.method_retrieve_node(state)

    legacy_mock.assert_awaited_once()
    assert result["method_context"]["upload_context"] == "uploaded"
    assert "doc-A" in result["method_context"]["retrieve_context"]


# ---------------------------------------------------------------------------
# Flag-on prep node stages ``knowledge_payload`` + ``pending_post_knowledge``.
# ---------------------------------------------------------------------------


async def test_method_retrieve_prep_node_stages_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prep node stages ``knowledge_payload`` and post sentinel."""
    agent = _build_agent(use_subgraph=True, monkeypatch=monkeypatch)
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
# Flag-on post node parses ``knowledge_response`` into ``method_context``.
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
    agent = _build_agent(use_subgraph=True, monkeypatch=monkeypatch)
    state = cast(
        AnalystState,
        {
            "obs_file_list": [],
            "knowledge_response": {
                "retrieved_docs": [{"title": "Doc A", "content": "doc-A"}],
            },
        },
    )
    result = await agent.method_retrieve_post_node(state)

    method_context = result["method_context"]
    assert method_context["upload_context"] == "uploaded"
    assert "doc-A" in method_context["retrieve_context"]


async def test_method_retrieve_post_node_defaults_empty_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post node tolerates a knowledge response without docs."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.analyst.graph_knowledge_subgraph"
        ".download_upload_context",
        AsyncMock(return_value=("", 0)),
    )
    agent = _build_agent(use_subgraph=True, monkeypatch=monkeypatch)
    state = cast(
        AnalystState,
        {
            "obs_file_list": [],
            "knowledge_response": None,
        },
    )
    result = await agent.method_retrieve_post_node(state)

    method_context = result["method_context"]
    assert method_context["upload_context"] == ""
    assert method_context["retrieve_context"] == ""


# ---------------------------------------------------------------------------
# Constructor: ``_knowledge_app`` is only instantiated when the flag is on.
# ---------------------------------------------------------------------------


def test_knowledge_app_built_only_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_knowledge_app`` is ``None`` flag-off, populated flag-on."""
    agent_off = _build_agent(use_subgraph=False)
    assert agent_off._knowledge_app is None
    fake_app = _install_fake_knowledge_app(monkeypatch)
    agent_on = AnalystAgent(
        analyst_config=AnalystConfig().model_copy(
            update={"USE_KNOWLEDGE_SUBGRAPH": True}
        ),
        sensitive_config=SensitiveConfig.load(),
    )
    assert agent_on._knowledge_app is fake_app


# ---------------------------------------------------------------------------
# Structural: flag-off has the legacy node; flag-on has the prep+post pair.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_off_keeps_legacy_method_retrieve_node() -> None:
    """Flag-off compiled graph has ``method_retrieve_node`` only."""
    agent = _build_agent(use_subgraph=False)
    node_keys = set(agent.app.get_graph(xray=True).nodes.keys())
    assert "method_retrieve_node" in node_keys
    assert "method_retrieve_prep_node" not in node_keys
    assert "method_retrieve_post_node" not in node_keys


def test_compiled_graph_flag_on_xray_expands_knowledge_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on graph exposes the shared knowledge subgraph to ``xray``.

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
    agent = _build_agent(use_subgraph=True, monkeypatch=monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(key.startswith("knowledge:") for key in node_keys), sorted(
        node_keys
    )
    assert "method_retrieve_prep_node" in node_keys
    assert "method_retrieve_post_node" in node_keys
    assert "method_retrieve_node" not in node_keys


# ---------------------------------------------------------------------------
# Cross-product: both flags on — chat AND knowledge subgraphs are mounted.
# ---------------------------------------------------------------------------


def test_compiled_graph_both_flags_on_xray_expands_both_subgraphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both flags on: xray surfaces ``chat:`` AND ``knowledge:`` keys."""
    agent = _build_agent(
        use_subgraph=True,
        monkeypatch=monkeypatch,
        use_chat_subgraph=True,
    )
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)
    assert any(key.startswith("knowledge:") for key in node_keys), sorted(
        node_keys
    )
    # Cross-product still substitutes the method_retrieve site.
    assert "method_retrieve_prep_node" in node_keys
    assert "method_retrieve_post_node" in node_keys
    assert "method_retrieve_node" not in node_keys
