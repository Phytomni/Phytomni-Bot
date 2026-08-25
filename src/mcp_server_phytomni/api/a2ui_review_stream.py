# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review A2UI streaming and terminal-settlement helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from fastapi.responses import StreamingResponse

from ..agents.shared.a2ui import A2UI_CUSTOM_NAME
from ..mcp.result_formatting import (
    AguiEvent,
    custom,
    run_error,
    run_finished,
    run_started,
)
from ..mcp.stream_lifecycle import (
    StreamLifecycleState,
    prime_agui_stream,
    run_persistence_error,
)
from ..runtime.checkpoint_instrumentation_v2 import (
    record_projected_input_required,
)
from ..runtime.langgraph_runner import build_runnable_config, invoke_graph
from ..runtime.resume import detect_interrupt
from .a2ui_projection import (
    ReviewSurfaceProjectionError,
    review_interrupt_result,
)
from .lifecycle_contract import (
    LifecycleInvariantError,
    SafeErrorCode,
    build_agent_run_response,
    empty_agent_result,
)
from .openai_mapping import to_chat_completion_chunks
from .schemas import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class ReviewStreamHooks:
    """Compatibility seams supplied by the extracted runtime."""

    prepare_review_stream: Callable[..., Any]
    project_interrupt: Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class A2UIStreamInputs:
    """Domain inputs shared by Chat and Review A2UI stream requests."""

    arguments: dict[str, Any]
    payload: ChatCompletionRequest
    user_query: str
    dependencies: Any
    runtime_run_id: str


@dataclass(frozen=True, slots=True)
class A2UIStreamRequest:
    """Prepared context factory and event projection for one A2UI stream."""

    prepare_context: Callable[..., Any]
    inputs: A2UIStreamInputs
    events: Callable[[Any, A2UITerminalState], AsyncIterator[AguiEvent]]


@dataclass(slots=True)
class A2UITerminalState:
    """Transport-neutral terminal observation owned by the outer Runtime."""

    status: str | None = None
    result: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.status is not None

    def record(self, status: str, result: dict[str, Any]) -> bool:
        """Record exactly one domain outcome without writing run lifecycle."""
        if self.ready:
            return False
        self.status = status
        self.result = result
        return True


async def invoke_a2ui_graph(context: Any) -> Mapping[str, Any]:
    """Invoke one prepared A2UI graph with its run-scoped thread config."""
    return await invoke_graph(
        context.graph,
        context.initial_state,
        config=build_runnable_config(context.run_id),
    )


def settle_a2ui_input_required(
    context: Any,
    dependencies: Any,
    result: dict[str, Any],
    terminal: A2UITerminalState,
) -> bool:
    """Persist a resumable surface, then report waiting-input to Runtime."""
    registry = dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    )
    if not registry.update_active_result(
        context.run_id,
        owner=context.owner,
        result=result,
    ):
        return False
    if not terminal.record("waiting_input", result):
        return False
    record_projected_input_required(result)
    return True


def settle_a2ui_stream_failure(
    terminal: A2UITerminalState,
    *,
    dependencies: Any,
) -> None:
    """Report a domain failure; Runtime remains the only lifecycle writer."""
    terminal.record("failed", dependencies.stream.failed_stream_result())


async def stream_a2ui_events(
    *,
    prepare_context: Callable[[], Any],
    payload: ChatCompletionRequest,
    dependencies: Any,
    events: Callable[[Any, A2UITerminalState], AsyncIterator[AguiEvent]],
) -> StreamingResponse:
    """Project one A2UI event generator through the shared SSE lifecycle."""
    context = prepare_context()
    lifecycle_state = StreamLifecycleState()
    terminal = A2UITerminalState()
    try:
        primed = await prime_agui_stream(events(context, terminal))
    except Exception as exc:
        settle_a2ui_stream_failure(
            terminal,
            dependencies=dependencies,
        )
        raise dependencies.stream.stream_setup_error(
            exc, priming=True
        ) from exc

    async def _wrapped() -> AsyncIterator[str]:
        """Forward SSE and settle failure only before a terminal result."""
        try:
            async for line in to_chat_completion_chunks(
                dependencies.stream.project_stream(
                    primed,
                    run_id=context.run_id,
                    lifecycle_state=lifecycle_state,
                ),
                payload.model,
            ):
                yield line
        finally:
            # Consumer disconnect is not a domain failure.  If no typed
            # terminal observation exists, the outer Runtime keeps the run
            # recoverable instead of manufacturing a failed settlement.
            pass

    response = StreamingResponse(_wrapped(), media_type="text/event-stream")
    setattr(
        response,
        "runtime_terminal_status",
        lambda: terminal.status or "failed",
    )
    setattr(
        response,
        "runtime_terminal_result",
        lambda: terminal.result or dependencies.stream.failed_stream_result(),
    )
    setattr(response, "runtime_terminal_ready", lambda: terminal.ready)
    return response


async def run_a2ui_stream(
    request: A2UIStreamRequest,
) -> StreamingResponse:
    """Bind one domain context factory to the shared A2UI stream lifecycle."""
    inputs = request.inputs
    return await stream_a2ui_events(
        prepare_context=partial(
            request.prepare_context,
            arguments=inputs.arguments,
            payload=inputs.payload,
            user_query=inputs.user_query,
            dependencies=inputs.dependencies,
            runtime_run_id=inputs.runtime_run_id,
        ),
        payload=inputs.payload,
        dependencies=inputs.dependencies,
        events=request.events,
    )


async def stream_review_a2ui_pause(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: Any,
    hooks: ReviewStreamHooks,
    runtime_run_id: str,
) -> StreamingResponse:
    """Stream Review until interrupt and emit one ``phyto.a2ui`` frame."""

    async def _agui_events(
        context: Any, terminal: A2UITerminalState
    ) -> AsyncIterator[AguiEvent]:
        yield run_started(context.run_id, payload.dialogue_id)
        final_state = await invoke_a2ui_graph(context)
        interrupt = detect_interrupt(final_state, context.run_id)
        if interrupt is not None:
            try:
                interrupt_dict = hooks.project_interrupt(dict(interrupt))
            except ReviewSurfaceProjectionError:
                terminal.record("failed", empty_agent_result())
                yield run_error(
                    SafeErrorCode.PROJECTION_FAILED.value,
                    "review surface projection failed",
                )
                return
            pause_result = review_interrupt_result(interrupt_dict)
            try:
                pause_body = build_agent_run_response(
                    run_id=context.run_id,
                    agent="review",
                    status="input_required",
                    task_ids=(),
                    result=pause_result,
                    persisted=True,
                )
            except (LifecycleInvariantError, TypeError, ValueError):
                terminal.record("failed", empty_agent_result())
                yield run_error(
                    SafeErrorCode.PROJECTION_FAILED.value,
                    "review surface projection failed",
                )
                return
            if not settle_a2ui_input_required(
                context, dependencies, pause_body["result"], terminal
            ):
                yield run_persistence_error()
                return
            draft = interrupt_dict.get("draft")
            if isinstance(draft, Mapping):
                a2ui_value = draft.get("a2ui")
                if isinstance(a2ui_value, Mapping):
                    yield custom(A2UI_CUSTOM_NAME, dict(a2ui_value))
        else:
            result = await dependencies.persistence.format_review_result(
                final_state, arguments=arguments
            )
            terminal.record("succeeded", result)
        yield run_finished(context.run_id)

    inputs = A2UIStreamInputs(
        arguments,
        payload,
        user_query,
        dependencies,
        runtime_run_id,
    )
    request = A2UIStreamRequest(
        prepare_context=hooks.prepare_review_stream,
        inputs=inputs,
        events=_agui_events,
    )
    return await run_a2ui_stream(request)
