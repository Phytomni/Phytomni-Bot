# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the A2A request handler and artifact projection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import (
    GetTaskRequest,
    InvalidParamsError,
    Message,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
)
from google.protobuf import json_format

from mcp_server_phytomni.agents.expert.router import ToolSelection
from mcp_server_phytomni.api.a2a.executor import (
    A2AHandlerOptions,
    A2ARegistration,
    A2ARequestHandler,
)
from mcp_server_phytomni.mcp.result_formatting import AguiEvent

pytestmark = pytest.mark.unit


async def _select_chat(_text: str) -> ToolSelection:
    return ToolSelection("ChatAgent", {"user_query": "routed"})


def _request(skill_id: str = "ChatAgent") -> SendMessageRequest:
    request = SendMessageRequest(
        message=Message(
            message_id="m1",
            context_id="c1",
            role=Role.ROLE_USER,
            parts=[{"text": "hello"}],
        )
    )
    json_format.ParseDict({"skill_id": skill_id}, request.metadata)
    return request


def _handler(
    body: dict[str, Any],
    status_code: int = 200,
    tool_to_agent: dict[str, str] | None = None,
) -> A2ARequestHandler:
    async def invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        return body, status_code

    return A2ARequestHandler(
        invoke_agent_run=invoke,
        tool_to_agent=(
            {"ChatAgent": "chat"} if tool_to_agent is None else tool_to_agent
        ),
        select_agent=_select_chat,
    )


async def test_send_message_projects_text_and_data_artifacts() -> None:
    """A completed agent run becomes a Task with text and data artifacts."""
    handler = _handler(
        {
            "id": "run-1",
            "agent": "chat",
            "status": "succeeded",
            "task_ids": [],
            "result": {
                "formatted": {
                    "answer": "answer",
                    "references": [{"title": "paper"}],
                    "metadata": {"tool": "ChatAgent"},
                }
            },
        }
    )

    task = await handler.on_message_send(_request(), ServerCallContext())

    assert isinstance(task, Task)
    assert task.id == "run-1"
    assert task.context_id == "c1"
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert task.artifacts[0].parts[0].text == "answer"
    data = json_format.MessageToDict(task.artifacts[1].parts[0].data)
    assert data["references"][0]["title"] == "paper"
    assert data["metadata"]["tool"] == "ChatAgent"


async def test_remote_running_run_is_not_marked_completed() -> None:
    """A 202 submission remains WORKING until a later task method exists."""
    handler = _handler(
        {
            "id": "run-2",
            "agent": "analyst",
            "status": "running",
            "task_ids": ["task-1"],
            "result": {"formatted": {"answer": "submitted"}},
        },
        status_code=202,
        tool_to_agent={"ChatAgent": "analyst"},
    )

    task = await handler.on_message_send(_request(), ServerCallContext())

    assert isinstance(task, Task)
    assert task.status.state == TaskState.TASK_STATE_WORKING
    assert json_format.MessageToDict(task.metadata)["task_ids"] == ["task-1"]


async def test_unsupported_task_method_is_explicit() -> None:
    """Phase 1 does not expose polling, cancellation, or streaming methods."""
    handler = _handler({"id": "run-1", "status": "succeeded"})

    with pytest.raises(UnsupportedOperationError, match="GetTask"):
        await handler.on_get_task(GetTaskRequest(), ServerCallContext())


async def test_get_task_uses_injected_owner_scoped_lookup() -> None:
    """GetTask delegates lookup and keeps unknown ids as ``None``."""
    expected = Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    calls: list[tuple[str, int]] = []

    def lookup(task_id: str, history_length: int) -> Task | None:
        calls.append((task_id, history_length))
        return expected if task_id == "task-1" else None

    async def invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        return {}, 200

    handler = A2ARequestHandler(
        invoke_agent_run=invoke,
        tool_to_agent={},
        select_agent=_select_chat,
        options=A2AHandlerOptions(get_a2a_task=lookup),
    )

    found = await handler.on_get_task(
        GetTaskRequest(id="task-1", history_length=2), ServerCallContext()
    )
    missing = await handler.on_get_task(
        GetTaskRequest(id="missing"), ServerCallContext()
    )

    assert found is expected
    assert missing is None
    assert calls == [("task-1", 2), ("missing", 0)]


async def test_missing_tool_mapping_is_invalid_params() -> None:
    """Catalogued skills still need an explicitly injected HTTP mapping."""
    handler = _handler(
        {"id": "run-1", "status": "succeeded"},
        tool_to_agent={},
    )

    with pytest.raises(InvalidParamsError, match="no HTTP agent mapping"):
        await handler.on_message_send(_request(), ServerCallContext())


async def test_send_message_records_a2a_correlation() -> None:
    """Blocking A2A responses provide the run-to-protocol id mapping."""
    registrations: list[A2ARegistration] = []

    async def invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        return {
            "id": "run-a2a-1",
            "agent": "chat",
            "status": "succeeded",
            "task_ids": [],
        }, 200

    handler = A2ARequestHandler(
        invoke_agent_run=invoke,
        tool_to_agent={"ChatAgent": "chat"},
        select_agent=_select_chat,
        options=A2AHandlerOptions(record_a2a=registrations.append),
    )

    task = await handler.on_message_send(_request(), ServerCallContext())

    assert isinstance(task, Task)
    assert registrations[0].run_id == "run-a2a-1"
    assert registrations[0].agent == "chat"
    assert registrations[0].correlation.task_id == "run-a2a-1"
    assert registrations[0].correlation.context_id == "c1"
    assert registrations[0].correlation.message_id == "m1"
    assert registrations[0].request_info.query == "hello"


async def test_send_stream_projects_status_text_and_data_events() -> None:
    """A2A streaming exposes task, incremental text, status, and data."""
    registrations: list[A2ARegistration] = []

    async def stream(
        _name: str, _arguments: dict[str, Any], **_kwargs: Any
    ) -> AsyncIterator[AguiEvent]:
        yield AguiEvent(
            type="RunStarted",
            data={"type": "RunStarted"},
        )
        yield AguiEvent(
            type="TextMessageContent",
            data={"type": "TextMessageContent", "delta": "Hel"},
        )
        yield AguiEvent(
            type="TextMessageContent",
            data={"type": "TextMessageContent", "delta": "lo"},
        )
        yield AguiEvent(
            type="TextMessageEnd",
            data={"type": "TextMessageEnd"},
        )
        yield AguiEvent(
            type="Custom",
            data={
                "type": "Custom",
                "name": "phyto.references",
                "value": {"doc_list": [{"title": "paper"}]},
            },
        )
        yield AguiEvent(
            type="RunFinished",
            data={"type": "RunFinished"},
        )

    async def invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        return {}, 200

    handler = A2ARequestHandler(
        invoke_agent_run=invoke,
        tool_to_agent={"ChatAgent": "chat"},
        select_agent=_select_chat,
        options=A2AHandlerOptions(
            invoke_agent_stream=stream,
            record_a2a=registrations.append,
        ),
    )

    events = [
        event
        async for event in handler.on_message_send_stream(
            _request(), ServerCallContext()
        )
    ]

    assert isinstance(events[0], Task)
    assert registrations[0].run_id == events[0].id
    assert registrations[0].correlation.task_id == events[0].id
    assert isinstance(events[1], TaskArtifactUpdateEvent)
    assert events[1].artifact.parts[0].text == "Hel"
    assert events[1].append is False
    assert events[1].last_chunk is False
    assert isinstance(events[2], TaskArtifactUpdateEvent)
    assert events[2].artifact.parts[0].text == "lo"
    assert events[2].append is True
    assert events[2].last_chunk is True
    assert any(
        isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_COMPLETED
        for event in events
    )
    data_events = [
        event
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent)
        and event.artifact.artifact_id.endswith("-data")
    ]
    assert len(data_events) == 1
    assert (
        json_format.MessageToDict(data_events[0].artifact.parts[0].data)[
            "references"
        ]["doc_list"][0]["title"]
        == "paper"
    )
