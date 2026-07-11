# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""A2A v1 request handler backed by the existing HTTP agent seam."""

from __future__ import annotations

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
    TaskPushNotificationConfig,
    UnsupportedOperationError,
)
from google.protobuf import json_format

from ...agents.expert.router import ToolSelection
from ...mcp.result_formatting import AguiEvent
from ...storage.path_policy import IdFactory
from .events import ArtifactUpdateOptions, build_artifact_update
from .messages import map_a2a_message
from .progress import A2AProgressProjector
from .status import build_task_status

__all__ = ["A2ARequestHandler"]

AgentRunInvoker = Callable[..., Awaitable[tuple[dict[str, Any], int]]]
ToolSelector = Callable[[str], Awaitable[ToolSelection | None]]
AgentStreamInvoker = Callable[..., AsyncIterator[AguiEvent]]
_ID_FACTORY = IdFactory()


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
            parts=[Part(text=text, media_type="text/plain")],
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
            state.pending_text = delta
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
                parts=[Part(text=answer)],
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


def _build_task(
    params: SendMessageRequest,
    body: Mapping[str, Any],
    status_code: int,
) -> Task:
    """Build a protocol Task without creating a second task registry."""
    task_id = str(body.get("id") or _ID_FACTORY.new_id("task", "a2a"))
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
    return task


class A2ARequestHandler(RequestHandler):
    """Serve only the Phase 1 non-streaming ``SendMessage`` operation."""

    def __init__(
        self,
        invoke_agent_run: AgentRunInvoker,
        tool_to_agent: Mapping[str, str],
        select_agent: ToolSelector,
        invoke_agent_stream: AgentStreamInvoker | None = None,
    ) -> None:
        self._invoke_agent_run = invoke_agent_run
        self._tool_to_agent = dict(tool_to_agent)
        self._select_agent = select_agent
        self._invoke_agent_stream = invoke_agent_stream

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
        del context
        return _build_task(params, body, status_code)

    async def on_get_task(
        self, params: GetTaskRequest, context: ServerCallContext
    ) -> Task | None:
        raise UnsupportedOperationError(message="GetTask is not supported")

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
