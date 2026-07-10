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
    draft_surface_id = info["draft"]["a2ui"]["surface_id"]
    final = await aresume_graph(
        app,
        thread_id,
        {
            "accepted": True,
            "surface_id": draft_surface_id,
            "widget": "confirm",
            "action_id": "act-1",
        },
    )
    assert detect_interrupt(final, thread_id) is None
    assert final["a2ui_surface"]["surface_id"] == draft_surface_id
    content = final["response"]["choices"][0]["message"]["content"]
    assert "Analysis complete." in content


@pytest.mark.asyncio
async def test_a2ui_graph_resume_rejected_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejected confirm settles a short cancel message without main LLM."""
    _patch_chat_llm(monkeypatch)

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


@pytest.mark.asyncio
async def test_a2ui_graph_form_pauses_and_submit_injects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Form query pauses as form; submit prepends user input then generates."""
    captured: dict[str, Any] = {}

    async def _fake_run(
        messages: list[dict[str, str]],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        del options
        captured["user"] = messages[1]["content"]
        return {
            "choices": [
                {
                    "message": {
                        "content": "Got form.",
                        "follow_up_questions": [],
                    }
                }
            ]
        }

    async def _fake_phyto_chat(query: str, **kwargs: Any) -> dict[str, Any]:
        del query, kwargs
        return {"choices": [{"message": {"content": "[]"}}]}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.graph._run_phyto_chat",
        _fake_run,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.service.phyto_chat",
        _fake_phyto_chat,
    )

    app = build_chat_a2ui_graph(checkpointer=MemorySaver())
    thread_id = "chat-a2ui-form-1"
    paused = await app.ainvoke(
        {
            "user_query": "请填写基因名",
            "obs_file_list": [],
            "chat_kwargs": {},
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    info = detect_interrupt(paused, thread_id)
    assert info is not None
    assert info["draft"]["a2ui"]["widget"] == "form"

    final = await aresume_graph(
        app,
        thread_id,
        {
            "surface_id": info["draft"]["a2ui"]["surface_id"],
            "widget": "form",
            "action_id": "act-1",
            "fields": {"value": "AT1G01010"},
        },
    )
    assert final["response"]["choices"][0]["message"]["content"] == (
        "Got form."
    )
    assert "AT1G01010" in captured["user"]
    assert "请填写基因名" in captured["user"]


@pytest.mark.asyncio
async def test_a2ui_graph_choice_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choice cancel settles the shared cancel message without LLM."""
    _patch_chat_llm(monkeypatch)
    app = build_chat_a2ui_graph(checkpointer=MemorySaver())
    thread_id = "chat-a2ui-choice-cancel"
    paused = await app.ainvoke(
        {
            "user_query": "请选择方案",
            "obs_file_list": [],
            "chat_kwargs": {},
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    info = detect_interrupt(paused, thread_id)
    assert info is not None
    assert info["draft"]["a2ui"]["widget"] == "choice"

    def _must_not_run(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("LLM must not run on cancel")

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.graph._run_phyto_chat",
        _must_not_run,
    )
    final = await aresume_graph(
        app,
        thread_id,
        {
            "surface_id": info["draft"]["a2ui"]["surface_id"],
            "widget": "choice",
            "action_id": "act-1",
            "cancelled": True,
        },
    )
    assert "Cancelled" in final["response"]["choices"][0]["message"]["content"]


@pytest.mark.asyncio
async def test_a2ui_graph_gene_id_domain_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gene-id form query mints a domain gene_id field on pause."""
    _patch_chat_llm(monkeypatch)
    app = build_chat_a2ui_graph(checkpointer=MemorySaver())
    thread_id = "chat-a2ui-gene-id-1"
    paused = await app.ainvoke(
        {
            "user_query": "请填写 gene_id for analysis",
            "obs_file_list": [],
            "chat_kwargs": {},
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    info = detect_interrupt(paused, thread_id)
    assert info is not None
    a2ui = info["draft"]["a2ui"]
    assert a2ui["widget"] == "form"
    assert a2ui["props"]["fields"][0]["name"] == "gene_id"


@pytest.mark.asyncio
async def test_a2ui_graph_two_round_reenter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assistant cue re-enters once; second round then settles terminal."""
    generate_calls = {"n": 0}

    async def _fake_run(
        messages: list[dict[str, str]],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        del messages, options
        generate_calls["n"] += 1
        if generate_calls["n"] == 1:
            content = "请选择 one option to continue"
        else:
            content = "Analysis complete."
        return {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "follow_up_questions": [],
                    }
                }
            ]
        }

    async def _fake_phyto_chat(query: str, **kwargs: Any) -> dict[str, Any]:
        del query, kwargs
        return {"choices": [{"message": {"content": "[]"}}]}

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.graph._run_phyto_chat",
        _fake_run,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.service.phyto_chat",
        _fake_phyto_chat,
    )

    app = build_chat_a2ui_graph(checkpointer=MemorySaver())
    thread_id = "chat-a2ui-two-round"
    paused1 = await app.ainvoke(
        {
            "user_query": "请确认是否继续",
            "obs_file_list": [],
            "chat_kwargs": {},
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    info1 = detect_interrupt(paused1, thread_id)
    assert info1 is not None
    surface1 = info1["draft"]["a2ui"]["surface_id"]

    paused2 = await aresume_graph(
        app,
        thread_id,
        {
            "accepted": True,
            "surface_id": surface1,
            "widget": "confirm",
            "action_id": "act-1",
        },
    )
    info2 = detect_interrupt(paused2, thread_id)
    assert info2 is not None
    surface2 = info2["draft"]["a2ui"]["surface_id"]
    assert surface2 != surface1
    assert "请选择" in paused2["response"]["choices"][0]["message"]["content"]

    final = await aresume_graph(
        app,
        thread_id,
        {
            "accepted": True,
            "surface_id": surface2,
            "widget": info2["draft"]["a2ui"]["widget"],
            "action_id": "act-2",
        },
    )
    assert detect_interrupt(final, thread_id) is None
    content = final["response"]["choices"][0]["message"]["content"]
    assert "请选择" not in content
    assert "Analysis complete." in content
    assert generate_calls["n"] == 2
