# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Service-token API-key and relay-audit route registration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from ...config.defaults import ApiConfig
from ..auth import get_key_store
from ..relay import RelayAuditQuery, get_audit_store
from ..schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyDeleteResponse,
    ApiKeyListResponse,
    ApiKeyRecordResponse,
)
from . import _paging_values


@dataclass(frozen=True)
class AdminRouteDependencies:
    """Explicit service-auth and audit projection seams."""

    require_service: Callable[..., Any]
    audit_record_to_dict: Callable[[Any, ApiConfig], dict[str, Any]]


class RelayAuditFilterQuery(TypedDict):
    """Filter query parameters for the service-only audit listing."""

    user_id: str | None
    key_prefix: str | None
    service: str | None
    status_code: int | None


class RelayAuditPagingQuery(TypedDict):
    """Paging query parameters for the service-only audit listing."""

    created_after: str | None
    created_before: str | None
    limit: int
    offset: int


# Keep the public query fields flat so the legacy OpenAPI document and
# coercion rules remain unchanged after route extraction.
async def _build_relay_audit_filter_query(
    *,
    user_id: str | None = None,
    key_prefix: str | None = None,
    service: str | None = None,
    status_code: int | None = None,
) -> RelayAuditFilterQuery:
    """Collect flat relay-audit filter fields asynchronously."""
    return {
        "user_id": user_id,
        "key_prefix": key_prefix,
        "service": service,
        "status_code": status_code,
    }


async def _build_relay_audit_paging_query(
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> RelayAuditPagingQuery:
    """Collect flat relay-audit paging fields asynchronously."""
    return cast(
        RelayAuditPagingQuery,
        _paging_values(created_after, created_before, limit, offset),
    )


def _register_key_routes(
    app: FastAPI,
    dependencies: AdminRouteDependencies,
) -> None:
    """Register API-key issuance, listing, and revocation routes."""

    @app.post(
        "/v1/api-keys",
        status_code=201,
        response_model=ApiKeyCreateResponse,
    )
    async def issue_api_key(
        payload: ApiKeyCreateRequest,
        _admin: Any = Depends(dependencies.require_service),
    ) -> ApiKeyCreateResponse:
        """Mint a per-user API key for the upstream service.

        The plaintext key is shown exactly once in the response. The
        service-token dependency is the only gate so a leaked user key
        cannot escalate to issuance.
        """
        del _admin
        expires_at: datetime | None = None
        if payload.expires_days is not None:
            expires_at = datetime.now(UTC) + timedelta(
                days=payload.expires_days
            )
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        created = store.create(
            user_id=payload.user_id,
            name=payload.name,
            expires_at=expires_at,
        )
        return ApiKeyCreateResponse(
            api_key=created.api_key,
            prefix=created.prefix,
            user_id=created.user_id,
            expires_at=expires_at.isoformat() if expires_at else None,
        )

    @app.get("/v1/api-keys", response_model=ApiKeyListResponse)
    async def list_api_keys(
        user_id: str | None = None,
        _admin: Any = Depends(dependencies.require_service),
    ) -> ApiKeyListResponse:
        """List per-user API keys; ``user_id`` filters to one user."""
        del _admin
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        records = store.list(user_id=user_id)
        return ApiKeyListResponse(
            data=[
                ApiKeyRecordResponse(
                    user_id=record.user_id,
                    name=record.name,
                    prefix=record.prefix,
                    created_at=record.created_at,
                    revoked_at=record.revoked_at,
                    last_used_at=record.last_used_at,
                    expires_at=record.expires_at,
                    active=record.active,
                    scopes=sorted(record.scopes),
                )
                for record in records
            ],
        )

    @app.delete(
        "/v1/api-keys/{prefix}",
        response_model=ApiKeyDeleteResponse,
    )
    async def revoke_api_key(
        prefix: str,
        _admin: Any = Depends(dependencies.require_service),
    ) -> ApiKeyDeleteResponse:
        """Revoke an active key by its public prefix."""
        del _admin
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        deleted = store.revoke(prefix)
        return ApiKeyDeleteResponse(prefix=prefix, deleted=deleted)


def _register_audit_routes(
    app: FastAPI,
    dependencies: AdminRouteDependencies,
) -> None:
    """Register service-token relay audit query routes."""

    @app.get("/v1/relay/audit")
    async def list_relay_audit(
        filters: RelayAuditFilterQuery = Depends(
            _build_relay_audit_filter_query
        ),
        paging: RelayAuditPagingQuery = Depends(
            _build_relay_audit_paging_query
        ),
        _admin: Any = Depends(dependencies.require_service),
    ) -> JSONResponse:
        """List relay audit records, gated by the service token.

        Only the service token may read the audit trail; a per-user
        ``ptm_...`` key alone cannot. The returned records carry no key
        hash, salt, or plaintext key, only the public ``key_prefix``.
        """
        del _admin
        config = ApiConfig()
        store = get_audit_store(config.RELAY_AUDIT_DB_PATH)
        records = store.query(
            RelayAuditQuery(
                user_id=filters["user_id"],
                key_prefix=filters["key_prefix"],
                service=filters["service"],
                status_code=filters["status_code"],
                created_after=paging["created_after"],
                created_before=paging["created_before"],
                limit=paging["limit"],
                offset=paging["offset"],
            )
        )
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    dependencies.audit_record_to_dict(record, config)
                    for record in records
                ],
            }
        )

    @app.get("/v1/relay/audit/{request_id}")
    async def get_relay_audit(
        request_id: str,
        _admin: Any = Depends(dependencies.require_service),
    ) -> JSONResponse:
        """Fetch relay audit records by request id, service-token gated."""
        del _admin
        config = ApiConfig()
        store = get_audit_store(config.RELAY_AUDIT_DB_PATH)
        records = store.get_by_request_id(request_id)
        return JSONResponse(
            {
                "object": "list",
                "request_id": request_id,
                "data": [
                    dependencies.audit_record_to_dict(record, config)
                    for record in records
                ],
            }
        )


def register_admin_routes(
    app: FastAPI,
    dependencies: AdminRouteDependencies,
) -> None:
    """Register API-key and relay-audit routes in legacy order."""
    _register_key_routes(app, dependencies)
    _register_audit_routes(app, dependencies)


__all__ = [
    "AdminRouteDependencies",
    "RelayAuditFilterQuery",
    "RelayAuditPagingQuery",
    "register_admin_routes",
]
