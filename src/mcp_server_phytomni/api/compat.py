# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility seams shared by the HTTP application and route units.

The application module historically exposed these small wrappers as private
test and integration seams. Keeping them in a separate module reduces the
factory's structural footprint while resolving patched app-level dependencies
at call time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from importlib import import_module
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from ..agents.shared.a2ui import select_chat_a2ui_widget
from ..config.defaults import ApiConfig
from ..mcp.result_formatting import AguiEvent
from ..mcp.stream_lifecycle import PrimedAguiStream, StreamLifecycleState
from ..runtime.run_registry import RunRecord
from ..storage.path_policy import IdFactory
from . import a2ui_runtime, run_lifecycle, streaming
from .relay.audit_filter import redact_body_text
from .schemas import A2uiActionRequest, ChatCompletionRequest


def _app_module() -> Any:
    """Return the application module lazily to preserve patch seams."""
    return import_module(".app", package=__package__)


def _app_attr(name: str) -> Any:
    """Read an app-level compatibility seam without static coupling."""
    return getattr(_app_module(), name)


def _purge_expired_runs_best_effort() -> None:
    """Compatibility seam for the shared run-registry TTL purge."""
    app = _app_module()
    run_lifecycle.purge_expired_runs_best_effort(
        db_path=app.resolve_tasks_db_path(),
        registry_factory=app.RunRegistry,
        logger=_app_attr("_LOGGER"),
    )


async def _purge_expired_runs_best_effort_async() -> None:
    """Compatibility seam for the off-loop coalesced TTL purge."""
    await run_lifecycle.purge_expired_runs_best_effort_async(
        purge=_app_attr("_purge_expired_runs_best_effort")
    )


def _claim_run_gc() -> bool:
    """Compatibility seam for the process-local GC slot."""
    return run_lifecycle.claim_run_gc()


def _release_run_gc() -> None:
    """Compatibility seam for releasing the process-local GC slot."""
    run_lifecycle.release_run_gc()


async def _schedule_run_gc(background: BackgroundTasks) -> None:
    """Compatibility seam for the FastAPI background GC dependency."""
    await run_lifecycle.schedule_run_gc(
        background, task=_app_attr("_purge_expired_runs_best_effort_async")
    )


def _extract_answer(result: Any) -> str | None:
    """Compatibility seam for answer extraction from a stored result."""
    return run_lifecycle.extract_answer(result)


def _run_record_to_dict(record: Any) -> dict[str, Any]:
    """Compatibility seam for flattening a registry record."""
    return run_lifecycle.run_record_to_dict(record)


def _relay_audit_record_to_dict(
    record: Any, config: ApiConfig
) -> dict[str, Any]:
    """Project a relay audit row with defense-in-depth body redaction."""
    payload = record.model_dump()
    payload["request_body"] = redact_body_text(
        payload.get("request_body"), config.RELAY_REQUEST_AUDIT_MAX_BYTES
    )
    payload["response_body"] = redact_body_text(
        payload.get("response_body"), config.RELAY_RESPONSE_AUDIT_MAX_BYTES
    )
    return payload


def _chat_a2ui_stream_app() -> Any:
    """Compatibility seam for the cached Chat A2UI graph."""
    return a2ui_runtime.build_chat_stream_app()


def _chat_a2ui_initial_state(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility seam for Chat A2UI initial-state construction."""
    return a2ui_runtime.build_chat_initial_state(arguments)


def _chat_a2ui_interrupt_result(
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for paused Chat A2UI result projection."""
    return a2ui_runtime.chat_interrupt_result(interrupt)


def _submitted_a2ui_value(
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for submitted A2UI result projection."""
    return a2ui_runtime.submitted_a2ui_value(prior_surface, resume_payload)


def _format_chat_a2ui_result(
    final_state: Mapping[str, Any],
    *,
    prior_surface: Mapping[str, Any],
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for terminal Chat A2UI result formatting."""
    return a2ui_runtime.format_chat_result(
        final_state,
        prior_surface=prior_surface,
        resume_payload=resume_payload,
    )


def _a2ui_interrupt_body(
    *,
    run_id: str,
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for the paused Chat A2UI HTTP body."""
    return a2ui_runtime.chat_interrupt_body(
        run_id=run_id,
        interrupt=interrupt,
    )


async def _resume_paused_run(
    app: Any,
    thread_id: str,
    resume_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for the shared paused-graph resume kernel."""
    return await a2ui_runtime.resume_paused_graph(
        app,
        thread_id,
        resume_payload,
    )


def _open_a2ui_surface_for_action(
    record: RunRecord,
    *,
    surface_id: str,
    widget: str,
) -> Mapping[str, Any]:
    """Compatibility seam for open-surface validation."""
    return a2ui_runtime.open_surface_for_action(
        record,
        surface_id=surface_id,
        widget=widget,
    )


async def _resume_a2ui_run(
    *,
    run_id: str,
    body: A2uiActionRequest,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Compatibility seam for the Web A2UI action resume runtime."""
    return await a2ui_runtime.resume_a2ui_run(
        run_id=run_id,
        body=body,
        debug=debug,
        dependencies=_a2ui_runtime_dependencies(),
    )


def _stream_setup_error(exc: Exception, *, priming: bool) -> HTTPException:
    """Map stream setup/prime failures to fixed pre-header HTTP errors."""
    return streaming.stream_setup_error(exc, priming=priming)


def _failed_stream_result() -> dict[str, Any]:
    """Return the minimal failed result persisted after pre-open failure."""
    return streaming.failed_stream_result()


async def _replay_primed_stream(
    primed: PrimedAguiStream,
) -> AsyncIterator[AguiEvent]:
    """Replay a primed first event before consuming its raw remainder."""
    async for event in streaming.replay_primed_stream(primed):
        yield event


async def _project_primed_stream(
    primed: PrimedAguiStream,
    *,
    run_id: str,
    lifecycle_state: StreamLifecycleState | None = None,
) -> AsyncIterator[AguiEvent]:
    """Project a primed raw stream through one typed lifecycle state."""
    app = _app_module()
    async for event in streaming.project_primed_stream(
        primed,
        run_id=run_id,
        request_id=app.current_request_id() or "unknown",
        lifecycle_state=lifecycle_state,
    ):
        yield event


def _a2ui_runtime_dependencies() -> a2ui_runtime.A2UIRuntimeDependencies:
    """Bind app compatibility seams into the A2UI runtime record."""
    app = _app_module()
    return a2ui_runtime.A2UIRuntimeDependencies(
        graphs=a2ui_runtime.A2UIGraphDependencies(
            chat_graph=_app_attr("_chat_a2ui_stream_app"),
            chat_initial_state=_app_attr("_chat_a2ui_initial_state"),
            review_graph=_app_attr("_review_stream_app"),
            review_initial_state=_app_attr("_review_initial_state"),
            validate_review=_app_attr("_validate_review_arguments"),
            has_checkpoint=_app_attr("_has_graph_checkpoint"),
            resume_graph=_app_attr("_resume_paused_run"),
        ),
        persistence=a2ui_runtime.A2UIPersistenceDependencies(
            registry_factory=app.RunRegistry,
            current_user=app.current_request_user,
            current_request_id=app.current_request_id,
            tasks_db_path=app.resolve_tasks_db_path,
            create_stream_run=_app_attr("_create_running_stream_run"),
            settle_stream_run=_app_attr("_settle_stream_run"),
            format_review_result=_app_attr("_format_review_result"),
        ),
        stream=a2ui_runtime.A2UIStreamDependencies(
            stream_setup_error=_app_attr("_stream_setup_error"),
            failed_stream_result=_app_attr("_failed_stream_result"),
            project_stream=_app_attr("_project_primed_stream"),
        ),
    )


def _settle_a2ui_stream_failure(
    run_id: str,
    owner: str,
    settled_terminal: list[bool],
    expected_revision: int,
) -> None:
    """Compatibility seam for failed A2UI stream settlement."""
    a2ui_runtime.settle_a2ui_stream_failure(
        run_id,
        owner,
        settled_terminal,
        dependencies=_a2ui_runtime_dependencies(),
        expected_revision=expected_revision,
    )


def _stream_a2ui_enabled() -> bool:
    """Read the current A2UI flag for the streaming runtime."""
    return ApiConfig().A2UI_ENABLED


def _new_stream_run_id(prefix: str, kind: str) -> str:
    """Mint a registry id without exposing the storage factory to streaming."""
    return IdFactory().new_id(prefix, kind)


def _stream_agent_slug(model: str) -> str | None:
    """Resolve the registry slug for one streamed public model."""
    return _app_attr("_MODEL_TO_AGENT_SLUG").get(model)


def _streaming_dependencies() -> streaming.StreamingDependencies:
    """Bind app-owned seams into the extracted streaming runtime."""
    app = _app_module()
    return streaming.StreamingDependencies(
        request=streaming.StreamingRequestDependencies(
            prepare_tool_stream=app.prepare_tool_stream,
            current_user=app.current_request_user,
            current_request_id=app.current_request_id,
            new_run_id=_new_stream_run_id,
            agent_slug=_stream_agent_slug,
        ),
        a2ui=streaming.StreamingA2UIDependencies(
            enabled=_stream_a2ui_enabled,
            select_widget=select_chat_a2ui_widget,
            runtime=_a2ui_runtime_dependencies,
        ),
        persistence=streaming.StreamingPersistenceDependencies(
            create_running_stream_run=_app_attr("_create_running_stream_run"),
            settle_stream_run=_app_attr("_settle_stream_run"),
            stream_answer_max_bytes=_app_attr("_stream_answer_max_bytes"),
        ),
    )


async def _stream_chat_a2ui_confirm(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Compatibility seam for the Chat A2UI stream runtime."""
    return await streaming.stream_chat_a2ui_confirm(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=_streaming_dependencies(),
    )


async def _stream_review_a2ui_pause(
    *,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Compatibility seam for the Review A2UI stream runtime."""
    return await streaming.stream_review_a2ui_pause(
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=_streaming_dependencies(),
    )


async def _stream_chat_completion(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Compatibility seam for the extracted HTTP streaming runtime."""
    return await streaming.stream_chat_completion(
        tool_name=tool_name,
        arguments=arguments,
        payload=payload,
        user_query=user_query,
        dependencies=_streaming_dependencies(),
    )


__all__ = [
    "_a2ui_interrupt_body",
    "_a2ui_runtime_dependencies",
    "_chat_a2ui_initial_state",
    "_chat_a2ui_interrupt_result",
    "_chat_a2ui_stream_app",
    "_claim_run_gc",
    "_extract_answer",
    "_failed_stream_result",
    "_format_chat_a2ui_result",
    "_new_stream_run_id",
    "_open_a2ui_surface_for_action",
    "_project_primed_stream",
    "_purge_expired_runs_best_effort",
    "_purge_expired_runs_best_effort_async",
    "_release_run_gc",
    "_relay_audit_record_to_dict",
    "_replay_primed_stream",
    "_resume_a2ui_run",
    "_resume_paused_run",
    "_run_record_to_dict",
    "_schedule_run_gc",
    "_settle_a2ui_stream_failure",
    "_stream_a2ui_enabled",
    "_stream_agent_slug",
    "_stream_chat_a2ui_confirm",
    "_stream_chat_completion",
    "_stream_review_a2ui_pause",
    "_stream_setup_error",
    "_streaming_dependencies",
    "_submitted_a2ui_value",
]
