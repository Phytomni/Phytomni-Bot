# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the knowledge subgraph IO TypedDicts.

Pins the public input/output contract: ``user_query`` is the only
required ``KnowledgeInput`` key (rest default inside ``arun``),
``KnowledgeOutput`` always exposes ``retrieved_docs`` +
``retrieval_outcome`` + ``final_response``, and ``KnowledgeState`` carries
every input + output key plus the intermediate slots node bodies write
between them.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge import agent as knowledge_agent
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    retrieval_unavailable_error,
)
from mcp_server_phytomni.agents.knowledge.state import (
    KnowledgeAgentState,
    KnowledgeInput,
    KnowledgeOutput,
    KnowledgeState,
)
from mcp_server_phytomni.graphs.knowledge_adapters import (
    extract_retrieved_docs,
)

pytestmark = pytest.mark.agent


def _required(td: type) -> frozenset[str]:
    """Return ``td.__required_keys__`` via getattr.

    PEP 705 attaches ``__required_keys__`` / ``__optional_keys__`` to
    every TypedDict class. Direct attribute access is not visible to
    every checker; ``getattr`` keeps the same runtime semantics.
    """
    return getattr(td, "__required_keys__")


def _optional(td: type) -> frozenset[str]:
    """Return ``td.__optional_keys__`` via getattr (see :func:`_required`)."""
    return getattr(td, "__optional_keys__")


def test_knowledge_input_required_keys_are_only_user_query() -> None:
    """``user_query`` is the lone required KnowledgeInput field.

    Pins the parent→subgraph contract: a parent graph mounting the
    knowledge subgraph only owes the ``user_query`` key; the
    ``obs_file_list`` / ``repo_id_dict`` / ``is_generate`` /
    ``is_follow_up`` keys default to None or the documented boolean
    inside ``arun`` so omitting them does not break the parent.
    """
    assert _required(KnowledgeInput) == frozenset({"user_query"})
    assert _optional(KnowledgeInput) == frozenset(
        {
            "locale",
            "obs_file_list",
            "conversation_messages",
            "retrieval_query",
            "answer_context",
            "repo_id_dict",
            "is_generate",
            "is_follow_up",
        }
    )


def test_knowledge_output_carries_evidence_and_answer_paths() -> None:
    """KnowledgeOutput always exposes evidence outcome and answer paths.

    Pins that ``arun`` callers can read whichever path their
    ``is_generate`` flag implied without an ``in`` guard:
    ``retrieved_docs`` for the retrieve-only branch,
    ``final_response`` for the retrieve-plus-generate branch. The
    unused branch returns the empty default rather than a missing
    key.
    """
    assert _required(KnowledgeOutput) == frozenset(
        {"retrieved_docs", "retrieval_outcome", "final_response"}
    )
    assert _optional(KnowledgeOutput) == frozenset()


async def test_retrieve_only_graph_populates_every_required_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compiled retrieve-only graph writes all required output keys."""
    docs = [
        {
            "chunk_id": "paper-1",
            "title": "Reliable paper",
            "content": "Reliable evidence",
        }
    ]

    async def partial_retrieval(**_kwargs: object) -> dict[str, object]:
        return {
            "doc_list": docs,
            "total": 1,
            "outcome": "partial",
            "failures": [
                {
                    "source": "repository-1",
                    "kind": "timeout",
                    "retryable": True,
                }
            ],
        }

    monkeypatch.setattr(
        knowledge_agent,
        "multi_retrieve",
        partial_retrieval,
    )
    agent = KnowledgeAgent(checkpointer=MemorySaver())

    payload: KnowledgeInput = {
        "user_query": "Find reliable evidence",
        "is_generate": False,
        "is_follow_up": False,
    }
    result = await agent.app.ainvoke(
        payload,
        config={"configurable": {"thread_id": "knowledge-output-contract"}},
    )

    assert payload == {
        "user_query": "Find reliable evidence",
        "is_generate": False,
        "is_follow_up": False,
    }
    assert result == {
        "retrieved_docs": docs,
        "retrieval_outcome": "partial",
        "final_response": {},
    }
    assert extract_retrieved_docs(result) == docs


async def test_retrieval_failure_stops_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable evidence emits no later progress or generation work."""
    generated: list[bool] = []
    progress_events: list[dict[str, object]] = []

    async def failed_retrieval(**_kwargs: object) -> dict[str, object]:
        raise retrieval_unavailable_error()

    async def unexpected_generation(
        _self: KnowledgeAgent,
        _state: object,
        _runtime: object = None,
    ) -> dict[str, object]:
        generated.append(True)
        return {}

    monkeypatch.setattr(
        knowledge_agent,
        "multi_retrieve",
        failed_retrieval,
    )
    monkeypatch.setattr(
        KnowledgeAgent,
        "generate_prep_node",
        unexpected_generation,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.mcp.progress_events.get_stream_writer",
        lambda: progress_events.append,
    )
    agent = KnowledgeAgent(checkpointer=MemorySaver())

    with pytest.raises(
        McpError,
        match="Knowledge retrieval temporarily unavailable",
    ) as exc_info:
        failure_payload: KnowledgeInput = {
            "user_query": "private user query",
            "is_generate": True,
            "is_follow_up": False,
        }
        await agent.app.ainvoke(
            failure_payload,
            config={
                "configurable": {"thread_id": "knowledge-failure-contract"}
            },
        )

    assert not generated
    assert [event["phase"] for event in progress_events] == ["retrieving"]
    assert "private user query" not in str(exc_info.value)


def test_knowledge_state_covers_input_and_output_keys() -> None:
    """KnowledgeState is the union of input, intermediate, and output.

    Pins that the working state carries every ``KnowledgeInput`` key
    (so the initial state in ``arun`` can be assembled from a parent
    input dict) and every ``KnowledgeOutput`` key (so the final
    state projects back without re-keying), plus the four
    intermediate slots ``process_files`` / ``retrieve`` / ``generate``
    /  ``follow_up`` nodes write between the input and output ends.
    """
    state_keys = _required(KnowledgeState) | _optional(KnowledgeState)
    input_keys = _required(KnowledgeInput) | _optional(KnowledgeInput)
    output_keys = _required(KnowledgeOutput) | _optional(KnowledgeOutput)
    assert input_keys <= state_keys
    assert output_keys <= state_keys
    assert {
        "upload_context",
        "retrieve_context",
        "main_response",
        "follow_up_questions",
    } <= state_keys


def test_knowledge_agent_state_alias_matches_knowledge_state() -> None:
    """``KnowledgeAgentState`` is a back-compat alias for KnowledgeState.

    Pins the alias contract: legacy importers reaching for
    ``mcp_server_phytomni.agents.knowledge.KnowledgeAgentState``
    receive the same TypedDict object as ``KnowledgeState``. Without
    this property, the internal node annotations on the
    ``KnowledgeAgent`` class would diverge from the new schema and
    type checkers would silently allow drift.
    """
    assert KnowledgeAgentState is KnowledgeState
