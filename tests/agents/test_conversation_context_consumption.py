# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Consumption tests for private bounded conversation history."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.chat import graph as chat_graph
from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_HISTORY = (
    {"role": "user", "content": "U1"},
    {"role": "assistant", "content": "A1"},
    {"role": "user", "content": "U2"},
    {"role": "assistant", "content": "A2"},
)


async def test_chat_consumes_private_history_before_current_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual Chat model call receives ordered prior turns first."""
    captured: dict[str, Any] = {}

    async def fake_run_phyto_chat(
        messages: list[dict[str, str]],
        _options: dict[str, Any],
    ) -> dict[str, Any]:
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(chat_graph, "_run_phyto_chat", fake_run_phyto_chat)

    response = await chat_service.phyto_chat(
        "current query",
        conversation_messages=_HISTORY,
    )

    assert response == {"choices": [{"message": {"content": "ok"}}]}
    assert [message["content"] for message in captured["messages"][1:]] == [
        "U1",
        "A1",
        "U2",
        "A2",
        "current query",
    ]
    assert [message["role"] for message in captured["messages"][1:]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]


async def test_knowledge_arun_consumes_private_history_in_chat_subgraph_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Knowledge's mounted chat subgraph receives ordered native history."""
    captured: dict[str, Any] = {}

    async def fake_multi_retrieve(**_kwargs: Any) -> dict[str, Any]:
        return {
            "doc_list": [
                {
                    "chunk_text": "doc body",
                    "file_id": "doc-1",
                    "title": "Paper One.pdf",
                }
            ]
        }

    async def fake_chat_ainvoke(chat_input: dict[str, Any]) -> dict[str, Any]:
        captured["chat_input"] = chat_input
        return {"response": {"choices": [{"message": {"content": "ok"}}]}}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.knowledge.agent.multi_retrieve",
        fake_multi_retrieve,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.chat_subgraph.CHAT_APP",
        SimpleNamespace(ainvoke=AsyncMock(side_effect=fake_chat_ainvoke)),
    )

    agent = KnowledgeAgent(
        knowledge_config=KnowledgeConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    response = await agent.arun(
        "current query",
        conversation_messages=_HISTORY,
        is_follow_up=False,
    )

    assert response["choices"][0]["message"]["content"] == "ok"
    chat_kwargs = captured["chat_input"]["chat_kwargs"]
    assert [
        message["content"] for message in chat_kwargs["conversation_messages"]
    ] == ["U1", "A1", "U2", "A2"]
    assert "current query" in captured["chat_input"]["user_query"]
