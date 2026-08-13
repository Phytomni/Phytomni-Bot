# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the OpenAI-compatible chat completions endpoint.

Covers auth enforcement, message flattening, ChatAgent passthrough with
follow_up_questions preserved, stream rejection, and unknown model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from tests.support.chat_fakes import (
    ChatCompletionOptions,
    chat_completion_payload,
    install_chat_handler,
    misplaced_reasoning_message,
    recent_knowledge_context,
    recent_knowledge_turns,
)
from tests.support.handler_fakes import (
    patch_chat_completion_service,
    patch_context_chat_runtime,
    review_success_result,
)
from tests.support.http_fakes import build_instant_chat_context_envelope

import mcp_server_phytomni.agents.chat.service as chat_service
import mcp_server_phytomni.agents.review.agent as review_agent
import mcp_server_phytomni.mcp.handlers as mcp_handlers
from mcp_server_phytomni import server
from mcp_server_phytomni.agents.knowledge.conversation import (
    KnowledgeConversationAdapter,
)
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.a2ui_runtime import ReviewExecution
from mcp_server_phytomni.runtime.conversation_context.adapters import (
    canonical_agent_invocation,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextProjection,
    RoleTaggedTurn,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id as context_agent_thread_id,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _conversation_envelope(*, turn_id: str = "1") -> dict[str, Any]:
    """Build one Instant V1 envelope for a chat completion test."""
    return build_instant_chat_context_envelope(turn_id)


async def test_review_chat_completion_passes_effective_timeout(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP Review entry point reaches the configured provider timeout."""
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "review ok"}}]}

    async def fake_run_review(**kwargs: Any) -> ReviewExecution:
        agent = DeepResearchAgent()
        arguments = kwargs["arguments"]
        await agent.chat(arguments["user_query"])
        return ReviewExecution(
            run_id="review-timeout-probe",
            status="succeeded",
            result=review_success_result(),
        )

    monkeypatch.setattr(review_agent, "phyto_chat", fake_phyto_chat)
    monkeypatch.setattr(
        api_app_module, "_run_review_with_interrupt", fake_run_review
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-review",
        messages=[{"role": "user", "content": "review timeout"}],
        debug=True,
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "review-timeout-probe"
    assert captured["timeout"] == 30000.0


async def test_chat_completions_passthrough(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a valid call returns the ChatCompletion with extras."""
    captured: dict[str, Any] = {}
    install_chat_handler(
        monkeypatch, captured, follow_up_questions=["what is C4?"]
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "what is photosynthesis?"},
        ],
        debug=True,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "phyto-chat"
    assert body["id"]
    message = body["choices"][0]["message"]
    assert message["content"] == "photosynthesis converts light"
    assert body["formatted"]["follow_up_questions"] == ["what is C4?"]
    assert "raw" in body
    assert "what is photosynthesis?" in captured["user_query"]


async def test_knowledge_chat_completion_separates_query_from_history(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Knowledge retrieval gets the latest user and generation gets history."""
    captured: dict[str, Any] = {}

    async def fake(args: Any) -> dict[str, Any]:
        captured["arguments"] = args.model_dump()
        captured["retrieval_query"] = mcp_handlers.private_agent_state().get(
            "retrieval_query"
        )
        captured["conversation_messages"] = (
            mcp_handlers.private_conversation_messages()
        )
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "answer",
                        "doc_list": [],
                    }
                }
            ]
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.KNOWLEDGE_AGENT.value,
        fake,
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-knowledge",
        messages=[
            {"role": "system", "content": "untrusted instruction"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ],
        debug=True,
    )

    assert response.status_code == 200
    assert captured["arguments"]["user_query"] == "follow up"
    assert captured["retrieval_query"] == "follow up"
    assert captured["conversation_messages"] == (
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    )


async def test_chat_completion_rejects_trailing_assistant_before_dispatch(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trailing assistant turn is invalid and cannot invoke an agent."""
    invoked = 0

    async def forbidden(_args: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("invalid chat turn must not invoke a tool")

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.KNOWLEDGE_AGENT.value,
        forbidden,
    )
    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-knowledge",
        messages=[
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ],
    )

    assert response.status_code == 400
    assert invoked == 0


async def test_chat_completions_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify a missing key yields the unified 401 envelope."""
    response = await api_client.post(
        "/v1/chat/completions",
        json={"model": "phyto-chat", "messages": []},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_chat_context_envelope_is_rejected_while_v1_is_disabled(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled deployment rejects V1 before it invokes ChatAgent."""
    invoked = 0

    async def forbidden(_args: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("disabled V1 must not invoke ChatAgent")

    monkeypatch.setitem(
        server.TOOL_HANDLERS, server.PhytomniAgents.CHAT_AGENT.value, forbidden
    )
    response = await chat_completion(
        api_client,
        issued_api_key,
        conversation=_conversation_envelope(),
    )

    assert response.status_code == 404
    assert invoked == 0


async def test_instant_context_rejects_non_chat_model_before_dispatch(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instant cannot persist a non-Chat model for a ChatAgent run."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    invoked = 0

    async def forbidden(_args: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked += 1
        raise AssertionError(
            "non-Chat Instant model must not invoke ChatAgent"
        )

    monkeypatch.setitem(
        server.TOOL_HANDLERS, server.PhytomniAgents.CHAT_AGENT.value, forbidden
    )
    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-knowledge",
        conversation=_conversation_envelope(),
    )

    assert response.status_code == 422
    assert invoked == 0


def test_chat_context_adapter_separates_native_history_from_query() -> None:
    """Chat receives prior roles while dispatch sees the latest turn."""
    thread_id = context_agent_thread_id(
        UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"), "ChatAgent"
    )
    dispatch = canonical_agent_invocation(
        ContextProjection(
            current_query="U3",
            relevant_recent_turns=[
                RoleTaggedTurn(role="user", content="U1"),
                RoleTaggedTurn(role="assistant", content="A1"),
                RoleTaggedTurn(role="user", content="U2"),
                RoleTaggedTurn(role="assistant", content="A2"),
            ],
            agent_thread_id=thread_id,
            locale="en-US",
            token_budget=100,
        )
    )

    assert dispatch.arguments["user_query"] == "U3"
    assert dispatch.agent_thread_id == thread_id
    assert dispatch.conversation_messages == (
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "A2"},
    )


def test_chat_context_adapter_keeps_unpaired_user_at_degraded_boundary() -> (
    None
):
    """A missing assistant summary does not reorder later user history."""
    dispatch = canonical_agent_invocation(
        ContextProjection(
            current_query="U2",
            relevant_recent_turns=[
                RoleTaggedTurn(role="user", content="U1"),
                RoleTaggedTurn(role="assistant", content="A1"),
                RoleTaggedTurn(role="user", content="U2"),
                RoleTaggedTurn(role="user", content="U3"),
                RoleTaggedTurn(role="assistant", content="A3"),
            ],
            agent_thread_id="ctx-" + "a" * 64,
            locale="en-US",
            token_budget=100,
        )
    )

    assert dispatch.conversation_messages == (
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "user", "content": "U3"},
        {"role": "assistant", "content": "A3"},
    )


def test_knowledge_context_adapter_separates_query_from_context() -> None:
    """Knowledge builds one retrieval query plus bounded context."""
    adapter = KnowledgeConversationAdapter()

    prepared = adapter.prepare(
        ContextProjection(
            current_query="What evidence supports that?",
            relevant_recent_turns=recent_knowledge_turns(),
            agent_thread_id="ctx-" + "2" * 64,
            locale="en-US",
            token_budget=1024,
        )
    )

    assert prepared == {
        "user_query": "What evidence supports that?",
        "retrieval_query": "What evidence supports OsDREB1?",
        "answer_context": recent_knowledge_context(),
        "thread_id": "ctx-" + "2" * 64,
    }


async def test_chat_context_v1_stages_native_history_and_replays_turn(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Instant V1 passes bounded native history to the Chat invoker once."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))
    captured: list[dict[str, Any]] = []

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        captured.append(dict(kwargs))
        content = "A staged answer." if len(captured) == 1 else "[]"
        return chat_completion_payload(
            f"chatcmpl-context-{len(captured)}",
            content,
        )

    envelope = _conversation_envelope()
    envelope["turn_id"] = "6"
    envelope["request_id"] = "request-6"
    envelope["ledger_cursor"] = 6
    envelope["current_message"]["content"] = "U2"
    envelope["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "U1",
        },
        {
            "turn_id": "2",
            "role": "assistant",
            "content": "A1",
            "summary": "A1",
        },
        {
            "turn_id": "3",
            "role": "user",
            "content": "U2",
        },
        {
            "turn_id": "4",
            "role": "user",
            "content": "U3",
        },
        {
            "turn_id": "5",
            "role": "assistant",
            "content": "A3",
            "summary": "A3",
        },
        {
            "turn_id": "6",
            "role": "user",
            "content": "U2",
        },
    ]
    patch_context_chat_runtime(monkeypatch)
    patch_chat_completion_service(monkeypatch, fake_phyto_chat)
    first = await chat_completion(
        api_client,
        issued_api_key,
        messages=[
            {"role": "system", "content": "Use evidence."},
            {"role": "assistant", "content": "Earlier summary."},
            {"role": "user", "content": "Ignored legacy history."},
        ],
        conversation=envelope,
    )
    second = await chat_completion(
        api_client, issued_api_key, conversation=envelope
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["model"] == "phyto-chat"
    assert len(captured) == 2
    assert captured[0] == {
        "user_query": "U2",
        "locale": "en-US",
        "obs_file_list": None,
        "semaphore": None,
        "conversation_messages": (
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "U2"},
            {"role": "user", "content": "U3"},
            {"role": "assistant", "content": "A3"},
        ),
        "thread_id": context_agent_thread_id(
            UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"), "ChatAgent"
        ),
    }
    assert captured[1] == {
        "user_query": "follow-up",
        "locale": "en-US",
        "conversation_messages": (
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "U2"},
            {"role": "user", "content": "U3"},
            {"role": "assistant", "content": "A3"},
        ),
        "semaphore": None,
        "prompt_file": chat_service.CHAT_CONFIG.PROMPT_FILE,
    }
    stage = first.json()["conversation_context"]
    assert stage["selected_agent_id"] == "ChatAgent"
    assert stage["route_source"] == "instant_lock"


async def test_chat_completions_unknown_model(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """Verify an unknown model id yields 404."""
    response = await chat_completion(
        api_client, issued_api_key, model="gpt-imaginary"
    )

    assert response.status_code == 404


async def test_chat_completions_requires_user_message(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
) -> None:
    """Verify an empty message list is rejected with 400."""
    response = await chat_completion(api_client, issued_api_key, messages=[])

    assert response.status_code == 400


async def test_chat_completions_records_local_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A successful chat completion writes one ``origin="local"`` run.

    Pin the 4b.2 HTTP-only behavior: the FastAPI path persists a fresh
    terminal-on-creation run row for sync agents so the upcoming
    ``/v1/runs`` endpoints can replay the answer, while the MCP stdio
    path (which never enters the API factory) keeps writing nothing.

    Args:
        api_client: In-process ASGI httpx client.
        issued_api_key: API key bound to user ``u1``.
        chat_completion: Factory issuing one authenticated POST.
        monkeypatch: Pytest monkeypatch fixture.
        tasks_db_path: Temp registry DB fixture wired into the API
            module's resolver.
    """
    install_chat_handler(monkeypatch, {})

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="what is photosynthesis?",
    )
    assert response.status_code == 200

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    record = listing[0]
    assert record.spec.agent == "chat"
    assert record.spec.origin == "local"
    assert record.spec.user_id == "u1"
    assert record.status == "succeeded"
    assert record.result is not None
    assert record.timestamps.expires_at is not None
    assert not record.task_ids


async def test_chat_completions_persists_request_info(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Chat completions write dialogue / query / tool / model into runs."""
    install_chat_handler(monkeypatch, {})

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="what is photosynthesis?",
        dialogue_id="dlg-42",
    )
    assert response.status_code == 200

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    info = listing[0].request_info
    assert info.dialogue_id == "dlg-42"
    assert info.query is not None
    assert "what is photosynthesis?" in info.query
    assert info.tool_name == "ChatAgent"
    assert info.model == "phyto-chat"
    assert info.request_json is not None
    assert "phyto-chat" in info.request_json


async def test_chat_completions_omits_dialogue_id_keeps_field_null(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A request without dialogue_id persists with the field NULL."""
    install_chat_handler(monkeypatch, {})

    await chat_completion(api_client, issued_api_key)

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    info = listing[0].request_info
    assert info.dialogue_id is None
    assert info.model == "phyto-chat"


async def test_chat_completions_preserves_provider_reasoning_and_usage(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reasoner-style raw fields survive in choices.message and top level.

    When a provider returns ``reasoning_content`` on the message and
    ``usage`` / ``system_fingerprint`` / ``finish_reason`` at the top
    level, the envelope route keeps them at their OpenAI canonical
    positions so SDK clients reading them by name continue to work, and
    also exposes the full handler payload under ``raw`` for clients
    that want every provider-returned field.
    """

    async def fake(args: Any) -> dict[str, Any]:
        del args
        return chat_completion_payload(
            "chatcmpl-reasoner",
            "Light energy is captured by chlorophyll.",
            ChatCompletionOptions(
                message_fields={
                    "reasoning_content": "Step 1: identify photons...",
                    "tool_calls": [],
                },
                usage={
                    "prompt_tokens": 42,
                    "completion_tokens": 11,
                    "total_tokens": 53,
                },
                system_fingerprint="fp_test",
            ),
        )

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="why are leaves green?",
        debug=True,
    )

    assert response.status_code == 200
    body = response.json()
    message = body["choices"][0]["message"]
    assert message["content"] == "Light energy is captured by chlorophyll."
    assert message["reasoning_content"] == "Step 1: identify photons..."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 42
    assert body["usage"]["total_tokens"] == 53
    assert body["system_fingerprint"] == "fp_test"
    assert body["raw"]["choices"][0]["message"]["reasoning_content"] == (
        "Step 1: identify photons..."
    )
    assert body["raw"]["usage"]["total_tokens"] == 53


async def test_chat_completions_repairs_reasoning_content_answer_tail(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical, formatted, and raw views share the repaired answer."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a provider payload with the answer misplaced."""
        del args
        return {
            "id": "chatcmpl-misplaced",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": misplaced_reasoning_message(),
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 12},
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="why are leaves green?",
        debug=True,
    )

    assert response.status_code == 200
    body = response.json()
    message = body["choices"][0]["message"]
    assert message["content"] == "Leaves capture light."
    assert message["reasoning_content"] == "identify chlorophyll"
    assert body["formatted"]["answer"] == "Leaves capture light."
    raw_message = body["raw"]["choices"][0]["message"]
    assert raw_message["content"] == "Leaves capture light."
    assert raw_message["reasoning_content"] == "identify chlorophyll"
    assert body["usage"]["total_tokens"] == 12


async def test_chat_completions_envelope_carries_formatted_and_raw(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every chat completion body exposes top-level formatted and raw blocks.

    Locks the envelope contract end to end: the legacy duplicated top
    level (``follow_up_questions`` / ``references`` / ``metadata``)
    moved inside ``formatted``, ``raw`` carries the sanitized handler
    payload, and the ChatCompletion shape stays OpenAI compatible.
    """
    install_chat_handler(monkeypatch, {})

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="hi",
        debug=True,
    )

    assert response.status_code == 200
    body = response.json()
    assert "formatted" in body
    assert "raw" in body
    assert "follow_up_questions" not in body
    assert "references" not in body
    assert "metadata" not in body
    assert set(body["formatted"].keys()) == {
        "answer",
        "follow_up_questions",
        "metadata",
        "references",
        "tabular",
        "output_dirs",
    }
    assert isinstance(body["raw"], dict)
    assert body["raw"]["choices"][0]["message"]["content"] == (
        "photosynthesis converts light"
    )


async def test_chat_completions_brief_gene_surfaces_literature_degraded(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A literature-degraded brief_gene run surfaces ``degraded`` on route.

    Drives the real ``/v1/chat/completions`` path for ``phyto-brief-gene``
    with a handler whose ``phytomni_state`` carries a
    ``literature_degraded`` record, then asserts the **default-mode**
    ``formatted.metadata.degraded`` block reaches the HTTP body. The
    cited formatter's degraded projection is pinned at the unit level;
    this is the one route-level proof that it survives the
    chat-completions envelope to the API surface, status untouched.
    """

    async def fake(args: Any) -> dict[str, Any]:
        """Return a brief_gene payload with a literature_degraded record."""
        _ = args
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "gene report"}}
            ],
            "phytomni_state": {
                "literature_degraded": [
                    {"task_label": "OsCAB1", "message": "boom"}
                ],
            },
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.BRIEF_GENE_AGENT.value,
        fake,
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        model="phyto-brief-gene",
        content="Os01g0177400",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["formatted"]["metadata"]["degraded"] == {
        "reason": "literature_retrieval",
        "count": 1,
        "labels": ["OsCAB1"],
    }


async def test_chat_completions_exposes_run_id_matching_runs_listing(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Default-mode response carries ``run_id`` equal to the runs listing.

    AF-001 acceptance: the chat completion response's ``run_id`` must
    equal the row returned by ``GET /v1/runs?dialogue_id=...`` so Web
    can join on the Bot run id (the OpenAI ``chatcmpl-*`` provider id
    is NOT the join key).
    """
    install_chat_handler(monkeypatch, {})

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="what is photosynthesis?",
        dialogue_id="dlg-join-test",
    )

    assert response.status_code == 200
    body = response.json()
    completion_run_id = body.get("run_id")
    assert completion_run_id is not None
    assert isinstance(completion_run_id, str)
    assert "-run-chat-" in completion_run_id

    listing_resp = await api_client.get(
        "/v1/runs",
        params={"dialogue_id": "dlg-join-test"},
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert listing_resp.status_code == 200
    listing_data = listing_resp.json()["data"]
    assert len(listing_data) == 1
    assert listing_data[0]["run_id"] == completion_run_id
    # Belt-and-braces: the same run_id is readable directly from the
    # registry the fixture wired up.
    record = RunRegistry(tasks_db_path).get_run(completion_run_id, owner="u1")
    assert record is not None
    assert record.spec.run_id == completion_run_id


async def test_chat_completions_run_id_survives_default_strip(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_id`` is preserved through ``strip_chat_completion``.

    Default (non-debug) mode strips ``raw`` and provider extensions,
    but ``run_id`` and ``degraded_tracking`` must survive the strip so
    non-debug clients can still join on the Bot run id.
    """
    install_chat_handler(monkeypatch, {})

    # Default mode: debug not set (defaults to False).
    response = await chat_completion(
        api_client,
        issued_api_key,
        content="hello",
    )

    assert response.status_code == 200
    body = response.json()
    assert "run_id" in body
    assert body["run_id"] is not None
    # raw must NOT be in default-mode response.
    assert "raw" not in body


async def test_chat_completions_degraded_tracking_on_persistence_failure(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry write failure surfaces ``degraded_tracking: True``.

    AF-004 acceptance: when ``_record_sync_run`` returns ``None``
    (SQLite / OS failure), the chat completion still succeeds (HTTP
    200) but the response advertises ``run_id: null`` and
    ``degraded_tracking: true`` so the client knows the run row was
    not persisted. Mirrors the remote-agent ``degraded_tracking``
    signal.
    """
    install_chat_handler(monkeypatch, {})
    monkeypatch.setattr(
        "mcp_server_phytomni.api.app._record_sync_run",
        lambda **_: None,
    )

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="what is photosynthesis?",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] is None
    assert body["degraded_tracking"] is True
