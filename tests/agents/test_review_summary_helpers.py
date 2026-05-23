# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for ReviewSummaryMixin's two workflow nodes.

Drives ``summary_node`` / ``post_process_node`` through a
``_SummaryProbe(ReviewSummaryMixin)`` subclass that stubs ``self._chat``
and ``get_prompt``. Pins the prompt-param contract (subsection_N
slots, follow-up parsing) plus the citation-renumber wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, cast

import pytest

from mcp_server_phytomni.agents.review import summary as summary_module
from mcp_server_phytomni.agents.review.agent import DeepResearchState
from mcp_server_phytomni.agents.review.summary import ReviewSummaryMixin

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _stub_get_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace get_prompt with a deterministic stub for every test."""
    monkeypatch.setattr(
        summary_module,
        "get_prompt",
        lambda prompt_file, path, params: f"PROMPT::{path}::{params}",
    )


class _SummaryProbe(ReviewSummaryMixin):
    """Mixin-bound probe with stand-in chat and review_config."""

    def __init__(self, chat_replies: List[Dict[str, Any]]) -> None:
        self.review_config = SimpleNamespace(PROMPT_FILE=".prompts.yaml")
        self._chat_replies = list(chat_replies)
        self.chat_calls: List[str] = []

    async def _chat(
        self,
        prompt: str,
        response_format_override: Any = None,
    ) -> Dict[str, Any]:
        """Return the next pre-canned chat reply and record the prompt."""
        del response_format_override
        self.chat_calls.append(prompt)
        return self._chat_replies.pop(0)


def _chat_reply(text: str) -> Dict[str, Any]:
    """Build a minimal phyto_chat-shaped response carrying ``text``."""
    return {"choices": [{"message": {"content": text}}]}


async def test_summary_node_packs_four_subsections_and_strips_backticks() -> (
    None
):
    """summary_node fills 4 subsection slots and strips backticks."""
    probe = _SummaryProbe([_chat_reply("Final `report` text")])
    state: Dict[str, Any] = {
        "original_user_query": "drought tolerance",
        "research_dimensions": ["Genetics", "Physiology"],
        "revised_reports": [
            {"revised_report": "G content"},
            {"revised_report": "P content"},
        ],
    }

    result = await probe.summary_node(cast(DeepResearchState, state))

    assert result == {"summary_content": "Final report text"}
    assert len(probe.chat_calls) == 1


async def test_summary_node_pads_missing_subsections_with_empty_strings() -> (
    None
):
    """Fewer than 4 dimensions fills the remaining slots with empty fields."""
    probe = _SummaryProbe([_chat_reply("ok")])
    state: Dict[str, Any] = {
        "original_user_query": "q",
        "research_dimensions": ["only-one"],
        "revised_reports": [{"revised_report": "first"}],
    }

    result = await probe.summary_node(cast(DeepResearchState, state))

    assert result == {"summary_content": "ok"}


async def test_summary_node_falls_back_to_default_on_empty_response() -> None:
    """Empty chat content yields the ``No summary generated`` sentinel."""
    probe = _SummaryProbe([_chat_reply("")])
    state: Dict[str, Any] = {
        "original_user_query": "q",
        "research_dimensions": [],
        "revised_reports": [],
    }

    result = await probe.summary_node(cast(DeepResearchState, state))

    assert result == {"summary_content": "No summary generated"}


async def test_post_process_node_renumbers_and_attaches_follow_ups() -> None:
    """post_process_node renumbers + follow_up, packs final payload."""
    follow_up_text = '["What about C4?", "Can we extend to wheat?"]'
    probe = _SummaryProbe([_chat_reply(follow_up_text)])
    state: Dict[str, Any] = {
        "summary_content": "Sentence with [document 001] cite.",
        "all_raw_doc_list": [
            {"doc_id": "document 001", "title": "Paper A"},
        ],
        "add_doc_list": [],
        "original_user_query": "drought",
    }

    result = await probe.post_process_node(cast(DeepResearchState, state))

    payload = result["final_response"]["choices"][0]["message"]
    assert "[document:1]" in payload["content"]
    assert "[document 001]" not in payload["content"]
    assert [doc["doc_id"] for doc in payload["doc_list"]] == [1]
    assert payload["doc_list"][0]["title"] == "Paper A"
    assert payload["total"] == 10000
    assert payload["follow_up_questions"] == [
        "What about C4?",
        "Can we extend to wheat?",
    ]


async def test_post_process_node_merges_raw_and_add_doc_lists() -> None:
    """Both raw and supplementary docs feed the renumber lookup table."""
    probe = _SummaryProbe([_chat_reply("[]")])
    state: Dict[str, Any] = {
        "summary_content": (
            "Raw [document 001] supplementary [add document S1-001] both."
        ),
        "all_raw_doc_list": [
            {"doc_id": "document 001", "title": "Raw paper"},
        ],
        "add_doc_list": [
            {"doc_id": "add document S1-001", "title": "Supplementary paper"},
        ],
        "original_user_query": "q",
    }

    result = await probe.post_process_node(cast(DeepResearchState, state))

    docs = result["final_response"]["choices"][0]["message"]["doc_list"]
    assert [doc["title"] for doc in docs] == [
        "Raw paper",
        "Supplementary paper",
    ]


async def test_post_process_node_yields_empty_follow_ups_for_garbage() -> None:
    """Non-JSON follow-up content produces an empty list, not an error."""
    probe = _SummaryProbe([_chat_reply("not a json array")])
    state: Dict[str, Any] = {
        "summary_content": "Plain summary.",
        "all_raw_doc_list": [],
        "add_doc_list": [],
        "original_user_query": "q",
    }

    result = await probe.post_process_node(cast(DeepResearchState, state))

    payload = result["final_response"]["choices"][0]["message"]
    assert payload["follow_up_questions"] == []
