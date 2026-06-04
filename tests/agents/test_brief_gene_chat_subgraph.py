# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for BriefGeneAgent's ``generate_node`` chat site.

Pins ``USE_CHAT_SUBGRAPH``: flag-off keeps the legacy ``generate_node``
direct ``phyto_chat`` call; flag-on routes through a prep + shared
``chat`` mount + post triple so LangGraph xray can inline the compiled
chat subgraph in the brief_gene render.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.brief_gene.core import BriefGeneAgent
from mcp_server_phytomni.agents.brief_gene.state import BriefGeneAgentState
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._subgraph_branch_fakes import install_chat_subgraph_mocks

pytestmark = pytest.mark.agent

_BRIEF_GENE_MODULE = "mcp_server_phytomni.agents.brief_gene.core"


def _build_agent(use_subgraph: bool) -> BriefGeneAgent:
    """Construct a ``BriefGeneAgent`` with ``USE_CHAT_SUBGRAPH`` set.

    Uses ``model_copy`` to flip the flag on the inherited
    ``ServerConfig`` field without tripping pylint ``C0103`` on a
    direct UPPERCASE attribute assignment.
    """
    config = BriefGeneConfig().model_copy(
        update={"USE_CHAT_SUBGRAPH": use_subgraph}
    )
    return BriefGeneAgent(
        brief_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


def _gene_found_state() -> BriefGeneAgentState:
    """Minimal ``gene_found=True`` state covering the prep prompt vars."""
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


def _gene_not_found_state() -> BriefGeneAgentState:
    """Minimal ``gene_found=False`` state covering the no-geneid branch."""
    base = _gene_found_state()
    base["gene_found"] = False
    return base


# ---------------------------------------------------------------------------
# Flag-off legacy: generate_node still awaits ``phyto_chat`` directly.
# ---------------------------------------------------------------------------


async def test_generate_node_flag_off_awaits_phyto_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``generate_node`` calls ``phyto_chat`` directly."""
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response={
            "choices": [{"message": {"content": "legacy answer"}}]
        },
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=False)
    state = _gene_found_state()
    delta = await agent.generate_node(state)

    legacy_mock.assert_awaited_once()
    fake_chat_app.ainvoke.assert_not_awaited()
    assert "final_response" in delta


# ---------------------------------------------------------------------------
# Flag-on prep: stages chat_payload + pending_post.
# ---------------------------------------------------------------------------


async def test_generate_prep_node_stages_payload_when_gene_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generate_prep_node`` emits a ChatInput payload for gene_found=True."""
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=True)
    state = _gene_found_state()
    delta = await agent.generate_prep_node(state)

    assert delta["pending_post"] == "generate_post_node"
    assert "chat_payload" in delta
    chat_payload = delta["chat_payload"]
    assert "user_query" in chat_payload
    assert "chat_kwargs" in chat_payload
    # ``with_follow_up`` is explicit-False per the brief_gene contract.
    assert chat_payload["chat_kwargs"]["with_follow_up"] is False


async def test_generate_prep_node_stages_payload_when_gene_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generate_prep_node`` emits a ChatInput payload (gene not found)."""
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=True)
    state = _gene_not_found_state()
    delta = await agent.generate_prep_node(state)

    assert delta["pending_post"] == "generate_post_node"
    chat_payload = delta["chat_payload"]
    # The no-geneid prompt template still produces a user_query string;
    # the prompt content itself differs but the payload shape stays
    # bit-equivalent so the shared chat mount treats both branches
    # uniformly.
    assert isinstance(chat_payload["user_query"], str)
    assert chat_payload["chat_kwargs"]["with_follow_up"] is False


async def test_generate_prep_node_drift_catch_with_follow_up_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drift catch: ``with_follow_up`` MUST stay False at this site.

    brief_gene generates its own follow-up questions in the separate
    ``follow_up_node`` step. The chat subgraph router's
    ``follow_up_node`` branch firing here would cascade an unwanted
    second follow-up generation on top of the main answer. The
    ``False`` value is the explicit AF-14 mitigation pattern; this
    test fails loud if a future refactor accidentally flips it.
    """
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=True)
    state = _gene_found_state()
    delta = await agent.generate_prep_node(state)

    assert delta["chat_payload"]["chat_kwargs"].get("with_follow_up") is False


# ---------------------------------------------------------------------------
# Flag-on post: parses chat_response into final_response.
# ---------------------------------------------------------------------------


async def test_generate_post_node_parses_chat_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generate_post_node`` projects chat_response to final_response."""
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=True)
    state = _gene_found_state()
    state["chat_response"] = {
        "choices": [{"message": {"content": "subgraph answer"}}]
    }
    delta = await agent.generate_post_node(state)

    assert "final_response" in delta
    # ``_attach_metadata`` attaches the retrieved docs onto the final
    # response payload; the chat-completions content survives the
    # projection unchanged.
    final = delta["final_response"]
    assert "choices" in final
    assert final["choices"][0]["message"]["content"] == "subgraph answer"


async def test_generate_post_node_handles_missing_chat_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generate_post_node`` falls back to an empty chat-completions shape.

    Preserves the ``phyto_response is None`` fallback the legacy
    ``generate_node`` already had so downstream ``_attach_metadata``
    always sees a valid ``{"choices": [{"message": {}}]}`` dict.
    """
    install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=True)
    state = _gene_found_state()
    # No ``chat_response`` key staged: simulates the upstream chat
    # mount returning a null payload.
    delta = await agent.generate_post_node(state)

    assert "final_response" in delta
    assert "choices" in delta["final_response"]


# ---------------------------------------------------------------------------
# Compile-time node registration: flag-off vs flag-on wire shapes.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_off_uses_legacy_generate_node() -> None:
    """Flag-off graph registers ``generate_node`` (no prep/post split)."""
    agent = _build_agent(use_subgraph=False)
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "generate_node" in nodes
    assert "generate_prep_node" not in nodes
    assert "generate_post_node" not in nodes
    assert "chat" not in nodes


def test_compiled_graph_flag_on_uses_prep_chat_post() -> None:
    """Flag-on graph registers prep + chat + post (no legacy generate)."""
    agent = _build_agent(use_subgraph=True)
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "generate_prep_node" in nodes
    assert "generate_post_node" in nodes
    assert "chat" in nodes
    # The legacy ``generate_node`` is replaced by the prep/post split.
    assert "generate_node" not in nodes


def test_compiled_graph_flag_on_xray_expands_chat_subgraph() -> None:
    """xray=1 surfaces ``chat:``-prefixed keys under the shared mount."""
    agent = _build_agent(use_subgraph=True)
    nodes = list(agent.app.get_graph(xray=1).nodes.keys())

    # Every child node of the shared ``chat`` subgraph mount appears
    # under the ``chat:`` prefix when xray walks one level down.
    chat_children = [n for n in nodes if n.startswith("chat:")]
    assert chat_children, (
        "Expected the shared chat subgraph to expand at xray=1 "
        f"(saw nodes: {sorted(nodes)})"
    )


# ---------------------------------------------------------------------------
# follow_up site: flag-off legacy + flag-on prep/post split.
# ---------------------------------------------------------------------------


def _state_post_generate() -> BriefGeneAgentState:
    """State after ``generate_post_node`` has staged a final_response.

    Pre-populates ``final_response`` with a chat-completions-shaped
    payload so ``message_content`` returns a non-empty string from
    both the generate site (what the prep node sees as input) and
    from ``_attach_metadata`` paths.
    """
    state = _gene_found_state()
    state["final_response"] = {
        "choices": [{"message": {"content": "Brief gene answer goes here."}}]
    }
    return state


async def test_follow_up_node_flag_off_routes_through_generate_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``follow_up_node`` delegates to ``_generate_follow_up``.

    ``_generate_follow_up`` lives in
    ``brief_gene.pipeline`` and the legacy ``follow_up_node`` body
    imports it through that module path; the chat subgraph mount
    must NOT fire on the legacy path. Mock the helper directly so the
    test stays insulated from the pipeline-module ``phyto_chat``
    import binding the helper closes over.
    """
    _, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_BRIEF_GENE_MODULE,
        legacy_response=None,
        subgraph_response=None,
    )
    fake_helper = AsyncMock(return_value=["Q1", "Q2", "Q3"])
    monkeypatch.setattr(
        f"{_BRIEF_GENE_MODULE}._generate_follow_up",
        fake_helper,
    )

    agent = _build_agent(use_subgraph=False)
    state = _state_post_generate()
    delta = await agent.follow_up_node(state)

    fake_helper.assert_awaited_once()
    fake_chat_app.ainvoke.assert_not_awaited()
    assert delta["follow_up_questions"] == ["Q1", "Q2", "Q3"]
    assert "final_response" in delta


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

    agent = _build_agent(use_subgraph=True)
    state = _state_post_generate()
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

    agent = _build_agent(use_subgraph=True)
    state = _state_post_generate()
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

    agent = _build_agent(use_subgraph=True)
    state = _state_post_generate()
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

    Mirrors the same defensive pattern ``generate_post_node`` uses:
    a missing or null chat_response should not crash the post node;
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

    agent = _build_agent(use_subgraph=True)
    state = _state_post_generate()
    delta = await agent.follow_up_post_node(state)

    assert delta["follow_up_questions"] == []
    assert "final_response" in delta


def test_compiled_graph_flag_on_uses_follow_up_prep_post() -> None:
    """Flag-on graph registers prep + post for follow_up (no legacy node)."""
    agent = _build_agent(use_subgraph=True)
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "follow_up_prep_node" in nodes
    assert "follow_up_post_node" in nodes
    # The legacy ``follow_up_node`` is replaced by the prep/post split
    # on the flag-on path. Its method body stays on the agent class
    # so flag-off (registered by ``_wire_legacy``) still works.
    assert "follow_up_node" not in nodes


def test_compiled_graph_flag_off_keeps_follow_up_node() -> None:
    """Flag-off graph still registers ``follow_up_node`` (no prep/post)."""
    agent = _build_agent(use_subgraph=False)
    nodes = set(agent.app.get_graph(xray=0).nodes.keys())

    assert "follow_up_node" in nodes
    assert "follow_up_prep_node" not in nodes
    assert "follow_up_post_node" not in nodes
