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
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from tests.server.upload_route_contract import build_upload_route_contracts
from tests.support.handler_fakes import RunningResponseKwargs

from mcp_server_phytomni.agents.brief_gene.resolve_query import (
    BriefGeneResolveError,
)
from mcp_server_phytomni.agents.deep_genome.resolve_query import (
    DeepGenomeResolveError,
)
from mcp_server_phytomni.agents.design.resolve_query import (
    DigitalDesignResolveError,
)
from mcp_server_phytomni.agents.network.resolve_query import (
    GeneNetworkResolveError,
)
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api import resolvers as resolver_module
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.lifecycle_contract import (
    build_agent_run_response,
    empty_agent_result,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request
# Generated from ``_openapi_hash(create_app())`` after the intentional public
# schema additions in 7321656 (locale), dd99f82 (dataset uploads), bcf20b6
# (attachment capabilities), the strict Expert request boundary, the private
# native conversation envelope, and resumable attachment references. The
# ``_normalized_openapi`` helper removes only unstable version/server fields.
_OPENAPI_HASH = (
    "3e18fb2ccf76f5dd10b366cb77f04bd90be9a05a7d56c381fc39726ba66afa67"
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
        "/v1/runs/{run_id}/logs",
        ("GET",),
        200,
        None,
        ("agents",),
        "get_run_logs",
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
        "/v1/relay/healthz",
        ("GET",),
        200,
        "dict[str, str]",
        ("relay_enabled",),
        "relay_healthz",
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

_INTEROP_ROUTE = _route(
    "/v1/interop/capabilities",
    ("GET",),
    200,
    None,
    ("agents",),
    "list_interop_capabilities",
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
        (_INTEROP_ROUTE,)
        + _DEFAULT_ROUTES[:3]
        + _MEMORY_ROUTES
        + _DEFAULT_ROUTES[3:23]
        + _A2A_ROUTES
        + _DEFAULT_ROUTES[23:]
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


def test_default_application_contract_is_literal() -> None:
    """Lock default routes, middleware, lifespan, and OpenAPI identity."""
    app = create_app()

    assert _route_manifest(app) == _DEFAULT_ROUTES
    assert tuple(
        getattr(item.cls, "__name__", "") for item in app.user_middleware
    ) == ("CORSMiddleware", "request_context_middleware")
    assert _original_lifespan_name(app) == "_http_lifespan"
    document = _normalized_openapi(app)
    if os.environ.get("PHYTOMNI_DEPENDENCY_FLOOR") != "1":
        assert _openapi_hash(app) == _OPENAPI_HASH
    assert len(document["paths"]) == 37
    assert len(document["components"]["schemas"]) == 26
    assert all(
        operation.get("operationId")
        for path_item in document["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "responses" in operation
    )


def test_optional_application_contract_is_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock every feature-flag route and its scope boundary."""
    for name in (
        "PHYTOMNI_MEMORY_ENABLED",
        "PHYTOMNI_INTEROP_ENABLED",
        "PHYTOMNI_A2A_ENABLED",
        "PHYTOMNI_A2UI_ENABLED",
        "PHYTOMNI_RELAY_ENABLED",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv(
        "PHYTOMNI_A2A_PUBLIC_BASE_URL", "https://compat.example"
    )

    app = create_app()

    assert _route_manifest(app) == _all_flag_routes()
    assert len(app.openapi()["paths"]) == 44
    assert _original_lifespan_name(app) == "_http_lifespan"


def _error_code(response: httpx.Response) -> str:
    """Return the unified safe string error code from an HTTP response."""
    body = response.json()
    assert set(body) >= {"error"}
    assert isinstance(body["error"], dict)
    return body["error"]["code"]


async def test_auth_owner_and_disabled_scope_boundaries(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock unauthenticated, wrong-scope, and foreign-owner failures."""
    unauthenticated = await api_client.get("/v1/models")
    assert unauthenticated.status_code == 401
    assert _error_code(unauthenticated) == "unauthenticated"
    assert unauthenticated.json()["error"] == {
        "code": "unauthenticated",
        "message": "authentication required",
        "request_id": unauthenticated.json()["error"]["request_id"],
        "retryable": False,
    }

    RunRegistry(tasks_db_path).create_run(
        RunSpec("foreign-run", "other-user", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result={"answer": "hidden"}),
    )
    foreign = await api_client.get(
        "/v1/runs/foreign-run",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert foreign.status_code == 404
    assert _error_code(foreign) == "not_found"
    assert foreign.json()["error"]["message"] == "resource not found"
    assert foreign.json()["error"]["retryable"] is False

    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    relay = await api_client.post(
        "/v1/relay/llm/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={},
    )
    assert relay.status_code == 403
    assert _error_code(relay) == "forbidden"
    assert relay.json()["error"]["message"] == "request is not permitted"
    assert relay.json()["error"]["retryable"] is False

    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "1")
    malformed_a2ui = await api_client.post(
        "/v1/runs/missing/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        content=b"{",
    )
    assert malformed_a2ui.status_code == 400
    assert _error_code(malformed_a2ui) == "invalid_argument"
    assert malformed_a2ui.json()["error"]["message"] == "invalid request"
    assert malformed_a2ui.json()["error"]["retryable"] is False


async def test_a2a_rejects_missing_protocol_version(
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock A2A transport validation before JSON-RPC dispatch."""
    monkeypatch.setenv("PHYTOMNI_A2A_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_A2A_PUBLIC_BASE_URL", "https://compat.example"
    )
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        response = await client.post(
            "/a2a",
            headers={"Authorization": f"Bearer {issued_api_key}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "GetTask"},
        )
    assert response.status_code == 400
    assert _error_code(response) == "invalid_argument"
    assert response.json()["error"]["message"] == (
        "A2A-Version must be exactly 1.0"
    )
    assert response.json()["error"]["retryable"] is False


def test_application_routes_keep_shared_mcp_seams() -> None:
    """Ensure HTTP handlers still delegate through shared MCP functions."""
    source = inspect.getsource(api_app_module.create_app)
    run_source = inspect.getsource(
        getattr(api_app_module, "_invoke_agent_run")
    )
    prepare_source = inspect.getsource(
        getattr(api_app_module, "_prepare_agent_run")
    )
    for seam in (
        "invoke_tool_enveloped",
        "invoke_tool_streamed",
        "_invoke_agent_run",
        "_route_expert_query",
    ):
        assert seam in source
    assert "apply_runs_resolver" in prepare_source
    assert "resolve_chat_query" in source
    assert "_maybe_resolve_brief_gene_query" not in source
    assert "_maybe_resolve_brief_gene_query" not in run_source
    assert "_apply_runs_resolver" not in run_source


def test_native_run_facade_preserves_signature_and_module_identity() -> None:
    """Keep the app-level native-run seam's callable metadata stable."""
    invoke = getattr(api_app_module, "_invoke_agent_run")
    signature = inspect.signature(invoke)
    assert tuple(signature.parameters) == (
        "agent",
        "arguments",
        "conversation_messages",
        "agent_thread_id",
        "private_agent_state",
        "dialogue_id",
        "request_json",
        "debug",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.return_annotation == "tuple[dict[str, Any], int]"
    assert invoke.__module__ == api_app_module.__name__
    assert invoke.__qualname__ == "_invoke_agent_run"
    annotations = invoke.__annotations__
    expected_annotations = (
        ("agent", "str"),
        ("arguments", "dict[str, Any]"),
        ("conversation_messages", "tuple[dict[str, str], ...]"),
        ("agent_thread_id", "str | None"),
        ("private_agent_state", "Mapping[str, Any] | None"),
        ("dialogue_id", "str | None"),
        ("request_json", "str | None"),
        ("debug", "bool"),
        ("return", "tuple[dict[str, Any], int]"),
    )
    assert tuple(annotations) == tuple(
        name for name, _value in expected_annotations
    )
    assert all(
        annotations[name] == value for name, value in expected_annotations
    )


def test_native_run_facade_binds_arguments_before_returning_coroutine() -> (
    None
):
    """Keep the old async facade's immediate argument errors."""
    invoke = getattr(api_app_module, "_invoke_agent_run")
    with pytest.raises(TypeError):
        invoke(arguments={})
    with pytest.raises(TypeError):
        invoke(agent="chat", arguments={}, unexpected=True)
    with pytest.raises(TypeError):
        getattr(invoke, "__call__")("chat", {})

    coroutine = invoke(agent="chat", arguments={})
    assert inspect.iscoroutine(coroutine)
    coroutine.close()


def test_native_run_preflight_uses_app_compatibility_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moved preflight keeps request-context and attachment seams patchable."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        api_app_module, "current_request_user", lambda: "compat-owner"
    )
    monkeypatch.setattr(
        api_app_module, "current_request_id", lambda: "compat-request"
    )
    monkeypatch.setattr(
        api_app_module,
        "resolve_tasks_db_path",
        lambda: "compat-db",
    )

    def capture_attachments(*args: Any, **kwargs: Any) -> None:
        """Capture the compatibility seam's owner and database inputs."""
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        api_app_module,
        "validate_native_attachments",
        capture_attachments,
    )
    preflight = getattr(api_app_module, "_preflight_agent_run")(
        agent="chat",
        arguments={"user_query": "compat query"},
        dialogue_id="compat-dialogue",
        request_json=None,
    )

    assert preflight.owner == "compat-owner"
    assert preflight.request_info.request_id == "compat-request"
    assert captured["args"] == ("chat", {"user_query": "compat query"})
    assert captured["kwargs"] == {
        "owner": "compat-owner",
        "db_path": "compat-db",
    }


@pytest.mark.parametrize(
    "case",
    (
        _ResolverContract(
            "brief_gene",
            "resolve_gene_id",
            "resolve_brief_gene_user_query",
            SimpleNamespace(gene_id="AT1G01010", species_code="ath"),
            ("user_query", "AT1G01010"),
            {"resolved_gene_id": "AT1G01010"},
        ),
        _ResolverContract(
            "deep_genome",
            "resolve_gene_id",
            "resolve_deep_genome_user_query",
            SimpleNamespace(gene_id="Os01g0177400", species_code="osa"),
            ("gene_id", "Os01g0177400"),
            {"resolved_gene_id": "Os01g0177400"},
        ),
        _ResolverContract(
            "design",
            "resolve_gene_id",
            "resolve_design_user_query",
            SimpleNamespace(gene_id="AT1G01010", species_code="ath"),
            ("gene_id", "AT1G01010"),
            {"resolved_gene_id": "AT1G01010"},
        ),
        _ResolverContract(
            "network",
            "resolve_to_id",
            "resolve_network_user_query",
            SimpleNamespace(to_id="TO:0000207", species_code="osa"),
            ("to_id", "TO:0000207"),
            {"resolved_to_id": "TO:0000207"},
        ),
    ),
)
async def test_resolver_module_applies_native_contract(
    case: _ResolverContract,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each supported native agent gets one typed metadata rewrite."""
    calls: list[str] = []

    async def fake_resolver(raw_query: str, **_kwargs: Any) -> Any:
        """Return the typed-shaped fixture and retain the raw query."""
        calls.append(raw_query)
        return case.result

    monkeypatch.setattr(resolver_module, case.resolver_name, fake_resolver)
    arguments: dict[str, Any] = {
        "user_query": "original resolver text",
        case.flag: True,
    }
    resolved_metadata = await resolver_module.apply_runs_resolver(
        case.agent, arguments
    )

    assert calls == ["original resolver text"]
    assert arguments[case.target[0]] == case.target[1]
    assert "resolve_gene_id" not in arguments
    assert "resolve_to_id" not in arguments
    assert resolved_metadata["original_query"] == "original resolver text"
    assert (
        resolved_metadata["resolved_species_code"] == case.result.species_code
    )
    assert all(
        resolved_metadata[key] == value for key, value in case.metadata.items()
    )


async def test_resolver_module_chat_contract_and_false_flag() -> None:
    """Chat resolves only when opted in and leaves false flags untouched."""
    calls: list[str] = []

    async def fake_resolver(raw_query: str, **_kwargs: Any) -> Any:
        """Return one canonical BriefGene result."""
        calls.append(raw_query)
        return SimpleNamespace(gene_id="AT5G42800", species_code="ath")

    resolved, metadata = await resolver_module.resolve_chat_query(
        raw_query="find AT5G42800",
        resolve_flag=True,
        tool_name="BriefGeneAgent",
        brief_gene_resolver=fake_resolver,
    )
    assert resolved == "AT5G42800"
    assert metadata == {
        "original_query": "find AT5G42800",
        "resolved_gene_id": "AT5G42800",
        "resolved_species_code": "ath",
        "resolve_gene_id": True,
    }
    assert calls == ["find AT5G42800"]

    untouched, no_metadata = await resolver_module.resolve_chat_query(
        raw_query="structured brief gene input",
        resolve_flag=False,
        tool_name="ChatAgent",
    )
    assert untouched == "structured brief gene input"
    assert no_metadata == {}


async def test_resolver_module_rejects_missing_and_unsupported_flags() -> None:
    """Missing input and cross-agent flags fail before resolver dispatch."""
    missing: dict[str, Any] = {"resolve_gene_id": True}
    with pytest.raises(HTTPException) as missing_error:
        await resolver_module.apply_runs_resolver("brief_gene", missing)
    assert missing_error.value.status_code == 400
    assert "user_query is required" in str(missing_error.value.detail)
    assert not missing

    unsupported: dict[str, Any] = {
        "user_query": "plant height",
        "resolve_gene_id": True,
    }
    with pytest.raises(HTTPException) as unsupported_error:
        await resolver_module.apply_runs_resolver("network", unsupported)
    assert unsupported_error.value.status_code == 400
    assert "BriefGene / DeepGenome / DigitalDesign" in str(
        unsupported_error.value.detail
    )
    assert not unsupported

    with pytest.raises(HTTPException) as chat_error:
        await resolver_module.resolve_chat_query(
            raw_query="not a BriefGene call",
            resolve_flag=True,
            tool_name="ChatAgent",
        )
    assert chat_error.value.status_code == 400
    assert "only valid for BriefGene calls" in str(chat_error.value.detail)


@pytest.mark.parametrize(
    ("agent", "flag", "resolver_name", "error_type"),
    (
        (
            "brief_gene",
            "resolve_gene_id",
            "resolve_brief_gene_user_query",
            BriefGeneResolveError,
        ),
        (
            "deep_genome",
            "resolve_gene_id",
            "resolve_deep_genome_user_query",
            DeepGenomeResolveError,
        ),
        (
            "design",
            "resolve_gene_id",
            "resolve_design_user_query",
            DigitalDesignResolveError,
        ),
        (
            "network",
            "resolve_to_id",
            "resolve_network_user_query",
            GeneNetworkResolveError,
        ),
    ),
)
async def test_resolver_module_maps_domain_errors(
    agent: str,
    flag: str,
    resolver_name: str,
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Domain resolver failures remain explicit HTTP 400 responses."""

    async def fail_resolver(_raw_query: str, **_kwargs: Any) -> Any:
        """Raise the domain-specific resolver error."""
        raise error_type("resolver failed for contract test")

    monkeypatch.setattr(resolver_module, resolver_name, fail_resolver)
    arguments: dict[str, Any] = {
        "user_query": "ambiguous input",
        flag: True,
    }
    with pytest.raises(HTTPException) as error:
        await resolver_module.apply_runs_resolver(agent, arguments)
    assert error.value.status_code == 400
    assert "resolver failed for contract test" in str(error.value.detail)
