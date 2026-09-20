# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Compatibility contracts for the FastAPI application factory.

These tests are intentionally literal: route order, public methods, status
codes, response models, and authorization seams are the HTTP contract that
route extraction must preserve.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from tests.server.upload_route_contract import build_upload_route_contracts
from tests.support.handler_fakes import RunningResponseKwargs

from mcp_server_phytomni.api.lifecycle_contract import (
    build_agent_run_response,
    empty_agent_result,
)

pytestmark = pytest.mark.server

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request
# Generated from ``_openapi_hash(create_app())`` after the intentional public
# schema additions in 7321656 (locale), dd99f82 (dataset uploads), bcf20b6
# (attachment capabilities), the strict Expert request boundary, the private
# native conversation envelope, resumable attachment references, the
# purpose-constrained upload contract in fd19dcbb, the unified attachment
# contract in 97867888, the always-on conversation-context mutation
# routes, and the always-on interop capabilities route. The
# owner-scoped private execution-target content route is also part of this
# literal public surface. The
# ``_normalized_openapi`` helper removes only unstable version/server
# fields.
_OPENAPI_HASH = (
    "dda354c483e062faeddc909b20cdda258f5829e742ec7903229ff3deddc6c5bc"
)


def test_persisted_running_response_accepts_empty_child_list() -> None:
    """Persisted running responses may expose no child tasks yet."""
    response_kwargs: RunningResponseKwargs = {
        "run_id": "run-empty-child",
        "agent": "analyst",
        "status": "running",
        "task_ids": [],
        "result": empty_agent_result(),
        "persisted": True,
        "degraded_tracking": False,
    }
    body = build_agent_run_response(**response_kwargs)

    assert body["id"] == body["run_id"] == "run-empty-child"
    assert not body["task_ids"]
    assert "degraded_tracking" not in body


@dataclass(frozen=True, slots=True)
class _RouteContract:
    """Stable, client-visible fields for one registered route."""

    path: str
    methods: tuple[str, ...]
    status: int
    response: str | None
    scopes: tuple[str, ...]
    name: str


@dataclass(frozen=True, slots=True)
class _ResolverContract:
    """Direct resolver-module contract for one native agent."""

    agent: str
    flag: str
    resolver_name: str
    result: SimpleNamespace
    target: tuple[str, str]
    metadata: dict[str, str]


def _route_response_name(route: APIRoute) -> str | None:
    """Return a stable response-model name for a route."""
    model = route.response_model
    if model is None:
        return None
    if getattr(model, "__origin__", None) is not None:
        return str(model).replace("typing.", "")
    return getattr(model, "__name__", str(model).replace("typing.", ""))


def _dependency_scopes(route: APIRoute) -> tuple[str, ...]:
    """Extract semantic auth requirements from FastAPI dependencies."""
    labels: set[str] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        call = dependency.call
        if call is None:
            continue
        if not inspect.isfunction(call):
            pending.extend(dependency.dependencies)
            continue
        name = getattr(call, "__name__", "")
        nonlocals = inspect.getclosurevars(call).nonlocals
        if name == "_scoped":
            labels.update(nonlocals.get("needed", ()))
        elif name == "_gated":
            service = nonlocals.get("service")
            if isinstance(service, str):
                labels.add(f"relay:{service}")
        elif name == "relay_enabled_guard":
            labels.add("relay_enabled")
        elif name == "require_service_principal":
            labels.add("service")
        pending.extend(dependency.dependencies)
    return tuple(sorted(labels))


def _route_manifest(app: FastAPI) -> tuple[_RouteContract, ...]:
    """Return the ordered public route manifest for ``app``."""

    def iter_routes(routes: list[Any]) -> list[APIRoute]:
        """Expand newer FastAPI/Starlette included-router wrappers."""
        expanded: list[APIRoute] = []
        for route in routes:
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                expanded.extend(iter_routes(original_router.routes))
            elif isinstance(route, APIRoute):
                expanded.append(route)
        return expanded

    return tuple(
        _RouteContract(
            path=route.path,
            methods=tuple(sorted(route.methods or ())),
            status=route.status_code or 200,
            response=_route_response_name(route),
            scopes=_dependency_scopes(route),
            name=route.name,
        )
        for route in iter_routes(app.routes)
    )


_route = _RouteContract


_DEFAULT_ROUTES = (
    _route(
        "/v1/interop/capabilities",
        ("GET",),
        200,
        None,
        ("agents",),
        "list_interop_capabilities",
    ),
    _route("/healthz", ("GET",), 200, "dict[str, str]", (), "healthz"),
    _route("/readyz", ("GET",), 200, None, (), "readyz"),
    _route("/v1/models", ("GET",), 200, None, ("agents",), "list_models"),
    _route(
        "/v1/api-keys",
        ("POST",),
        201,
        "ApiKeyCreateResponse",
        ("service",),
        "issue_api_key",
    ),
    _route(
        "/v1/api-keys",
        ("GET",),
        200,
        "ApiKeyListResponse",
        ("service",),
        "list_api_keys",
    ),
    _route(
        "/v1/api-keys/{prefix}",
        ("DELETE",),
        200,
        "ApiKeyDeleteResponse",
        ("service",),
        "revoke_api_key",
    ),
    _route(
        "/v1/relay/audit",
        ("GET",),
        200,
        None,
        ("service",),
        "list_relay_audit",
    ),
    _route(
        "/v1/relay/audit/{request_id}",
        ("GET",),
        200,
        None,
        ("service",),
        "get_relay_audit",
    ),
    _route(
        "/v1/chat/completions",
        ("POST",),
        200,
        None,
        ("agents",),
        "chat_completions",
    ),
    _route("/v1/agents", ("GET",), 200, None, ("agents",), "list_agents"),
    _route(
        "/v1/agents/{agent}/runs",
        ("POST",),
        200,
        None,
        ("agents",),
        "create_agent_run",
    ),
    _route(
        "/v1/query/route",
        ("POST",),
        200,
        None,
        ("agents",),
        "route_query",
    ),
    *build_upload_route_contracts(_route),
    _route(
        "/v1/conversation-context/settle",
        ("POST",),
        200,
        "ContextMutationResponse",
        ("agents",),
        "settle_context",
    ),
    _route(
        "/v1/conversation-context/tombstone",
        ("POST",),
        200,
        "ContextMutationResponse",
        ("agents",),
        "tombstone_context",
    ),
    _route(
        "/v1/executions/{execution_id}/events",
        ("GET",),
        200,
        "ExecutionEventCorrelationPageResponse",
        ("agents",),
        "get_execution_events",
    ),
    _route(
        "/v1/runs/{run_id}/logs",
        ("GET",),
        200,
        None,
        ("agents",),
        "get_run_logs",
    ),
    _route(
        "/v1/runs/{run_id}/events",
        ("GET",),
        200,
        "ExecutionEventPageResponse",
        ("agents",),
        "get_run_events",
    ),
    _route(
        "/v1/runs/{run_id}/event-projection",
        ("GET",),
        200,
        "RunEventProjectionV1",
        ("agents",),
        "get_run_event_projection",
    ),
    _route(
        "/v1/runs/{run_id}/events/stream",
        ("GET",),
        200,
        None,
        ("agents",),
        "stream_run_events",
    ),
    _route(
        "/v1/runs/{run_id}/events/{event_id}",
        ("GET",),
        200,
        "ExecutionEventV1",
        ("agents",),
        "get_run_event_detail",
    ),
    _route(
        "/v1/executions/{execution_id}/event-projection",
        ("GET",),
        200,
        "ExecutionEventCorrelationProjectionResponse",
        ("agents",),
        "get_execution_event_projection",
    ),
    _route(
        "/v1/executions/{execution_id}/events/stream",
        ("GET",),
        200,
        None,
        ("agents",),
        "stream_execution_events",
    ),
    _route(
        "/v1/executions/{execution_id}/events/{event_id}",
        ("GET",),
        200,
        "ExecutionEventV1",
        ("agents",),
        "get_execution_event_detail",
    ),
    _route(
        "/v1/runs/{run_id}",
        ("GET",),
        200,
        None,
        ("agents",),
        "get_run",
    ),
    _route(
        "/v1/runs/{run_id}/delivery/retry",
        ("POST",),
        200,
        None,
        ("agents",),
        "retry_run_delivery",
    ),
    _route(
        "/v1/runs/{run_id}/cancel",
        ("POST",),
        200,
        None,
        ("agents",),
        "cancel_run",
    ),
    _route(
        "/v1/runs/{run_id}/a2ui-actions",
        ("POST",),
        200,
        None,
        ("agents",),
        "post_a2ui_action",
    ),
    _route(
        "/v1/runs/{thread_id}/resume",
        ("POST",),
        200,
        None,
        ("agents",),
        "resume_run",
    ),
    _route(
        "/v1/runs",
        ("GET",),
        200,
        None,
        ("agents",),
        "list_runs",
    ),
    _route(
        "/v2/executions",
        ("POST",),
        202,
        None,
        ("service",),
        "admit_execution",
    ),
    _route(
        "/v2/executions/{execution_id}",
        ("GET",),
        200,
        None,
        ("service",),
        "get_execution_snapshot",
    ),
    _route(
        "/v2/executions/{execution_id}/events",
        ("GET",),
        200,
        None,
        ("service",),
        "get_execution_events",
    ),
    _route(
        "/v2/executions/{execution_id}/events/stream",
        ("GET",),
        200,
        None,
        ("service",),
        "stream_execution_events",
    ),
    _route(
        "/v2/executions/{execution_id}/events/{event_id}",
        ("GET",),
        200,
        None,
        ("service",),
        "get_execution_event_detail",
    ),
    _route(
        "/v2/executions/{execution_id}/operations/{operation_id}",
        ("GET",),
        200,
        None,
        ("service",),
        "get_execution_operation_detail",
    ),
    _route(
        "/v2/executions/{execution_id}/targets/{kind}/{target_id}",
        ("GET",),
        200,
        None,
        ("service",),
        "get_execution_target",
    ),
    _route(
        "/v2/executions/{execution_id}/targets/{kind}/{target_id}/content",
        ("GET",),
        200,
        None,
        ("service",),
        "get_execution_target_content",
    ),
    _route(
        "/v2/executions/{execution_id}/actions",
        ("POST",),
        200,
        None,
        ("service",),
        "post_execution_action",
    ),
    _route(
        "/v2/executions/{execution_id}/cancel",
        ("POST",),
        200,
        None,
        ("service",),
        "cancel_execution",
    ),
    _route(
        "/v1/relay/healthz",
        ("GET",),
        200,
        "dict[str, str]",
        ("relay_enabled",),
        "relay_healthz",
    ),
    _route(
        "/v1/relay/capabilities",
        ("GET",),
        200,
        "dict[str, dict[str, list[int] | int]]",
        ("relay:research-input", "relay_enabled"),
        "research_input_capabilities",
    ),
    _route(
        "/v1/relay/research-input/object-grants",
        ("POST",),
        200,
        "dict[str, object]",
        ("relay:research-input", "relay_enabled"),
        "resolve_research_object_grants",
    ),
    _route(
        "/v1/relay/research-input/object-grants/verify",
        ("POST",),
        200,
        "dict[str, object]",
        ("relay:research-input", "relay_enabled"),
        "verify_research_object_grants",
    ),
    _route(
        "/v1/relay/research-input/object-grants/revoke",
        ("POST",),
        200,
        "dict[str, int]",
        ("relay:research-input", "relay_enabled"),
        "revoke_research_object_grants",
    ),
    _route(
        "/v1/relay/llm/chat/completions",
        ("POST",),
        200,
        None,
        ("relay:llm", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/coder/chat/completions",
        ("POST",),
        200,
        None,
        ("relay:coder", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/embed/embeddings",
        ("POST",),
        200,
        None,
        ("relay:embed", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/retrieve/search",
        ("POST",),
        200,
        None,
        ("relay:retrieve", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/rerank/rank",
        ("POST",),
        200,
        None,
        ("relay:rerank", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/database/nl2sql",
        ("POST",),
        200,
        None,
        ("relay:database", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/analysis/tasks",
        ("POST",),
        200,
        None,
        ("relay:analysis", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/bi/query",
        ("POST",),
        200,
        None,
        ("relay:bi", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/analysis/{task_id}",
        ("GET",),
        200,
        None,
        ("relay:analysis", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/analysis/{task_id}/logs",
        ("GET",),
        200,
        None,
        ("relay:analysis", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/analysis/{task_id}/terminate",
        ("POST",),
        200,
        None,
        ("relay:analysis", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/spa-faq/{repo_id}",
        ("GET",),
        200,
        None,
        ("relay:spa-faq", "relay_enabled"),
        "_handler",
    ),
    _route(
        "/v1/relay/obs/object",
        ("PUT",),
        200,
        None,
        ("relay:obs", "relay_enabled"),
        "_put_object",
    ),
    _route(
        "/v1/relay/obs/object",
        ("GET",),
        200,
        None,
        ("relay:obs", "relay_enabled"),
        "_get_object",
    ),
    _route(
        "/v1/relay/obs/list",
        ("GET",),
        200,
        None,
        ("relay:obs", "relay_enabled"),
        "_list_objects",
    ),
    _route(
        "/v1/relay/obs/dir",
        ("PUT",),
        200,
        None,
        ("relay:obs", "relay_enabled"),
        "_put_dir",
    ),
)

_MEMORY_ROUTES = (
    _route(
        "/v1/memories",
        ("POST",),
        201,
        "MemoryResponse",
        ("agents",),
        "create_memory",
    ),
    _route(
        "/v1/memories",
        ("GET",),
        200,
        "MemoryListResponse",
        ("agents",),
        "list_memories",
    ),
    _route(
        "/v1/memories/export",
        ("GET",),
        200,
        "MemoryExportResponse",
        ("agents",),
        "export_memories",
    ),
    _route(
        "/v1/memories/audit",
        ("GET",),
        200,
        "MemoryAuditListResponse",
        ("service",),
        "list_memory_audit",
    ),
    _route(
        "/v1/memories/{memory_id}",
        ("GET",),
        200,
        "MemoryResponse",
        ("agents",),
        "get_memory",
    ),
    _route(
        "/v1/memories/{memory_id}",
        ("PUT",),
        200,
        "MemoryResponse",
        ("agents",),
        "update_memory",
    ),
    _route(
        "/v1/memories/{memory_id}",
        ("DELETE",),
        200,
        "MemoryDeleteResponse",
        ("agents",),
        "delete_memory",
    ),
)

_A2A_ROUTES = (
    _route(
        "/.well-known/agent-card.json",
        ("GET",),
        200,
        None,
        (),
        "a2a_agent_card",
    ),
    _route("/a2a", ("POST",), 200, None, ("agents",), "a2a_jsonrpc"),
)


def _all_flag_routes() -> tuple[_RouteContract, ...]:
    """Build the expected order when every optional surface is enabled."""
    return (
        _DEFAULT_ROUTES[:4]
        + _MEMORY_ROUTES
        + _DEFAULT_ROUTES[4:46]
        + _A2A_ROUTES
        + _DEFAULT_ROUTES[46:]
    )


def _normalized_openapi(app: FastAPI) -> dict[str, Any]:
    """Remove only documented unstable OpenAPI metadata."""
    document = copy.deepcopy(app.openapi())
    info = document.get("info")
    if isinstance(info, dict):
        info.pop("version", None)
    document.pop("servers", None)
    return document


def _openapi_hash(app: FastAPI) -> str:
    """Hash canonical OpenAPI JSON after the minimal normalization."""
    payload = json.dumps(
        _normalized_openapi(app), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _original_lifespan_name(app: FastAPI) -> str | None:
    """Find the application lifespan inside FastAPI's merged wrapper."""
    current: Any = app.router.lifespan_context
    for _ in range(3):
        if not callable(current):
            return None
        nonlocals = inspect.getclosurevars(current).nonlocals
        original = nonlocals.get("original_context")
        if callable(original):
            return getattr(original, "__name__", None)
        current = nonlocals.get("func")
    return None
