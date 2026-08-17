# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""A2A v1 request handler backed by the existing HTTP agent seam."""

from __future__ import annotations

import json
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
)
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import RequestHandler
from a2a.types import (
    AgentCard,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    InvalidParamsError,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    SendMessageRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    UnsupportedOperationError,
)
from google.protobuf import json_format

from ...agents.expert.router import ToolSelection
from ...config.defaults import ApiConfig
from ...mcp.result_formatting import AguiEvent
from ...runtime.run_registry import (
    A2ACorrelation,
    RunRecord,
    RunRequestInfo,
)
from ...storage.path_policy import IdFactory
from .events import ArtifactUpdateOptions, build_artifact_update
from .messages import map_a2a_message
from .progress import A2AProgressProjector
from .status import build_task_status

__all__ = [
    "A2AHandlerOptions",
    "A2ARequestHandler",
    "A2ARegistration",
    "task_from_run_record",
]

AgentRunInvoker = Callable[..., Awaitable[tuple[dict[str, Any], int]]]
ToolSelector = Callable[[str], Awaitable[ToolSelection | None]]
AgentStreamInvoker = Callable[..., AsyncIterator[AguiEvent]]
A2ARegistrationWriter = Callable[["A2ARegistration"], None]
A2ATaskLookup = Callable[[str, int], Task | None]
A2AResumeInvoker = Callable[
    [str, str, Mapping[str, Any]],
    Awaitable[tuple[dict[str, Any], int] | None],
]
_ID_FACTORY = IdFactory()


def _bound_artifact_text(value: str) -> str:
    """Keep one local A2A text artifact within the configured byte cap."""
    max_bytes = ApiConfig().A2A_MAX_ARTIFACT_BYTES
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = "\n<artifact-truncated>"
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= max_bytes:
        return encoded[:max_bytes].decode("utf-8", errors="ignore")
    prefix = encoded[: max_bytes - len(marker_bytes)].decode(
        "utf-8", errors="ignore"
    )
    return f"{prefix}{marker}"


@dataclass(frozen=True)
class A2ARegistration:
    """A2A ids and request metadata to persist against one run."""

    run_id: str
    agent: str
    correlation: A2ACorrelation
    request_info: RunRequestInfo


@dataclass(frozen=True)
class A2AHandlerOptions:
    """Optional persistence and streaming seams for the handler."""

    invoke_agent_stream: AgentStreamInvoker | None = None
    record_a2a: A2ARegistrationWriter | None = None
    get_a2a_task: A2ATaskLookup | None = None
    resume_a2a: A2AResumeInvoker | None = None


def _data_part(value: Mapping[str, Any]) -> Part:
    """Encode one JSON object as a protobuf ``Part.data`` oneof."""
    part = Part()
    json_format.ParseDict({"data": dict(value)}, part)
    return part


def _text_artifact_update(
    task_id: str,
    context_id: str,
    text: str,
    *,
    append: bool,
    last_chunk: bool,
) -> TaskArtifactUpdateEvent:
    """Build one incremental text artifact update."""
    response = build_artifact_update(
        task_id,
        context_id,
        Artifact(
            artifact_id=f"{task_id}-answer",
            parts=[
                Part(text=_bound_artifact_text(text), media_type="text/plain")
            ],
        ),
        options=ArtifactUpdateOptions(
            append=append,
            last_chunk=last_chunk,
        ),
    )
    return response.artifact_update


def _data_artifact_update(
    task_id: str,
    context_id: str,
    value: Mapping[str, Any],
) -> TaskArtifactUpdateEvent:
    """Build one terminal data artifact update."""
    response = build_artifact_update(
        task_id,
        context_id,
        Artifact(
            artifact_id=f"{task_id}-data",
            name="result-data",
            parts=[_data_part(value)],
        ),
        options=ArtifactUpdateOptions(last_chunk=True),
    )
    return response.artifact_update


def _input_required_artifact(
    task_id: str,
    body: Mapping[str, Any],
) -> Artifact:
    """Build a safe input schema artifact for a paused run."""
    interrupt = body.get("interrupt")
    draft = interrupt.get("draft") if isinstance(interrupt, Mapping) else {}
    has_a2ui = isinstance(draft, Mapping) and "a2ui" in draft
    properties: dict[str, Any] = {
        "approved": {"type": "boolean"},
        "edits": {"type": "string"},
    }
    if has_a2ui:
        properties.update(
            {
                "fields": {"type": "object"},
                "selected": {"type": "string"},
                "cancelled": {"type": "boolean"},
            }
        )
    value = {
        "run_id": str(body.get("run_id") or body.get("id") or task_id),
        "generation": body.get("generation", 0),
        "schema": {
            "type": "object",
            "properties": properties,
            "required": ["approved"],
        },
    }
    return Artifact(
        artifact_id=f"{task_id}-input",
        name="input-required",
        parts=[_data_part(value)],
    )


@dataclass
class _ArtifactStreamState:
    """Mutable accumulator for one A2A text/data artifact stream."""

    emitted_text: bool = False
    pending_text: str | None = None
    structured: dict[str, Any] = field(default_factory=dict)


def _artifact_updates_for_event(
    task_id: str,
    context_id: str,
    event: AguiEvent,
    state: _ArtifactStreamState,
) -> list[TaskArtifactUpdateEvent]:
    """Project one AG-UI event into zero or more artifact updates."""
    updates: list[TaskArtifactUpdateEvent] = []
    if event.type == "TextMessageContent":
        delta = event.data.get("delta")
        if isinstance(delta, str) and delta:
            if state.pending_text is not None:
                updates.append(
                    _text_artifact_update(
                        task_id,
                        context_id,
                        state.pending_text,
                        append=state.emitted_text,
                        last_chunk=False,
                    )
                )
                state.emitted_text = True
            state.pending_text = _bound_artifact_text(delta)
    elif event.type == "TextMessageEnd":
        if state.pending_text is not None:
            updates.append(
                _text_artifact_update(
                    task_id,
                    context_id,
                    state.pending_text,
                    append=state.emitted_text,
                    last_chunk=True,
                )
            )
            state.emitted_text = True
            state.pending_text = None
    elif event.type == "Custom":
        name = event.data.get("name")
        if name == "phyto.references":
            state.structured["references"] = event.data.get("value")
        elif name == "phyto.follow_up":
            state.structured["follow_up_questions"] = event.data.get("value")
    if event.type == "RunFinished":
        if state.pending_text is not None:
            updates.append(
                _text_artifact_update(
                    task_id,
                    context_id,
                    state.pending_text,
                    append=state.emitted_text,
                    last_chunk=True,
                )
            )
            state.emitted_text = True
            state.pending_text = None
        if state.structured:
            updates.append(
                _data_artifact_update(task_id, context_id, state.structured)
            )
    return updates


def _status_events_for_stream(
    updates: Iterable[StreamResponse], submitted_skipped: bool
) -> tuple[list[Event], bool]:
    """Drop the duplicate submitted update after the initial Task event."""
    events: list[Event] = []
    for update in updates:
        if not submitted_skipped:
            submitted_skipped = True
            continue
        events.append(update.status_update)
    return events, submitted_skipped


def _result_artifacts(
    task_id: str,
    result: Mapping[str, Any] | None,
) -> list[Artifact]:
    """Project formatted text and structured fields into A2A artifacts."""
    if not isinstance(result, Mapping):
        return []
    formatted = result.get("formatted")
    if not isinstance(formatted, Mapping):
        return []

    artifacts: list[Artifact] = []
    answer = formatted.get("answer")
    if isinstance(answer, str) and answer:
        artifacts.append(
            Artifact(
                artifact_id=f"{task_id}-answer",
                name="answer",
                parts=[Part(text=_bound_artifact_text(answer))],
            )
        )
    structured = {
        key: formatted[key]
        for key in ("references", "tabular", "metadata")
        if formatted.get(key) not in (None, [], {})
    }
    if structured:
        artifacts.append(
            Artifact(
                artifact_id=f"{task_id}-data",
                name="result-data",
                parts=[_data_part(structured)],
            )
        )
    return artifacts


def task_from_run_record(record: RunRecord, history_length: int) -> Task:
    """Project one owner-checked run record into an A2A Task.

    Only the request message and formatted result artifacts cross the
    protocol boundary. Internal raw state and secrets remain in the
    registry and are never copied into task metadata.
    """
    task_id = record.a2a.task_id or record.spec.run_id
    context_id = record.a2a.context_id or ""
    task = Task(
        id=task_id,
        context_id=context_id,
        status=build_task_status(record.status),
    )
    metadata: dict[str, Any] = {
        "run_id": record.spec.run_id,
        "agent": record.spec.agent,
        "task_ids": list(record.task_ids),
    }
    json_format.ParseDict(metadata, task.metadata)
    for artifact in _result_artifacts(task_id, record.result):
        task.artifacts.add().CopyFrom(artifact)
    if record.status == "input_required":
        stored = record.result or {}
        task.artifacts.add().CopyFrom(
            _input_required_artifact(
                task_id,
                {
                    "id": record.spec.run_id,
                    "run_id": record.spec.run_id,
                    "interrupt": stored.get("interrupt"),
                    "generation": stored.get("generation", 0),
                },
            )
        )
    bounded_history_length = min(
        max(history_length, 0), ApiConfig().A2A_MAX_HISTORY_MESSAGES
    )
    if bounded_history_length != 0:
        message_json = record.request_info.request_json
        if isinstance(message_json, str):
            try:
                payload = json.loads(message_json)
                message_payload = payload.get("message")
                if isinstance(message_payload, Mapping):
                    message = Message()
                    json_format.ParseDict(
                        cast(dict[str, Any], message_payload), message
                    )
                    task.history.append(message)
            except (TypeError, ValueError):
                pass
    if 0 < bounded_history_length < len(task.history):
        del task.history[:-bounded_history_length]
    return task


def _build_task(
    params: SendMessageRequest,
    body: Mapping[str, Any],
    status_code: int,
) -> Task:
    """Build a protocol Task without creating a second task registry."""
    task_id = str(
        body.get("id")
        or params.message.task_id
        or _ID_FACTORY.new_id("task", "a2a")
    )
    context_id = params.message.context_id or _ID_FACTORY.new_id(
        "context", "a2a"
    )
    status = body.get("status")
    status_text = status if isinstance(status, str) else "unknown"
    detail = None
    if body.get("degraded_tracking") is True:
        detail = "local run tracking is degraded"
    task = Task(
        id=task_id,
        context_id=context_id,
        status=build_task_status(status_text, detail=detail),
    )
    task.history.append(params.message)
    metadata: dict[str, Any] = {
        "http_status": status_code,
    }
    if isinstance(body.get("agent"), str):
        metadata["agent"] = body["agent"]
    if isinstance(body.get("task_ids"), list):
        metadata["task_ids"] = body["task_ids"]
    json_format.ParseDict(metadata, task.metadata)
    for artifact in _result_artifacts(task_id, body.get("result")):
        task.artifacts.add().CopyFrom(artifact)
    if status_text.strip().lower().replace("-", "_") == "input_required":
        task.artifacts.add().CopyFrom(_input_required_artifact(task_id, body))
    return task


def _query_from_arguments(arguments: Mapping[str, Any]) -> str | None:
    """Return the user-facing query captured in an A2A request."""
    for key in ("user_query", "goal_description"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class A2ARequestHandler(RequestHandler):
    """Serve A2A ``SendMessage``, optional streaming, and optional GetTask.

    Blocking ``on_message_send`` is always wired. Streaming
    ``on_message_send_stream`` and ``on_get_task`` run when the
    corresponding option is injected. Every other SDK hook raises
    ``UnsupportedOperationError`` on purpose.
    """

    def __init__(
        self,
        invoke_agent_run: AgentRunInvoker,
        tool_to_agent: Mapping[str, str],
        select_agent: ToolSelector,
        options: A2AHandlerOptions | None = None,
    ) -> None:
        options = options or A2AHandlerOptions()
        self._invoke_agent_run = invoke_agent_run
        self._tool_to_agent = dict(tool_to_agent)
        self._select_agent = select_agent
        self._invoke_agent_stream = options.invoke_agent_stream
        self._record_a2a = options.record_a2a
        self._get_a2a_task = options.get_a2a_task
        self._resume_a2a = options.resume_a2a

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | Message:
        """Map, invoke, and return one blocking or running A2A Task."""
        if not params.HasField("message"):
            raise InvalidParamsError(message="SendMessage requires message")
        request = await map_a2a_message(
            params.message,
            request_metadata=json_format.MessageToDict(
                params.metadata,
                preserving_proto_field_name=True,
            ),
            select_agent=self._select_agent,
        )
        if params.message.task_id:
            if self._resume_a2a is None:
                raise UnsupportedOperationError(
                    message="A2A task resume is not supported"
                )
            try:
                resumed = await self._resume_a2a(
                    params.message.task_id,
                    params.message.context_id or "",
                    request.arguments,
                )
            except ValueError as exc:
                raise InvalidParamsError(message=str(exc)) from exc
            if resumed is None:
                raise TaskNotFoundError
            body, status_code = resumed
            agent = self._tool_to_agent.get(request.tool_name)
            if agent is None:
                raise InvalidParamsError(
                    message=f"no HTTP agent mapping for {request.tool_name}"
                )
            task = _build_task(params, body, status_code)
            if self._record_a2a is not None:
                self._record_a2a(
                    A2ARegistration(
                        run_id=str(body.get("id") or task.id),
                        agent=agent,
                        correlation=A2ACorrelation(
                            task_id=task.id,
                            context_id=task.context_id,
                            message_id=params.message.message_id or None,
                        ),
                        request_info=RunRequestInfo(
                            dialogue_id=task.context_id,
                            query=_query_from_arguments(request.arguments),
                            tool_name=request.tool_name,
                            request_json=json_format.MessageToJson(params),
                        ),
                    )
                )
            del context
            return task
        agent = self._tool_to_agent.get(request.tool_name)
        if agent is None:
            raise InvalidParamsError(
                message=f"no HTTP agent mapping for {request.tool_name}"
            )
        body, status_code = await self._invoke_agent_run(
            agent=agent,
            arguments=request.arguments,
            dialogue_id=params.message.context_id or None,
            request_json=json_format.MessageToJson(params),
            debug=False,
        )
        task = _build_task(params, body, status_code)
        if self._record_a2a is not None:
            run_id = str(body.get("id") or task.id)
            self._record_a2a(
                A2ARegistration(
                    run_id=run_id,
                    agent=agent,
                    correlation=A2ACorrelation(
                        task_id=task.id,
                        context_id=task.context_id,
                        message_id=params.message.message_id or None,
                    ),
                    request_info=RunRequestInfo(
                        dialogue_id=task.context_id,
                        query=_query_from_arguments(request.arguments),
                        tool_name=request.tool_name,
                        request_json=json_format.MessageToJson(params),
                    ),
                )
            )
        del context
        return task

    async def on_get_task(
        self, params: GetTaskRequest, context: ServerCallContext
    ) -> Task | None:
        if self._get_a2a_task is None:
            raise UnsupportedOperationError(message="GetTask is not supported")
        del context
        return self._get_a2a_task(params.id, params.history_length)

    async def on_list_tasks(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        raise UnsupportedOperationError(message="ListTasks is not supported")

    async def on_cancel_task(
        self, params: CancelTaskRequest, context: ServerCallContext
    ) -> Task | None:
        raise UnsupportedOperationError(message="CancelTask is not supported")

    async def on_message_send_stream(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event, None]:
        """Stream A2A status and artifact events over the AG-UI seam."""
        if self._invoke_agent_stream is None:
            if TYPE_CHECKING:
                yield cast(Event, None)
            raise UnsupportedOperationError(
                message="SendStreamingMessage is not supported"
            )
        if not params.HasField("message"):
            raise InvalidParamsError(message="SendMessage requires message")
        request = await map_a2a_message(
            params.message,
            request_metadata=json_format.MessageToDict(
                params.metadata,
                preserving_proto_field_name=True,
            ),
            select_agent=self._select_agent,
        )
        if request.tool_name not in self._tool_to_agent:
            raise InvalidParamsError(
                message=f"no HTTP agent mapping for {request.tool_name}"
            )

        task_id = params.message.task_id or _ID_FACTORY.new_id("task", "a2a")
        context_id = params.message.context_id or _ID_FACTORY.new_id(
            "context", "a2a"
        )
        task = Task(
            id=task_id,
            context_id=context_id,
            status=build_task_status("submitted"),
        )
        task.history.append(params.message)
        if self._record_a2a is not None:
            self._record_a2a(
                A2ARegistration(
                    run_id=task_id,
                    agent=self._tool_to_agent[request.tool_name],
                    correlation=A2ACorrelation(
                        task_id=task_id,
                        context_id=context_id,
                        message_id=params.message.message_id or None,
                    ),
                    request_info=RunRequestInfo(
                        dialogue_id=context_id,
                        query=_query_from_arguments(request.arguments),
                        tool_name=request.tool_name,
                        request_json=json_format.MessageToJson(params),
                    ),
                )
            )
        yield task

        projector = A2AProgressProjector(task_id, context_id)
        submitted_status_skipped = False
        artifact_state = _ArtifactStreamState()
        stream = self._invoke_agent_stream(
            request.tool_name,
            request.arguments,
            run_id=task_id,
            dialogue_id=context_id,
        )
        async for event in stream:
            for update in _artifact_updates_for_event(
                task_id, context_id, event, artifact_state
            ):
                yield update
            status_events, submitted_status_skipped = (
                _status_events_for_stream(
                    projector.project(event), submitted_status_skipped
                )
            )
            for status_event in status_events:
                yield status_event
        del context

    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError(
            message="CreateTaskPushNotificationConfig is not supported"
        )

    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError(
            message="GetTaskPushNotificationConfig is not supported"
        )

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        if TYPE_CHECKING:
            yield cast(Event, None)
        raise UnsupportedOperationError(
            message="SubscribeToTask is not supported"
        )

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        raise UnsupportedOperationError(
            message="ListTaskPushNotificationConfigs is not supported"
        )

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        raise UnsupportedOperationError(
            message="DeleteTaskPushNotificationConfig is not supported"
        )

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        raise UnsupportedOperationError(
            message="GetExtendedAgentCard is not supported"
        )
