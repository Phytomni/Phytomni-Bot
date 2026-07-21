# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Run status, history, and pause-action route registration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ...mcp.result_formatting import resolve_debug, strip_agent_result
from ..a2ui_limits import (
    A2uiPayloadError,
    A2uiPayloadTooLargeError,
    ensure_a2ui_response_size,
    read_a2ui_action_request,
)
from ..auth import ApiPrincipal
from ..schemas import ResumeRequest
from . import _paging_values


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
    list_owner_runs: Callable[..., dict[str, Any]]
    strip_run_result: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class RunPauseDependencies:
    """A2UI and Review pause/resume seams."""

    a2ui_enabled: Callable[[], bool]
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


def _register_status_routes(
    app: FastAPI,
    dependencies: RunRouteDependencies,
) -> None:
    """Register logs and single-run status routes in public order."""

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
        if not dependencies.pause.a2ui_enabled():
            raise HTTPException(status_code=403, detail="a2ui disabled")
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
            owner=owner,
            status=filters["status"],
            agent=filters["agent"],
            origin=filters["origin"],
            dialogue_id=filters["dialogue_id"],
            created_after=paging["created_after"],
            created_before=paging["created_before"],
            limit=paging["limit"],
            offset=paging["offset"],
            debug=resolve_debug(paging["debug"]),
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
    "RunFilterQuery",
    "RunPagingQuery",
    "register_run_routes",
]
