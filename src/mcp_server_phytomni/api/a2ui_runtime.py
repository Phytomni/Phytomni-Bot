# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""A2UI pause, resume, and projection runtime for the HTTP API.

Application seams are supplied through frozen dependencies so tests and
compatibility callers need not import ``api.app`` here.
"""

from __future__ import annotations

import logging
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from ..agents.chat.a2ui_builder import build_chat_a2ui_graph
from ..agents.review.agent import review_stream_target
from ..agents.shared.a2ui import A2UI_CUSTOM_NAME
from ..config.defaults import ChatConfig
from ..mcp.handler_support import chat_kwargs, load_handler_runtime
from ..mcp.result_formatting import (
    AguiEvent,
    custom,
    run_finished,
    run_started,
)
from ..mcp.schemas import ReviewAgent as ReviewAgentArgs
from ..mcp.stream_lifecycle import (
    run_persistence_error,
)
from ..runtime.checkpoint_instrumentation_v2 import (
    record_projected_input_required,
)
from ..runtime.execution_instrumentation_v2 import current_execution_boundary
from ..runtime.langgraph_runner import (
    build_runnable_config,
    ensure_checkpointer,
    invoke_graph,
)
from ..runtime.locale import current_effective_locale
from ..runtime.resume import aresume_graph, detect_interrupt
from ..runtime.run_registry import (
    RunRegistry,
    RunRequestInfo,
)
from .a2ui_projection import (
    ReviewSurfaceProjectionError,
    chat_interrupt_body,
    chat_interrupt_result,
    format_chat_result,
    format_review_result,
    project_review_interrupt,
    review_interrupt_body,
    review_interrupt_result,
    review_projection_error,
    submitted_a2ui_value,
)
from .a2ui_resume import (
    ReviewExecution,
    open_surface_for_action,
    resume_a2ui_run,
    resume_review_run,
    review_run_body,
)
from .a2ui_review_stream import (
    A2UIStreamInputs,
    A2UIStreamRequest,
    ReviewStreamHooks,
    invoke_a2ui_graph,
    run_a2ui_stream,
    settle_a2ui_input_required,
    settle_a2ui_stream_failure,
)
from .a2ui_review_stream import (
    stream_review_a2ui_pause as _stream_review_a2ui_pause,
)
from .app_support import build_safe_chat_request_info
from .attachments import (
    ManagedAttachmentEvidence,
    redact_managed_attachment_values,
)
from .schemas import ChatCompletionRequest

_LOGGER = logging.getLogger(__name__)

type GraphFactory = Callable[[], Any]
type InitialStateFactory = Callable[[Any], Mapping[str, Any]]
type ResumeGraph = Callable[
    [Any, str, Mapping[str, Any]], Awaitable[dict[str, Any]]
]
type RegistryFactory = Callable[[str], RunRegistry]
type CurrentUser = Callable[[], str | None]
type CurrentRequestId = Callable[[], str | None]
type DbPath = Callable[[], str]
type HasCheckpoint = Callable[[Any, str], Awaitable[bool]]
type ReviewFormatter = Callable[..., Awaitable[dict[str, Any]]]
type ReviewValidator = Callable[[dict[str, Any]], ReviewAgentArgs]
type StreamSetupError = Callable[..., HTTPException]
type FailedStreamResult = Callable[[], dict[str, Any]]
type ProjectStream = Callable[..., AsyncIterator[AguiEvent]]


@dataclass(frozen=True, slots=True)
class A2UIGraphDependencies:
    """Graph and action-validation seams used by the runtime."""

    chat_graph: GraphFactory
    chat_initial_state: InitialStateFactory
    review_graph: GraphFactory
    review_initial_state: InitialStateFactory
    validate_review: ReviewValidator
    has_checkpoint: HasCheckpoint
    resume_graph: ResumeGraph


@dataclass(frozen=True, slots=True)
class A2UIPersistenceDependencies:
    """Registry and result-projection seams used by the runtime."""

    registry_factory: RegistryFactory
    current_user: CurrentUser
    current_request_id: CurrentRequestId
    tasks_db_path: DbPath
    format_review_result: ReviewFormatter


@dataclass(frozen=True, slots=True)
class A2UIStreamDependencies:
    """SSE lifecycle seams supplied by the application."""

    stream_setup_error: StreamSetupError
    failed_stream_result: FailedStreamResult
    project_stream: ProjectStream


@dataclass(frozen=True, slots=True)
class A2UIRuntimeDependencies:
    """Explicit seam groups used by the A2UI lifecycle runtime."""

    graphs: A2UIGraphDependencies
    persistence: A2UIPersistenceDependencies
    stream: A2UIStreamDependencies


@lru_cache(maxsize=1)
def build_chat_stream_app() -> Any:
    """Return the cached Chat A2UI graph used by the default runtime."""
    return build_chat_a2ui_graph(checkpointer=ensure_checkpointer())


def build_chat_initial_state(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Build the initial state for a Chat A2UI graph invocation."""
    chat_config = ChatConfig()
    runtime = load_handler_runtime()
    return {
        "user_query": str(arguments["user_query"]),
        "obs_file_list": list(arguments.get("obs_file_list") or []),
        "locale": arguments.get("locale") or current_effective_locale(),
        "chat_kwargs": chat_kwargs(chat_config, runtime.sensitive),
    }


def build_review_stream_app() -> Any:
    """Return the compiled ReviewAgent graph used by HTTP pause paths."""
    app, _ = review_stream_target("", [])
    return app


def build_review_initial_state(args: ReviewAgentArgs) -> Mapping[str, Any]:
    """Build the ReviewAgent initial graph state for one request."""
    _, initial_state = review_stream_target(
        args.user_query,
        args.obs_file_list,
        locale=current_effective_locale(),
    )
    return initial_state


def build_review_request_info(
    payload: ChatCompletionRequest,
    user_query: str,
    *,
    execution_id: str | None = None,
) -> RunRequestInfo:
    """Build the stable request metadata stored for Review runs."""
    return replace(
        build_safe_chat_request_info(
            payload,
            user_query,
            tool_name="ReviewAgent",
        ),
        execution_id=execution_id,
    )


def validate_review_arguments(arguments: dict[str, Any]) -> ReviewAgentArgs:
    """Validate a ReviewAgent argument dict with the MCP schema."""
    try:
        return ReviewAgentArgs(**arguments)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="invalid ReviewAgent arguments",
        ) from exc


async def resume_paused_graph(
    app: Any,
    thread_id: str,
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Resume a paused graph through the shared LangGraph kernel."""
    return await aresume_graph(app, thread_id, dict(resume_payload))


def _redact_review_payload(
    payload: dict[str, Any],
    evidence: ManagedAttachmentEvidence | None,
) -> dict[str, Any]:
    """Project every ordered managed evidence reference out of one payload."""
    if evidence is None:
        return payload
    return redact_managed_attachment_values(payload, evidence)


@dataclass(frozen=True, slots=True)
class _ReviewInterruptPersistRequest:
    """Inputs needed to persist one Review pause row."""

    registry: RunRegistry
    run_id: str
    owner: str
    request_info: RunRequestInfo
    interrupt: Mapping[str, Any]
    attachment_evidence: ManagedAttachmentEvidence | None


def _persist_review_interrupt(
    request: _ReviewInterruptPersistRequest,
) -> ReviewExecution:
    """Persist one Review pause after projecting its interrupt surface."""
    try:
        interrupt_dict = project_review_interrupt(request.interrupt)
    except ReviewSurfaceProjectionError as exc:
        raise review_projection_error() from exc
    result = _redact_review_payload(
        review_interrupt_result(interrupt_dict),
        request.attachment_evidence,
    )
    if not request.registry.update_active_result(
        request.run_id,
        owner=request.owner,
        result=result,
    ):
        raise HTTPException(
            status_code=500, detail="review persistence failed"
        )
    record_projected_input_required(result)
    return ReviewExecution(
        run_id=request.run_id,
        status="input_required",
        result=result,
        interrupt=interrupt_dict,
    )


async def execute_review_with_run_id(
    *,
    run_id: str,
    arguments: dict[str, Any],
    dependencies: A2UIRuntimeDependencies,
    attachment_evidence: ManagedAttachmentEvidence | None = None,
) -> ReviewExecution:
    """Execute Review against a run identity reserved by the caller."""
    args = dependencies.graphs.validate_review(arguments)
    final_state = await invoke_graph(
        dependencies.graphs.review_graph(),
        dependencies.graphs.review_initial_state(args),
        config=build_runnable_config(run_id),
    )
    interrupt = detect_interrupt(final_state, run_id)
    if interrupt is not None:
        interrupt_dict = project_review_interrupt(interrupt)
        return ReviewExecution(
            run_id=run_id,
            status="input_required",
            result=_redact_review_payload(
                review_interrupt_result(interrupt_dict),
                attachment_evidence,
            ),
            interrupt=interrupt_dict,
        )
    result = _redact_review_payload(
        await dependencies.persistence.format_review_result(
            final_state, arguments=arguments
        ),
        attachment_evidence,
    )
    return ReviewExecution(run_id=run_id, status="succeeded", result=result)


async def run_review_with_interrupt(
    *,
    arguments: dict[str, Any],
    request_info: RunRequestInfo,
    dependencies: A2UIRuntimeDependencies,
    attachment_evidence: ManagedAttachmentEvidence | None = None,
) -> ReviewExecution:
    """Run ReviewAgent once, surfacing a LangGraph interrupt if present."""
    args = dependencies.graphs.validate_review(arguments)
    owner = dependencies.persistence.current_user() or "anonymous"
    registry = dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    )
    boundary = current_execution_boundary()
    if boundary is None or boundary.context.run_id is None:
        raise HTTPException(
            status_code=500, detail="execution runtime boundary required"
        )
    if (
        request_info.execution_id is not None
        and boundary.context.execution_id != request_info.execution_id
    ):
        raise HTTPException(status_code=409, detail="execution id mismatch")
    if boundary.context.owner_ref != owner:
        raise HTTPException(status_code=403, detail="execution owner mismatch")
    run_id = boundary.context.run_id
    registry.update_request_info(
        run_id,
        owner=owner,
        request_info=replace(
            request_info,
            execution_id=boundary.context.execution_id,
        ),
    )
    final_state = await invoke_graph(
        dependencies.graphs.review_graph(),
        dependencies.graphs.review_initial_state(args),
        config=build_runnable_config(run_id),
    )
    interrupt = detect_interrupt(final_state, run_id)
    if interrupt is not None:
        return _persist_review_interrupt(
            _ReviewInterruptPersistRequest(
                registry=registry,
                run_id=run_id,
                owner=owner,
                request_info=request_info,
                interrupt=interrupt,
                attachment_evidence=attachment_evidence,
            )
        )
    result = _redact_review_payload(
        await dependencies.persistence.format_review_result(
            final_state, arguments=arguments
        ),
        attachment_evidence,
    )
    return ReviewExecution(run_id=run_id, status="succeeded", result=result)


@dataclass(frozen=True, slots=True)
class _StreamContext:
    """Prepared graph and registry metadata for one A2UI stream."""

    agent_slug: str
    owner: str
    run_id: str
    request_info: RunRequestInfo
    graph: Any
    initial_state: Mapping[str, Any]


def _prepare_chat_stream(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: A2UIRuntimeDependencies,
    runtime_run_id: str,
) -> _StreamContext:
    """Prepare a Chat A2UI graph on the Runtime-reserved run."""
    owner = dependencies.persistence.current_user() or "anonymous"
    try:
        graph = dependencies.graphs.chat_graph()
        initial_state = dependencies.graphs.chat_initial_state(arguments)
    except Exception as exc:
        raise dependencies.stream.stream_setup_error(
            exc, priming=False
        ) from exc
    agent_slug = "chat"
    run_id = runtime_run_id
    request_info = RunRequestInfo(
        dialogue_id=payload.dialogue_id,
        query=user_query,
        tool_name="ChatAgent",
        model=payload.model,
        request_json=payload.model_dump_json(),
        locale=current_effective_locale(),
    )
    dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    ).update_request_info(
        run_id,
        owner=owner,
        request_info=request_info,
    )
    return _StreamContext(
        agent_slug=agent_slug,
        owner=owner,
        run_id=run_id,
        request_info=request_info,
        graph=graph,
        initial_state=initial_state,
    )


def _prepare_review_stream(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: A2UIRuntimeDependencies,
    runtime_run_id: str,
) -> _StreamContext:
    """Prepare a Review A2UI graph on the Runtime-reserved run."""
    owner = dependencies.persistence.current_user() or "anonymous"
    try:
        args = dependencies.graphs.validate_review(arguments)
        graph = dependencies.graphs.review_graph()
        initial_state = dependencies.graphs.review_initial_state(args)
    except Exception as exc:
        raise dependencies.stream.stream_setup_error(
            exc, priming=False
        ) from exc
    agent_slug = "review"
    run_id = runtime_run_id
    boundary = current_execution_boundary()
    execution_id = (
        boundary.context.execution_id if boundary is not None else None
    )
    request_info = build_review_request_info(
        payload,
        user_query,
        execution_id=execution_id,
    )
    dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    ).update_request_info(
        run_id,
        owner=owner,
        request_info=request_info,
    )
    return _StreamContext(
        agent_slug=agent_slug,
        owner=owner,
        run_id=run_id,
        request_info=request_info,
        graph=graph,
        initial_state=initial_state,
    )


async def stream_chat_a2ui_confirm(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: A2UIRuntimeDependencies,
    runtime_run_id: str,
) -> StreamingResponse:
    """Short-circuit Chat streaming into an A2UI pause."""

    async def _agui_events(
        context: Any, terminal: Any
    ) -> AsyncIterator[AguiEvent]:
        """Yield AG-UI frames for one Chat A2UI pause."""
        yield run_started(context.run_id, payload.dialogue_id)
        final_state = await invoke_a2ui_graph(context)
        interrupt = detect_interrupt(final_state, context.run_id)
        if interrupt is None:
            yield run_persistence_error()
            return
        a2ui_value = interrupt["draft"]["a2ui"]
        if not settle_a2ui_input_required(
            context,
            dependencies,
            chat_interrupt_result(interrupt),
            terminal,
        ):
            yield run_persistence_error()
            return
        yield custom(A2UI_CUSTOM_NAME, a2ui_value)
        yield run_finished(context.run_id)

    inputs = A2UIStreamInputs(
        arguments,
        payload,
        user_query,
        dependencies,
        runtime_run_id,
    )
    request = A2UIStreamRequest(
        prepare_context=_prepare_chat_stream,
        inputs=inputs,
        events=_agui_events,
    )
    return await run_a2ui_stream(request)


async def stream_review_a2ui_pause(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: A2UIRuntimeDependencies,
    runtime_run_id: str,
) -> StreamingResponse:
    """Stream Review through the dedicated Review stream runtime."""
    return await _stream_review_a2ui_pause(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=dependencies,
        runtime_run_id=runtime_run_id,
        hooks=ReviewStreamHooks(
            prepare_review_stream=_prepare_review_stream,
            project_interrupt=project_review_interrupt,
        ),
    )


__all__ = [
    "A2UIGraphDependencies",
    "A2UIPersistenceDependencies",
    "A2UIRuntimeDependencies",
    "A2UIStreamDependencies",
    "ReviewExecution",
    "ReviewSurfaceProjectionError",
    "build_chat_initial_state",
    "build_chat_stream_app",
    "build_review_initial_state",
    "build_review_request_info",
    "build_review_stream_app",
    "chat_interrupt_body",
    "chat_interrupt_result",
    "execute_review_with_run_id",
    "format_chat_result",
    "format_review_result",
    "open_surface_for_action",
    "project_review_interrupt",
    "resume_a2ui_run",
    "resume_paused_graph",
    "resume_review_run",
    "review_interrupt_body",
    "review_interrupt_result",
    "review_projection_error",
    "review_run_body",
    "run_review_with_interrupt",
    "settle_a2ui_stream_failure",
    "stream_chat_a2ui_confirm",
    "stream_review_a2ui_pause",
    "submitted_a2ui_value",
    "validate_review_arguments",
]
