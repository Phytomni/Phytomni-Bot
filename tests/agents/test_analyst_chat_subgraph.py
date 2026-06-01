# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for ``AnalystAgent`` chat invocations.

Pins ``USE_CHAT_SUBGRAPH``: flag-off keeps the legacy nine-node form
where each of the five chat nodes (``parse_query`` / ``data_select`` /
``plan`` / ``check`` / ``tool_extract``) calls ``phyto_chat``
directly; flag-on routes through prep + post pairs surrounding a
single shared chat node registered via ``add_node`` from the
``agents/shared/chat_subgraph`` factory.
"""

from __future__ import annotations

import json
from typing import cast

import pytest

from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.analyst.state import AnalystState
from mcp_server_phytomni.config.defaults import AnalystConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._subgraph_branch_fakes import install_chat_subgraph_mocks

pytestmark = pytest.mark.agent

_ANALYST_MODULE = "mcp_server_phytomni.agents.analyst.graph"


def _build_agent(use_subgraph: bool) -> AnalystAgent:
    """Construct an ``AnalystAgent`` with ``USE_CHAT_SUBGRAPH`` set.

    Uses ``model_copy`` to flip the flag on the inherited
    ``ServerConfig`` field without tripping pylint ``C0103`` on a
    direct UPPERCASE attribute assignment, mirroring the
    ``_build_agent`` shape used by the knowledge-subgraph tests.
    """
    config = AnalystConfig().model_copy(
        update={"USE_CHAT_SUBGRAPH": use_subgraph}
    )
    return AnalystAgent(
        analyst_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Flag-off legacy: each chat node still awaits ``phyto_chat`` directly.
# ---------------------------------------------------------------------------


def _parse_query_legacy_response() -> dict:
    """Return the chat payload the legacy ``parse_query_node`` parses."""
    body = json.dumps(
        {
            "goal_description": "study photosynthesis pathway",
            "data_list": json.dumps({"obs://input.fa": "fasta"}),
            "plan": "",
        }
    )
    return {"choices": [{"message": {"content": body}}]}


async def test_parse_query_node_flag_off_awaits_phyto_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``parse_query_node`` calls ``phyto_chat`` directly."""
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_ANALYST_MODULE,
        legacy_response=_parse_query_legacy_response(),
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=False)
    state = cast(
        AnalystState,
        {
            "goal_description": None,
            "data_list": {},
            "query": "Analyse photosynthesis",
        },
    )
    result = await agent.parse_query_node(state)

    legacy_mock.assert_awaited_once()
    fake_chat_app.ainvoke.assert_not_awaited()
    assert result["goal_description"] == "study photosynthesis pathway"
    assert result["data_list"] == {"obs://input.fa": "fasta"}


async def test_data_select_node_flag_off_awaits_phyto_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``data_select_node`` calls ``phyto_chat`` directly."""
    selected_payload = json.dumps(
        {"selected_data": {"obs://auto.fa": "fasta"}}
    )
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_ANALYST_MODULE,
        legacy_response={
            "choices": [{"message": {"content": selected_payload}}]
        },
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=False)
    state = cast(
        AnalystState,
        {
            "goal_description": "study photosynthesis pathway",
            "data_list": {"obs://user.fa": "fasta"},
        },
    )
    result = await agent.data_select_node(state)

    legacy_mock.assert_awaited_once()
    fake_chat_app.ainvoke.assert_not_awaited()
    assert result["data_list"] == {
        "obs://user.fa": "fasta",
        "obs://auto.fa": "fasta",
    }


async def test_plan_node_flag_off_awaits_phyto_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``plan_node`` calls ``phyto_chat`` directly."""
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_ANALYST_MODULE,
        legacy_response={
            "choices": [
                {"message": {"content": "1. run trimmomatic\n2. assemble"}}
            ]
        },
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=False)
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "method_context": {
                "retrieve_context": "retr",
                "upload_context": "",
            },
            "plan_feedback": None,
            "obs_file_list": [],
            "plan_retries": 0,
        },
    )
    result = await agent.plan_node(state)

    legacy_mock.assert_awaited_once()
    fake_chat_app.ainvoke.assert_not_awaited()
    assert result["plan"] == "1. run trimmomatic\n2. assemble"
    assert result["plan_retries"] == 1
    assert result["plan_feedback"] is None


async def test_check_node_flag_off_awaits_phyto_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``check_node`` calls ``phyto_chat`` directly."""
    critic_payload = json.dumps(
        {"decision": "APPROVED", "score": 9, "feedback": "looks good"}
    )
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_ANALYST_MODULE,
        legacy_response={
            "choices": [{"message": {"content": critic_payload}}]
        },
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=False)
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "data_list": {},
            "method_context": {
                "retrieve_context": "retr",
                "upload_context": "",
            },
            "plan": "1. run trimmomatic\n2. assemble",
            "plan_retries": 1,
            "is_preset_plan": False,
        },
    )
    result = await agent.check_node(state)

    legacy_mock.assert_awaited_once()
    fake_chat_app.ainvoke.assert_not_awaited()
    assert result == {"plan_feedback": "APPROVED"}


async def test_tool_extract_node_flag_off_awaits_phyto_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off ``tool_extract_node`` calls ``phyto_chat`` directly."""
    extract_payload = json.dumps({"tools": ["trimmomatic", "trinity"]})
    legacy_mock, fake_chat_app = install_chat_subgraph_mocks(
        monkeypatch,
        module_path=_ANALYST_MODULE,
        legacy_response={
            "choices": [{"message": {"content": extract_payload}}]
        },
        subgraph_response=None,
    )

    agent = _build_agent(use_subgraph=False)
    state = cast(
        AnalystState,
        {"plan": "1. run trimmomatic\n2. assemble with trinity"},
    )
    result = await agent.tool_extract_node(state)

    legacy_mock.assert_awaited_once()
    fake_chat_app.ainvoke.assert_not_awaited()
    assert result == {"extracted_tools": ["trimmomatic", "trinity"]}


# ---------------------------------------------------------------------------
# Flag-on prep nodes stage ``chat_payload`` + ``pending_post``.
# ---------------------------------------------------------------------------


async def test_parse_query_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for parse_query."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {
            "goal_description": None,
            "data_list": {},
            "query": "Analyse photosynthesis",
        },
    )
    result = await agent.parse_query_prep_node(state)

    assert result["pending_post"] == "parse_query_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "Analyse photosynthesis" in chat_payload["user_query"]
    assert isinstance(chat_payload["chat_kwargs"], dict)
    assert len(chat_payload["chat_kwargs"]) == 17
    assert chat_payload["chat_kwargs"]["response_format"] == {
        "type": "json_schema"
    }
    assert "obs_file_list" not in chat_payload


async def test_parse_query_prep_node_early_exits_when_goal_set() -> None:
    """Prep node skips chat when ``goal_description`` is already set."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {
            "goal_description": "preset goal",
            "data_list": {"obs://x.fa": "fasta"},
            "plan": "preset plan",
            "query": "anything",
        },
    )
    result = await agent.parse_query_prep_node(state)

    assert result["chat_payload"] is None
    assert result["pending_post"] == "parse_query_post_node"
    assert result["goal_description"] == "preset goal"
    assert result["data_list"] == {"obs://x.fa": "fasta"}
    assert result["plan"] == "preset plan"


async def test_data_select_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for data_select."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {
            "goal_description": "study photosynthesis pathway",
            "data_list": {"obs://user.fa": "fasta"},
        },
    )
    result = await agent.data_select_prep_node(state)

    assert result["pending_post"] == "data_select_post_node"
    chat_payload = result["chat_payload"]
    assert "study photosynthesis pathway" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 17
    assert chat_payload["chat_kwargs"]["response_format"] == {
        "type": "json_schema"
    }


async def test_plan_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for plan."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "method_context": {
                "retrieve_context": "retr-ctx",
                "upload_context": "",
            },
            "plan_feedback": None,
            "obs_file_list": [],
            "plan": "",
        },
    )
    result = await agent.plan_prep_node(state)

    assert result["pending_post"] == "plan_post_node"
    chat_payload = result["chat_payload"]
    assert "retr-ctx" in chat_payload["user_query"]
    assert "assemble transcriptome" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 17
    # plan_node defers to analyst_config.RESPONSE_FORMAT
    assert chat_payload["chat_kwargs"]["response_format"] == (
        agent.analyst_config.RESPONSE_FORMAT
    )


async def test_check_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for check."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "data_list": {},
            "method_context": {
                "retrieve_context": "retr",
                "upload_context": "",
            },
            "plan": "draft plan",
            "is_preset_plan": False,
        },
    )
    result = await agent.check_prep_node(state)

    assert result["pending_post"] == "check_post_node"
    chat_payload = result["chat_payload"]
    assert "draft plan" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 17
    assert chat_payload["chat_kwargs"]["response_format"] == {
        "type": "json_object"
    }


async def test_check_prep_node_auto_approves_preset_plan() -> None:
    """Prep node auto-approves when preset plan + no method_context."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {
            "is_preset_plan": True,
            "method_context": None,
        },
    )
    result = await agent.check_prep_node(state)

    assert result["chat_payload"] is None
    assert result["pending_post"] == "check_post_node"
    assert result["plan_feedback"] == "APPROVED"


async def test_tool_extract_prep_node_stages_payload() -> None:
    """Prep node stages payload + pending_post for tool_extract."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {"plan": "1. run trimmomatic\n2. assemble with trinity"},
    )
    result = await agent.tool_extract_prep_node(state)

    assert result["pending_post"] == "tool_extract_post_node"
    chat_payload = result["chat_payload"]
    assert "trimmomatic" in chat_payload["user_query"]
    assert len(chat_payload["chat_kwargs"]) == 17
    assert chat_payload["chat_kwargs"]["response_format"] == {
        "type": "json_object"
    }


# ---------------------------------------------------------------------------
# Flag-on post nodes parse ``state['chat_response']`` into the legacy delta.
# ---------------------------------------------------------------------------


async def test_parse_query_post_node_parses_chat_response() -> None:
    """Post node parses ``chat_response`` into the parse_query delta."""
    agent = _build_agent(use_subgraph=True)
    body = json.dumps(
        {
            "goal_description": "study photosynthesis pathway",
            "data_list": json.dumps({"obs://input.fa": "fasta"}),
            "plan": "",
        }
    )
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "chat_response": {"choices": [{"message": {"content": body}}]},
        },
    )
    result = await agent.parse_query_post_node(state)

    assert result["goal_description"] == "study photosynthesis pathway"
    assert result["data_list"] == {"obs://input.fa": "fasta"}


async def test_parse_query_post_node_noops_on_skip() -> None:
    """Post node returns ``{}`` when prep took the early-return path."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {"chat_payload": None},
    )
    result = await agent.parse_query_post_node(state)

    assert result == {}


async def test_data_select_post_node_parses_chat_response() -> None:
    """Post node merges selected data into the existing ``data_list``."""
    agent = _build_agent(use_subgraph=True)
    selected = json.dumps({"selected_data": {"obs://auto.fa": "fasta"}})
    state = cast(
        AnalystState,
        {
            "data_list": {"obs://user.fa": "fasta"},
            "chat_response": {"choices": [{"message": {"content": selected}}]},
        },
    )
    result = await agent.data_select_post_node(state)

    assert result["data_list"] == {
        "obs://user.fa": "fasta",
        "obs://auto.fa": "fasta",
    }


async def test_plan_post_node_parses_chat_response() -> None:
    """Post node lifts plan content from the chat response."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {
            "plan_retries": 1,
            "chat_response": {
                "choices": [{"message": {"content": "1. trimmomatic"}}]
            },
        },
    )
    result = await agent.plan_post_node(state)

    assert result["plan"] == "1. trimmomatic"
    assert result["plan_retries"] == 2
    assert result["plan_feedback"] is None


async def test_check_post_node_parses_chat_response() -> None:
    """Post node parses the critic decision into ``plan_feedback``."""
    agent = _build_agent(use_subgraph=True)
    critic_payload = json.dumps(
        {"decision": "APPROVED", "score": 9, "feedback": "ok"}
    )
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "plan_retries": 1,
            "chat_response": {
                "choices": [{"message": {"content": critic_payload}}]
            },
        },
    )
    result = await agent.check_post_node(state)

    assert result == {"plan_feedback": "APPROVED"}


async def test_check_post_node_noops_on_skip() -> None:
    """Post node returns ``{}`` when prep took the auto-approve path."""
    agent = _build_agent(use_subgraph=True)
    state = cast(
        AnalystState,
        {"chat_payload": None},
    )
    result = await agent.check_post_node(state)

    assert result == {}


async def test_tool_extract_post_node_parses_chat_response() -> None:
    """Post node lifts ``tools`` from the chat response."""
    agent = _build_agent(use_subgraph=True)
    extract_payload = json.dumps({"tools": ["trimmomatic", "trinity"]})
    state = cast(
        AnalystState,
        {
            "chat_response": {
                "choices": [{"message": {"content": extract_payload}}]
            },
        },
    )
    result = await agent.tool_extract_post_node(state)

    assert result == {"extracted_tools": ["trimmomatic", "trinity"]}


# ---------------------------------------------------------------------------
# Structural xray check: shared chat subgraph is mounted into the analyst app.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_xray_expands_chat_subgraph() -> None:
    """Flag-on graph exposes the shared chat subgraph to ``xray``.

    Structural check: ``StateGraph.get_graph(xray=True)`` walks the
    compiled graph and inlines any node whose body closes over a
    ``CompiledStateGraph``. Mounting chat via
    ``make_chat_node_wrapper(...)`` keeps the compiled subgraph at
    the wrapper's module-level globals, so ``find_subgraph_pregel``
    discovers it and the xray render carries node keys prefixed with
    ``chat:`` (the parent node name plus the subgraph node names).
    A flat ``chat`` key with no child prefix would mean the wrapper
    hid the subgraph behind another closure and the render reverted
    to an opaque box.
    """
    agent = _build_agent(use_subgraph=True)
    node_keys = agent.app.get_graph(xray=True).nodes.keys()
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)
