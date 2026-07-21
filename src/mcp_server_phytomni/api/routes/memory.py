# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Authenticated user-memory route registration."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import ValidationError

from ...runtime.memory import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryPolicyError,
    MemoryStore,
    MemoryStoreError,
    memory_audit_context,
)
from ..schemas import (
    MemoryAuditListResponse,
    MemoryAuditRecordResponse,
    MemoryCreateRequest,
    MemoryDeleteResponse,
    MemoryExportResponse,
    MemoryListResponse,
    MemoryResponse,
    MemoryUpdateRequest,
)


@dataclass(frozen=True)
class MemoryAuthDependencies:
    """Authentication dependencies shared by memory routes."""

    require_agents: Callable[..., Any]
    require_service: Callable[..., Any]


@dataclass(frozen=True)
class MemoryContextDependencies:
    """Request identity accessors used by memory persistence."""

    current_user: Callable[[], str | None]
    current_request_id: Callable[[], str | None]


@dataclass(frozen=True)
class MemoryProjectionDependencies:
    """Application-owned memory conversion and validation seams."""

    memory_write: Callable[[str, Any], Any]
    memory_response: Callable[[Any], MemoryResponse]
    memory_audit_response: Callable[[Any], MemoryAuditRecordResponse]
    memory_revision: Callable[..., int | None]


@dataclass(frozen=True)
class MemoryRouteDependencies:
    """Explicit dependencies required to register memory routes."""

    get_store: Callable[[], MemoryStore]
    auth: MemoryAuthDependencies
    context: MemoryContextDependencies
    projection: MemoryProjectionDependencies


def _owner(dependencies: MemoryRouteDependencies) -> str:
    """Return the authenticated owner or fail closed."""
    owner = dependencies.context.current_user()
    if not owner:
        raise HTTPException(status_code=401, detail="user context missing")
    return owner


def _register_collection_routes(
    app: FastAPI,
    dependencies: MemoryRouteDependencies,
) -> None:
    """Register create, list, and export endpoints in public order."""

    @app.post(
        "/v1/memories",
        status_code=201,
        response_model=MemoryResponse,
    )
    async def create_memory(
        payload: MemoryCreateRequest,
        principal: Any = Depends(dependencies.auth.require_agents),
    ) -> MemoryResponse:
        """Create one memory in the authenticated user's namespace."""
        del principal
        owner = _owner(dependencies)
        write = dependencies.projection.memory_write(owner, payload)
        try:
            with memory_audit_context(
                owner, dependencies.context.current_request_id()
            ):
                record = dependencies.get_store().create(write)
        except MemoryPolicyError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="invalid memory"
            ) from exc
        except (MemoryStoreError, OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503, detail="memory store unavailable"
            ) from exc
        return dependencies.projection.memory_response(record)

    @app.get("/v1/memories", response_model=MemoryListResponse)
    async def list_memories(
        kind: str | None = None,
        limit: int | None = None,
        principal: Any = Depends(dependencies.auth.require_agents),
    ) -> MemoryListResponse:
        """List live memories owned by the authenticated user."""
        del principal
        owner = _owner(dependencies)
        try:
            records = dependencies.get_store().list(
                owner, kind=kind, limit=limit
            )
        except MemoryPolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="invalid memory query"
            ) from exc
        except (MemoryStoreError, OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503, detail="memory store unavailable"
            ) from exc
        return MemoryListResponse(
            data=[
                dependencies.projection.memory_response(record)
                for record in records
            ]
        )

    @app.get("/v1/memories/export", response_model=MemoryExportResponse)
    async def export_memories(
        principal: Any = Depends(dependencies.auth.require_agents),
    ) -> MemoryExportResponse:
        """Export live memories owned by the authenticated user."""
        del principal
        owner = _owner(dependencies)
        try:
            records = dependencies.get_store().export(owner)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="invalid memory export"
            ) from exc
        except (MemoryStoreError, OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503, detail="memory store unavailable"
            ) from exc
        return MemoryExportResponse(
            data=[
                dependencies.projection.memory_response(record)
                for record in records
            ]
        )


def _register_audit_route(
    app: FastAPI,
    dependencies: MemoryRouteDependencies,
) -> None:
    """Register the service-only digest audit endpoint."""

    @app.get("/v1/memories/audit", response_model=MemoryAuditListResponse)
    async def list_memory_audit(
        user_id: str | None = None,
        operation: str | None = None,
        memory_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        _admin: Any = Depends(dependencies.auth.require_service),
    ) -> MemoryAuditListResponse:
        """List digest-only memory mutations for service operators."""
        del _admin
        try:
            records = dependencies.get_store().list_audit(
                user_id=user_id,
                operation=operation,
                memory_id=memory_id,
                limit=limit,
                offset=offset,
            )
        except (MemoryPolicyError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (MemoryStoreError, OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503, detail="memory store unavailable"
            ) from exc
        return MemoryAuditListResponse(
            data=[
                dependencies.projection.memory_audit_response(record)
                for record in records
            ]
        )


def _register_item_routes(
    app: FastAPI,
    dependencies: MemoryRouteDependencies,
) -> None:
    """Register get, update, and delete endpoints for one memory."""

    @app.get("/v1/memories/{memory_id}", response_model=MemoryResponse)
    async def get_memory(
        memory_id: str,
        principal: Any = Depends(dependencies.auth.require_agents),
    ) -> MemoryResponse:
        """Return one live memory only when it belongs to the caller."""
        del principal
        owner = _owner(dependencies)
        try:
            record = dependencies.get_store().get(owner, memory_id)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="invalid memory id"
            ) from exc
        except (MemoryStoreError, OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503, detail="memory store unavailable"
            ) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return dependencies.projection.memory_response(record)

    @app.put("/v1/memories/{memory_id}", response_model=MemoryResponse)
    async def update_memory(
        memory_id: str,
        payload: MemoryUpdateRequest,
        if_match: str | None = Header(default=None, alias="If-Match"),
        principal: Any = Depends(dependencies.auth.require_agents),
    ) -> MemoryResponse:
        """Replace one memory when ``If-Match`` is its current revision."""
        del principal
        owner = _owner(dependencies)
        expected_revision = dependencies.projection.memory_revision(
            if_match, required=True
        )
        assert expected_revision is not None
        write = dependencies.projection.memory_write(owner, payload)
        try:
            with memory_audit_context(
                owner, dependencies.context.current_request_id()
            ):
                record = dependencies.get_store().update(
                    owner,
                    memory_id,
                    write,
                    expected_revision=expected_revision,
                )
        except MemoryNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="memory not found"
            ) from exc
        except MemoryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MemoryPolicyError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="invalid memory id"
            ) from exc
        except (MemoryStoreError, OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503, detail="memory store unavailable"
            ) from exc
        return dependencies.projection.memory_response(record)

    @app.delete(
        "/v1/memories/{memory_id}", response_model=MemoryDeleteResponse
    )
    async def delete_memory(
        memory_id: str,
        if_match: str | None = Header(default=None, alias="If-Match"),
        principal: Any = Depends(dependencies.auth.require_agents),
    ) -> MemoryDeleteResponse:
        """Delete one caller-owned memory, idempotently."""
        del principal
        owner = _owner(dependencies)
        expected_revision = dependencies.projection.memory_revision(
            if_match, required=False
        )
        try:
            with memory_audit_context(
                owner, dependencies.context.current_request_id()
            ):
                deleted = dependencies.get_store().delete(
                    owner,
                    memory_id,
                    expected_revision=expected_revision,
                )
        except MemoryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="invalid memory id"
            ) from exc
        except (MemoryStoreError, OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=503, detail="memory store unavailable"
            ) from exc
        return MemoryDeleteResponse(id=memory_id, deleted=deleted)


def register_memory_routes(
    app: FastAPI,
    dependencies: MemoryRouteDependencies,
) -> None:
    """Register memory routes in the legacy public order."""
    _register_collection_routes(app, dependencies)
    _register_audit_route(app, dependencies)
    _register_item_routes(app, dependencies)


__all__ = [
    "MemoryAuthDependencies",
    "MemoryContextDependencies",
    "MemoryProjectionDependencies",
    "MemoryRouteDependencies",
    "register_memory_routes",
]
