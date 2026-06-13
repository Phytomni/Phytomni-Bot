# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for BriefGeneAgent's follow_up chat site.

The preamble graph produces ``final_response`` via the render node, then
the trailing follow-up hop routes through a prep node that stages the
chat payload, the shared ``chat`` subgraph mount, and a post node, so
LangGraph xray can inline the compiled chat subgraph in the render.
"""

from __future__ import annotations

from typing import cast

import pytest

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.agents.brief_gene.state import BriefGeneAgentState
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._subgraph_branch_fakes import install_chat_subgraph_mocks

pytestmark = pytest.mark.agent

_BRIEF_GENE_MODULE = "mcp_server_phytomni.agents.brief_gene.core"


def _build_agent() -> BriefGeneAgent:
    """Construct a ``BriefGeneAgent`` with default config.

    The chat subgraph mount is unconditional, so no flag override is
    needed.
    """
    return BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _gene_found_state() -> BriefGeneAgentState:
    """Minimal ``gene_found=True`` state covering the follow-up prompt."""
    return cast(
        BriefGeneAgentState,
        {
            "user_query": "What does AT1G01010 do?",
            "is_follow_up": False,
            "gene_found": True,
            "gene_id": "AT1G01010",
            "query_id_version": "tair10",
            "gene_id_version": "tair10",
            "species_code": "ath",
            "species_latin_name": "Arabidopsis thaliana",
            "species_english_name": "thale cress",
            "species_all_name": "Arabidopsis thaliana / thale cress",
            "gene_name_symbol_list": ["AT1G01010"],
            "gene_id_list": ["AT1G01010"],
            "gene_chr": "1",
            "gene_start": "3631",
            "gene_end": "5899",
            "gene_strand": "+",
            "go_string": "GO:0003700",
            "kegg_string": "ath:AT1G01010",
            "interpro_string": "IPR036093",
            "description_string": "transcription factor",
            "retrieved_docs": [{"title": "doc1", "content": "..."}],
            "retrieve_context": "context block",
            "follow_up_questions": [],
            "final_response": {},
        },
    )


# ---------------------------------------------------------------------------
# Compile-time node registration + xray expansion of the shared chat mount.
# ---------------------------------------------------------------------------


def test_compiled_graph_xray_expands_chat_subgraph() -> None:
    """xray=1 surfaces ``chat:``-prefixed keys under the shared mount."""
    agent = _build_agent()
    nodes = list(agent.app.get_graph(xray=1).nodes.keys())

    # Every child node of the shared ``chat`` subgraph mount appears
    # under the ``chat:`` prefix when xray walks one level down.
    chat_children = [n for n in nodes if n.startswith("chat:")]
    assert chat_children, (
        "Expected the shared chat subgraph to expand at xray=1 "
        f"(saw nodes: {sorted(nodes)})"
    )


# ---------------------------------------------------------------------------
# follow_up site: prep + shared chat + post split.
# ---------------------------------------------------------------------------


def _state_post_render() -> BriefGeneAgentState:
    """State after ``render_node`` has staged a final_response.

    Pre-populates ``final_response`` with a chat-completions-shaped
    payload so ``message_content`` returns a non-empty string for the
    follow-up prep node (which summarizes the rendered preamble).
    """
    state = _gene_found_state()
    state["final_response"] = {
        "choices": [{"message": {"content": "Brief gene preamble here."}}]
    }
    return state


async def test_follow_up_prep_node_stages_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``follow_up_prep_node`` emits a ChatInput payload + sentinel."""
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent()
    state = _state_post_render()
    delta = await agent.follow_up_prep_node(state)

    assert delta["pending_post"] == "follow_up_post_node"
    chat_payload = delta["chat_payload"]
    assert isinstance(chat_payload["user_query"], str)
    assert "chat_kwargs" in chat_payload


async def test_follow_up_prep_node_with_follow_up_false_drift_catch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drift catch: ``follow_up_prep_node`` MUST keep ``with_follow_up=False``.

    This site IS the follow-up generation; the chat subgraph router's
    ``follow_up_node`` branch firing here would cascade an unwanted
    recursive follow-up-on-follow-up generation. Asserts the explicit
    False value so a future refactor that accidentally flips it
    trips loud.
    """
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent()
    state = _state_post_render()
    delta = await agent.follow_up_prep_node(state)

    chat_kwargs = delta["chat_payload"]["chat_kwargs"]
    assert chat_kwargs.get("with_follow_up") is False


async def test_follow_up_post_node_parses_chat_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``follow_up_post_node`` projects chat_response to questions list."""
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent()
    state = _state_post_render()
    state["chat_response"] = {
        "choices": [{"message": {"content": "1. What about Q1?\n2. Or Q2?"}}]
    }
    delta = await agent.follow_up_post_node(state)

    assert "follow_up_questions" in delta
    assert isinstance(delta["follow_up_questions"], list)
    # ``_attach_metadata`` carries the original final_response forward
    # with the freshly parsed questions list bolted on.
    assert "final_response" in delta


async def test_follow_up_post_node_handles_missing_chat_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``follow_up_post_node`` falls back when chat_response is absent.

    A missing or null chat_response should not crash the post node;
    ``message_content`` returns "" for an empty dict and
    ``parse_follow_up_questions`` returns an empty list, leaving
    ``follow_up_questions`` as a well-formed empty list.
    """
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent()
    state = _state_post_render()
    delta = await agent.follow_up_post_node(state)

    assert delta["follow_up_questions"] == []
    assert "final_response" in delta


def test_compiled_graph_uses_follow_up_prep_post() -> None:
    """The graph registers prep + post for follow_up (no legacy node)."""
    agent = _build_agent()
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "follow_up_prep_node" in nodes
    assert "follow_up_post_node" in nodes
    # The legacy ``follow_up_node`` was removed; the graph now
    # registers only the prep/post split.
    assert "follow_up_node" not in nodes
