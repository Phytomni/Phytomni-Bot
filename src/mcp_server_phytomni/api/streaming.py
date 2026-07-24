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

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from httpx import ConnectError, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from ..mcp.result_formatting import AguiEvent
from ..mcp.stream_lifecycle import (
    EmptyStreamError,
    PrimedAguiStream,
    StreamLifecycleState,
    durable_settlement_succeeded,
    prime_agui_stream,
    project_stream_failures,
    project_terminal_settlement,
)
from ..runtime.run_registry import RunRequestInfo
from . import a2ui_runtime
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
    settle_stream_run: Callable[[str, str, str, dict[str, Any]], bool | None]
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
    accumulator: StreamAnswerAccumulator
    lifecycle_state: StreamLifecycleState


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
    *,
    tool_name: str,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: StreamingDependencies,
) -> _PreparedStream:
    """Prepare, persist, prime, and shape one ordinary stream."""
    agent_slug = dependencies.request.agent_slug(payload.model)
    owner = dependencies.request.current_user() or "anonymous"
    run_id = dependencies.request.new_run_id("run", agent_slug or "chat")
    try:
        raw_events = dependencies.request.prepare_tool_stream(
            tool_name,
            arguments,
            run_id=run_id,
            dialogue_id=payload.dialogue_id,
        )
    except Exception as exc:
        raise stream_setup_error(exc, priming=False) from exc

    if agent_slug is not None:
        dependencies.persistence.create_running_stream_run(
            run_id,
            agent_slug,
            owner,
            _prepare_request_info(
                payload=payload,
                user_query=user_query,
                tool_name=tool_name,
            ),
        )
    try:
        primed = await prime_agui_stream(raw_events)
    except Exception as exc:
        if agent_slug is not None:
            dependencies.persistence.settle_stream_run(
                run_id,
                owner,
                "failed",
                failed_stream_result(),
            )
        raise stream_setup_error(exc, priming=True) from exc

    lifecycle_state = StreamLifecycleState()
    events = project_primed_stream(
        primed,
        run_id=run_id,
        request_id=dependencies.request.current_request_id() or "unknown",
        lifecycle_state=lifecycle_state,
    )
    accumulator = StreamAnswerAccumulator(
        events,
        max_bytes=dependencies.persistence.stream_answer_max_bytes(),
        lifecycle_state=lifecycle_state,
    )
    return _PreparedStream(
        run_id=run_id,
        owner=owner,
        agent_slug=agent_slug,
        accumulator=accumulator,
        lifecycle_state=lifecycle_state,
    )


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

    prepared = await _prepare_stream(
        tool_name=tool_name,
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=dependencies,
    )

    def _settle_terminal_success() -> bool:
        snapshot = prepared.accumulator.snapshot
        return (
            dependencies.persistence.settle_stream_run(
                prepared.run_id,
                prepared.owner,
                "succeeded",
                {
                    "formatted": {"answer": snapshot.answer},
                    "raw": None,
                    "stream": True,
                    "truncated": snapshot.truncated,
                    "partial": False,
                },
            )
            is True
        )

    def _settle_terminal_failure() -> bool:
        snapshot = prepared.accumulator.snapshot
        return (
            dependencies.persistence.settle_stream_run(
                prepared.run_id,
                prepared.owner,
                "failed",
                {
                    "formatted": {"answer": snapshot.answer},
                    "raw": None,
                    "stream": True,
                    "truncated": snapshot.truncated,
                    "partial": True,
                },
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
    sse_lines = to_chat_completion_chunks(terminal_events, payload.model)

    async def _wrapped() -> AsyncIterator[str]:
        """Forward SSE lines and settle the run from typed lifecycle flags."""
        try:
            async for line in sse_lines:
                yield line
        finally:
            if (
                prepared.agent_slug is not None
                and not prepared.lifecycle_state.durably_settled
            ):
                durable_settlement_succeeded(_settle_terminal_failure)

    return StreamingResponse(_wrapped(), media_type="text/event-stream")


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
