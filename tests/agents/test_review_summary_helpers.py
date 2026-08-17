# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for ReviewSummaryMixin's summary + follow-up nodes.

Drives the wired ``summary_prep_node`` / ``summary_post_node`` and
``follow_up_prep_node`` / ``follow_up_post_node`` split (the chat call
runs in the shared chat node). Pins the subsection_N prompt-param
contract, the backtick-strip + empty fallback, and the citation-renumber
plus follow-up assembly the legacy single nodes owned.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.review import summary as summary_module
from mcp_server_phytomni.agents.review.agent import (
    DeepResearchAgent,
    DeepResearchState,
)
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent() -> DeepResearchAgent:
    """Construct a DeepResearchAgent for the summary / follow-up nodes."""
    return DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _capture_summary_params(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Stub summary_module.get_prompt; capture the params dict per call."""
    captured: list[dict[str, Any]] = []

    def fake_prompt(
        _prompt_file: str, _path: str, params: dict[str, Any]
    ) -> str:
        captured.append(params)
        return "PROMPT"

    monkeypatch.setattr(summary_module, "get_prompt", fake_prompt)
    return captured


def _chat_response(text: str) -> dict[str, Any]:
    """Build a chat_response carrying assistant ``text``."""
    return {"choices": [{"message": {"content": text}}]}


async def _run_follow_up(
    agent: DeepResearchAgent,
    prep_state: DeepResearchState,
    follow_up_text: str,
) -> dict[str, Any]:
    """Drive follow_up_prep -> (chat) -> follow_up_post; return the message."""
    prep = await agent.follow_up_prep_node(prep_state)
    post_state = cast(
        DeepResearchState,
        {
            **prep_state,
            **prep,
            "chat_response": _chat_response(follow_up_text),
        },
    )
    result = await agent.follow_up_post_node(post_state)
    return result["final_response"]["choices"][0]["message"]


async def test_summary_prep_node_packs_four_subsections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summary_prep_node fills 4 subsection slots from reports + dims."""
    captured = _capture_summary_params(monkeypatch)
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "drought tolerance",
            "research_dimensions": ["Genetics", "Physiology"],
            "revised_reports": [
                {"revised_report": "G content"},
                {"revised_report": "P content"},
            ],
        },
    )

    await agent.summary_prep_node(state)

    params = captured[0]
    assert params["subsection_1_title"] == "Genetics"
    assert params["subsection_1_content"] == "G content"
    assert params["subsection_2_title"] == "Physiology"
    assert params["subsection_2_content"] == "P content"
    assert params["thesis"] == ""
    assert params["in_scope"] == ""
    assert params["out_of_scope"] == ""


async def test_summary_prep_node_pads_missing_subsections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fewer than 4 dimensions fills the remaining slots with empty fields."""
    captured = _capture_summary_params(monkeypatch)
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "q",
            "research_dimensions": ["only-one"],
            "revised_reports": [{"revised_report": "first"}],
        },
    )

    await agent.summary_prep_node(state)

    params = captured[0]
    assert params["subsection_1_title"] == "only-one"
    assert params["subsection_2_title"] == ""
    assert params["subsection_2_content"] == ""
    assert params["subsection_4_content"] == ""


async def test_summary_prep_node_forwards_thesis_and_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner thesis and scope sentences reach the summary prompt."""
    captured = _capture_summary_params(monkeypatch)
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "ZOS7 in upland rice",
            "research_dimensions": ["Context"],
            "revised_reports": [{"revised_report": "body"}],
            "thesis": "A wax module is proposed.",
            "in_scope": "This rice module only.",
            "out_of_scope": "Human PPI methods are out of scope.",
        },
    )

    await agent.summary_prep_node(state)

    params = captured[0]
    assert params["thesis"] == "A wax module is proposed."
    assert params["in_scope"] == "This rice module only."
    assert params["out_of_scope"] == "Human PPI methods are out of scope."


async def test_summary_post_node_strips_backticks() -> None:
    """summary_post_node strips backticks from the chat content."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {"chat_response": _chat_response("Final `report` text")},
    )

    result = await agent.summary_post_node(state)

    assert result == {"summary_content": "Final report text"}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            '```json\n{"summary": "fenced"}\n```',
            'json\n{"summary": "fenced"}\n',
        ),
        ("plain summary", "plain summary"),
    ],
)
async def test_summary_post_node_preserves_content_contract(
    content: str, expected: str
) -> None:
    """Summary keeps text content while removing legacy backtick markers."""
    result = await _build_agent().summary_post_node(
        cast(DeepResearchState, {"chat_response": _chat_response(content)})
    )

    assert result == {"summary_content": expected}


async def test_summary_post_node_falls_back_on_empty() -> None:
    """Empty chat content yields the ``No summary generated`` sentinel."""
    agent = _build_agent()
    state = cast(DeepResearchState, {"chat_response": _chat_response("")})

    result = await agent.summary_post_node(state)

    assert result == {"summary_content": "No summary generated"}


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": []},
        {"choices": [{"message": "not-a-mapping"}]},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
async def test_summary_post_node_handles_malformed_response_shapes(
    response: Any,
) -> None:
    """Malformed OpenAI envelopes keep the summary fallback sentinel."""
    agent = _build_agent()

    result = await agent.summary_post_node(
        cast(DeepResearchState, {"chat_response": response})
    )

    assert result == {"summary_content": "No summary generated"}


async def test_summary_post_node_uses_common_message_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summary keeps its backtick policy around canonical message access."""
    response = {"choices": [{"message": {"content": "payload"}}]}
    monkeypatch.setattr(summary_module, "message_content", lambda value: "A")

    result = await _build_agent().summary_post_node(
        cast(DeepResearchState, {"chat_response": response})
    )

    assert result == {"summary_content": "A"}


async def test_follow_up_renumbers_and_attaches_follow_ups() -> None:
    """follow_up renumbers citations and packs total + follow-up list."""
    agent = _build_agent()
    prep_state = cast(
        DeepResearchState,
        {
            "summary_content": "Sentence with [document 001] cite.",
            "all_raw_doc_list": [
                {"doc_id": "document 001", "title": "Paper A"}
            ],
            "add_doc_list": [],
            "original_user_query": "drought",
        },
    )

    message = await _run_follow_up(
        agent, prep_state, '["What about C4?", "Can we extend to wheat?"]'
    )

    assert "[document:1]" in message["content"]
    assert "[document 001]" not in message["content"]
    assert [doc["doc_id"] for doc in message["doc_list"]] == [1]
    assert message["doc_list"][0]["title"] == "Paper A"
    assert message["total"] == 10000
    assert message["follow_up_questions"] == [
        "What about C4?",
        "Can we extend to wheat?",
    ]


@pytest.mark.parametrize(
    ("follow_up_text", "expected"),
    [
        ('["one", "two"]', ["one", "two"]),
        ('```json\n["fenced"]\n```', ["fenced"]),
        ("not-json", []),
        ('{"question": "not-a-list"}', []),
    ],
)
async def test_follow_up_post_node_parses_list_fragments(
    follow_up_text: str, expected: list[str]
) -> None:
    """Follow-up parsing keeps list support and rejects other JSON shapes."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "summary_content": "Summary",
            "ordered_doc_list": [],
            "chat_response": _chat_response(follow_up_text),
        },
    )

    result = await agent.follow_up_post_node(state)

    assert (
        result["final_response"]["choices"][0]["message"][
            "follow_up_questions"
        ]
        == expected
    )


async def test_follow_up_merges_raw_and_add_doc_lists() -> None:
    """Both raw and supplementary docs feed the renumber lookup table."""
    agent = _build_agent()
    prep_state = cast(
        DeepResearchState,
        {
            "summary_content": (
                "Raw [document 001] supplementary "
                "[add document S1-001] both."
            ),
            "all_raw_doc_list": [
                {"doc_id": "document 001", "title": "Raw paper"}
            ],
            "add_doc_list": [
                {
                    "doc_id": "add document S1-001",
                    "title": "Supplementary paper",
                }
            ],
            "original_user_query": "q",
        },
    )

    message = await _run_follow_up(agent, prep_state, "[]")

    assert [doc["title"] for doc in message["doc_list"]] == [
        "Raw paper",
        "Supplementary paper",
    ]


async def test_follow_up_yields_empty_follow_ups_for_garbage() -> None:
    """Non-JSON follow-up content produces an empty list, not an error."""
    agent = _build_agent()
    prep_state = cast(
        DeepResearchState,
        {
            "summary_content": "Plain summary.",
            "all_raw_doc_list": [],
            "add_doc_list": [],
            "original_user_query": "q",
        },
    )

    message = await _run_follow_up(agent, prep_state, "not a json array")

    assert message["follow_up_questions"] == []
