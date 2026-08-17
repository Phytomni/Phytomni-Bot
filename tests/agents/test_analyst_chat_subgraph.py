# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``AnalystAgent`` chat invocations.

Each of the five chat sites (``parse_query`` / ``data_select`` /
``plan`` / ``check`` / ``tool_extract``) routes through prep + post
pairs surrounding a single shared chat node registered via
``mount_chat_node`` from the ``agents/shared/chat_subgraph`` factory.
"""

from __future__ import annotations

import json
from typing import cast

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst.core import AnalystAgent
from mcp_server_phytomni.agents.analyst.state import AnalystState
from mcp_server_phytomni.config.defaults import AnalystConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from tests.support.subgraph_fakes import (
    ANALYST_CHAT_MOUNT_TOPOLOGY,
    assert_agent_chat_mount_topology,
)

pytestmark = pytest.mark.agent


def _build_agent() -> AnalystAgent:
    """Construct an ``AnalystAgent`` with the default analyst config.

    The chat subgraph is now unconditional, so the constructor needs
    no flag override; this mirrors the ``_build_agent`` shape used by
    the knowledge-subgraph tests.
    """
    return AnalystAgent(
        analyst_config=AnalystConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def test_compiled_graph_preserves_chat_mount_topology() -> None:
    """Pin Analyst's public schema, routes, xray, and checkpointer contract."""
    assert_agent_chat_mount_topology(
        _build_agent(), ANALYST_CHAT_MOUNT_TOPOLOGY
    )


async def test_parse_query_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for parse_query."""
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
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
    assert len(chat_payload["chat_kwargs"]) == 18
    assert chat_payload["chat_kwargs"]["response_format"] == {
        "type": "json_schema"
    }
    assert chat_payload["chat_kwargs"]["max_tokens"] == 4096


async def test_plan_prep_node_stages_payload() -> None:
    """Prep node stages ``chat_payload`` + ``pending_post`` for plan."""
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
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


async def test_parse_query_post_node_parses_chat_response() -> None:
    """Post node parses ``chat_response`` into the parse_query delta."""
    agent = _build_agent()
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
    agent = _build_agent()
    state = cast(
        AnalystState,
        {"chat_payload": None},
    )
    result = await agent.parse_query_post_node(state)

    assert result == {}


async def test_data_select_post_node_parses_chat_response() -> None:
    """Post node merges selected data into the existing ``data_list``."""
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
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
    agent = _build_agent()
    state = cast(
        AnalystState,
        {"chat_payload": None},
    )
    result = await agent.check_post_node(state)

    assert result == {}


async def test_tool_extract_post_node_parses_chat_response() -> None:
    """Post node lifts ``tools`` from the chat response."""
    agent = _build_agent()
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
    agent = _build_agent()
    node_keys = agent.app.get_graph(xray=True).nodes.keys()
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)


async def test_parse_query_post_node_parses_json_code_fence() -> None:
    """Post node extracts JSON from a ```json ... ``` fenced block."""
    agent = _build_agent()
    body = json.dumps(
        {
            "goal_description": "study drought tolerance",
            "data_list": json.dumps({"obs://sample.fa": "fasta"}),
            "plan": "initial plan",
        }
    )
    fenced_content = f"```json\n{body}\n```"
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "chat_response": {
                "choices": [{"message": {"content": fenced_content}}]
            },
        },
    )
    result = await agent.parse_query_post_node(state)

    assert result["goal_description"] == "study drought tolerance"
    assert result["data_list"] == {"obs://sample.fa": "fasta"}
    assert result["plan"] == "initial plan"


async def test_data_select_prep_node_raises_on_species_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prep node raises McpError when ``load_species_data`` fails."""

    def _raise(_path: str) -> None:
        raise FileNotFoundError("not found")

    _module = "mcp_server_phytomni.agents.analyst.graph_chat_subgraph"
    monkeypatch.setattr(f"{_module}.load_species_data", _raise)
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble genome",
            "data_list": {"obs://user.fa": "fasta"},
        },
    )
    with pytest.raises(McpError, match="Failed to load species data list"):
        await agent.data_select_prep_node(state)


# data_select_post_node: no selected_data key → use whole parsed object


async def test_data_select_post_node_uses_whole_response_when_key_absent() -> (
    None
):
    """Post node uses entire parsed JSON when ``selected_data`` absent."""
    agent = _build_agent()
    # The LLM returns just the dict directly, not wrapped in "selected_data"
    direct_payload = json.dumps({"obs://auto.fa": "fasta"})
    state = cast(
        AnalystState,
        {
            "data_list": {"obs://user.fa": "fasta"},
            "chat_response": {
                "choices": [{"message": {"content": direct_payload}}]
            },
        },
    )
    result = await agent.data_select_post_node(state)

    assert result["data_list"] == {
        "obs://user.fa": "fasta",
        "obs://auto.fa": "fasta",
    }


async def test_data_select_post_node_raises_on_json_decode_error() -> None:
    """Post node raises McpError when the LLM content is not parseable JSON."""
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "data_list": {"obs://user.fa": "fasta"},
            "chat_response": {
                "choices": [{"message": {"content": "not valid json {"}}]
            },
        },
    )
    with pytest.raises(
        McpError, match="Failed to parse data selection response"
    ):
        await agent.data_select_post_node(state)


# data_select_post_node: empty/missing chat_response → empty selected_data


async def test_data_select_post_node_returns_unchanged_on_empty_response() -> (
    None
):
    """Post node returns data_list unchanged when chat response is empty."""
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "data_list": {"obs://user.fa": "fasta"},
            "chat_response": {},
        },
    )
    result = await agent.data_select_post_node(state)

    assert result["data_list"] == {"obs://user.fa": "fasta"}


async def test_plan_prep_node_uses_retrieve_file_feedback_template() -> None:
    """Prep picks ``analysis_retrieve_file_feedback``: feedback + files."""
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "method_context": {
                "retrieve_context": "retr-ctx",
                "upload_context": "upload-ctx",
            },
            "plan_feedback": "needs improvement",
            "obs_file_list": ["obs://some.fa"],
            "plan": "draft plan",
        },
    )
    result = await agent.plan_prep_node(state)

    assert result["pending_post"] == "plan_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    # The feedback and upload context should be embedded in the prompt
    assert "needs improvement" in chat_payload["user_query"]
    assert "upload-ctx" in chat_payload["user_query"]


async def test_plan_prep_node_retrieve_file_template_no_feedback() -> None:
    """Prep uses ``analysis_retrieve_file``: obs files present, no feedback."""
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "method_context": {
                "retrieve_context": "retr-ctx",
                "upload_context": "upload-ctx",
            },
            "plan_feedback": None,
            "obs_file_list": ["obs://some.fa"],
            "plan": "",
        },
    )
    result = await agent.plan_prep_node(state)

    assert result["pending_post"] == "plan_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "upload-ctx" in chat_payload["user_query"]


async def test_plan_prep_node_uses_retrieve_feedback_no_obs_files() -> None:
    """Prep uses ``analysis_retrieve_feedback``: feedback present, no files."""
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "goal_description": "assemble transcriptome",
            "method_context": {
                "retrieve_context": "retr-ctx",
                "upload_context": "",
            },
            "plan_feedback": "needs more detail",
            "obs_file_list": [],
            "plan": "draft plan",
        },
    )
    result = await agent.plan_prep_node(state)

    assert result["pending_post"] == "plan_post_node"
    chat_payload = result["chat_payload"]
    assert chat_payload is not None
    assert "needs more detail" in chat_payload["user_query"]


async def test_plan_post_node_raises_mcp_error_when_no_content() -> None:
    """Post node raises McpError when LLM response has no usable content."""
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "plan_retries": 0,
            "chat_response": {},
        },
    )
    with pytest.raises(McpError, match="Failed to generate plan"):
        await agent.plan_post_node(state)


async def test_plan_post_node_raises_mcp_error_on_empty_content() -> None:
    """Post node raises McpError when LLM message content is empty string."""
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "plan_retries": 0,
            "chat_response": {"choices": [{"message": {"content": ""}}]},
        },
    )
    with pytest.raises(McpError, match="Failed to generate plan"):
        await agent.plan_post_node(state)


async def test_check_post_node_parses_json_code_fence() -> None:
    """Post node extracts JSON from a fenced critic response."""
    agent = _build_agent()
    body = json.dumps(
        {"decision": "APPROVED", "score": 8, "feedback": "looks great"}
    )
    fenced_content = f"```json\n{body}\n```"
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "plan_retries": 1,
            "chat_response": {
                "choices": [{"message": {"content": fenced_content}}]
            },
        },
    )
    result = await agent.check_post_node(state)

    assert result == {"plan_feedback": "APPROVED"}


async def test_check_post_node_handles_json_decode_error_gracefully() -> None:
    """Post node sets score=0/REJECTED when JSON parse fails."""
    agent = _build_agent()
    # Return invalid JSON so the except branch fires
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "plan_retries": 0,
            "chat_response": {
                "choices": [{"message": {"content": "not valid json {"}}]
            },
        },
    )
    # plan_retries=0 < max_retries=5, decision=REJECTED → returns feedback=""
    result = await agent.check_post_node(state)

    assert result == {"plan_feedback": ""}


async def test_check_post_node_approves_exhausted_retries_zero_min_score() -> (
    None
):
    """Post node returns APPROVED: retries exhausted, min_score=0."""
    config = AnalystConfig().model_copy(
        update={
            "MAX_RETRIES": 1,
            "PLAN_MIN_SCORE": 0,
        }
    )
    agent = AnalystAgent(
        analyst_config=config,
        sensitive_config=SensitiveConfig.load(),
    )
    critic_payload = json.dumps(
        {"decision": "REJECTED", "score": 3, "feedback": "needs work"}
    )
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "plan_retries": 1,  # == max_retries
            "chat_response": {
                "choices": [{"message": {"content": critic_payload}}]
            },
        },
    )
    result = await agent.check_post_node(state)

    assert result == {"plan_feedback": "APPROVED"}


async def test_check_post_node_raises_exhausted_retries_with_min_score() -> (
    None
):
    """Post node raises McpError: retries exhausted, min_score > 0."""
    config = AnalystConfig().model_copy(
        update={
            "MAX_RETRIES": 1,
            "PLAN_MIN_SCORE": 7,
        }
    )
    agent = AnalystAgent(
        analyst_config=config,
        sensitive_config=SensitiveConfig.load(),
    )
    critic_payload = json.dumps(
        {"decision": "REJECTED", "score": 3, "feedback": "not good enough"}
    )
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "plan_retries": 1,  # == max_retries
            "chat_response": {
                "choices": [{"message": {"content": critic_payload}}]
            },
        },
    )
    with pytest.raises(McpError, match="Analysis plan rejected"):
        await agent.check_post_node(state)


async def test_check_post_node_returns_feedback_rejected_retries_remain() -> (
    None
):
    """Post node returns plain feedback: rejected, retries remain."""
    agent = _build_agent()
    critic_payload = json.dumps(
        {"decision": "REJECTED", "score": 4, "feedback": "add more steps"}
    )
    state = cast(
        AnalystState,
        {
            "chat_payload": {"user_query": "x", "chat_kwargs": {}},
            "plan_retries": 0,
            "chat_response": {
                "choices": [{"message": {"content": critic_payload}}]
            },
        },
    )
    result = await agent.check_post_node(state)

    assert result == {"plan_feedback": "add more steps"}


async def test_tool_extract_post_node_parses_json_code_fence() -> None:
    """Post node extracts tool list from a ```json ... ``` fenced response."""
    agent = _build_agent()
    body = json.dumps({"tools": ["bwa", "samtools"]})
    fenced_content = f"```json\n{body}\n```"
    state = cast(
        AnalystState,
        {
            "chat_response": {
                "choices": [{"message": {"content": fenced_content}}]
            },
        },
    )
    result = await agent.tool_extract_post_node(state)

    assert result == {"extracted_tools": ["bwa", "samtools"]}


async def test_tool_extract_post_node_uses_default_on_empty_response() -> None:
    """Post node parses ``{}`` default when response has no choices.

    Covers the ``576->584`` branch: the ``if``-block is skipped so
    ``content`` stays as ``"{}"``; ``result["tools"]`` then raises
    ``KeyError`` — the expected behavior with no LLM output.
    """
    agent = _build_agent()
    state = cast(
        AnalystState,
        {
            "chat_response": {},
        },
    )
    with pytest.raises(KeyError):
        await agent.tool_extract_post_node(state)
