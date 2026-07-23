# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral A2A peer seams shared by offline interoperability tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

__all__ = [
    "build_agent_card",
    "input_required_resume_sequence",
    "read_asgi_body",
    "send_json_response",
]


async def read_asgi_body(receive: Any) -> bytes:
    """Read all body frames from an ASGI receive callable."""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    return body


async def send_json_response(send: Any, payload: Mapping[str, object]) -> None:
    """Send one JSON response through an ASGI send callable."""
    encoded = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": encoded})


def build_agent_card(
    *,
    base_url: str,
    name: str,
    description: str,
    skill_description: str,
) -> AgentCard:
    """Build the reduced Agent Card shared by local peer fixtures."""
    return AgentCard(
        name=name,
        description=description,
        version="1",
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url}/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="annotate",
                name="Annotate",
                description=skill_description,
            )
        ],
    )


def input_required_resume_sequence(
    terminal_task: Task,
) -> list[list[tuple[Task | TaskStatusUpdateEvent, float]]]:
    """Build an input-required event followed by a terminal task."""
    return [
        [
            (
                TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_INPUT_REQUIRED,
                        message=Message(parts=[Part(text="choose")]),
                    ),
                ),
                0.0,
            )
        ],
        [(terminal_task, 0.0)],
    ]
