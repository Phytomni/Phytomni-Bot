# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for conversation-context persistence on streamed Chat responses."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import httpx
import pytest
from tests.server.test_api_chat_streaming import (
    _consume_context_stage,
    _conversation_envelope,
    _extract_custom_context,
    _runtime_context_service,
    _stream_frames,
)
from tests.support.http_fakes import expected_context_staged_value

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.app import (
    _stream_chat_response as _stream_chat_completion,
)
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.mcp.result_formatting import (
    run_error,
    run_finished,
    run_started,
    text_message_content,
    text_message_end,
    text_message_start,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.request_context import request_context
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


async def test_context_stream_stages_before_custom_and_then_finishes(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1 streaming settles the run before the custom frame."""
    captured: dict[str, str] = {}
    answer_marker = "STREAM_CONTEXT_ANSWER_OUTPUT_SENTINEL"

    async def fake_streamed(
        _tool_name: Any,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        captured["run_id"] = run_id
        yield run_started(run_id, dialogue_id)
        yield text_message_start("msg-v1")
        yield text_message_content("msg-v1", answer_marker)
        yield text_message_end("msg-v1")
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", fake_streamed)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="context")],
        stream=True,
        conversation=_conversation_envelope(),
    )
    assert payload.conversation is not None
    with request_context("u1", "req-context-stream"):
        response = await _stream_chat_completion(
            tool_name="ChatAgent",
            arguments={
                "user_query": "context",
                "locale": "en-US",
                "obs_file_list": [],
            },
            payload=payload,
            user_query="context",
        )
        accumulated, conversation_data = await _consume_context_stage(
            response,
            payload,
            tasks_db_path,
            captured["run_id"],
            answer_marker,
        )

    assert answer_marker in accumulated
    committed = await _runtime_context_service().acknowledge_settlement(
        payload.conversation,
        conversation_data["ledger_version"],
    )
    assert committed is not None
    assert answer_marker not in json.dumps(committed.context, sort_keys=True)

    assert accumulated.index(
        '"name": "phyto.context_staged"'
    ) < accumulated.index("event: RunFinished\n")
    assert accumulated.count("event: RunFinished\n") == 1
    assert _extract_custom_context(accumulated) == (
        expected_context_staged_value("21")
    )


async def test_context_stream_http_route_uses_context_stream_runtime(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public chat route forwards V1 streams to the context runtime."""
    del tasks_db_path

    async def fake_stream(
        _tool_name: str,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str,
    ) -> AsyncIterator[Any]:
        """Yield one valid AG-UI stream without invoking a provider."""
        del _tool_name, _arguments
        yield run_started(run_id, dialogue_id)
        yield text_message_start("route-context-message")
        yield text_message_content(
            "route-context-message", "context stream answer"
        )
        yield text_message_end("route-context-message")
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", fake_stream)
    response = await chat_completion(
        api_client,
        issued_api_key,
        conversation=_conversation_envelope().model_dump(mode="json"),
        stream=True,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"name": "phyto.context_staged"' in response.text
    assert "event: RunFinished\n" in response.text
    assert response.text.rstrip().endswith("data: [DONE]")


async def test_context_stream_duplicate_turn_replays_without_reinvocation(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated V1 turn replays the stream instead of reinvoking Chat."""
    del tasks_db_path
    invocations = 0

    async def fake_streamed(
        _tool_name: Any,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        nonlocal invocations
        invocations += 1
        yield run_started(run_id, dialogue_id)
        yield text_message_start("msg-dup")
        yield text_message_content("msg-dup", "Replay me")
        yield text_message_end("msg-dup")
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", fake_streamed)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="context")],
        stream=True,
        conversation=_conversation_envelope(turn_id="22"),
    )

    async def drive() -> str:
        with request_context("u1", "req-context-dup"):
            response = await _stream_chat_completion(
                tool_name="ChatAgent",
                arguments={
                    "user_query": "context",
                    "locale": "en-US",
                    "obs_file_list": [],
                },
                payload=payload,
                user_query="context",
                execution_id="turn-context-24",
            )
            return "".join(
                [
                    line
                    async for line in cast(
                        AsyncIterator[str], response.body_iterator
                    )
                ]
            )

    first = await drive()
    second = await drive()

    assert invocations == 1
    assert _stream_frames(first) == _stream_frames(second)
    assert first.count("event: RunFinished\n") == 1
    assert '"name": "phyto.context_staged"' in first


async def test_context_stream_committed_turn_replays_without_reinvocation(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed V1 turn replays stored SSE bytes without Chat replay."""
    invocations = 0

    async def fake_streamed(
        _tool_name: Any,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        nonlocal invocations
        invocations += 1
        yield run_started(run_id, dialogue_id)
        yield text_message_start("msg-committed")
        yield text_message_content("msg-committed", "Replay me")
        yield text_message_end("msg-committed")
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", fake_streamed)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="context")],
        stream=True,
        conversation=_conversation_envelope(turn_id="24"),
    )

    async def drive(request_id: str) -> str:
        with request_context("u1", request_id):
            response = await _stream_chat_completion(
                tool_name="ChatAgent",
                arguments={
                    "user_query": "context",
                    "locale": "en-US",
                    "obs_file_list": [],
                },
                payload=payload,
                user_query="context",
                execution_id="turn-context-committed-24",
            )
            return "".join(
                [
                    line
                    async for line in cast(
                        AsyncIterator[str], response.body_iterator
                    )
                ]
            )

    first = await drive("req-context-committed-first")
    assert payload.conversation is not None
    conversation = payload.conversation
    conversation_data = vars(conversation)
    committed = await _runtime_context_service().acknowledge_settlement(
        conversation, conversation_data["ledger_version"]
    )
    second = await drive("req-context-committed-second")

    registry = RunRegistry(db_path=tasks_db_path)
    runs = [
        run
        for run in registry.list_runs(owner="u1")
        if run.spec.agent == "chat" and run.request_info is not None
    ]
    stored_turn = ConversationContextStore(tasks_db_path).load_turn(
        str(conversation_data["conversation_key"]),
        conversation_data["turn_id"],
    )

    assert invocations == 1
    assert committed is not None
    assert committed.context_version == 1
    assert committed.ledger_version == conversation_data["ledger_version"]
    assert stored_turn is not None
    assert stored_turn.state == "committed"
    assert len(runs) == 1
    assert _stream_frames(first) == _stream_frames(second)
    assert [name for name, _payload in _stream_frames(second)] == [
        "RunStarted",
        "TextMessageStart",
        "TextMessageContent",
        "TextMessageEnd",
        "Custom",
        "RunFinished",
    ]
    assert second.count("event: RunFinished\n") == 1
    assert _extract_custom_context(second) == (
        expected_context_staged_value("24")
    )


async def test_context_stream_failure_emits_no_successful_context_metadata(
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed V1 stream never emits the successful context custom event."""
    del tasks_db_path

    async def failing_streamed(
        _tool_name: Any,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[Any]:
        yield run_started(run_id, dialogue_id)
        yield text_message_start("msg-fail")
        yield text_message_content("msg-fail", "partial")
        yield text_message_end("msg-fail")
        yield run_error("agent_execution_failed", "already safe")

    monkeypatch.setattr(api_app, "prepare_tool_stream", failing_streamed)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="context")],
        stream=True,
        conversation=_conversation_envelope(turn_id="23"),
    )
    with request_context("u1", "req-context-fail"):
        response = await _stream_chat_completion(
            tool_name="ChatAgent",
            arguments={
                "user_query": "context",
                "locale": "en-US",
                "obs_file_list": [],
            },
            payload=payload,
            user_query="context",
        )
        body = "".join(
            [
                line
                async for line in cast(
                    AsyncIterator[str], response.body_iterator
                )
            ]
        )

    assert '"name": "phyto.context_staged"' not in body
    assert "event: RunFinished\n" not in body
    assert body.count("event: RunError\n") == 1
