# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for AG-UI to A2A progress projection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from a2a.types import TaskState
from google.protobuf import json_format

from mcp_server_phytomni.api.a2a.progress import project_agui_progress
from mcp_server_phytomni.mcp.result_formatting import AguiEvent

pytestmark = pytest.mark.unit


async def _source(events: list[AguiEvent]) -> AsyncIterator[AguiEvent]:
    for event in events:
        yield event


def _custom_progress(value: dict[str, Any]) -> AguiEvent:
    return AguiEvent(
        type="Custom",
        data={"type": "Custom", "name": "phyto.progress", "value": value},
    )


async def test_graph_progress_keeps_lifecycle_order_and_metadata() -> None:
    """Submitted and terminal states surround lossless working updates."""
    events = [
        AguiEvent(
            type="RunStarted",
            data={"type": "RunStarted", "run_id": "run-1"},
        ),
        AguiEvent(
            type="StepStarted",
            data={"type": "StepStarted", "step_name": "retrieval"},
        ),
        _custom_progress(
            {"phase": "retrieval", "current": 2, "total": 4, "detail": None}
        ),
        AguiEvent(
            type="TextMessageContent",
            data={"type": "TextMessageContent", "delta": "ignored here"},
        ),
        AguiEvent(
            type="RunFinished",
            data={"type": "RunFinished", "run_id": "run-1"},
        ),
    ]

    responses = [
        response
        async for response in project_agui_progress(
            _source(events), task_id="task-1", context_id="context-1"
        )
    ]

    assert [response.WhichOneof("payload") for response in responses] == [
        "status_update"
    ] * 4
    assert [response.status_update.status.state for response in responses] == [
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    ]
    assert json_format.MessageToDict(responses[1])["statusUpdate"][
        "metadata"
    ] == {"phase": "retrieval"}
    assert json_format.MessageToDict(responses[2])["statusUpdate"][
        "metadata"
    ] == {
        "phase": "retrieval",
        "current": 2.0,
        "total": 4.0,
        "detail": None,
    }


async def test_progress_without_run_started_still_starts_submitted() -> None:
    """A malformed source cannot skip the initial lifecycle state."""
    responses = [
        response
        async for response in project_agui_progress(
            _source(
                [
                    AguiEvent(
                        type="RunFinished",
                        data={"type": "RunFinished"},
                    )
                ]
            ),
            task_id="task-1",
            context_id="context-1",
        )
    ]

    assert [response.status_update.status.state for response in responses] == [
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_COMPLETED,
    ]


async def test_run_error_projects_failed_terminal_state() -> None:
    """AG-UI run errors terminate the A2A lifecycle as failed."""
    responses = [
        response
        async for response in project_agui_progress(
            _source(
                [
                    AguiEvent(
                        type="RunStarted",
                        data={"type": "RunStarted"},
                    ),
                    AguiEvent(
                        type="RunError",
                        data={
                            "type": "RunError",
                            "code": "agent_execution_failed",
                            "message": "backend failed",
                        },
                    ),
                ]
            ),
            task_id="task-1",
            context_id="context-1",
        )
    ]

    assert (
        responses[-1].status_update.status.state == TaskState.TASK_STATE_FAILED
    )
    assert json_format.MessageToDict(responses[-1])["statusUpdate"][
        "metadata"
    ] == {
        "code": "agent_execution_failed",
        "message": "backend failed",
    }
