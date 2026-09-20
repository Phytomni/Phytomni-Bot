# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Run status, history, and pause-action route registration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal, TypedDict, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ...mcp.result_formatting import resolve_debug, strip_agent_result
from ...runtime.execution_event_limits import DEFAULT_EXECUTION_EVENT_LIMITS
from ...runtime.execution_events import ExecutionEventV1, RunEventProjectionV1
from ...runtime.execution_v1_projection_v2 import (
    V1ExecutionCompatibilityReader,
)
from ...runtime.run_registry import RunRegistry
from .. import run_lifecycle
from ..a2ui_limits import (
    A2uiPayloadError,
    A2uiPayloadTooLargeError,
    ensure_a2ui_response_size,
    read_a2ui_action_request,
)
from ..auth import ApiPrincipal
from ..schemas import ResumeRequest
from . import _paging_values

EXECUTION_EVENT_HEARTBEAT_POLL_TICKS = 60
EXECUTION_REGISTRATION_WAIT_SECONDS = 30.0
EXECUTION_REGISTRATION_POLL_SECONDS = 0.25


def _tasks_db_path() -> str:
    """Resolve the app DB without importing the app at module load."""
    app_module = import_module("mcp_server_phytomni.api.app")
    return cast(Callable[[], str], app_module.resolve_tasks_db_path)()


@dataclass(frozen=True)
class RunAuthDependencies:
    """Authentication dependencies shared by run routes."""

    require_agents: Callable[..., Any]


@dataclass(frozen=True)
class RunContextDependencies:
    """Request identity and service-token checks for run history."""

    current_user: Callable[[], str | None]
    service_token_valid: Callable[[str | None, str | None], bool]


@dataclass(frozen=True)
class RunProjectionDependencies:
    """Owner-scoped run lookup and history projection seams."""

    reconcile_task_logs: Callable[..., Awaitable[dict[str, Any]]]
    fetch_owner_run: Callable[..., Awaitable[dict[str, Any]]]
    retry_owner_delivery: Callable[..., Awaitable[dict[str, Any]]]
    list_owner_runs: Callable[[RunListRequest], dict[str, Any]]
    strip_run_result: Callable[[dict[str, Any]], dict[str, Any]]
    cancel_research_run: Callable[..., Awaitable[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class RunPauseDependencies:
    """A2UI and Review pause/resume seams."""

    a2ui_max_response_bytes: Callable[[], int]
    resume_a2ui: Callable[..., Awaitable[tuple[dict[str, Any], int]]]
    resume_review: Callable[..., Awaitable[tuple[dict[str, Any], int]]]


@dataclass(frozen=True)
class RunRouteDependencies:
    """Explicit dependencies required to register run routes."""

    auth: RunAuthDependencies
    context: RunContextDependencies
    projection: RunProjectionDependencies
    pause: RunPauseDependencies


@dataclass(frozen=True)
class RunListFilter:
    """Typed filter values for owner-scoped run history."""

    status: str | None
    agent: str | None
    origin: str | None
    dialogue_id: str | None
    user_id: str | None


@dataclass(frozen=True)
class RunListPaging:
    """Typed paging and projection values for run history."""

    created_after: str | None
    created_before: str | None
    limit: int
    offset: int
    debug: bool


@dataclass(frozen=True)
class RunListRequest:
    """Typed owner, filter, paging, and projection input for run listing."""

    owner: str
    filters: RunListFilter
    paging: RunListPaging


class RunFilterQuery(TypedDict):
    """Filter query parameters for owner-scoped run history."""

    status: str | None
    agent: str | None
    origin: str | None
    user_id: str | None
    dialogue_id: str | None


class RunPagingQuery(TypedDict):
    """Paging and projection query parameters for run history."""

    created_after: str | None
    created_before: str | None
    limit: int
    offset: int
    debug: bool


class ExecutionEventPageResponse(BaseModel):
    """Finite V1 response envelope for resumable event history."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str = Field(min_length=1, max_length=128)
    items: tuple[ExecutionEventV1, ...]
    next_after_seq: int = Field(ge=0)
    has_more: bool


class ExecutionEventCorrelationPageResponse(ExecutionEventPageResponse):
    """Event page addressed by the browser-known execution identity."""

    execution_id: str = Field(min_length=1, max_length=128)


class ExecutionEventCorrelationProjectionResponse(RunEventProjectionV1):
    """Projection addressed by the browser-known execution identity."""

    execution_id: str = Field(min_length=1, max_length=128)


# FastAPI needs these fields to remain flat query parameters so the public
# OpenAPI document and request coercion stay byte-compatible with the legacy
# route. The aggregation helper is intentionally narrow and tested below.
async def _build_run_filter_query(
    *,
    status: str | None = None,
    agent: str | None = None,
    origin: str | None = None,
    user_id: str | None = None,
    dialogue_id: str | None = None,
) -> RunFilterQuery:
    """Collect legacy flat filter fields without changing their schema."""
    return {
        "status": status,
        "agent": agent,
        "origin": origin,
        "user_id": user_id,
        "dialogue_id": dialogue_id,
    }


async def _build_run_paging_query(
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 10,
    offset: int = 0,
    debug: bool = False,
) -> RunPagingQuery:
    """Collect legacy flat paging fields without changing their schema."""
    return cast(
        RunPagingQuery,
        {
            **_paging_values(created_after, created_before, limit, offset),
            "debug": debug,
        },
    )


def _owned_execution_run_id(execution_id: str, owner: str) -> str:
    """Resolve one owner-visible execution identity to its run id."""
    record = RunRegistry(_tasks_db_path()).get_run_by_execution_id(
        execution_id,
        owner=owner,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="resource not found")
    return record.spec.run_id


async def _await_owned_execution_run_id(
    execution_id: str,
    owner: str,
    request: Request,
) -> str:
    """Wait briefly for detached execution registration to become visible."""
    deadline = (
        asyncio.get_running_loop().time() + EXECUTION_REGISTRATION_WAIT_SECONDS
    )
    while True:
        record = RunRegistry(_tasks_db_path()).get_run_by_execution_id(
            execution_id,
            owner=owner,
        )
        if record is not None:
            return record.spec.run_id
        if await request.is_disconnected():
            raise HTTPException(status_code=404, detail="resource not found")
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise HTTPException(status_code=404, detail="resource not found")
        await asyncio.sleep(
            min(EXECUTION_REGISTRATION_POLL_SECONDS, remaining)
        )


def _resolve_event_cursor(
    after_seq: int | None,
    last_event_id: str | None,
) -> int:
    """Resolve one bounded SSE cursor from query or resume headers."""
    if after_seq is not None:
        return after_seq
    if last_event_id is None or not last_event_id.strip():
        return 0
    try:
        cursor = int(last_event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="invalid event cursor"
        ) from exc
    if cursor < 0:
        raise HTTPException(status_code=400, detail="invalid event cursor")
    return cursor


def _sse_frame(
    *,
    event: str,
    data: dict[str, Any],
    event_id: int | None = None,
) -> str:
    """Serialize one public event as an SSE frame."""
    lines = [] if event_id is None else [f"id: {event_id}"]
    lines.extend(
        (
            f"event: {event}",
            "data: "
            + json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            "",
            "",
        )
    )
    return "\n".join(lines)


def _register_event_read_routes(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register the initial logs and bounded event read routes."""

    @app.get(
        "/v1/executions/{execution_id}/events",
        response_model=ExecutionEventCorrelationPageResponse,
    )
    async def get_execution_events(
        execution_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(
            default=DEFAULT_EXECUTION_EVENT_LIMITS.default_page_size,
            ge=1,
            le=DEFAULT_EXECUTION_EVENT_LIMITS.max_page_size,
        ),
    ) -> JSONResponse:
        """Resolve an owned public execution identity to its root ledger."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        run_id = _owned_execution_run_id(execution_id, owner)
        page = V1ExecutionCompatibilityReader(_tasks_db_path()).list_events(
            run_id,
            owner=owner,
            after_seq=after_seq,
            limit=limit,
        )
        if page is None:
            raise HTTPException(status_code=404, detail="resource not found")
        return JSONResponse(
            {
                "schema_version": 1,
                "execution_id": execution_id,
                "run_id": run_id,
                "items": [item.to_public_dict() for item in page.items],
                "next_after_seq": page.next_after_seq,
                "has_more": page.has_more,
            }
        )

    @app.get("/v1/runs/{run_id}/logs")
    async def get_run_logs(
        run_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        debug: bool = False,
    ) -> JSONResponse:
        """Return reconciled task logs for a run.

        Fetches the run to verify ownership, then reconciles logs for
        each task in the run. Default mode strips the raw handler
        payload from each task log; pass ``debug=true`` to include it.
        """
        del principal
        return JSONResponse(
            await dependencies.projection.reconcile_task_logs(
                run_id, resolve_debug(debug)
            )
        )

    @app.get(
        "/v1/runs/{run_id}/events",
        response_model=ExecutionEventPageResponse,
    )
    async def get_run_events(
        run_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(
            default=DEFAULT_EXECUTION_EVENT_LIMITS.default_page_size,
            ge=1,
            le=DEFAULT_EXECUTION_EVENT_LIMITS.max_page_size,
        ),
    ) -> JSONResponse:
        """Return one bounded, owner-scoped page of committed run events."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        page = V1ExecutionCompatibilityReader(_tasks_db_path()).list_events(
            run_id,
            owner=owner,
            after_seq=after_seq,
            limit=limit,
        )
        if page is None:
            raise HTTPException(status_code=404, detail="run not found")
        return JSONResponse(
            {
                "schema_version": 1,
                "run_id": run_id,
                "items": [item.to_public_dict() for item in page.items],
                "next_after_seq": page.next_after_seq,
                "has_more": page.has_more,
            }
        )

    @app.get(
        "/v1/runs/{run_id}/event-projection",
        response_model=RunEventProjectionV1,
    )
    async def get_run_event_projection(
        run_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Return the replaceable projection, rebuilding from history."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        projection = V1ExecutionCompatibilityReader(
            _tasks_db_path()
        ).get_projection(run_id, owner=owner)
        if projection is None:
            raise HTTPException(status_code=404, detail="run not found")
        return JSONResponse(projection.to_public_dict())


def _register_run_event_stream_route(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register the resumable run-event stream route."""

    @app.get("/v1/runs/{run_id}/events/stream")
    async def stream_run_events(
        run_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        after_seq: int | None = Query(default=None, ge=0),
        last_event_id: str | None = Header(
            default=None,
            alias="Last-Event-ID",
        ),
    ) -> StreamingResponse:
        """Drain committed events, then follow commits with resumable ids."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        cursor = _resolve_event_cursor(after_seq, last_event_id)
        store = V1ExecutionCompatibilityReader(_tasks_db_path())
        initial = store.list_events(
            run_id,
            owner=owner,
            after_seq=cursor,
            limit=1,
        )
        if initial is None:
            raise HTTPException(status_code=404, detail="run not found")

        async def follow() -> AsyncIterator[str]:
            current = cursor
            delivered = 0
            gap_reported = False
            idle_polls = 0
            while True:
                page = store.list_events(
                    run_id,
                    owner=owner,
                    after_seq=current,
                    limit=DEFAULT_EXECUTION_EVENT_LIMITS.max_page_size,
                )
                if page is None:
                    return
                if page.items:
                    idle_polls = 0
                    first_seq = page.items[0].seq
                    if first_seq > current + 1 and not gap_reported:
                        yield _sse_frame(
                            event="execution_gap",
                            data={
                                "schema_version": 1,
                                "run_id": run_id,
                                "after_seq": current,
                                "next_available_seq": first_seq,
                                "reason": "history_pruned_or_gap",
                            },
                        )
                        gap_reported = True
                    for item in page.items:
                        delivered += 1
                        if delivered > (
                            DEFAULT_EXECUTION_EVENT_LIMITS.max_live_backlog
                        ):
                            yield _sse_frame(
                                event="execution_gap",
                                data={
                                    "schema_version": 1,
                                    "run_id": run_id,
                                    "after_seq": current,
                                    "next_available_seq": item.seq,
                                    "reason": "backlog_exceeded",
                                },
                            )
                            return
                        current = item.seq
                        yield _sse_frame(
                            event="execution_event",
                            data=item.to_public_dict(),
                            event_id=item.seq,
                        )
                    continue
                projection = store.get_projection(run_id, owner=owner)
                if (
                    projection is None
                    or (
                        projection.terminal is not None
                        and current >= projection.latest_seq
                    )
                    or await request.is_disconnected()
                ):
                    return
                idle_polls += 1
                if idle_polls >= EXECUTION_EVENT_HEARTBEAT_POLL_TICKS:
                    yield ": heartbeat\n\n"
                    idle_polls = 0
                await asyncio.sleep(0.25)

        return StreamingResponse(
            follow(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/v1/runs/{run_id}/events/{event_id}",
        response_model=ExecutionEventV1,
    )
    async def get_run_event_detail(
        run_id: str,
        event_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Return one typed event only through its owner-visible parent run."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        event = V1ExecutionCompatibilityReader(_tasks_db_path()).get_event(
            run_id,
            event_id,
            owner=owner,
        )
        if event is None:
            raise HTTPException(status_code=404, detail="run not found")
        return JSONResponse(event.to_public_dict())


def _register_execution_projection_route(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register execution-addressed projection lookup."""

    @app.get(
        "/v1/executions/{execution_id}/event-projection",
        response_model=ExecutionEventCorrelationProjectionResponse,
    )
    async def get_execution_event_projection(
        execution_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Resolve one execution identity to its root event projection."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        run_id = _owned_execution_run_id(execution_id, owner)
        projection = V1ExecutionCompatibilityReader(
            _tasks_db_path()
        ).get_projection(run_id, owner=owner)
        if projection is None:
            raise HTTPException(status_code=404, detail="resource not found")
        return JSONResponse(
            {
                **projection.to_public_dict(),
                "execution_id": execution_id,
            }
        )


def _register_execution_event_stream_route(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register the resumable execution-addressed event stream route."""

    @app.get("/v1/executions/{execution_id}/events/stream")
    async def stream_execution_events(
        execution_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        after_seq: int | None = Query(default=None, ge=0),
        last_event_id: str | None = Header(
            default=None,
            alias="Last-Event-ID",
        ),
    ) -> StreamingResponse:
        """Follow the root ledger through a browser-known execution id."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        run_id = await _await_owned_execution_run_id(
            execution_id,
            owner,
            request,
        )
        cursor = _resolve_event_cursor(after_seq, last_event_id)
        store = V1ExecutionCompatibilityReader(_tasks_db_path())
        if (
            store.list_events(run_id, owner=owner, after_seq=cursor, limit=1)
            is None
        ):
            raise HTTPException(status_code=404, detail="resource not found")

        async def follow() -> AsyncIterator[str]:
            current = cursor
            delivered = 0
            gap_reported = False
            idle_polls = 0
            while True:
                page = store.list_events(
                    run_id,
                    owner=owner,
                    after_seq=current,
                    limit=DEFAULT_EXECUTION_EVENT_LIMITS.max_page_size,
                )
                if page is None:
                    return
                if page.items:
                    idle_polls = 0
                    first_seq = page.items[0].seq
                    if first_seq > current + 1 and not gap_reported:
                        yield _sse_frame(
                            event="execution_gap",
                            data={
                                "schema_version": 1,
                                "execution_id": execution_id,
                                "run_id": run_id,
                                "after_seq": current,
                                "next_available_seq": first_seq,
                                "reason": "history_pruned_or_gap",
                            },
                        )
                        gap_reported = True
                    for item in page.items:
                        delivered += 1
                        if delivered > (
                            DEFAULT_EXECUTION_EVENT_LIMITS.max_live_backlog
                        ):
                            yield _sse_frame(
                                event="execution_gap",
                                data={
                                    "schema_version": 1,
                                    "execution_id": execution_id,
                                    "run_id": run_id,
                                    "after_seq": current,
                                    "next_available_seq": item.seq,
                                    "reason": "backlog_exceeded",
                                },
                            )
                            return
                        current = item.seq
                        yield _sse_frame(
                            event="execution_event",
                            data=item.to_public_dict(),
                            event_id=item.seq,
                        )
                    continue
                projection = store.get_projection(run_id, owner=owner)
                if (
                    projection is None
                    or (
                        projection.terminal is not None
                        and current >= projection.latest_seq
                    )
                    or await request.is_disconnected()
                ):
                    return
                idle_polls += 1
                if idle_polls >= EXECUTION_EVENT_HEARTBEAT_POLL_TICKS:
                    yield ": heartbeat\n\n"
                    idle_polls = 0
                await asyncio.sleep(0.25)

        return StreamingResponse(
            follow(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


def _register_run_detail_routes(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register event detail and owner-scoped run lifecycle routes."""

    @app.get(
        "/v1/executions/{execution_id}/events/{event_id}",
        response_model=ExecutionEventV1,
    )
    async def get_execution_event_detail(
        execution_id: str,
        event_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Read one event through its owner-scoped execution binding."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        run_id = _owned_execution_run_id(execution_id, owner)
        event = V1ExecutionCompatibilityReader(_tasks_db_path()).get_event(
            run_id,
            event_id,
            owner=owner,
        )
        if event is None:
            raise HTTPException(status_code=404, detail="resource not found")
        return JSONResponse(event.to_public_dict())

    @app.get("/v1/runs/{run_id}")
    async def get_run(
        run_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        debug: bool = False,
    ) -> JSONResponse:
        """Return one owner-scoped run record by id.

        Default mode strips the raw handler payload from result;
        pass ``debug=true`` to include it.
        """
        del principal
        record = await dependencies.projection.fetch_owner_run(
            run_id, debug=resolve_debug(debug)
        )
        if not resolve_debug(debug) and isinstance(record.get("result"), dict):
            record = {
                **record,
                "result": strip_agent_result(record["result"]),
            }
        return JSONResponse(record)

    @app.post("/v1/runs/{run_id}/delivery/retry")
    async def retry_run_delivery(
        run_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
    ) -> JSONResponse:
        """Begin one owner-scoped archive delivery retry."""
        del principal
        return JSONResponse(
            await dependencies.projection.retry_owner_delivery(run_id)
        )

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        expected_revision: int | None = None,
    ) -> JSONResponse:
        """Cancel an owner-scoped Research run before remote dispatch."""
        owner = (
            principal.user_id
            or dependencies.context.current_user()
            or "anonymous"
        )
        callback = dependencies.projection.cancel_research_run
        if callback is None:
            body = await run_lifecycle.cancel_research_run(
                run_id,
                owner=owner,
                expected_revision=expected_revision,
                db_path=_tasks_db_path(),
            )
        else:
            body = await callback(
                run_id,
                owner=owner,
                expected_revision=expected_revision,
            )
        return JSONResponse(body)


def _register_status_routes(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register logs and single-run status routes in public order."""
    _register_event_read_routes(app, dependencies)
    _register_run_event_stream_route(app, dependencies)
    _register_execution_projection_route(app, dependencies)
    _register_execution_event_stream_route(app, dependencies)
    _register_run_detail_routes(app, dependencies)


def _register_pause_routes(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register A2UI action and Review resume routes."""

    @app.post("/v1/runs/{run_id}/a2ui-actions")
    async def post_a2ui_action(
        run_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        debug: bool = False,
    ) -> JSONResponse:
        """Resume a paused Chat A2UI run from a Web action envelope."""
        del principal
        try:
            body = await read_a2ui_action_request(request)
        except A2uiPayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except A2uiPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response_body, status_code = await dependencies.pause.resume_a2ui(
            run_id=run_id,
            body=body,
            debug=resolve_debug(debug),
        )
        try:
            ensure_a2ui_response_size(
                response_body,
                max_bytes=dependencies.pause.a2ui_max_response_bytes(),
            )
        except A2uiPayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except A2uiPayloadError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse(response_body, status_code=status_code)

    @app.post("/v1/runs/{thread_id}/resume")
    async def resume_run(
        thread_id: str,
        body: ResumeRequest,
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        debug: bool = False,
    ) -> JSONResponse:
        """Resume a paused ReviewAgent run by LangGraph thread id."""
        del principal
        response_body, status_code = await dependencies.pause.resume_review(
            thread_id=thread_id,
            payload=body,
            debug=resolve_debug(debug),
        )
        return JSONResponse(response_body, status_code=status_code)


def _register_list_route(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register owner-scoped run listing and service delegation."""

    @app.get("/v1/runs")
    async def list_runs(
        filters: RunFilterQuery = Depends(_build_run_filter_query),
        paging: RunPagingQuery = Depends(_build_run_paging_query),
        principal: ApiPrincipal = Depends(dependencies.auth.require_agents),
        authorization: str | None = Header(default=None),
        x_service_token: str | None = Header(
            default=None, alias="X-Service-Token"
        ),
    ) -> JSONResponse:
        """List runs with owner-only or service-token-delegated scoping.

        Without ``user_id`` the route returns the authenticated user's
        runs only. With ``user_id`` it requires a valid service token
        in addition to the user key, then scopes the listing to that
        user instead of the caller — the path Phytomni-Web Go uses to
        render history pages for any tenant. Acts as the lazy GC
        trigger via ``_purge_expired_runs_best_effort``.

        ``dialogue_id`` runs as a server-side ``WHERE`` predicate
        before ``limit`` / ``offset`` so a chat-ai history query for
        one dialogue always retrieves every matching row regardless
        of the caller's total run count.

        Default mode strips the raw handler payload from each result;
        pass ``debug=true`` to include it.
        """
        del principal
        is_service = dependencies.context.service_token_valid(
            authorization, x_service_token
        )
        if filters["user_id"] is not None and not is_service:
            raise HTTPException(
                status_code=403,
                detail=("user_id query parameter requires the service token"),
            )
        owner = (
            filters["user_id"]
            if filters["user_id"] is not None
            else (dependencies.context.current_user() or "anonymous")
        )
        body = dependencies.projection.list_owner_runs(
            RunListRequest(
                owner=owner,
                filters=RunListFilter(
                    status=filters["status"],
                    agent=filters["agent"],
                    origin=filters["origin"],
                    dialogue_id=filters["dialogue_id"],
                    user_id=filters["user_id"],
                ),
                paging=RunListPaging(
                    created_after=paging["created_after"],
                    created_before=paging["created_before"],
                    limit=paging["limit"],
                    offset=paging["offset"],
                    debug=resolve_debug(paging["debug"]),
                ),
            )
        )
        if not resolve_debug(paging["debug"]):
            data = body.get("data")
            if isinstance(data, list):
                body = {
                    **body,
                    "data": [
                        dependencies.projection.strip_run_result(item)
                        for item in data
                    ],
                }
        return JSONResponse(body)


def register_run_routes(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register run status, pause, and list routes in legacy order."""
    _register_status_routes(app, dependencies)
    _register_pause_routes(app, dependencies)
    _register_list_route(app, dependencies)


__all__ = [
    "RunAuthDependencies",
    "RunContextDependencies",
    "RunPauseDependencies",
    "RunProjectionDependencies",
    "RunRouteDependencies",
    "RunListFilter",
    "RunListPaging",
    "RunListRequest",
    "RunFilterQuery",
    "RunPagingQuery",
    "register_run_routes",
]
