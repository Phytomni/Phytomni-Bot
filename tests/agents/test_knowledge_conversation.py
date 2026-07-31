# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for KnowledgeAgent conversation-context separation."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.knowledge import agent as knowledge_agent
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.conversation import (
    KnowledgeClarificationError,
    KnowledgeConversationAdapter,
)
from mcp_server_phytomni.agents.knowledge.state import KnowledgeState
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextEntity,
    ContextProjection,
    RoleTaggedTurn,
)
from tests.support.chat_fakes import recent_knowledge_turns

pytestmark = pytest.mark.agent


def _projection(
    *,
    current_query: str = "What evidence supports that?",
    active_entities: list[ContextEntity] | None = None,
    relevant_recent_turns: list[RoleTaggedTurn] | None = None,
) -> ContextProjection:
    """Build one bounded Knowledge projection for adapter tests."""
    return ContextProjection(
        current_query=current_query,
        active_entities=active_entities or [],
        relevant_recent_turns=relevant_recent_turns or [],
        agent_thread_id="ctx-" + "1" * 64,
        locale="en-US",
        token_budget=1024,
    )


def _sensitive_config() -> SensitiveConfig:
    """Load the local sensitive config for offline graph instances."""
    return SensitiveConfig.load()


def test_prepare_resolves_follow_up_into_standalone_retrieval_query() -> None:
    """Follow-up retrieval resolves the prior topic without role history."""
    adapter = KnowledgeConversationAdapter()

    prepared = adapter.prepare(
        _projection(relevant_recent_turns=recent_knowledge_turns())
    )

    assert prepared["user_query"] == "What evidence supports that?"
    assert prepared["thread_id"] == "ctx-" + "1" * 64
    assert prepared["retrieval_query"] == ("What evidence supports OsDREB1?")
    assert "role" not in prepared["retrieval_query"].lower()
    assert (
        "OsDREB1 improves drought tolerance [1]." in prepared["answer_context"]
    )


def test_prepare_requires_clarification_for_unresolved_pronoun() -> None:
    """Knowledge follow-ups ask for clarification instead of guessing."""
    adapter = KnowledgeConversationAdapter()

    with pytest.raises(KnowledgeClarificationError, match="clarify"):
        adapter.prepare(_projection())


@pytest.mark.asyncio
async def test_retrieve_node_uses_retrieval_query_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrieval receives the standalone query, not transcript text."""
    captured: dict[str, Any] = {}

    async def fake_multi_retrieve(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"doc_list": []}

    monkeypatch.setattr(knowledge_agent, "multi_retrieve", fake_multi_retrieve)
    agent = KnowledgeAgent(
        knowledge_config=KnowledgeConfig(),
        sensitive_config=_sensitive_config(),
    )

    await agent.retrieve_node(
        cast(
            KnowledgeState,
            agent.initial_state(
                "What evidence supports that?",
                retrieval_query="What evidence supports OsDREB1?",
                conversation_messages=[
                    {"role": "user", "content": "Tell me about OsDREB1."},
                    {
                        "role": "assistant",
                        "content": "OsDREB1 improves drought tolerance.",
                    },
                ],
            ),
        )
    )

    assert captured["user_query"] == "What evidence supports OsDREB1?"
    assert "Tell me about OsDREB1." not in captured["user_query"]
    assert "assistant" not in captured["user_query"].lower()


@pytest.mark.asyncio
async def test_generate_prep_uses_answer_context_without_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generation sees the bounded answer context and no raw history."""
    captured: dict[str, Any] = {}
    prompt_args: dict[str, Any] = {}

    def fake_get_prompt(
        _prompt_file: str,
        _template: str,
        values: dict[str, Any],
    ) -> str:
        prompt_args.update(values)
        return "retrieval prompt body"

    monkeypatch.setattr(knowledge_agent, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(
        knowledge_agent,
        "build_chat_kwargs_for",
        lambda *_args, **_kwargs: {},
    )

    def fake_build_chat_input(
        *,
        user_query: str,
        chat_kwargs: dict[str, Any],
        conversation_messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        captured["user_query"] = user_query
        captured["chat_kwargs"] = chat_kwargs
        captured["conversation_messages"] = conversation_messages
        return {"messages": []}

    monkeypatch.setattr(
        knowledge_agent, "build_chat_input", fake_build_chat_input
    )
    agent = KnowledgeAgent(
        knowledge_config=KnowledgeConfig(),
        sensitive_config=_sensitive_config(),
    )

    state = cast(
        KnowledgeState,
        agent.initial_state(
            "What evidence supports that?",
            retrieval_query="What evidence supports OsDREB1?",
            answer_context=(
                "Prior answer summary: OsDREB1 improves drought tolerance [1]."
            ),
        ),
    )
    state["retrieve_context"] = "[document 1 begin] Evidence [document 1 end]"
    await agent.generate_prep_node(state)

    assert "Prior answer summary" in captured["user_query"]
    assert prompt_args["retrieve_results"] == (
        "[document 1 begin] Evidence [document 1 end]"
    )
    assert prompt_args["user_query"] == "What evidence supports that?"
    assert captured["conversation_messages"] == []


def test_delta_promotes_bounded_summary_and_topic_without_raw_docs() -> None:
    """Knowledge delta retains citations and topic, not full report bodies."""
    adapter = KnowledgeConversationAdapter()
    adapter.prepare(
        _projection(current_query="Tell me about OsDREB1 drought evidence.")
    )

    delta = adapter.delta(
        {
            "result": {
                "formatted": {
                    "answer": "OsDREB1 improves drought tolerance [1].",
                    "follow_up_questions": [
                        "What promoter evidence exists for OsDREB1?"
                    ],
                },
                "raw": {
                    "choices": [
                        {
                            "message": {
                                "doc_list": [
                                    {
                                        "title": "Paper 1",
                                        "content": (
                                            "full report body that must "
                                            "not persist"
                                        ),
                                    }
                                ]
                            }
                        }
                    ]
                },
            }
        }
    )

    assert delta.summary_update == ("OsDREB1 improves drought tolerance [1].")
    assert delta.open_question_updates == [
        "What promoter evidence exists for OsDREB1?"
    ]
    assert [item.label for item in delta.entity_upserts] == ["OsDREB1"]
    assert delta.agent_memory_update is not None
    assert delta.agent_memory_update.summary == (
        "OsDREB1 improves drought tolerance [1]."
    )
    assert "full report body" not in json.dumps(delta.model_dump(mode="json"))


def test_delta_replaces_prior_active_topic_after_explicit_switch() -> None:
    """A successful explicit topic switch removes the previous active gene."""
    adapter = KnowledgeConversationAdapter()
    adapter.prepare(
        _projection(
            current_query="Tell me about OsNAC6 drought evidence.",
            active_entities=[
                ContextEntity(
                    entity_id="knowledge:osdreb1",
                    entity_type="gene",
                    label="OsDREB1",
                )
            ],
            relevant_recent_turns=[
                RoleTaggedTurn(
                    role="assistant",
                    content="OsDREB1 improves drought tolerance [1].",
                )
            ],
        )
    )

    delta = adapter.delta(
        {
            "result": {
                "formatted": {
                    "answer": "OsNAC6 improves drought tolerance [2].",
                    "follow_up_questions": [],
                }
            }
        }
    )

    assert [item.label for item in delta.entity_upserts] == ["OsNAC6"]
    assert delta.entity_removals == ["knowledge:osdreb1"]
