# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP streaming orchestration for the authenticated API.

This module owns the HTTP-only stream boundary: eager setup and first-frame
priming, typed lifecycle projection, bounded answer accumulation, and run
settlement.  FastAPI route registration remains in :mod:`api.app`; callers
bind application-specific registry, graph, and request-context seams through
``StreamingDependencies`` so the runtime does not import the app factory.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from httpx import ConnectError, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from ..config.defaults import ApiConfig
from ..mcp.handlers import (
    reset_private_conversation_messages,
    set_private_conversation_messages,
)
from ..mcp.result_formatting import (
    AguiEvent,
    ContextStagedPayload,
    context_staged,
)
from ..mcp.stream_lifecycle import (
    EmptyStreamError,
    PrimedAguiStream,
    StreamLifecycleState,
    durable_settlement_succeeded,
    prime_agui_stream,
    project_stream_failures,
    project_terminal_settlement,
)
from ..runtime.conversation_context.adapters import (
    canonical_agent_invocation,
)
from ..runtime.conversation_context.models import ContextDelta
from ..runtime.conversation_context.projection import build_context_projection
from ..runtime.conversation_context.service import (
    AsyncAgentAcceptance,
    ConversationContextService,
    PreparedTurn,
    PrepareStatus,
)
from ..runtime.conversation_context.store import (
    ConversationContextStore,
    StagedTurn,
    StoredTurn,
)
from ..runtime.run_registry import RunRequestInfo
from ..runtime.task_manager import (
    resolve_tasks_db_path as _default_tasks_db_path,
)
from . import a2ui_runtime
from .lifecycle_contract import empty_agent_result
from .openai_mapping import to_chat_completion_chunks
from .schemas import ChatCompletionRequest
from .stream_answer import StreamAnswerAccumulator


@dataclass(frozen=True)
class StreamingRequestDependencies:
    """Request-context and eager stream preparation seams."""

    prepare_tool_stream: Callable[..., AsyncIterator[AguiEvent]]
    current_user: Callable[[], str | None]
    current_request_id: Callable[[], str | None]
    new_run_id: Callable[[str, str], str]
    agent_slug: Callable[[str], str | None]


@dataclass(frozen=True)
class StreamingA2UIDependencies:
    """A2UI feature-flag and domain-runtime seams."""

    enabled: Callable[[], bool]
    select_widget: Callable[[str], str | None]
    runtime: Callable[[], a2ui_runtime.A2UIRuntimeDependencies]


@dataclass(frozen=True)
class StreamingPersistenceDependencies:
    """Run-registry and answer-storage seams."""

    create_running_stream_run: Callable[[str, str, str, RunRequestInfo], None]
    settle_stream_run: Callable[..., bool | None]
    stream_answer_max_bytes: Callable[[], int]


@dataclass(frozen=True)
class StreamingDependencies:
    """Application seams required by the HTTP stream runtime."""

    request: StreamingRequestDependencies
    a2ui: StreamingA2UIDependencies
    persistence: StreamingPersistenceDependencies


@dataclass(frozen=True)
class _PreparedStream:
    """State shared by the opened stream and its finalizer."""

    run_id: str
    owner: str
    agent_slug: str | None
    expected_revision: int
    accumulator: StreamAnswerAccumulator
    lifecycle_state: StreamLifecycleState


def _settle_stream_run_compat(
    settle: Callable[..., bool | None],
    *args: Any,
    expected_revision: int,
) -> bool | None:
    """Preserve four-argument settlement injection seams."""
    try:
        parameters = inspect.signature(settle).parameters
    except (TypeError, ValueError):
        revision_parameter = None
        accepts_var_keyword = True
    else:
        revision_parameter = parameters.get("expected_revision")
        accepts_var_keyword = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
    accepts_revision = (
        revision_parameter is not None
        and revision_parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
    ) or accepts_var_keyword
    if not accepts_revision:
        return settle(*args)
    return settle(*args, expected_revision=expected_revision)


@dataclass(frozen=True)
class _StreamPreparationRequest:
    """Inputs needed to prepare and prime one ordinary stream."""

    tool_name: str
    arguments: dict[str, Any]
    payload: ChatCompletionRequest
    user_query: str
    dependencies: StreamingDependencies
    raw_event_factory: Callable[[str], AsyncIterator[AguiEvent]] | None = None


@dataclass(frozen=True)
class _PreparedContextStream:
    """Conversation-context state needed to finalize one V1 stream."""

    envelope: Any
    key: str
    context: Any
    rebuilt: bool
    stored_turn: StoredTurn


_CONTEXT_STREAM_RESULT_KEY = "__conversation_context_stream__"


def stream_setup_error(exc: Exception, *, priming: bool) -> HTTPException:
    """Map stream setup/prime failures to fixed pre-header HTTP errors."""
    if isinstance(exc, HTTPException):
        return exc
    status_code = 500
    detail = "stream setup failed"
    if isinstance(exc, ConnectError):
        status_code = 502
        detail = "stream upstream unavailable"
    elif isinstance(exc, TimeoutException):
        status_code = 504
        detail = "stream upstream timed out"
    elif not priming and isinstance(exc, NotImplementedError):
        status_code = 400
        detail = "streaming is not supported for this model"
    elif (
        not priming
        and isinstance(exc, McpError)
        and exc.error.code == INVALID_PARAMS
    ):
        status_code = 400
        detail = "invalid streaming request"
    elif isinstance(exc, EmptyStreamError):
        detail = "stream produced no data"
    return HTTPException(status_code=status_code, detail=detail)


def failed_stream_result() -> dict[str, Any]:
    """Return the minimal failed result persisted after pre-open failure."""
    return {
        "formatted": {"answer": ""},
        "execution": empty_agent_result()["execution"],
        "raw": None,
        "stream": True,
        "partial": True,
    }


async def replay_primed_stream(
    primed: PrimedAguiStream,
) -> AsyncIterator[AguiEvent]:
    """Replay a primed first event before consuming its raw remainder."""
    yield primed.first
    async for event in primed.remainder:
        yield event


async def project_primed_stream(
    primed: PrimedAguiStream,
    *,
    run_id: str,
    request_id: str,
    lifecycle_state: StreamLifecycleState | None = None,
) -> AsyncIterator[AguiEvent]:
    """Project one primed stream through the typed lifecycle boundary."""
    state = (
        lifecycle_state
        if lifecycle_state is not None
        else StreamLifecycleState()
    )
    async for event in project_stream_failures(
        replay_primed_stream(primed),
        state=state,
        run_id=run_id,
        request_id=request_id,
    ):
        yield event


async def stream_chat_a2ui_confirm(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: StreamingDependencies,
) -> StreamingResponse:
    """Delegate Chat A2UI pause shaping to the domain runtime."""
    return await a2ui_runtime.stream_chat_a2ui_confirm(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=dependencies.a2ui.runtime(),
    )


async def stream_review_a2ui_pause(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: StreamingDependencies,
) -> StreamingResponse:
    """Delegate Review A2UI pause shaping to the domain runtime."""
    return await a2ui_runtime.stream_review_a2ui_pause(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=dependencies.a2ui.runtime(),
    )


def _prepare_request_info(
    *,
    payload: ChatCompletionRequest,
    user_query: str,
    tool_name: str,
) -> RunRequestInfo:
    """Build the request metadata persisted before the first stream frame."""
    values: dict[str, Any] = {
        "dialogue_id": payload.dialogue_id,
        "query": user_query,
        "tool_name": tool_name,
        "model": payload.model,
        "request_json": payload.model_dump_json(),
    }
    return RunRequestInfo(**values)


async def _prepare_stream(
    request: _StreamPreparationRequest,
) -> _PreparedStream:
    """Prepare, persist, prime, and shape one ordinary stream."""
    agent_slug = request.dependencies.request.agent_slug(request.payload.model)
    owner = request.dependencies.request.current_user() or "anonymous"
    run_id = request.dependencies.request.new_run_id(
        "run", agent_slug or "chat"
    )
    try:
        raw_events = (
            request.raw_event_factory(run_id)
            if request.raw_event_factory is not None
            else request.dependencies.request.prepare_tool_stream(
                request.tool_name,
                request.arguments,
                run_id=run_id,
                dialogue_id=request.payload.dialogue_id,
            )
        )
    except Exception as exc:
        raise stream_setup_error(exc, priming=False) from exc

    if agent_slug is not None:
        request.dependencies.persistence.create_running_stream_run(
            run_id,
            agent_slug,
            owner,
            _prepare_request_info(
                payload=request.payload,
                user_query=request.user_query,
                tool_name=request.tool_name,
            ),
        )
    try:
        primed = await prime_agui_stream(raw_events)
    except Exception as exc:
        if agent_slug is not None:
            _settle_stream_run_compat(
                request.dependencies.persistence.settle_stream_run,
                run_id,
                owner,
                "failed",
                failed_stream_result(),
                expected_revision=0,
            )
        raise stream_setup_error(exc, priming=True) from exc

    lifecycle_state = StreamLifecycleState()
    events = project_primed_stream(
        primed,
        run_id=run_id,
        request_id=(
            request.dependencies.request.current_request_id() or "unknown"
        ),
        lifecycle_state=lifecycle_state,
    )
    accumulator = StreamAnswerAccumulator(
        events,
        max_bytes=request.dependencies.persistence.stream_answer_max_bytes(),
        lifecycle_state=lifecycle_state,
    )
    return _PreparedStream(
        run_id=run_id,
        owner=owner,
        agent_slug=agent_slug,
        expected_revision=0,
        accumulator=accumulator,
        lifecycle_state=lifecycle_state,
    )


def _context_delta_for_stream(
    _answer: str | None = None,
) -> tuple[ContextDelta | None, bool]:
    """Return the default Chat stream context delta and degradation flag."""
    return ContextDelta(), False


def _context_service() -> ConversationContextService:
    """Build the direct V1 service from the current task DB path."""
    store = ConversationContextStore(_tasks_db_path())
    return ConversationContextService(
        store,
        router=_unsupported_context_router,
        invoke=_unsupported_context_invoke,
        delegate_async=_unsupported_context_delegate_async,
    )


def context_service() -> ConversationContextService:
    """Build the direct V1 service through the public runtime seam."""
    return _context_service()


async def _unsupported_context_router(
    _user_query: str,
    _allowed_agent_ids: tuple[str, ...],
    _context: Any,
) -> Any:
    """Instant V1 streaming must not invoke the expert router."""
    raise AssertionError("instant stream unexpectedly invoked the router")


async def _unsupported_context_invoke(
    _selected_agent_id: str, _envelope: Any, _projection: Any
) -> Any:
    """Instant V1 streaming stages after the typed stream, not here."""
    raise AssertionError("instant stream unexpectedly invoked sync staging")


async def _unsupported_context_delegate_async(
    _selected_agent_id: str, _envelope: Any
) -> AsyncAgentAcceptance:
    """Instant V1 streaming must not delegate asynchronously."""
    raise AssertionError("instant stream unexpectedly delegated async work")


async def _prepare_context_stream(
    *,
    tool_name: str,
    _arguments: dict[str, Any],
    payload: ChatCompletionRequest,
) -> tuple[_PreparedContextStream | None, PreparedTurn | None]:
    """Prepare an Instant V1 turn or return a staged replay envelope."""
    envelope = payload.conversation
    if envelope is None:
        return None, None
    if envelope.mode != "instant":
        raise HTTPException(
            status_code=422, detail="chat context requires instant mode"
        )
    if tool_name != "ChatAgent":
        raise HTTPException(
            status_code=422,
            detail="instant context requires a ChatAgent model",
        )
    service = _context_service()
    prepared = await service.prepare_turn(envelope)
    if prepared.status is PrepareStatus.REBUILD_REQUIRED:
        raise HTTPException(
            status_code=409, detail="conversation context rebuild required"
        )
    if prepared.status is PrepareStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=409, detail="conversation context turn in progress"
        )
    if prepared.status in {
        PrepareStatus.RETURN_STAGED,
        PrepareStatus.RETURN_COMMITTED,
    }:
        return None, prepared
    if prepared.context is None or prepared.stored_turn is None:
        raise HTTPException(
            status_code=500, detail="conversation context failed"
        )
    return (
        _PreparedContextStream(
            envelope=envelope,
            key=str(envelope.conversation_key),
            context=prepared.context,
            rebuilt=prepared.context.version == 0,
            stored_turn=prepared.stored_turn,
        ),
        None,
    )


def _replay_stream_events(result: dict[str, Any]) -> list[AguiEvent]:
    """Rebuild typed replay events from a stored staged stream payload."""
    stream_payload = result.get(_CONTEXT_STREAM_RESULT_KEY)
    if not isinstance(stream_payload, dict):
        raise HTTPException(
            status_code=500, detail="conversation context replay failed"
        )
    raw_events = stream_payload.get("events")
    if not isinstance(raw_events, list):
        raise HTTPException(
            status_code=500, detail="conversation context replay failed"
        )
    events: list[AguiEvent] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=500, detail="conversation context replay failed"
            )
        event_type = raw.get("type")
        data = raw.get("data")
        if not isinstance(event_type, str) or not isinstance(data, dict):
            raise HTTPException(
                status_code=500, detail="conversation context replay failed"
            )
        events.append(AguiEvent(type=event_type, data=data))
    return events


def _record_replay_event(
    events: list[dict[str, Any]], event: AguiEvent
) -> None:
    """Append one typed event to the staged replay payload."""
    events.append({"type": event.type, "data": dict(event.data)})


def _context_stream_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Pack the staged replay payload kept on the stored conversation turn."""
    return {
        _CONTEXT_STREAM_RESULT_KEY: {"schema_version": 1, "events": events}
    }


def _prepare_contextual_raw_events(
    *,
    dependencies: StreamingDependencies,
    tool_name: str,
    arguments: dict[str, Any],
    run_id: str,
    _payload: ChatCompletionRequest,
    context_stream: _PreparedContextStream,
) -> AsyncIterator[AguiEvent]:
    """Open one raw tool stream while injecting private native-role history."""
    projection = build_context_projection(
        conversation_key=context_stream.envelope.conversation_key,
        current_query=context_stream.envelope.current_message.content,
        locale=context_stream.envelope.current_message.locale,
        selected_agent_id="ChatAgent",
        context=context_stream.context,
        authorized_artifacts=context_stream.envelope.artifact_refs,
        api_config=ApiConfig(),
        exclude_current_user_turn=context_stream.rebuilt,
    )
    dispatch = canonical_agent_invocation(projection)
    stream_arguments = dict(dispatch.arguments)
    if "obs_file_list" in arguments:
        stream_arguments["obs_file_list"] = list(arguments["obs_file_list"])

    async def _wrapped() -> AsyncIterator[AguiEvent]:
        token = set_private_conversation_messages(
            dispatch.conversation_messages
        )
        try:
            raw_events = dependencies.request.prepare_tool_stream(
                tool_name,
                stream_arguments,
                run_id=run_id,
                dialogue_id=str(context_stream.envelope.dialogue_id),
            )
            async for event in raw_events:
                yield event
        finally:
            with suppress(ValueError):
                reset_private_conversation_messages(token)
                # ``aclose()`` during client disconnect can finalize this
                # generator from a different ``ContextVar`` context. The
                # original request context is already unwinding in that case,
                # so skipping the reset preserves the failed-turn cleanup path.

    return _wrapped()


def _stage_context_stream_success(
    *,
    context_stream: _PreparedContextStream,
    snapshot: Any,
    replay_events: list[dict[str, Any]],
    finish_event: AguiEvent,
) -> AguiEvent:
    """Durably stage one successful V1 stream before ``RunFinished``."""
    # Keep stream answer accumulation for visible run settlement only.  No
    # display answer is passed across the Bot context boundary.
    delta, degraded = _context_delta_for_stream(None)
    delta = ContextDelta() if delta is None else delta
    proposed = ConversationContextService.advance_context(
        context_stream.context,
        context_stream.envelope,
        delta,
        add_current_user_turn=not context_stream.rebuilt,
    )
    stage_metadata = {
        "selected_agent_id": "ChatAgent",
        "route_source": "instant_lock",
        "route_reason_code": "INSTANT_LOCK",
        "base_business_context_version": (
            context_stream.envelope.base_business_context_version
        ),
        "proposed_business_context_version": (
            context_stream.envelope.base_business_context_version + 1
        ),
        "last_applied_ledger_cursor": context_stream.envelope.ledger_cursor,
        "context_truncated": snapshot.truncated,
        "context_rebuilt": context_stream.rebuilt,
        "context_degraded": degraded,
    }
    custom_event = context_staged(
        ContextStagedPayload(
            turn_id=context_stream.envelope.turn_id,
            selected_agent_id="ChatAgent",
            route_source="instant_lock",
            proposed_business_context_version=(
                context_stream.envelope.base_business_context_version + 1
            ),
            context_truncated=snapshot.truncated,
            context_rebuilt=context_stream.rebuilt,
            context_degraded=degraded,
        )
    )
    final_replay_events = [
        *replay_events,
        {"type": custom_event.type, "data": dict(custom_event.data)},
        {"type": finish_event.type, "data": dict(finish_event.data)},
    ]
    ConversationContextStore(_tasks_db_path()).stage_turn(
        context_stream.key,
        context_stream.envelope.turn_id,
        StagedTurn(
            operation=context_stream.envelope.operation,
            base_context_version=(
                context_stream.envelope.base_business_context_version
            ),
            selected_agent_id="ChatAgent",
            route_source="instant_lock",
            result=_context_stream_result(final_replay_events),
            delta=proposed.model_dump(mode="json"),
            ledger_version=context_stream.envelope.ledger_version,
            schema_version=proposed.schema_version,
            ledger_cursor=context_stream.envelope.ledger_cursor,
            observed_mode=context_stream.envelope.mode,
            stage_metadata=stage_metadata,
        ),
    )
    return custom_event


def _mark_context_stream_failed(
    context_stream: _PreparedContextStream,
) -> None:
    """Fail a prepared V1 turn when the stream never stages successfully."""
    ConversationContextStore(_tasks_db_path()).mark_turn_failed(
        context_stream.key, context_stream.envelope.turn_id
    )


def _tasks_db_path() -> str:
    """Resolve the task DB through the app seam when available."""
    app = import_module(".app", package=__package__)
    resolver = getattr(app, "resolve_tasks_db_path", None)
    if callable(resolver):
        return str(resolver())
    return str(_default_tasks_db_path())


async def stream_chat_completion(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: StreamingDependencies,
) -> StreamingResponse:
    """Prepare and wrap one streamed tool response."""
    if (
        tool_name == "ChatAgent"
        and dependencies.a2ui.enabled()
        and dependencies.a2ui.select_widget(user_query) is not None
    ):
        return await stream_chat_a2ui_confirm(
            arguments=arguments,
            payload=payload,
            user_query=user_query,
            dependencies=dependencies,
        )

    context_stream, replay_prepared = await _prepare_context_stream(
        tool_name=tool_name,
        _arguments=arguments,
        payload=payload,
    )
    if replay_prepared is not None:
        if replay_prepared.result is None:
            raise HTTPException(
                status_code=500, detail="conversation context replay failed"
            )
        replay_events = _replay_stream_events(replay_prepared.result)

        async def _replayed_events() -> AsyncIterator[AguiEvent]:
            for event in replay_events:
                yield event

        return StreamingResponse(
            to_chat_completion_chunks(_replayed_events(), payload.model),
            media_type="text/event-stream",
        )

    try:
        prepared = await _prepare_stream(
            _StreamPreparationRequest(
                tool_name=tool_name,
                arguments=arguments,
                payload=payload,
                user_query=user_query,
                dependencies=dependencies,
                raw_event_factory=(
                    (
                        lambda run_id: _prepare_contextual_raw_events(
                            dependencies=dependencies,
                            tool_name=tool_name,
                            arguments=arguments,
                            run_id=run_id,
                            _payload=payload,
                            context_stream=context_stream,
                        )
                    )
                    if context_stream is not None
                    else None
                ),
            )
        )
    except Exception:
        if context_stream is not None:
            _mark_context_stream_failed(context_stream)
        raise

    def _settle_terminal_success() -> bool:
        snapshot = prepared.accumulator.snapshot
        return (
            _settle_stream_run_compat(
                dependencies.persistence.settle_stream_run,
                prepared.run_id,
                prepared.owner,
                "succeeded",
                {
                    "formatted": {"answer": snapshot.answer},
                    "execution": empty_agent_result()["execution"],
                    "raw": None,
                    "stream": True,
                    "truncated": snapshot.truncated,
                    "partial": False,
                },
                expected_revision=prepared.expected_revision,
            )
            is True
        )

    def _settle_terminal_failure() -> bool:
        snapshot = prepared.accumulator.snapshot
        return (
            _settle_stream_run_compat(
                dependencies.persistence.settle_stream_run,
                prepared.run_id,
                prepared.owner,
                "failed",
                {
                    "formatted": {"answer": snapshot.answer},
                    "execution": empty_agent_result()["execution"],
                    "raw": None,
                    "stream": True,
                    "truncated": snapshot.truncated,
                    "partial": True,
                },
                expected_revision=prepared.expected_revision,
            )
            is True
        )

    terminal_events = project_terminal_settlement(
        prepared.accumulator,
        state=prepared.lifecycle_state,
        settle=(
            _settle_terminal_success
            if prepared.agent_slug is not None
            else None
        ),
    )
    if context_stream is not None:
        terminal_events = _project_context_stage(
            terminal_events,
            prepared=prepared,
            context_stream=context_stream,
        )
    sse_lines = to_chat_completion_chunks(terminal_events, payload.model)

    async def _wrapped() -> AsyncIterator[str]:
        """Forward SSE lines and settle the run from typed lifecycle flags."""
        try:
            async for line in sse_lines:
                yield line
        finally:
            try:
                closer = getattr(sse_lines, "aclose", None)
                if callable(closer):
                    await cast(Callable[[], Awaitable[None]], closer)()
            finally:
                if (
                    prepared.agent_slug is not None
                    and not prepared.lifecycle_state.durably_settled
                ):
                    durable_settlement_succeeded(_settle_terminal_failure)

    return StreamingResponse(_wrapped(), media_type="text/event-stream")


async def _project_context_stage(
    events: AsyncIterator[AguiEvent],
    *,
    prepared: _PreparedStream,
    context_stream: _PreparedContextStream,
) -> AsyncIterator[AguiEvent]:
    """Insert staged context metadata before a successful ``RunFinished``."""
    replay_events: list[dict[str, Any]] = []
    staged = False
    try:
        async for event in events:
            if event.type != "RunFinished":
                _record_replay_event(replay_events, event)
                yield event
                continue
            custom_event = _stage_context_stream_success(
                context_stream=context_stream,
                snapshot=prepared.accumulator.snapshot,
                replay_events=replay_events,
                finish_event=event,
            )
            staged = True
            yield custom_event
            yield event
    finally:
        if not staged:
            _mark_context_stream_failed(context_stream)


__all__ = [
    "StreamingA2UIDependencies",
    "StreamingDependencies",
    "StreamingPersistenceDependencies",
    "StreamingRequestDependencies",
    "failed_stream_result",
    "project_primed_stream",
    "replay_primed_stream",
    "stream_chat_a2ui_confirm",
    "stream_chat_completion",
    "stream_review_a2ui_pause",
    "stream_setup_error",
]
