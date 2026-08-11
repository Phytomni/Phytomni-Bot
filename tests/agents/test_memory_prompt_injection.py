# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for read-only explicit-memory prompt injection."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from langgraph.runtime import Runtime

from mcp_server_phytomni.agents.chat import graph as chat_graph
from mcp_server_phytomni.agents.knowledge import agent as knowledge_module
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.state import KnowledgeState
from mcp_server_phytomni.agents.shared.memory_context import (
    memory_context_for_graph,
    render_user_memory_context,
)
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime.memory import (
    MemoryAccessor,
    MemoryGraphContext,
    MemoryRecord,
    MemoryStore,
    MemoryWrite,
)
from mcp_server_phytomni.runtime.request_context import request_context

pytestmark = pytest.mark.agent


def _record() -> MemoryRecord:
    """Return one deterministic record for renderer tests."""
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    return MemoryRecord(
        id="mem-secret-id",
        user_id="alice",
        kind="preference",
        content="User prefers concise plant-science explanations.",
        created_at=now,
        updated_at=now,
    )


def _accessor(tmp_path: Path) -> tuple[MemoryAccessor, MemoryStore]:
    """Create one explicit memory and its read accessor."""
    store = MemoryStore(str(tmp_path / "memory.sqlite"))
    store.create(
        MemoryWrite(
            user_id="alice",
            kind="preference",
            content="User prefers concise plant-science explanations.",
        ),
        memory_id="mem-secret-id",
        now=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
    )
    return MemoryAccessor(store), store


def test_memory_renderer_marks_content_untrusted_and_omits_ids() -> None:
    """Prompt context carries content only inside an untrusted delimiter."""
    rendered = render_user_memory_context([_record()])

    assert "untrusted reference data" in rendered
    assert "BEGIN UNTRUSTED USER EXPLICIT MEMORY" in rendered
    assert "User prefers concise plant-science explanations." in rendered
    assert "mem-secret-id" not in rendered


def test_memory_context_for_graph_without_user_is_empty(
    tmp_path: Path,
) -> None:
    """An unbound MCP graph cannot read another user's memory."""
    accessor, _ = _accessor(tmp_path)
    with request_context(None, None):
        assert memory_context_for_graph(accessor=accessor) == ""


async def test_chat_generate_injects_memory_as_untrusted_system_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chat reads memory before generation and never writes it."""
    accessor, store = _accessor(tmp_path)
    captured: dict[str, Any] = {}

    def fake_get_prompt(*_args: object, **_kwargs: object) -> str:
        """Return a deterministic system prompt."""
        return "base system prompt"

    async def fake_run_phyto_chat(
        messages: list[dict[str, str]],
        _options: dict[str, Any],
    ) -> dict[str, Any]:
        """Capture the prompt and return a minimal completion."""
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "answer"}}]}

    monkeypatch.setattr(chat_graph.service, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(chat_graph, "_run_phyto_chat", fake_run_phyto_chat)
    runtime = Runtime[MemoryGraphContext](
        context={"memory_accessor": accessor}
    )
    state = {
        "user_query": "Explain drought tolerance.",
        "chat_kwargs": {
            "prompt_file": "prompts.yaml",
            "prompt_path": "system/chat",
            "model": "test-model",
        },
    }

    with request_context("alice", "req-chat"):
        await chat_graph.generate_node(cast(Any, state), runtime)

    system_content = captured["messages"][0]["content"]
    assert "base system prompt" in system_content
    assert "untrusted reference data" in system_content
    assert "User prefers concise plant-science explanations." in system_content
    assert "mem-secret-id" not in system_content
    assert len(store.list_audit()) == 1


async def test_knowledge_generate_injects_memory_before_shared_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Knowledge prep adds the same untrusted block before its chat call."""
    accessor, store = _accessor(tmp_path)
    agent = KnowledgeAgent(
        knowledge_config=KnowledgeConfig(),
        sensitive_config=SensitiveConfig.load(),
    )

    def fake_get_prompt(
        _prompt_file: str,
        _prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Render only the retrieval context needed by this test."""
        return f"retrieved={params.get('retrieve_results') if params else ''}"

    monkeypatch.setattr(knowledge_module, "get_prompt", fake_get_prompt)
    runtime = Runtime[MemoryGraphContext](
        context={"memory_accessor": accessor}
    )
    state = cast(
        KnowledgeState,
        {
            "user_query": "What is photosynthesis?",
            "retrieve_context": "retrieved document",
            "upload_context": "",
            "retrieved_docs": [],
        },
    )

    with request_context("alice", "req-knowledge"):
        result = await agent.generate_prep_node(state, runtime)

    query = result["chat_payload"]["user_query"]
    assert "untrusted reference data" in query
    assert "User prefers concise plant-science explanations." in query
    assert "mem-secret-id" not in query
    assert len(store.list_audit()) == 1
