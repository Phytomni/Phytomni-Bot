# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Map A2A v1 protobuf messages to bounded, transport-neutral DTOs.

The SDK's protobuf messages are deliberately not exposed past this module.
Peer supplied text, bytes, and JSON remain data, never instructions.  In
particular, this mapper does not build prompts, concatenate text into a system
message, follow URLs, or retain protobuf metadata and extensions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from a2a.types import (
    Artifact,
    Message,
    Part,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf import json_format, struct_pb2

Value = getattr(struct_pb2, "Value")

type JSONValue = (
    None
    | bool
    | int
    | float
    | str
    | list["JSONValue"]
    | dict[str, "JSONValue"]
)
type PartKind = Literal["text", "data", "raw"]
type EventKind = Literal["task", "message", "status_update", "artifact_update"]

MAX_PARTS = 64
MAX_TEXT_BYTES = 64 * 1024
MAX_DATA_BYTES = 64 * 1024
MAX_RAW_BYTES = 64 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_ID_BYTES = 256

_TERMINAL_STATES = frozenset(
    f"TASK_STATE_{state}"
    for state in (
        "COMPLETED",
        "FAILED",
        "CANCELED",
        "INPUT_REQUIRED",
        "REJECTED",
        "AUTH_REQUIRED",
    )
)


class A2AMappingError(ValueError):
    """Raised when an untrusted A2A message exceeds the mapping boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(f"external A2A payload {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ExternalA2APart:
    """One bounded A2A part with no executable or network semantics."""

    kind: PartKind
    value: str | bytes | JSONValue
    media_type: str | None = None

    @property
    def text(self) -> str | None:
        """Return text content for convenience, without changing its value."""
        return cast(str, self.value) if self.kind == "text" else None

    @property
    def data(self) -> JSONValue | None:
        """Return JSON content for convenience."""
        return cast(JSONValue, self.value) if self.kind == "data" else None

    @property
    def raw(self) -> bytes | None:
        """Return bounded bytes for convenience."""
        return cast(bytes, self.value) if self.kind == "raw" else None


@dataclass(frozen=True, slots=True)
class ExternalA2AIdentity:
    """Operator-owned identity attached to one normalized event."""

    target_id: str
    capability: str


@dataclass(frozen=True, slots=True)
class ExternalA2AEventFlags:
    """Artifact chunk flags kept separate from the event payload."""

    append: bool = False
    last_chunk: bool = False


@dataclass(frozen=True, slots=True)
class ExternalA2AEvent:
    """A normalized event from one external A2A stream response."""

    kind: EventKind
    identity: ExternalA2AIdentity
    task_id: str | None = None
    context_id: str | None = None
    state: str | None = None
    parts: tuple[ExternalA2APart, ...] = ()
    flags: ExternalA2AEventFlags = ExternalA2AEventFlags()

    @property
    def target_id(self) -> str:
        """Return the operator target id."""
        return self.identity.target_id

    @property
    def capability(self) -> str:
        """Return the allowlisted capability id."""
        return self.identity.capability

    @property
    def append(self) -> bool:
        """Return the artifact append flag."""
        return self.flags.append

    @property
    def last_chunk(self) -> bool:
        """Return whether this is the final artifact chunk."""
        return self.flags.last_chunk

    @property
    def terminal(self) -> bool:
        """Return whether this event ends the remote interaction."""
        return (
            self.kind == "message" or self.last_chunk or _terminal(self.state)
        )

    @property
    def text(self) -> str | None:
        """Return the first text part; ``parts`` preserves all boundaries."""
        for part in self.parts:
            if part.kind == "text":
                return part.text
        return None

    @property
    def data(self) -> JSONValue | None:
        """Return the first data part; ``parts`` preserves all values."""
        for part in self.parts:
            if part.kind == "data":
                return part.data
        return None

    @property
    def texts(self) -> tuple[str, ...]:
        """Return every text part without joining untrusted peer content."""
        return tuple(
            cast(str, part.value) for part in self.parts if part.kind == "text"
        )

    @property
    def data_values(self) -> tuple[JSONValue, ...]:
        """Return every JSON part as detached values."""
        return tuple(
            cast(JSONValue, part.value)
            for part in self.parts
            if part.kind == "data"
        )


@dataclass(slots=True)
class _PartBudget:
    """Track aggregate limits while mapping one response."""

    count: int = 0
    encoded_bytes: int = 0

    def add(self, count: int, encoded_bytes: int) -> None:
        """Reject a response that exceeds count or aggregate byte limits."""
        self.count += count
        self.encoded_bytes += encoded_bytes
        if self.count > MAX_PARTS:
            raise A2AMappingError("too_many_parts")
        if self.encoded_bytes > MAX_EVENT_BYTES:
            raise A2AMappingError("event_too_large")


def _bounded_id(value: str, *, required: bool = False) -> str | None:
    """Copy a peer id only when it is a bounded non-empty string."""
    if not isinstance(value, str):
        raise A2AMappingError("invalid_id")
    if not value:
        if required:
            raise A2AMappingError("missing_id")
        return None
    if len(value.encode("utf-8")) > MAX_ID_BYTES:
        raise A2AMappingError("id_too_large")
    return value


def _optional_id(value: object) -> str | None:
    """Validate one optional outgoing task/context id."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise A2AMappingError("invalid_id")
    return _bounded_id(value)


def _bounded_text(value: str) -> str:
    """Copy one text part under the UTF-8 byte limit."""
    if not isinstance(value, str):
        raise A2AMappingError("invalid_text")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise A2AMappingError("text_too_large")
    return value


def _bounded_json(value: Any) -> JSONValue:
    """Detach and bound one protobuf JSON ``Value``."""
    try:
        decoded = json_format.MessageToDict(
            value,
            preserving_proto_field_name=True,
        )
        encoded = json.dumps(
            decoded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > MAX_DATA_BYTES:
            raise A2AMappingError("data_too_large")
        detached = json.loads(encoded)
    except A2AMappingError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        raise A2AMappingError("invalid_data") from None
    return detached


def _map_part(part: Part, budget: _PartBudget) -> ExternalA2APart:
    """Map one protobuf content oneof and account for its bounded size."""
    try:
        content = part.WhichOneof("content")
    except (AttributeError, ValueError):
        raise A2AMappingError("invalid_part") from None
    media_type = part.media_type or None
    if content == "text":
        text_value = _bounded_text(part.text)
        budget.add(1, len(text_value.encode("utf-8")))
        return ExternalA2APart("text", text_value, media_type)
    if content == "data":
        data_value = _bounded_json(part.data)
        encoded = json.dumps(
            data_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        budget.add(1, len(encoded.encode("utf-8")))
        return ExternalA2APart("data", data_value, media_type)
    if content == "raw":
        raw_value = bytes(part.raw)
        if len(raw_value) > MAX_RAW_BYTES:
            raise A2AMappingError("raw_too_large")
        budget.add(1, len(raw_value))
        return ExternalA2APart("raw", raw_value, media_type)
    if content == "url":
        raise A2AMappingError("url_part_unsupported")
    raise A2AMappingError("empty_part")


def _map_parts(
    parts: Any,
    budget: _PartBudget,
) -> tuple[ExternalA2APart, ...]:
    """Map repeated protobuf parts while enforcing aggregate limits."""
    try:
        count = len(parts)
    except (TypeError, ValueError):
        raise A2AMappingError("invalid_parts") from None
    if count > MAX_PARTS:
        raise A2AMappingError("too_many_parts")
    return tuple(_map_part(part, budget) for part in parts)


def _map_message_parts(
    message: Message | None,
    budget: _PartBudget,
) -> tuple[ExternalA2APart, ...]:
    """Map an optional status/message payload."""
    if message is None:
        return ()
    return _map_parts(message.parts, budget)


def _state_name(status: TaskStatus | None) -> str | None:
    """Normalize a protobuf task state to its stable enum name."""
    if status is None:
        return None
    try:
        return TaskState.Name(status.state)
    except (TypeError, ValueError):
        raise A2AMappingError("unknown_task_state") from None


def _terminal(state: str | None) -> bool:
    return state in _TERMINAL_STATES


def _map_task(
    task: Task,
    *,
    target_id: str,
    capability: str,
) -> ExternalA2AEvent:
    """Map a task, including status message and artifact parts."""
    budget = _PartBudget()
    parts: list[ExternalA2APart] = []
    parts.extend(_map_message_parts(task.status.message, budget))
    for artifact in task.artifacts:
        parts.extend(_map_parts(artifact.parts, budget))
    state = _state_name(task.status)
    return ExternalA2AEvent(
        kind="task",
        identity=ExternalA2AIdentity(target_id, capability),
        task_id=_bounded_id(task.id, required=True),
        context_id=_bounded_id(task.context_id),
        state=state,
        parts=tuple(parts),
    )


def _map_status_update(
    event: TaskStatusUpdateEvent,
    *,
    target_id: str,
    capability: str,
) -> ExternalA2AEvent:
    """Map a status update and retain ids needed for a later resume."""
    budget = _PartBudget()
    state = _state_name(event.status)
    return ExternalA2AEvent(
        kind="status_update",
        identity=ExternalA2AIdentity(target_id, capability),
        task_id=_bounded_id(event.task_id, required=True),
        context_id=_bounded_id(event.context_id),
        state=state,
        parts=_map_message_parts(event.status.message, budget),
    )


def _map_artifact_update(
    event: Any,
    *,
    target_id: str,
    capability: str,
) -> ExternalA2AEvent:
    """Map an artifact update without interpreting its peer metadata."""
    budget = _PartBudget()
    artifact: Artifact = event.artifact
    return ExternalA2AEvent(
        kind="artifact_update",
        identity=ExternalA2AIdentity(target_id, capability),
        task_id=_bounded_id(event.task_id, required=True),
        context_id=_bounded_id(event.context_id),
        parts=_map_parts(artifact.parts, budget),
        flags=ExternalA2AEventFlags(
            append=bool(event.append),
            last_chunk=bool(event.last_chunk),
        ),
    )


def map_stream_response(
    response: StreamResponse,
    *,
    target_id: str,
    capability: str,
) -> ExternalA2AEvent:
    """Map any SDK v1 ``StreamResponse`` payload into a bounded DTO.

    The caller supplies operator-owned ids; neither a peer URL nor peer
    metadata is accepted by this function.  All four v1 payload oneofs are
    handled explicitly so a future SDK addition fails closed.
    """
    if not isinstance(response, StreamResponse):
        raise A2AMappingError("invalid_stream_response")
    try:
        payload = response.WhichOneof("payload")
    except (AttributeError, ValueError):
        raise A2AMappingError("invalid_stream_response") from None
    if payload == "task":
        return _map_task(
            response.task,
            target_id=target_id,
            capability=capability,
        )
    if payload == "message":
        budget = _PartBudget()
        message = response.message
        return ExternalA2AEvent(
            kind="message",
            identity=ExternalA2AIdentity(target_id, capability),
            task_id=_bounded_id(message.task_id),
            context_id=_bounded_id(message.context_id),
            parts=_map_parts(message.parts, budget),
        )
    if payload == "status_update":
        return _map_status_update(
            response.status_update,
            target_id=target_id,
            capability=capability,
        )
    if payload == "artifact_update":
        return _map_artifact_update(
            response.artifact_update,
            target_id=target_id,
            capability=capability,
        )
    raise A2AMappingError("empty_stream_response")


def build_user_message(
    *,
    message_id: str,
    capability: str,
    **options: object,
) -> Message:
    """Build a safe user message without prompt interpolation."""
    allowed_options = {"text", "data", "task_id", "context_id"}
    if set(options) - allowed_options:
        raise A2AMappingError("invalid_request")
    text = options.get("text")
    data = options.get("data")
    task_id = options.get("task_id")
    context_id = options.get("context_id")
    if text is None and data is None:
        raise A2AMappingError("empty_request")
    if not isinstance(message_id, str) or not message_id:
        raise A2AMappingError("invalid_message_id")
    _bounded_id(message_id, required=True)
    normalized_task = _optional_id(task_id)
    normalized_context = _optional_id(context_id)
    if not isinstance(capability, str) or not capability:
        raise A2AMappingError("invalid_capability")
    parts: list[Part] = []
    if text is not None:
        if not isinstance(text, str):
            raise A2AMappingError("invalid_text")
        normalized_text = _bounded_text(text)
        parts.append(Part(text=normalized_text, media_type="text/plain"))
    if data is not None:
        value = Value()
        try:
            json_format.ParseDict(data, value)
        except (TypeError, ValueError):
            raise A2AMappingError("invalid_data") from None
        _bounded_json(value)
        parts.append(Part(data=value, media_type="application/json"))
    message = Message(
        message_id=message_id,
        role="ROLE_USER",
        parts=parts,
    )
    if normalized_task is not None:
        message.task_id = normalized_task
    if normalized_context is not None:
        message.context_id = normalized_context
    return message


__all__ = [
    "A2AMappingError",
    "EventKind",
    "ExternalA2AEvent",
    "ExternalA2APart",
    "JSONValue",
    "MAX_DATA_BYTES",
    "MAX_EVENT_BYTES",
    "MAX_ID_BYTES",
    "MAX_PARTS",
    "MAX_RAW_BYTES",
    "MAX_TEXT_BYTES",
    "build_user_message",
    "map_stream_response",
]
