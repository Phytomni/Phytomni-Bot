# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contracts for HTTP route registration units."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api import routes as route_package
from mcp_server_phytomni.api.routes import admin, memory, runs
from mcp_server_phytomni.api.schemas import (
    MemoryAuditRecordResponse,
    MemoryResponse,
)

pytestmark = pytest.mark.unit


def _routes(app: FastAPI) -> list[APIRoute]:
    """Return only application routes, preserving registration order."""
    return [route for route in app.routes if isinstance(route, APIRoute)]


def _path_methods(app: FastAPI) -> list[tuple[str, tuple[str, ...]]]:
    """Return a compact public route manifest for one isolated app."""
    return [
        (route.path, tuple(sorted(route.methods or ())))
        for route in _routes(app)
    ]


def _noop() -> None:
    """Stand in for a FastAPI dependency in isolated registrations."""


def _service_token_invalid(
    _authorization: str | None,
    _service_token: str | None,
) -> bool:
    """Stand in for a rejecting service-token validator."""
    return False


async def _empty_run_dict(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Return an empty run projection for registration-only tests."""
    return {}


async def _empty_resume(
    **_kwargs: Any,
) -> tuple[dict[str, Any], int]:
    """Return an empty pause response for registration-only tests."""
    return {}, 200


@dataclass
class _MemoryStore:
    """Tiny store double that makes singleton access observable."""

    list_calls: int = 0

    def list(
        self,
        _owner: str,
        *,
        kind: str | None,
        limit: int | None,
    ) -> list[Any]:
        """Return no records while recording the accessor invocation."""
        del kind, limit
        self.list_calls += 1
        return []


def _memory_dependencies(
    agent_guard: Callable[..., Any],
    service_guard: Callable[..., Any],
    get_store: Callable[[], Any],
) -> memory.MemoryRouteDependencies:
    """Build deterministic memory dependencies for registration tests."""
    return memory.MemoryRouteDependencies(
        get_store=get_store,
        auth=memory.MemoryAuthDependencies(
            require_agents=agent_guard,
            require_service=service_guard,
        ),
        context=memory.MemoryContextDependencies(
            current_user=lambda: "tenant-a",
            current_request_id=lambda: "request-a",
        ),
        projection=memory.MemoryProjectionDependencies(
            memory_write=lambda _owner, payload: payload,
            memory_response=lambda record: MemoryResponse.model_validate(
                record.model_dump()
            ),
            memory_audit_response=(
                lambda record: MemoryAuditRecordResponse.model_validate(
                    record.model_dump()
                )
            ),
            memory_revision=lambda _value, *, required: (
                1 if required else None
            ),
        ),
    )


@pytest.mark.asyncio
async def test_memory_registration_isolated_and_reuses_store() -> None:
    """Memory routes register once and use one injected store accessor."""
    app = FastAPI()
    agent_guard = _noop
    service_guard = _noop
    store = _MemoryStore()
    accesses: list[Any] = []

    def get_store() -> _MemoryStore:
        accesses.append(store)
        return store

    memory.register_memory_routes(
        app,
        _memory_dependencies(agent_guard, service_guard, get_store),
    )

    assert _path_methods(app) == [
        ("/v1/memories", ("POST",)),
        ("/v1/memories", ("GET",)),
        ("/v1/memories/export", ("GET",)),
        ("/v1/memories/audit", ("GET",)),
        ("/v1/memories/{memory_id}", ("GET",)),
        ("/v1/memories/{memory_id}", ("PUT",)),
        ("/v1/memories/{memory_id}", ("DELETE",)),
    ]
    assert len(set(_path_methods(app))) == 7
    assert _routes(app)[0].response_model is MemoryResponse
    assert _routes(app)[3].response_model.__name__ == "MemoryAuditListResponse"
    assert _routes(app)[0].dependant.dependencies[0].call is agent_guard
    assert _routes(app)[3].dependant.dependencies[0].call is service_guard

    list_route = next(
        route
        for route in _routes(app)
        if route.path == "/v1/memories" and route.methods == {"GET"}
    )
    await list_route.endpoint()
    await list_route.endpoint()
    assert accesses == [store, store]
    assert store.list_calls == 2


def test_admin_and_run_registration_preserve_public_contract() -> None:
    """Admin and run units expose each legacy path exactly once."""
    app = FastAPI()
    service_guard = _noop
    agent_guard = _noop

    admin.register_admin_routes(
        app,
        admin.AdminRouteDependencies(
            require_service=service_guard,
            audit_record_to_dict=lambda _record, _config: {},
        ),
    )
    runs.register_run_routes(
        app,
        runs.RunRouteDependencies(
            auth=runs.RunAuthDependencies(require_agents=agent_guard),
            context=runs.RunContextDependencies(
                current_user=lambda: "tenant-a",
                service_token_valid=_service_token_invalid,
            ),
            projection=runs.RunProjectionDependencies(
                reconcile_task_logs=_empty_run_dict,
                fetch_owner_run=_empty_run_dict,
                retry_owner_delivery=_empty_run_dict,
                list_owner_runs=lambda _request: {"data": []},
                strip_run_result=lambda record: record,
            ),
            pause=runs.RunPauseDependencies(
                a2ui_max_response_bytes=lambda: 1024,
                resume_a2ui=_empty_resume,
                resume_review=_empty_resume,
            ),
        ),
    )

    manifest = _path_methods(app)
    assert manifest == [
        ("/v1/api-keys", ("POST",)),
        ("/v1/api-keys", ("GET",)),
        ("/v1/api-keys/{prefix}", ("DELETE",)),
        ("/v1/relay/audit", ("GET",)),
        ("/v1/relay/audit/{request_id}", ("GET",)),
        ("/v1/runs/{run_id}/stream", ("GET",)),
        ("/v1/runs/{run_id}/logs", ("GET",)),
        ("/v1/runs/{run_id}", ("GET",)),
        ("/v1/runs/{run_id}/delivery/retry", ("POST",)),
        ("/v1/runs/{run_id}/cancel", ("POST",)),
        ("/v1/runs/{run_id}/a2ui-actions", ("POST",)),
        ("/v1/runs/{thread_id}/resume", ("POST",)),
        ("/v1/runs", ("GET",)),
    ]
    assert all(
        any(
            dependency.call is service_guard
            for dependency in route.dependant.dependencies
        )
        for route in _routes(app)[:5]
    )
    assert all(
        any(
            dependency.call is agent_guard
            for dependency in route.dependant.dependencies
        )
        for route in _routes(app)[5:]
    )


def test_feature_flag_off_omits_memory_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The application factory still omits memory routes when disabled."""
    monkeypatch.setenv("PHYTOMNI_MEMORY_ENABLED", "0")
    monkeypatch.delenv("MEMORY_ENABLED", raising=False)
    app = api_app_module.create_app()
    assert not any(
        route.path.startswith("/v1/memories") for route in _routes(app)
    )


def test_route_package_exports_only_registration_units() -> None:
    """The package surface remains explicit and does not leak app helpers."""
    assert route_package.__doc__
    assert set(route_package.__all__) == set()
    assert admin.__all__ == [
        "AdminRouteDependencies",
        "RelayAuditFilterQuery",
        "RelayAuditPagingQuery",
        "register_admin_routes",
    ]
    assert "register_memory_routes" in memory.__all__
    assert "register_run_routes" in runs.__all__
