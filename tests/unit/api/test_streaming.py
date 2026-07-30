# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Direct contracts for the extracted HTTP streaming runtime."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import ConnectError, TimeoutException

from mcp_server_phytomni.api import streaming
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.mcp.result_formatting import (
    AguiEvent,
    run_error,
    run_finished,
    run_started,
    text_message_content,
    text_message_end,
    text_message_start,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

pytestmark = pytest.mark.unit


def _disabled() -> bool:
    """Keep the direct ordinary-stream contract off the A2UI branch."""
    return False


def _no_widget(_query: str) -> str | None:
    """Return no A2UI widget for an ordinary stream."""
    return None


def _fixed_run_id(_prefix: str, _kind: str) -> str:
    """Return a deterministic id for the adapter contract."""
    return "run-direct-contract"


def _chat_slug(_model: str) -> str | None:
    """Map the direct contract model to its registry slug."""
    return "chat"


def _current_owner() -> str | None:
    """Return the owner captured by the fake request context."""
    return "owner-direct-contract"


def _current_request_id() -> str | None:
    """Return the request id captured by the fake request context."""
    return "request-direct-contract"


def _max_answer_bytes() -> int:
    """Keep the direct stream answer cap comfortably above the fixture."""
    return 1024


def _unused_runtime() -> Any:
    """Fail if the ordinary contract unexpectedly enters A2UI runtime."""
    raise AssertionError("ordinary stream unexpectedly requested A2UI runtime")


async def _direct_events(
    _tool_name: str,
    _arguments: dict[str, Any],
    *,
    run_id: str,
    dialogue_id: str | None,
) -> AsyncIterator[AguiEvent]:
    """Yield one complete typed stream for the adapter contract."""
    yield run_started(run_id, dialogue_id)
    yield text_message_content("direct-message", "adapter answer")
    yield run_finished(run_id)


def _conversation_envelope(*, turn_id: str = "11") -> ConversationEnvelopeV1:
    """Build one Instant V1 envelope for direct streaming tests."""
    return ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(
                UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
            ),
            "dialogue_id": str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad8")),
            "turn_id": turn_id,
            "request_id": f"request-{turn_id}",
            "operation": "append",
            "mode": "instant",
            "current_message": {
                "content": "What is photosynthesis?",
                "locale": "en-US",
            },
            "requested_agent_id": None,
            "allowed_agent_ids": ["ChatAgent"],
            "ledger_cursor": int(turn_id),
            "ledger_version": "a" * 64,
            "base_business_context_version": 0,
            "history_delta": [
                {
                    "turn_id": turn_id,
                    "role": "user",
                    "content": "What is photosynthesis?",
                }
            ],
            "artifact_refs": [],
        }
    )


def _stream_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse one SSE response body into ``(event, payload)`` pairs."""
    frames: list[tuple[str, dict[str, Any]]] = []
    for chunk in body.split("\n\n"):
        if not chunk.startswith("event: "):
            continue
        lines = chunk.splitlines()
        if len(lines) < 2 or not lines[1].startswith("data: "):
            continue
        frames.append(
            (
                lines[0][len("event: ") :],
                json.loads(lines[1][len("data: ") :]),
            )
        )
    return frames


async def _consume_disconnect_stream(
    response: Any,
    payload: ChatCompletionRequest,
    db_path: str,
) -> tuple[str, str, str, ConversationContextStore]:
    """Close an SSE body after its text and return the persisted-turn seam."""
    body = cast(AsyncGenerator[str, None], response.body_iterator)
    seen: list[str] = []
    async for line in body:
        seen.append(line)
        if "event: TextMessageEnd\n" not in line:
            continue
        await body.aclose()
        break
    with pytest.raises(StopAsyncIteration):
        await anext(body)

    rendered = "".join(seen)
    assert payload.conversation is not None
    conversation_data = vars(payload.conversation)
    key = str(conversation_data["conversation_key"])
    turn_id = conversation_data["turn_id"]
    return rendered, key, turn_id, ConversationContextStore(db_path)


def _dependencies(settlements: list[tuple[str, str, str, dict[str, Any]]]):
    """Build explicit request, A2UI, and persistence seams for one test."""

    def _record_run(
        run_id: str,
        agent: str,
        owner: str,
        request_info: Any,
    ) -> None:
        settlements.append((run_id, agent, owner, {"request": request_info}))

    def _settle(
        run_id: str,
        owner: str,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        settlements.append((run_id, owner, status, result))
        return True

    return streaming.StreamingDependencies(
        request=streaming.StreamingRequestDependencies(
            prepare_tool_stream=_direct_events,
            current_user=_current_owner,
            current_request_id=_current_request_id,
            new_run_id=_fixed_run_id,
            agent_slug=_chat_slug,
        ),
        a2ui=streaming.StreamingA2UIDependencies(
            enabled=_disabled,
            select_widget=_no_widget,
            runtime=_unused_runtime,
        ),
        persistence=streaming.StreamingPersistenceDependencies(
            create_running_stream_run=_record_run,
            settle_stream_run=_settle,
            stream_answer_max_bytes=_max_answer_bytes,
        ),
    )


def test_stream_setup_error_keeps_preopen_mapping() -> None:
    """Known setup failures retain their fixed HTTP boundary mapping."""
    assert (
        streaming.stream_setup_error(
            ConnectError("secret upstream"), priming=False
        ).status_code
        == 502
    )
    assert (
        streaming.stream_setup_error(
            TimeoutException("secret timeout"), priming=False
        ).status_code
        == 504
    )
    unsupported = streaming.stream_setup_error(
        NotImplementedError("internal"), priming=False
    )
    assert isinstance(unsupported, HTTPException)
    assert unsupported.status_code == 400


async def test_stream_runtime_uses_adapters_and_settles_answer() -> None:
    """The extracted runtime persists running then terminal state via seams."""
    settlements: list[tuple[str, str, str, dict[str, Any]]] = []
    dependencies = _dependencies(settlements)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="adapter query")],
        stream=True,
        dialogue_id="dialogue-direct-contract",
    )

    response = await streaming.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={"user_query": "adapter query", "obs_file_list": []},
        payload=payload,
        user_query="adapter query",
        dependencies=dependencies,
    )
    body_iterator = cast(AsyncIterator[str], response.body_iterator)
    body = "".join([line async for line in body_iterator])

    assert "event: RunStarted\n" in body
    assert "adapter answer" in body
    assert body.rstrip().endswith("data: [DONE]")
    assert len(settlements) == 2
    assert settlements[0][0:3] == (
        "run-direct-contract",
        "chat",
        "owner-direct-contract",
    )
    assert settlements[1][0:3] == (
        "run-direct-contract",
        "owner-direct-contract",
        "succeeded",
    )
    assert settlements[1][3]["formatted"]["answer"] == "adapter answer"


async def test_context_stream_inserts_bounded_custom_before_finish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """V1 success stages bounded metadata before the terminal finish."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))
    settlements: list[tuple[str, str, str, dict[str, Any]]] = []
    dependencies = _dependencies(settlements)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="adapter query")],
        stream=True,
        conversation=_conversation_envelope(),
    )

    response = await streaming.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={
            "user_query": "adapter query",
            "locale": "en-US",
            "obs_file_list": [],
        },
        payload=payload,
        user_query="adapter query",
        dependencies=dependencies,
    )
    body = "".join(
        [
            line
            async for line in cast(AsyncIterator[str], response.body_iterator)
        ]
    )
    frames = _stream_frames(body)

    assert [name for name, _payload in frames] == [
        "RunStarted",
        "TextMessageContent",
        "Custom",
        "RunFinished",
    ]
    assert settlements[-1][2] == "succeeded"
    assert frames[2][1] == {
        "type": "Custom",
        "name": "phyto.context_staged",
        "value": {
            "schema_version": 1,
            "turn_id": "11",
            "selected_agent_id": "ChatAgent",
            "route_source": "instant_lock",
            "proposed_business_context_version": 1,
            "context_truncated": False,
            "context_rebuilt": True,
            "context_degraded": False,
        },
    }


async def test_context_stream_degraded_keeps_answer_and_finish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A degraded context stage still yields the visible answer and finish."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))
    monkeypatch.setattr(
        streaming,
        "_context_delta_for_stream",
        lambda _answer: (None, True),
    )
    settlements: list[tuple[str, str, str, dict[str, Any]]] = []
    dependencies = _dependencies(settlements)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="adapter query")],
        stream=True,
        conversation=_conversation_envelope(turn_id="12"),
    )

    response = await streaming.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={
            "user_query": "adapter query",
            "locale": "en-US",
            "obs_file_list": [],
        },
        payload=payload,
        user_query="adapter query",
        dependencies=dependencies,
    )
    body = "".join(
        [
            line
            async for line in cast(AsyncIterator[str], response.body_iterator)
        ]
    )
    frames = _stream_frames(body)

    assert [name for name, _payload in frames][-2:] == [
        "Custom",
        "RunFinished",
    ]
    assert frames[-2][1]["value"]["context_degraded"] is True
    assert settlements[-1][3]["formatted"]["answer"] == "adapter answer"


async def test_context_stream_disconnect_before_stage_marks_turn_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Closing a V1 stream before ``RunFinished`` never stages context."""
    db_path = str(tmp_path / "context.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db_path)

    async def staged_events(
        _tool_name: str,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[AguiEvent]:
        yield run_started(run_id, dialogue_id)
        yield text_message_start("msg-cancel")
        yield text_message_content("msg-cancel", "adapter answer")
        yield text_message_end("msg-cancel")
        yield run_finished(run_id)

    settlements: list[tuple[str, str, str, dict[str, Any]]] = []
    dependencies = _dependencies(settlements)
    dependencies = streaming.StreamingDependencies(
        request=streaming.StreamingRequestDependencies(
            prepare_tool_stream=staged_events,
            current_user=dependencies.request.current_user,
            current_request_id=dependencies.request.current_request_id,
            new_run_id=dependencies.request.new_run_id,
            agent_slug=dependencies.request.agent_slug,
        ),
        a2ui=dependencies.a2ui,
        persistence=dependencies.persistence,
    )
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="adapter query")],
        stream=True,
        conversation=_conversation_envelope(turn_id="14"),
    )

    response = await streaming.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={
            "user_query": "adapter query",
            "locale": "en-US",
            "obs_file_list": [],
        },
        payload=payload,
        user_query="adapter query",
        dependencies=dependencies,
    )
    rendered, key, turn_id, store = await _consume_disconnect_stream(
        response, payload, db_path
    )

    assert '"name": "phyto.context_staged"' not in rendered
    assert "event: RunFinished\n" not in rendered
    assert settlements[-1][2] == "failed"
    assert settlements[-1][3]["formatted"]["answer"] == "adapter answer"
    assert settlements[-1][3]["partial"] is True
    stored_turn = store.load_turn(key, turn_id)
    assert stored_turn is not None
    assert stored_turn.state == "failed"
    assert stored_turn.result is None
    assert store.load_context(key) is None


async def test_context_stream_run_error_emits_no_successful_context_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A terminal RunError never yields the successful context marker."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))

    async def failing_events(
        _tool_name: str,
        _arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
    ) -> AsyncIterator[AguiEvent]:
        yield run_started(run_id, dialogue_id)
        yield text_message_start("msg-err")
        yield text_message_content("msg-err", "partial")
        yield text_message_end("msg-err")
        yield run_error("agent_execution_failed", "already safe")

    settlements: list[tuple[str, str, str, dict[str, Any]]] = []
    dependencies = _dependencies(settlements)
    dependencies = streaming.StreamingDependencies(
        request=streaming.StreamingRequestDependencies(
            prepare_tool_stream=failing_events,
            current_user=dependencies.request.current_user,
            current_request_id=dependencies.request.current_request_id,
            new_run_id=dependencies.request.new_run_id,
            agent_slug=dependencies.request.agent_slug,
        ),
        a2ui=dependencies.a2ui,
        persistence=dependencies.persistence,
    )
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="adapter query")],
        stream=True,
        conversation=_conversation_envelope(turn_id="13"),
    )

    response = await streaming.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={
            "user_query": "adapter query",
            "locale": "en-US",
            "obs_file_list": [],
        },
        payload=payload,
        user_query="adapter query",
        dependencies=dependencies,
    )
    body = "".join(
        [
            line
            async for line in cast(AsyncIterator[str], response.body_iterator)
        ]
    )

    assert "phyto.context_staged" not in body
    assert "event: RunFinished\n" not in body
    assert body.count("event: RunError\n") == 1


async def test_v0_stream_bytes_remain_unchanged() -> None:
    """The legacy V0 stream shape stays byte-for-byte identical."""
    settlements: list[tuple[str, str, str, dict[str, Any]]] = []
    dependencies = _dependencies(settlements)
    payload = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="adapter query")],
        stream=True,
        dialogue_id="dialogue-direct-contract",
    )

    response = await streaming.stream_chat_completion(
        tool_name="ChatAgent",
        arguments={"user_query": "adapter query", "obs_file_list": []},
        payload=payload,
        user_query="adapter query",
        dependencies=dependencies,
    )
    body = "".join(
        [
            line
            async for line in cast(AsyncIterator[str], response.body_iterator)
        ]
    )

    assert body == (
        "event: RunStarted\n"
        'data: {"type": "RunStarted", "run_id": "run-direct-contract", '
        '"dialogue_id": "dialogue-direct-contract"}\n\n'
        "event: TextMessageContent\n"
        'data: {"type": "TextMessageContent", "message_id": '
        '"direct-message", "delta": "adapter answer"}\n\n'
        "event: RunFinished\n"
        'data: {"type": "RunFinished", "run_id": "run-direct-contract"}\n\n'
        "data: [DONE]\n\n"
    )
