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
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute

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
from mcp_server_phytomni.api import run_lifecycle as lifecycle_module
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.server

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request
_OPENAPI_HASH = (
    "881e33fda651ceacc95126e91f58e9f9cf7bc15ad4290b21586c5d8d4d78c762"
)


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
    return tuple(
        _RouteContract(
            path=route.path,
            methods=tuple(sorted(route.methods or ())),
            status=route.status_code or 200,
            response=_route_response_name(route),
            scopes=_dependency_scopes(route),
            name=route.name,
        )
        for route in app.routes
        if isinstance(route, APIRoute)
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
    _route(
        "/v1/files",
        ("POST",),
        201,
        "FileUploadResponse",
        ("agents",),
        "upload_file",
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
        + _DEFAULT_ROUTES[3:18]
        + _A2A_ROUTES
        + _DEFAULT_ROUTES[18:]
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
    ) == ("request_context_middleware",)
    assert _original_lifespan_name(app) == "_http_lifespan"
    document = _normalized_openapi(app)
    assert _openapi_hash(app) == _OPENAPI_HASH
    assert len(document["paths"]) == 33
    assert len(document["components"]["schemas"]) == 14
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
    assert len(app.openapi()["paths"]) == 40
    assert _original_lifespan_name(app) == "_http_lifespan"


def _error_code(response: httpx.Response) -> int:
    """Return the unified numeric error code from an HTTP response."""
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
    assert _error_code(unauthenticated) == 401

    RunRegistry(tasks_db_path).create_run(
        RunSpec("foreign-run", "other-user", "chat", "local"),
        outcome=RunOutcome(status="succeeded", result={"answer": "hidden"}),
    )
    foreign = await api_client.get(
        "/v1/runs/foreign-run",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert foreign.status_code == 404
    assert _error_code(foreign) == 404

    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    relay = await api_client.post(
        "/v1/relay/llm/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={},
    )
    assert relay.status_code == 403
    assert _error_code(relay) == 403

    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "1")
    malformed_a2ui = await api_client.post(
        "/v1/runs/missing/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        content=b"{",
    )
    assert malformed_a2ui.status_code == 400
    assert _error_code(malformed_a2ui) == 400


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
    assert _error_code(response) == 400


def test_application_routes_keep_shared_mcp_seams() -> None:
    """Ensure HTTP handlers still delegate through shared MCP functions."""
    source = inspect.getsource(api_app_module.create_app)
    run_source = inspect.getsource(
        getattr(api_app_module, "_invoke_agent_run")
    )
    for seam in (
        "invoke_tool_enveloped",
        "invoke_tool_streamed",
        "_invoke_agent_run",
        "_route_expert_query",
    ):
        assert seam in source
    assert "apply_runs_resolver" in run_source
    assert "resolve_chat_query" in source
    assert "_maybe_resolve_brief_gene_query" not in source
    assert "_maybe_resolve_brief_gene_query" not in run_source
    assert "_apply_runs_resolver" not in run_source


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


@pytest.mark.parametrize(
    "status",
    ("running", "input_required", "succeeded", "failed"),
)
def test_run_lifecycle_projection_preserves_statuses(
    tmp_path: Any, status: str
) -> None:
    """The extracted projection keeps every public registry status."""
    db_path = str(tmp_path / "runs.db")
    RunRegistry(db_path).create_run(
        RunSpec("run-status", "alice", "chat", "local"),
        outcome=RunOutcome(
            status=status,
            result={"formatted": {"answer": "answer"}},
        ),
    )
    record = RunRegistry(db_path).get_run("run-status", owner="alice")
    assert record is not None
    projected = lifecycle_module.project_public_run_record(
        record, db_path=db_path
    )
    assert projected["status"] == status
    assert projected["answer"] == "answer"


def test_run_lifecycle_stream_settlement_and_owner_scope(
    tmp_path: Any,
) -> None:
    """Streaming settlement preserves request info and owner isolation."""
    db_path = str(tmp_path / "runs.db")
    request_info = RunRequestInfo(
        dialogue_id="dialogue-1",
        query="plant height",
        tool_name="ChatAgent",
        model="phyto-chat",
    )
    lifecycle_module.create_running_stream_run(
        "run-stream",
        "chat",
        "alice",
        request_info,
        db_path=db_path,
    )
    lifecycle_module.stamp_remote_request_info(
        run_id="run-stream",
        owner="alice",
        request_info=request_info,
        db_path=db_path,
    )
    purges: list[bool] = []
    lifecycle_module.settle_stream_run(
        "run-stream",
        "bob",
        "failed",
        {"error": "foreign"},
        context=lifecycle_module.RunLifecycleContext(
            db_path=db_path,
            purge=lambda: purges.append(True),
        ),
    )
    untouched = RunRegistry(db_path).get_run("run-stream", owner="alice")
    assert untouched is not None
    assert untouched.status == "running"
    lifecycle_module.settle_stream_run(
        "run-stream",
        "alice",
        "succeeded",
        {"answer": "done"},
        context=lifecycle_module.RunLifecycleContext(
            db_path=db_path,
            purge=lambda: purges.append(True),
        ),
    )
    settled = RunRegistry(db_path).get_run("run-stream", owner="alice")
    assert settled is not None
    assert settled.status == "succeeded"
    assert settled.result == {"answer": "done"}
    assert settled.request_info.dialogue_id == "dialogue-1"
    assert len(purges) == 2


async def test_run_lifecycle_owner_lookup_and_task_log_projection(
    tmp_path: Any,
) -> None:
    """Missing/foreign owners fail closed and logs honor debug stripping."""
    db_path = str(tmp_path / "runs.db")
    RunRegistry(db_path).create_run(
        RunSpec("run-logs", "alice", "analyst", "remote"),
        outcome=RunOutcome(status="succeeded"),
    )
    public = await lifecycle_module.fetch_owner_run(
        "run-logs", owner="alice", db_path=db_path
    )
    assert public["run_id"] == "run-logs"
    with pytest.raises(HTTPException) as foreign:
        await lifecycle_module.fetch_owner_run(
            "run-logs", owner="bob", db_path=db_path
        )
    assert foreign.value.status_code == 404
    with pytest.raises(HTTPException) as missing:
        await lifecycle_module.fetch_owner_run(
            "missing", owner="alice", db_path=db_path
        )
    assert missing.value.status_code == 404

    async def fetch(_run_id: str) -> dict[str, Any]:
        return {"task_ids": ["task-1"]}

    async def reconcile(_task_id: str) -> dict[str, Any]:
        return {"formatted": {"answer": "ok"}, "raw": {"secret": True}}

    def strip(result: dict[str, Any]) -> dict[str, Any]:
        return {"formatted": result["formatted"]}

    public_logs = await lifecycle_module.reconcile_run_task_logs(
        "run-logs",
        False,
        fetch=fetch,
        reconcile=reconcile,
        strip=strip,
    )
    debug_logs = await lifecycle_module.reconcile_run_task_logs(
        "run-logs",
        True,
        fetch=fetch,
        reconcile=reconcile,
        strip=strip,
    )
    assert public_logs["task_logs"] == [{"formatted": {"answer": "ok"}}]
    assert debug_logs["task_logs"][0]["raw"] == {"secret": True}


def test_run_lifecycle_gc_claim_release() -> None:
    """The extracted GC slot coalesces and releases process-local work."""
    assert lifecycle_module.claim_run_gc() is True
    try:
        assert lifecycle_module.claim_run_gc() is False
    finally:
        lifecycle_module.release_run_gc()
    assert lifecycle_module.claim_run_gc() is True
    lifecycle_module.release_run_gc()
