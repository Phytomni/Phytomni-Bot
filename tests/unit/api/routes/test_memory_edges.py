# pylint: disable=too-few-public-methods, duplicate-code
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Edge coverage for authenticated memory route failure mapping."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

from mcp_server_phytomni.api.routes import memory
from mcp_server_phytomni.api.schemas import (
    MemoryAuditRecordResponse,
    MemoryCreateRequest,
    MemoryResponse,
    MemoryUpdateRequest,
)
from mcp_server_phytomni.runtime.memory import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryPolicyError,
    MemoryStore,
    MemoryStoreError,
)

pytestmark = pytest.mark.unit


class _RaisingStore:
    """Store double that raises one configured error from every method."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def create(self, _write: object) -> object:
        """Raise the configured create failure."""
        raise self.error

    def list(self, _owner: str, **_kwargs: object) -> Sequence[object]:
        """Raise the configured list failure."""
        raise self.error

    def export(self, _owner: str) -> Sequence[object]:
        """Raise the configured export failure."""
        raise self.error

    def list_audit(self, **_kwargs: object) -> Sequence[object]:
        """Raise the configured audit failure."""
        raise self.error

    def get(self, _owner: str, _memory_id: str) -> object | None:
        """Raise the configured get failure."""
        raise self.error

    def update(self, *_args: object, **_kwargs: object) -> object:
        """Raise the configured update failure."""
        raise self.error

    def delete(self, *_args: object, **_kwargs: object) -> bool:
        """Raise the configured delete failure."""
        raise self.error


class _EmptyGetStore:
    """Store double whose get reports a missing live record."""

    def get(self, _owner: str, _memory_id: str) -> None:
        """Return no record so the route can map a 404."""
        return None


def _validation_error() -> ValidationError:
    """Build one Pydantic validation error for route mapping tests."""
    with pytest.raises(ValidationError) as captured:
        MemoryCreateRequest.model_validate({"kind": "", "content": "x"})
    return captured.value


def _dependencies(
    store: object,
    *,
    current_user: Callable[[], str | None] = lambda: "tenant-a",
) -> memory.MemoryRouteDependencies:
    """Build isolated memory dependencies around one store double."""
    return memory.MemoryRouteDependencies(
        get_store=lambda: cast(MemoryStore, store),
        auth=memory.MemoryAuthDependencies(
            require_agents=lambda: None,
            require_service=lambda: None,
        ),
        context=memory.MemoryContextDependencies(
            current_user=current_user,
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


def _app(store: object, **kwargs: Any) -> FastAPI:
    """Register memory routes against one isolated FastAPI app."""
    app = FastAPI()
    memory.register_memory_routes(app, _dependencies(store, **kwargs))
    return app


def _endpoint(app: FastAPI, path: str, method: str) -> Any:
    """Return one registered route callable by path and method."""
    return next(
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method in (route.methods or set())
    )


async def test_owner_fails_closed_without_user_context() -> None:
    """Every user-scoped route rejects a missing authenticated owner."""
    app = _app(
        _RaisingStore(MemoryStoreError("unused")),
        current_user=lambda: None,
    )
    create = _endpoint(app, "/v1/memories", "POST")
    with pytest.raises(HTTPException) as captured:
        await create(MemoryCreateRequest(kind="note", content="hello"))
    assert captured.value.status_code == 401
    assert captured.value.detail == "user context missing"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MemoryPolicyError("too large"), 413),
        (ValueError("bad write"), 400),
        (MemoryStoreError("down"), 503),
        (OSError("io"), 503),
        (sqlite3.Error("sql"), 503),
    ],
)
async def test_create_maps_store_failures(
    error: BaseException,
    status: int,
) -> None:
    """Create maps policy, validation, and store failures to HTTP codes."""
    create = _endpoint(_app(_RaisingStore(error)), "/v1/memories", "POST")
    with pytest.raises(HTTPException) as captured:
        await create(MemoryCreateRequest(kind="note", content="hello"))
    assert captured.value.status_code == status


async def test_create_maps_validation_error() -> None:
    """A Pydantic validation failure from the store is a 400."""
    create = _endpoint(
        _app(_RaisingStore(_validation_error())),
        "/v1/memories",
        "POST",
    )
    with pytest.raises(HTTPException) as captured:
        await create(MemoryCreateRequest(kind="note", content="hello"))
    assert captured.value.status_code == 400
    assert captured.value.detail == "invalid memory"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MemoryPolicyError("bad query"), 400),
        (ValueError("bad filter"), 400),
        (MemoryStoreError("down"), 503),
    ],
)
async def test_list_maps_store_failures(
    error: BaseException,
    status: int,
) -> None:
    """List maps policy, validation, and store failures to HTTP codes."""
    list_memories = _endpoint(
        _app(_RaisingStore(error)), "/v1/memories", "GET"
    )
    with pytest.raises(HTTPException) as captured:
        await list_memories(kind=None, limit=None)
    assert captured.value.status_code == status


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ValueError("bad export"), 400, "invalid memory export"),
        (MemoryStoreError("down"), 503, "memory store unavailable"),
    ],
)
async def test_export_maps_store_failures(
    error: BaseException,
    status: int,
    detail: str,
) -> None:
    """Export maps validation and store failures without leaking paths."""
    export = _endpoint(
        _app(_RaisingStore(error)),
        "/v1/memories/export",
        "GET",
    )
    with pytest.raises(HTTPException) as captured:
        await export()
    assert captured.value.status_code == status
    assert captured.value.detail == detail


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MemoryPolicyError("bad audit"), 400),
        (ValueError("bad filter"), 400),
        (MemoryStoreError("down"), 503),
    ],
)
async def test_audit_maps_store_failures(
    error: BaseException,
    status: int,
) -> None:
    """Audit maps policy, validation, and store failures to HTTP codes."""
    audit = _endpoint(_app(_RaisingStore(error)), "/v1/memories/audit", "GET")
    with pytest.raises(HTTPException) as captured:
        await audit()
    assert captured.value.status_code == status


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ValueError("bad id"), 400, "invalid memory id"),
        (MemoryStoreError("down"), 503, "memory store unavailable"),
    ],
)
async def test_get_maps_store_failures(
    error: BaseException,
    status: int,
    detail: str,
) -> None:
    """Get maps invalid identifiers and store outages."""
    get_memory = _endpoint(
        _app(_RaisingStore(error)),
        "/v1/memories/{memory_id}",
        "GET",
    )
    with pytest.raises(HTTPException) as captured:
        await get_memory("mem-1")
    assert captured.value.status_code == status
    assert captured.value.detail == detail


async def test_get_maps_missing_record() -> None:
    """A live miss is a 404 rather than an empty body."""
    get_memory = _endpoint(
        _app(_EmptyGetStore()),
        "/v1/memories/{memory_id}",
        "GET",
    )
    with pytest.raises(HTTPException) as captured:
        await get_memory("mem-missing")
    assert captured.value.status_code == 404
    assert captured.value.detail == "memory not found"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MemoryNotFoundError("gone"), 404),
        (MemoryConflictError("stale"), 409),
        (MemoryPolicyError("too large"), 413),
        (ValueError("bad id"), 400),
        (MemoryStoreError("down"), 503),
    ],
)
async def test_update_maps_store_failures(
    error: BaseException,
    status: int,
) -> None:
    """Update maps not-found, conflict, policy, and store failures."""
    update = _endpoint(
        _app(_RaisingStore(error)),
        "/v1/memories/{memory_id}",
        "PUT",
    )
    with pytest.raises(HTTPException) as captured:
        await update(
            "mem-1",
            MemoryUpdateRequest(kind="note", content="next"),
        )
    assert captured.value.status_code == status


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (MemoryConflictError("stale"), 409),
        (ValueError("bad id"), 400),
        (MemoryStoreError("down"), 503),
    ],
)
async def test_delete_maps_store_failures(
    error: BaseException,
    status: int,
) -> None:
    """Delete maps conflict, validation, and store failures."""
    delete = _endpoint(
        _app(_RaisingStore(error)),
        "/v1/memories/{memory_id}",
        "DELETE",
    )
    with pytest.raises(HTTPException) as captured:
        await delete("mem-1")
    assert captured.value.status_code == status
