# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for bounded external A2A event mapping."""

from __future__ import annotations

import pytest
from a2a.types import (
    Artifact,
    Message,
    Part,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.proto_utils import to_stream_response
from google.protobuf import struct_pb2

from mcp_server_phytomni.interop import a2a_mapping as mapping
from mcp_server_phytomni.interop.a2a_mapping import (
    A2AMappingError,
    build_user_message,
    map_stream_response,
)

Value = getattr(struct_pb2, "Value")

pytestmark = pytest.mark.unit


def _map(response: StreamResponse):
    """Map a fixture response through the public boundary."""
    return map_stream_response(
        response,
        target_id="peer",
        capability="peer__annotate",
    )


@pytest.mark.parametrize(
    ("response", "kind", "state", "terminal"),
    [
        (
            to_stream_response(
                Task(
                    id="task-1",
                    context_id="context-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                    artifacts=[Artifact(parts=[Part(text="result")])],
                )
            ),
            "task",
            "TASK_STATE_COMPLETED",
            True,
        ),
        (
            to_stream_response(
                Message(
                    message_id="message-1",
                    task_id="task-1",
                    context_id="context-1",
                    parts=[Part(text="message")],
                )
            ),
            "message",
            None,
            True,
        ),
        (
            to_stream_response(
                TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_INPUT_REQUIRED,
                        message=Message(parts=[Part(text="need input")]),
                    ),
                )
            ),
            "status_update",
            "TASK_STATE_INPUT_REQUIRED",
            True,
        ),
        (
            to_stream_response(
                TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=Artifact(parts=[Part(text="chunk")]),
                    append=True,
                    last_chunk=True,
                )
            ),
            "artifact_update",
            None,
            True,
        ),
    ],
)
def test_all_stream_oneofs_preserve_ids_and_terminal_state(
    response: StreamResponse,
    kind: str,
    state: str | None,
    terminal: bool,
) -> None:
    """Every SDK v1 oneof maps to a stable event with resume ids."""
    event = _map(response)

    assert event.kind == kind
    assert event.task_id == "task-1"
    assert event.context_id == "context-1"
    assert event.state == state
    assert event.terminal is terminal
    assert event.text in {"result", "message", "need input", "chunk"}


def test_data_and_raw_parts_are_detached_and_bounded() -> None:
    """JSON is detached and raw bytes cannot exceed the mapper cap."""
    value = Value()
    value.struct_value["answer"] = "ok"
    response = to_stream_response(
        Message(
            message_id="message-1",
            parts=[Part(data=value), Part(raw=b"bytes")],
        )
    )

    event = _map(response)

    assert event.data == {"answer": "ok"}
    assert event.parts[1].raw == b"bytes"
    assert event.data_values == ({"answer": "ok"},)

    oversized = to_stream_response(
        Message(
            message_id="message-2",
            parts=[Part(raw=b"x" * (mapping.MAX_RAW_BYTES + 1))],
        )
    )
    with pytest.raises(A2AMappingError, match="raw_too_large"):
        _map(oversized)


def test_url_and_unknown_payloads_fail_closed() -> None:
    """The client never follows peer-provided URLs or future oneofs."""
    with pytest.raises(A2AMappingError, match="url_part_unsupported"):
        _map(
            to_stream_response(
                Message(message_id="message-1", parts=[Part(url="https://x")])
            )
        )
    with pytest.raises(A2AMappingError, match="empty_stream_response"):
        _map(StreamResponse())


def test_part_and_text_limits_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Part count and normalized text/data have independent bounds."""
    monkeypatch.setattr(mapping, "MAX_PARTS", 1)
    with pytest.raises(A2AMappingError, match="too_many_parts"):
        _map(
            to_stream_response(
                Message(
                    message_id="message-1",
                    parts=[Part(text="one"), Part(text="two")],
                )
            )
        )

    monkeypatch.setattr(mapping, "MAX_PARTS", 64)
    monkeypatch.setattr(mapping, "MAX_TEXT_BYTES", 2)
    with pytest.raises(A2AMappingError, match="text_too_large"):
        _map(
            to_stream_response(
                Message(message_id="message-2", parts=[Part(text="long")])
            )
        )


def test_build_user_message_supports_resume_and_skill_metadata_shape() -> None:
    """Outgoing text/data requests retain task/context ids as data fields."""
    message = build_user_message(
        text="follow up",
        data={"choice": "yes"},
        task_id="task-1",
        context_id="context-1",
        message_id="request-1",
        capability="annotate",
    )

    assert message.task_id == "task-1"
    assert message.context_id == "context-1"
    assert message.message_id == "request-1"
    assert [part.WhichOneof("content") for part in message.parts] == [
        "text",
        "data",
    ]


def test_build_user_message_rejects_empty_request() -> None:
    """A remote call cannot be made without an explicit text/data payload."""
    with pytest.raises(A2AMappingError, match="empty_request"):
        build_user_message(
            message_id="request-1",
            capability="annotate",
        )
