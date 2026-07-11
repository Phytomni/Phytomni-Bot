# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the A2A request handler and artifact projection."""

from __future__ import annotations

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
    TaskState,
    UnsupportedOperationError,
)
from google.protobuf import json_format

from mcp_server_phytomni.agents.expert.router import ToolSelection
from mcp_server_phytomni.api.a2a.executor import A2ARequestHandler

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


async def test_missing_tool_mapping_is_invalid_params() -> None:
    """Catalogued skills still need an explicitly injected HTTP mapping."""
    handler = _handler(
        {"id": "run-1", "status": "succeeded"},
        tool_to_agent={},
    )

    with pytest.raises(InvalidParamsError, match="no HTTP agent mapping"):
        await handler.on_message_send(_request(), ServerCallContext())
