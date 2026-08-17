# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch coverage for KnowledgeAgent node and response helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from langgraph.runtime import Runtime
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge import agent as knowledge_mod
from mcp_server_phytomni.agents.knowledge.agent import (
    KnowledgeAgent,
    response_to_string,
)
from mcp_server_phytomni.agents.knowledge.retrieval_result import (
    RETRIEVAL_UNAVAILABLE_MESSAGE,
)
from mcp_server_phytomni.agents.knowledge.state import KnowledgeState
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime.memory import (
    MemoryAccessor,
    MemoryGraphContext,
    MemoryStore,
    MemoryWrite,
)
from mcp_server_phytomni.runtime.request_context import request_context

pytestmark = pytest.mark.agent


def _build_agent() -> KnowledgeAgent:
    """Construct a KnowledgeAgent with default offline config."""
    return KnowledgeAgent(
        knowledge_config=KnowledgeConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _generate_state(**overrides: Any) -> KnowledgeState:
    """Return a generate-prep state with optional field overrides."""
    state: dict[str, Any] = {
        "user_query": "What is photosynthesis?",
        "retrieve_context": "retrieved document",
        "upload_context": "",
        "retrieved_docs": [{"title": "Plant Biology.pdf"}],
    }
    state.update(overrides)
    return cast(KnowledgeState, state)


async def test_retrieve_node_maps_protocol_error_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid retrieval payload becomes the fixed unavailable error."""

    async def fake_multi_retrieve(**_kwargs: Any) -> str:
        return "not-a-retrieval-payload"

    monkeypatch.setattr(knowledge_mod, "multi_retrieve", fake_multi_retrieve)
    agent = _build_agent()

    with pytest.raises(McpError, match=RETRIEVAL_UNAVAILABLE_MESSAGE):
        await agent.retrieve_node(
            cast(KnowledgeState, {"user_query": "q", "upload_context": ""})
        )


async def test_generate_prep_node_uses_upload_context_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploaded files select the retrieval_file prompt template."""
    captured: dict[str, Any] = {}

    def fake_get_prompt(
        _prompt_file: str,
        prompt_path: str,
        values: dict[str, Any],
    ) -> str:
        captured["prompt_path"] = prompt_path
        captured["values"] = values
        return "file-backed prompt"

    monkeypatch.setattr(knowledge_mod, "get_prompt", fake_get_prompt)
    result = await _build_agent().generate_prep_node(
        _generate_state(upload_context="uploaded notes")
    )

    assert captured["prompt_path"] == "user/retrieval_file"
    assert captured["values"]["upload_context"] == "uploaded notes"
    assert "file-backed prompt" in result["chat_payload"]["user_query"]


@pytest.mark.parametrize(
    ("chat_response", "expected_message"),
    [
        ({}, {"doc_list": [{"title": "D"}], "total": 1}),
        ({"usage": 1}, {"doc_list": [{"title": "D"}], "total": 1}),
        ({"choices": []}, {"doc_list": [{"title": "D"}], "total": 1}),
        (
            {"choices": [{}]},
            {"doc_list": [{"title": "D"}], "total": 1},
        ),
        (
            {"choices": [{"message": None}]},
            {"doc_list": [{"title": "D"}], "total": 1},
        ),
    ],
)
async def test_generate_post_node_fills_missing_message_slots(
    chat_response: dict[str, Any],
    expected_message: dict[str, Any],
) -> None:
    """Missing choices or message slots still receive the doc payload."""
    result = await _build_agent().generate_post_node(
        cast(
            KnowledgeState,
            {
                "retrieved_docs": [{"title": "D"}],
                "chat_response": chat_response,
            },
        )
    )

    message = result["main_response"]["choices"][0]["message"]
    assert message == expected_message
    assert result["final_response"] is result["main_response"]


async def test_follow_up_prep_node_prefixes_memory_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Follow-up prep prepends the untrusted memory block when present."""
    memory_store = MemoryStore(str(tmp_path / "knowledge-memory.sqlite"))
    memory_store.create(
        MemoryWrite(
            content="User prefers short answers.",
            kind="preference",
            user_id="alice",
        ),
        now=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
        memory_id="mem-follow-up",
    )
    runtime = Runtime[MemoryGraphContext](
        context={"memory_accessor": MemoryAccessor(memory_store)}
    )
    monkeypatch.setattr(
        knowledge_mod,
        "get_prompt",
        lambda *_args, **_kwargs: "follow-up prompt",
    )
    state = cast(
        KnowledgeState,
        {
            "user_query": "What is photosynthesis?",
            "main_response": {
                "choices": [{"message": {"content": "primary answer"}}]
            },
        },
    )

    with request_context("alice", "req-follow-up"):
        result = await _build_agent().follow_up_prep_node(state, runtime)

    query = result["chat_payload"]["user_query"]
    assert "untrusted reference data" in query
    assert "User prefers short answers." in query
    assert "follow-up prompt" in query


async def test_arun_returns_retrieved_docs_when_not_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrieve-only arun returns the document list instead of a merge."""
    docs = [{"title": "Only docs"}]

    async def fake_ainvoke(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"retrieved_docs": docs, "final_response": {"unused": True}}

    monkeypatch.setattr(knowledge_mod, "ainvoke_graph", fake_ainvoke)
    result = await _build_agent().arun("query", is_generate=False)

    assert result == docs


def test_response_to_string_formats_titles_and_empty_payloads() -> None:
    """References strip PDF suffixes and ignore empty or missing titles."""
    formatted = response_to_string(
        {
            "choices": [
                {
                    "message": {
                        "content": "Photosynthesis is...",
                        "doc_list": [
                            {"title": "Plant Biology.pdf"},
                            {"title": "Botany Research.PDF"},
                            {"title": "Notes"},
                            {"title": ""},
                            None,
                        ],
                    }
                }
            ]
        }
    )

    assert formatted.startswith("Photosynthesis is...")
    assert "[1] Plant Biology\n\n" in formatted
    assert "[2] Botany Research\n\n" in formatted
    assert "[3] Notes\n\n" in formatted
    assert response_to_string({}) == "\n\n## Reference:\n\n"
    assert response_to_string({"choices": []}) == "\n\n## Reference:\n\n"
    assert (
        response_to_string({"choices": [{"message": None}]})
        == "\n\n## Reference:\n\n"
    )
