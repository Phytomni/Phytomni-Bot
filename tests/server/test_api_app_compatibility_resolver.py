# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Application compatibility and resolver contract tests."""

from __future__ import annotations

import inspect
import os
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from tests.server.test_api_app_compatibility import (
    _DEFAULT_ROUTES,
    _OPENAPI_HASH,
    _REAL_ASYNC_REQUEST,
    _all_flag_routes,
    _normalized_openapi,
    _openapi_hash,
    _original_lifespan_name,
    _ResolverContract,
    _route_manifest,
)

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
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


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
    assert len(document["paths"]) == 64
    assert len(document["components"]["schemas"]) == 58
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
        "PHYTOMNI_A2A_ENABLED",
        "PHYTOMNI_RELAY_ENABLED",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv(
        "PHYTOMNI_A2A_PUBLIC_BASE_URL", "https://compat.example"
    )

    app = create_app()

    assert _route_manifest(app) == _all_flag_routes()
    assert len(app.openapi()["paths"]) == 70
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
