# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat A2UI confirm graph interrupt/resume tests."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from mcp_server_phytomni.agents.chat.a2ui_builder import (
    build_chat_a2ui_graph,
)
from mcp_server_phytomni.runtime.resume import (
    aresume_graph,
    detect_interrupt,
)
from tests.agents._network_escape import install_network_escape_guard

pytestmark = pytest.mark.agent


@pytest.fixture(autouse=True)
def _fail_fast_on_network_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert any un-mocked outbound call into an instant named failure."""
    install_network_escape_guard(monkeypatch, label="chat-a2ui")


def _patch_chat_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub primary and follow-up LLM calls for offline graph tests."""

    async def _fake_run(
        messages: list[dict[str, str]],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        del messages, options
        return {
            "choices": [
                {
                    "message": {
                        "content": "Analysis complete.",
                        "follow_up_questions": [],
                    }
                }
            ]
        }

    async def _fake_phyto_chat(
        query: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del query, kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": '["Follow-up one?"]',
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.graph._run_phyto_chat",
        _fake_run,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.service.phyto_chat",
        _fake_phyto_chat,
    )


@pytest.mark.asyncio
async def test_a2ui_graph_pauses_with_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First invoke pauses at confirm with a phyto.a2ui draft surface."""
    _patch_chat_llm(monkeypatch)

    app = build_chat_a2ui_graph(checkpointer=MemorySaver())
    thread_id = "chat-a2ui-thread-1"
    final = await app.ainvoke(
        {
            "user_query": "请确认是否继续",
            "obs_file_list": [],
            "chat_kwargs": {},
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    info = detect_interrupt(final, thread_id)
    assert info is not None
    draft = info["draft"]
    assert draft["a2ui"]["widget"] == "confirm"
    assert draft["a2ui"]["surface_id"]


@pytest.mark.asyncio
async def test_a2ui_graph_resume_accepted_generates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted confirm resumes into a real assistant answer."""
    _patch_chat_llm(monkeypatch)

    app = build_chat_a2ui_graph(checkpointer=MemorySaver())
    thread_id = "chat-a2ui-thread-2"
    paused = await app.ainvoke(
        {
            "user_query": "Confirm before proceeding?",
            "obs_file_list": [],
            "chat_kwargs": {},
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    info = detect_interrupt(paused, thread_id)
    assert info is not None
    surface = info["draft"]["a2ui"]["surface_id"]
    final = await aresume_graph(
        app,
        thread_id,
        {
            "accepted": True,
            "surface_id": surface,
            "widget": "confirm",
            "action_id": "act-1",
        },
    )
    assert detect_interrupt(final, thread_id) is None
    content = final["response"]["choices"][0]["message"]["content"]
    assert "Analysis complete." in content


@pytest.mark.asyncio
async def test_a2ui_graph_resume_rejected_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejected confirm settles a short cancel message without main LLM."""

    async def _must_not_run(
        messages: list[dict[str, str]],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        del messages, options
        raise AssertionError("generate_node must not run on reject")

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.graph._run_phyto_chat",
        _must_not_run,
    )

    app = build_chat_a2ui_graph(checkpointer=MemorySaver())
    thread_id = "chat-a2ui-thread-3"
    paused = await app.ainvoke(
        {
            "user_query": "Confirm before proceeding?",
            "obs_file_list": [],
            "chat_kwargs": {},
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    info = detect_interrupt(paused, thread_id)
    assert info is not None
    surface = info["draft"]["a2ui"]["surface_id"]
    final = await aresume_graph(
        app,
        thread_id,
        {
            "accepted": False,
            "surface_id": surface,
            "widget": "confirm",
            "action_id": "act-2",
        },
    )
    assert detect_interrupt(final, thread_id) is None
    content = final["response"]["choices"][0]["message"]["content"]
    assert "Cancelled" in content
