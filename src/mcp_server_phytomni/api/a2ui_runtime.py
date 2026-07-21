# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""A2UI pause, resume, and projection runtime for the HTTP API.

The FastAPI application owns route decorators and request parsing.  This
module owns the Chat/Review A2UI graph lifecycle after a typed request has
been parsed.  All application seams are supplied through a frozen dependency
record so tests and compatibility callers can replace graph, registry, and
projection operations without importing ``api.app`` here.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
)
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from ..agents.chat.a2ui_builder import build_chat_a2ui_graph
from ..agents.review.agent import review_stream_target
from ..agents.shared.a2ui import (
    A2UI_CUSTOM_NAME,
    A2uiActionEnvelope,
    action_to_resume_payload,
    attach_review_a2ui,
    build_submitted_value,
    review_action_to_resume,
)
from ..agents.shared.intermediate_state import merge_intermediate_state
from ..config.defaults import ApiConfig, ChatConfig
from ..mcp.handler_support import chat_kwargs, load_handler_runtime
from ..mcp.result_formatting import (
    AguiEvent,
    build_tool_result_envelope,
    custom,
    run_finished,
    run_started,
    strip_agent_result,
)
from ..mcp.schemas import ReviewAgent as ReviewAgentArgs
from ..mcp.stream_lifecycle import (
    StreamLifecycleState,
    prime_agui_stream,
)
from ..runtime.langgraph_runner import (
    build_runnable_config,
    ensure_checkpointer,
)
from ..runtime.resume import NoCheckpointError, aresume_graph, detect_interrupt
from ..runtime.run_registry import (
    RunOutcome,
    RunRecord,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from ..storage.path_policy import IdFactory
from . import run_lifecycle
from .openai_mapping import to_chat_completion_chunks
from .schemas import A2uiActionRequest, ChatCompletionRequest, ResumeRequest

_LOGGER = logging.getLogger(__name__)

type GraphFactory = Callable[[], Any]
type InitialStateFactory = Callable[[Any], Mapping[str, Any]]
type ResumeGraph = Callable[
    [Any, str, Mapping[str, Any]], Awaitable[dict[str, Any]]
]
type RegistryFactory = Callable[[str], RunRegistry]
type CurrentUser = Callable[[], str | None]
type DbPath = Callable[[], str]
type CreateStreamRun = Callable[[str, str, str, RunRequestInfo], None]
type SettleStreamRun = Callable[[str, str, str, dict[str, Any]], None]
type ReviewFormatter = Callable[..., dict[str, Any]]
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
    resume_graph: ResumeGraph


@dataclass(frozen=True, slots=True)
class A2UIPersistenceDependencies:
    """Registry and result-projection seams used by the runtime."""

    registry_factory: RegistryFactory
    current_user: CurrentUser
    tasks_db_path: DbPath
    create_stream_run: CreateStreamRun
    settle_stream_run: SettleStreamRun
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
        "chat_kwargs": chat_kwargs(chat_config, runtime.sensitive),
    }


def build_review_stream_app() -> Any:
    """Return the compiled ReviewAgent graph used by HTTP pause paths."""
    app, _ = review_stream_target("", [])
    return app


def build_review_initial_state(args: ReviewAgentArgs) -> Mapping[str, Any]:
    """Build the ReviewAgent initial graph state for one request."""
    _, initial_state = review_stream_target(
        args.user_query, args.obs_file_list
    )
    return initial_state


def build_review_request_info(
    payload: ChatCompletionRequest,
    user_query: str,
) -> RunRequestInfo:
    """Build the stable request metadata stored for Review runs."""
    return RunRequestInfo(
        dialogue_id=payload.dialogue_id,
        query=user_query,
        tool_name="ReviewAgent",
        model=payload.model,
        request_json=payload.model_dump_json(),
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


def chat_interrupt_result(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Return the registry result for a paused Chat A2UI run."""
    return {
        "interrupt": dict(interrupt),
        "status": "input_required",
    }


def review_interrupt_result(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Return the registry result for a paused Review A2UI run."""
    return {
        "interrupt": dict(interrupt),
        "status": "input_required",
    }


def submitted_a2ui_value(
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the submitted downlink echoed in a terminal result."""
    accepted = resume_payload.get("accepted")
    if not isinstance(accepted, bool):
        approved = resume_payload.get("approved")
        if (
            isinstance(approved, bool)
            and resume_payload.get("cancelled") is not True
            and "fields" not in resume_payload
            and "selected" not in resume_payload
        ):
            accepted = approved
    fields = resume_payload.get("fields")
    return build_submitted_value(
        prior_surface,
        accepted=accepted if isinstance(accepted, bool) else None,
        cancelled=(True if resume_payload.get("cancelled") is True else None),
        fields=fields if isinstance(fields, Mapping) else None,
        selected=resume_payload.get("selected"),
    )


def format_chat_result(
    final_state: Mapping[str, Any],
    *,
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Format a terminal Chat A2UI graph state for registry storage."""
    raw_payload = merge_intermediate_state(
        final_state,
        final_response_key="response",
    )
    envelope = build_tool_result_envelope("ChatAgent", raw_payload)
    return {
        "formatted": asdict(envelope.formatted),
        "raw": envelope.raw,
        "a2ui": submitted_a2ui_value(prior_surface, resume_payload),
    }


def format_review_result(
    final_state: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a terminal ReviewAgent graph state for registry storage."""
    raw_payload = merge_intermediate_state(final_state)
    envelope = build_tool_result_envelope(
        "ReviewAgent",
        raw_payload,
        arguments=arguments,
    )
    return {
        "formatted": asdict(envelope.formatted),
        "raw": envelope.raw,
    }


def chat_interrupt_body(
    *, run_id: str, interrupt: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the HTTP body for a paused Chat A2UI run."""
    return {
        "id": run_id,
        "run_id": run_id,
        "object": "agent.run",
        "agent": "chat",
        "status": "input_required",
        "task_ids": [],
        "interrupt": dict(interrupt),
    }


def review_interrupt_body(
    *, thread_id: str, interrupt: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the HTTP body for a paused ReviewAgent run."""
    return {
        "id": thread_id,
        "run_id": thread_id,
        "object": "agent.run",
        "agent": "review",
        "status": "input_required",
        "task_ids": [],
        "interrupt": dict(interrupt),
    }


def project_review_interrupt(
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach a2ui to a Review interrupt when the feature is enabled."""
    if not ApiConfig().A2UI_ENABLED:
        return dict(interrupt)
    try:
        return attach_review_a2ui(interrupt)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _LOGGER.error(
            "review a2ui projection failed (%s); continuing without",
            exc.__class__.__name__,
        )
        return dict(interrupt)


def open_surface_for_action(
    record: RunRecord,
    *,
    surface_id: str,
    widget: str,
) -> Mapping[str, Any]:
    """Return the open A2UI draft surface or raise an HTTP conflict."""
    if record.status != "input_required":
        raise HTTPException(
            status_code=409,
            detail="run is not awaiting input",
        )
    stored = record.result or {}
    interrupt = stored.get("interrupt") or {}
    draft = interrupt.get("draft") or {}
    open_surface = draft.get("a2ui")
    if not isinstance(open_surface, Mapping):
        raise HTTPException(status_code=409, detail="no open a2ui surface")
    if open_surface.get("surface_id") != surface_id:
        raise HTTPException(status_code=409, detail="surface_id mismatch")
    if open_surface.get("widget") != widget:
        raise HTTPException(status_code=400, detail="widget mismatch")
    return open_surface


_RESUME_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_RESUME_LOCKS_GUARD = threading.Lock()


@contextmanager
def _claim_resume(run_id: str, owner: str) -> Any:
    """Claim one run so concurrent Web uplinks have a single winner."""
    key = (owner, run_id)
    with _RESUME_LOCKS_GUARD:
        lock = _RESUME_LOCKS.setdefault(key, threading.Lock())
        claimed = lock.acquire(blocking=False)
    if not claimed:
        raise HTTPException(
            status_code=409,
            detail="run is not awaiting input",
        )
    try:
        yield
    finally:
        lock.release()
        with _RESUME_LOCKS_GUARD:
            if not lock.locked():
                _RESUME_LOCKS.pop(key, None)


def _failed_resume_result() -> dict[str, Any]:
    """Return a safe failure payload without retaining backend exceptions."""
    return {
        "formatted": {"answer": ""},
        "raw": None,
        "error": "a2ui resume failed",
    }


@dataclass(frozen=True, slots=True)
class _ActionContext:
    """Validated owner, surface, graph, and registry for one uplink."""

    owner: str
    agent: str
    surface: Mapping[str, Any]
    resume_payload: dict[str, Any]
    graph: Any
    registry: RunRegistry
    format_review_result: ReviewFormatter


def _prepare_action_context(
    *,
    run_id: str,
    body: A2uiActionRequest,
    dependencies: A2UIRuntimeDependencies,
) -> _ActionContext:
    """Validate ownership and translate one Web action envelope."""
    owner = dependencies.persistence.current_user() or "anonymous"
    registry = dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    )
    record = registry.get_run(run_id, owner=owner)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"run not found: {run_id}",
        )
    agent = record.spec.agent
    if agent not in ("chat", "review"):
        raise HTTPException(
            status_code=400,
            detail="unsupported agent for a2ui",
        )
    surface = open_surface_for_action(
        record,
        surface_id=body.surface_id,
        widget=body.widget,
    )
    try:
        envelope = A2uiActionEnvelope.model_validate(body.model_dump())
        if agent == "chat":
            resume_payload = action_to_resume_payload(envelope)
            graph = dependencies.graphs.chat_graph()
        else:
            resume_payload = review_action_to_resume(envelope)
            graph = dependencies.graphs.review_graph()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ActionContext(
        owner=owner,
        agent=agent,
        surface=surface,
        resume_payload=resume_payload,
        graph=graph,
        registry=registry,
        format_review_result=dependencies.persistence.format_review_result,
    )


def _settled_action_interrupt(
    *,
    run_id: str,
    context: _ActionContext,
    interrupt: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Persist and shape a re-interrupt from one resumed A2UI graph."""
    if context.agent == "chat":
        interrupt_dict = dict(interrupt)
        context.registry.settle_run(
            run_id,
            owner=context.owner,
            status="input_required",
            result=chat_interrupt_result(interrupt_dict),
        )
        return (
            chat_interrupt_body(run_id=run_id, interrupt=interrupt_dict),
            200,
        )
    interrupt_dict = project_review_interrupt(interrupt)
    context.registry.settle_run(
        run_id,
        owner=context.owner,
        status="input_required",
        result=review_interrupt_result(interrupt_dict),
    )
    return (
        review_interrupt_body(thread_id=run_id, interrupt=interrupt_dict),
        200,
    )


def _terminal_action_result(
    *,
    context: _ActionContext,
    final_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Format a terminal Chat or Review A2UI resume result."""
    if context.agent == "chat":
        return format_chat_result(
            final_state,
            prior_surface=context.surface,
            resume_payload=context.resume_payload,
        )
    return {
        **context.format_review_result(final_state),
        "a2ui": submitted_a2ui_value(
            context.surface,
            context.resume_payload,
        ),
    }


async def resume_a2ui_run(
    *,
    run_id: str,
    body: A2uiActionRequest,
    debug: bool,
    dependencies: A2UIRuntimeDependencies,
) -> tuple[dict[str, Any], int]:
    """Resume a Chat or Review A2UI action and settle its owner-scoped row."""
    if not ApiConfig().A2UI_ENABLED:
        raise HTTPException(status_code=403, detail="a2ui disabled")
    if run_id != body.run_id:
        raise HTTPException(status_code=400, detail="run_id mismatch")

    owner = dependencies.persistence.current_user() or "anonymous"
    with _claim_resume(run_id, owner):
        context = _prepare_action_context(
            run_id=run_id,
            body=body,
            dependencies=dependencies,
        )
        try:
            final_state = await dependencies.graphs.resume_graph(
                context.graph,
                run_id,
                context.resume_payload,
            )
        except NoCheckpointError as exc:
            _LOGGER.warning(
                "a2ui resume checkpoint missing for run %s (%s)",
                run_id,
                exc.__class__.__name__,
            )
            raise HTTPException(
                status_code=409,
                detail="no pause point for run",
            ) from exc
        except Exception as exc:
            _LOGGER.error(
                "a2ui resume failed for run %s (%s)",
                run_id,
                exc.__class__.__name__,
            )
            context.registry.settle_run(
                run_id,
                owner=context.owner,
                status="failed",
                result=_failed_resume_result(),
            )
            raise HTTPException(
                status_code=500,
                detail="a2ui resume failed",
            ) from exc

        interrupt_after = detect_interrupt(final_state, run_id)
        if interrupt_after is not None:
            return _settled_action_interrupt(
                run_id=run_id,
                context=context,
                interrupt=interrupt_after,
            )

        result = _terminal_action_result(
            context=context,
            final_state=final_state,
        )
        context.registry.settle_run(
            run_id,
            owner=context.owner,
            status="succeeded",
            result=result,
        )
        response_result = result if debug else strip_agent_result(result)
        return (
            run_lifecycle.agent_run_response(
                run_id=run_id,
                agent=context.agent,
                status="succeeded",
                result=response_result,
            ),
            200,
        )


@dataclass(frozen=True, slots=True)
class ReviewExecution:
    """Internal result of one interrupt-aware ReviewAgent graph run."""

    run_id: str
    status: str
    result: dict[str, Any] | None = None
    interrupt: dict[str, Any] | None = None


async def run_review_with_interrupt(
    *,
    arguments: dict[str, Any],
    request_info: RunRequestInfo,
    dependencies: A2UIRuntimeDependencies,
) -> ReviewExecution:
    """Run ReviewAgent once, surfacing a LangGraph interrupt if present."""
    args = dependencies.graphs.validate_review(arguments)
    owner = dependencies.persistence.current_user() or "anonymous"
    run_id = IdFactory().new_id("run", "review")
    graph = dependencies.graphs.review_graph()
    initial_state = dependencies.graphs.review_initial_state(args)
    final_state = await graph.ainvoke(
        initial_state,
        config=build_runnable_config(run_id),
    )
    interrupt = detect_interrupt(final_state, run_id)
    registry = dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    )
    if interrupt is not None:
        interrupt_dict = project_review_interrupt(interrupt)
        registry.create_run(
            RunSpec(
                run_id=run_id,
                user_id=owner,
                agent="review",
                origin="local",
            ),
            outcome=RunOutcome(
                status="input_required",
                result=review_interrupt_result(interrupt_dict),
            ),
            request_info=request_info,
        )
        return ReviewExecution(
            run_id=run_id,
            status="input_required",
            interrupt=interrupt_dict,
        )
    result = dependencies.persistence.format_review_result(
        final_state, arguments=arguments
    )
    registry.create_run(
        RunSpec(
            run_id=run_id,
            user_id=owner,
            agent="review",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded", result=result),
        request_info=request_info,
    )
    return ReviewExecution(run_id=run_id, status="succeeded", result=result)


def review_run_body(
    execution: ReviewExecution,
    *,
    debug: bool,
) -> dict[str, Any]:
    """Shape a ReviewAgent execution as an ``agent.run`` response."""
    if execution.interrupt is not None:
        return review_interrupt_body(
            thread_id=execution.run_id,
            interrupt=execution.interrupt,
        )
    result = execution.result or {"formatted": {"answer": ""}, "raw": None}
    response_result = result if debug else strip_agent_result(result)
    return {
        "id": execution.run_id,
        "run_id": execution.run_id,
        "object": "agent.run",
        "agent": "review",
        "status": execution.status,
        "task_ids": [],
        "result": response_result,
    }


@dataclass(frozen=True, slots=True)
class _ReviewResumeContext:
    """Validated Review run ownership and prior A2UI surface."""

    owner: str
    registry: RunRegistry
    prior_surface: Mapping[str, Any] | None


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
) -> _StreamContext:
    """Prepare a Chat A2UI graph and persist its running row."""
    owner = dependencies.persistence.current_user() or "anonymous"
    try:
        graph = dependencies.graphs.chat_graph()
        initial_state = dependencies.graphs.chat_initial_state(arguments)
    except Exception as exc:
        raise dependencies.stream.stream_setup_error(
            exc, priming=False
        ) from exc
    agent_slug = "chat"
    run_id = IdFactory().new_id("run", agent_slug)
    request_info = RunRequestInfo(
        dialogue_id=payload.dialogue_id,
        query=user_query,
        tool_name="ChatAgent",
        model=payload.model,
        request_json=payload.model_dump_json(),
    )
    dependencies.persistence.create_stream_run(
        run_id, agent_slug, owner, request_info
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
) -> _StreamContext:
    """Prepare a Review A2UI graph and persist its running row."""
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
    run_id = IdFactory().new_id("run", agent_slug)
    request_info = build_review_request_info(payload, user_query)
    dependencies.persistence.create_stream_run(
        run_id, agent_slug, owner, request_info
    )
    return _StreamContext(
        agent_slug=agent_slug,
        owner=owner,
        run_id=run_id,
        request_info=request_info,
        graph=graph,
        initial_state=initial_state,
    )


def _prepare_review_resume_context(
    *,
    thread_id: str,
    dependencies: A2UIRuntimeDependencies,
) -> _ReviewResumeContext:
    """Load a paused Review run and capture its submitted-surface context."""
    owner = dependencies.persistence.current_user() or "anonymous"
    registry = dependencies.persistence.registry_factory(
        dependencies.persistence.tasks_db_path()
    )
    record = registry.get_run(thread_id, owner=owner)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"run not found: {thread_id}",
        )
    if record.status != "input_required":
        raise HTTPException(
            status_code=409,
            detail="run is not awaiting input",
        )
    stored = record.result or {}
    interrupt_stored = stored.get("interrupt") or {}
    draft = interrupt_stored.get("draft") or {}
    candidate = draft.get("a2ui")
    prior_surface = candidate if isinstance(candidate, Mapping) else None
    return _ReviewResumeContext(
        owner=owner,
        registry=registry,
        prior_surface=prior_surface,
    )


async def resume_review_run(
    *,
    thread_id: str,
    payload: ResumeRequest,
    debug: bool,
    dependencies: A2UIRuntimeDependencies,
) -> tuple[dict[str, Any], int]:
    """Resume a ReviewAgent run and preserve the dual transport contract."""
    owner = dependencies.persistence.current_user() or "anonymous"
    with _claim_resume(thread_id, owner):
        context = _prepare_review_resume_context(
            thread_id=thread_id,
            dependencies=dependencies,
        )
        try:
            final_state = await dependencies.graphs.resume_graph(
                dependencies.graphs.review_graph(),
                thread_id,
                {"approved": payload.approved, "edits": payload.edits},
            )
        except NoCheckpointError as exc:
            _LOGGER.warning(
                "resume checkpoint missing for run %s (%s)",
                thread_id,
                exc.__class__.__name__,
            )
            raise HTTPException(
                status_code=409,
                detail="no pause point for run",
            ) from exc
        except Exception as exc:
            _LOGGER.error(
                "review resume failed for run %s (%s)",
                thread_id,
                exc.__class__.__name__,
            )
            context.registry.settle_run(
                thread_id,
                owner=context.owner,
                status="failed",
                result=_failed_resume_result(),
            )
            raise HTTPException(
                status_code=500,
                detail="review resume failed",
            ) from exc
        interrupt = detect_interrupt(final_state, thread_id)
        if interrupt is not None:
            interrupt_dict = project_review_interrupt(interrupt)
            context.registry.settle_run(
                thread_id,
                owner=context.owner,
                status="input_required",
                result=review_interrupt_result(interrupt_dict),
            )
            return (
                review_interrupt_body(
                    thread_id=thread_id,
                    interrupt=interrupt_dict,
                ),
                200,
            )
        result = dependencies.persistence.format_review_result(final_state)
        if context.prior_surface is not None:
            result = {
                **result,
                "a2ui": submitted_a2ui_value(
                    context.prior_surface,
                    {"approved": payload.approved},
                ),
            }
        context.registry.settle_run(
            thread_id,
            owner=context.owner,
            status="succeeded",
            result=result,
        )
        execution = ReviewExecution(
            run_id=thread_id,
            status="succeeded",
            result=result,
        )
        return review_run_body(execution, debug=debug), 200


def settle_a2ui_stream_failure(
    run_id: str,
    owner: str,
    settled_terminal: list[bool],
    *,
    dependencies: A2UIRuntimeDependencies,
) -> None:
    """Settle an A2UI stream failed when no domain terminal was committed."""
    if settled_terminal[0]:
        return
    dependencies.persistence.settle_stream_run(
        run_id,
        owner,
        "failed",
        dependencies.stream.failed_stream_result(),
    )
    settled_terminal[0] = True


async def stream_chat_a2ui_confirm(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: A2UIRuntimeDependencies,
) -> StreamingResponse:
    """Short-circuit Chat streaming into an A2UI pause."""
    context = _prepare_chat_stream(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=dependencies,
    )

    async def _agui_events(
        settled: list[bool],
    ) -> AsyncIterator[AguiEvent]:
        """Yield AG-UI frames for one Chat A2UI pause."""
        yield run_started(context.run_id, payload.dialogue_id)
        final_state = await context.graph.ainvoke(
            context.initial_state,
            config=build_runnable_config(context.run_id),
        )
        interrupt = detect_interrupt(final_state, context.run_id)
        if interrupt is not None:
            a2ui_value = interrupt["draft"]["a2ui"]
            dependencies.persistence.settle_stream_run(
                context.run_id,
                context.owner,
                "input_required",
                chat_interrupt_result(interrupt),
            )
            settled[0] = True
            yield custom(A2UI_CUSTOM_NAME, a2ui_value)
        yield run_finished(context.run_id)

    lifecycle_state = StreamLifecycleState()
    settled_terminal = [False]
    try:
        primed = await prime_agui_stream(_agui_events(settled_terminal))
    except Exception as exc:
        dependencies.persistence.settle_stream_run(
            context.run_id,
            context.owner,
            "failed",
            dependencies.stream.failed_stream_result(),
        )
        raise dependencies.stream.stream_setup_error(
            exc, priming=True
        ) from exc

    async def _wrapped() -> AsyncIterator[str]:
        """Forward SSE; settle failed only when pause was not recorded."""
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
            settle_a2ui_stream_failure(
                context.run_id,
                context.owner,
                settled_terminal,
                dependencies=dependencies,
            )

    return StreamingResponse(_wrapped(), media_type="text/event-stream")


async def stream_review_a2ui_pause(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
    dependencies: A2UIRuntimeDependencies,
) -> StreamingResponse:
    """Stream Review until interrupt and emit one ``phyto.a2ui`` frame."""
    context = _prepare_review_stream(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=dependencies,
    )

    async def _agui_events(settled: list[bool]) -> AsyncIterator[AguiEvent]:
        yield run_started(context.run_id, payload.dialogue_id)
        final_state = await context.graph.ainvoke(
            context.initial_state,
            config=build_runnable_config(context.run_id),
        )
        interrupt = detect_interrupt(final_state, context.run_id)
        if interrupt is not None:
            interrupt_dict = project_review_interrupt(dict(interrupt))
            dependencies.persistence.settle_stream_run(
                context.run_id,
                context.owner,
                "input_required",
                review_interrupt_result(interrupt_dict),
            )
            settled[0] = True
            draft = interrupt_dict.get("draft")
            if isinstance(draft, Mapping):
                a2ui_value = draft.get("a2ui")
                if isinstance(a2ui_value, Mapping):
                    yield custom(A2UI_CUSTOM_NAME, dict(a2ui_value))
        else:
            result = dependencies.persistence.format_review_result(
                final_state, arguments=arguments
            )
            dependencies.persistence.settle_stream_run(
                context.run_id, context.owner, "succeeded", result
            )
            settled[0] = True
        yield run_finished(context.run_id)

    lifecycle_state = StreamLifecycleState()
    settled_terminal = [False]
    try:
        primed = await prime_agui_stream(_agui_events(settled_terminal))
    except Exception as exc:
        dependencies.persistence.settle_stream_run(
            context.run_id,
            context.owner,
            "failed",
            dependencies.stream.failed_stream_result(),
        )
        raise dependencies.stream.stream_setup_error(
            exc, priming=True
        ) from exc

    async def _wrapped() -> AsyncIterator[str]:
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
            settle_a2ui_stream_failure(
                context.run_id,
                context.owner,
                settled_terminal,
                dependencies=dependencies,
            )

    return StreamingResponse(_wrapped(), media_type="text/event-stream")


__all__ = [
    "A2UIGraphDependencies",
    "A2UIPersistenceDependencies",
    "A2UIRuntimeDependencies",
    "A2UIStreamDependencies",
    "ReviewExecution",
    "build_chat_initial_state",
    "build_chat_stream_app",
    "build_review_initial_state",
    "build_review_request_info",
    "build_review_stream_app",
    "chat_interrupt_body",
    "chat_interrupt_result",
    "format_chat_result",
    "format_review_result",
    "open_surface_for_action",
    "project_review_interrupt",
    "resume_a2ui_run",
    "resume_paused_graph",
    "resume_review_run",
    "review_interrupt_body",
    "review_interrupt_result",
    "review_run_body",
    "run_review_with_interrupt",
    "settle_a2ui_stream_failure",
    "stream_chat_a2ui_confirm",
    "stream_review_a2ui_pause",
    "submitted_a2ui_value",
    "validate_review_arguments",
]
