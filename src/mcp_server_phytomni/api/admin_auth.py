# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Service-token authentication for /v1/api-keys management routes.

Functions: require_service_principal.

The service token is read from ``ApiConfig.API_SERVICE_TOKEN`` and grants
the holder authority to mint, list, and revoke per-user ``ptm_...`` API
keys. It is intentionally separate from ``ApiKeyStore`` so a leaked or
compromised per-user key cannot escalate to issuance scope.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from ..config.defaults import ApiConfig

__all__ = ["is_service_token_valid", "require_service_principal"]

_SERVICE_TOKEN_HEADERS = {"WWW-Authenticate": "Bearer"}


def _extract_service_token(
    authorization: str | None, x_service_token: str | None
) -> str | None:
    """Pull the service token from Bearer or X-Service-Token headers."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer ") :].strip()
        return token or None
    if x_service_token:
        token = x_service_token.strip()
        return token or None
    return None


def is_service_token_valid(
    authorization: str | None, x_service_token: str | None
) -> bool:
    """Return True when the headers carry the configured service token.

    Soft variant of ``require_service_principal``: it never raises so a
    route can fall back to user-key auth when the headers do not match.
    Used by endpoints that accept either credential (e.g. delegated
    ``GET /v1/runs?user_id=`` queries that elevate to admin scope when
    the service token is present).

    The check prefers ``X-Service-Token`` over ``Authorization: Bearer``
    so a caller who already carries a user-key Bearer header can still
    elevate by passing the service token in the dedicated header. The
    admin-only routes (``/v1/api-keys/*``) still go through
    ``require_service_principal`` which accepts either header
    interchangeably because there is no user-key path to confuse them
    with.

    Args:
        authorization: Raw ``Authorization`` header value, if any.
        x_service_token: Alternative ``X-Service-Token`` header value.

    Returns:
        True when ``API_SERVICE_TOKEN`` is configured and the
        presented token matches it; False otherwise.
    """
    configured = ApiConfig().API_SERVICE_TOKEN
    if configured is None:
        return False
    if x_service_token:
        presented = x_service_token.strip() or None
    else:
        presented = _extract_service_token(authorization, x_service_token)
    if presented is None:
        return False
    return secrets.compare_digest(presented, configured.get_secret_value())


async def require_service_principal(
    authorization: str | None = Header(default=None),
    x_service_token: str | None = Header(
        default=None, alias="X-Service-Token"
    ),
) -> None:
    """FastAPI dependency that gates a route on the configured service token.

    Returns silently when the presented token matches. Raises 503 when
    ops has not configured ``API_SERVICE_TOKEN`` so a fresh deployment
    fails closed instead of silently exposing key issuance. Raises 401
    when the token is absent or wrong. The comparison uses
    ``secrets.compare_digest`` to resist timing analysis.

    Args:
        authorization: Bearer authorization header value, if any.
        x_service_token: Alternative ``X-Service-Token`` header value.

    Raises:
        HTTPException: 503 when no service token is configured; 401 when
            the presented token is missing or does not match.
    """
    configured = ApiConfig().API_SERVICE_TOKEN
    if configured is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "/v1/api-keys/* admin path is not enabled "
                "(no service token configured)"
            ),
        )
    presented = _extract_service_token(authorization, x_service_token)
    if presented is None:
        raise HTTPException(
            status_code=401,
            detail="Missing service token",
            headers=dict(_SERVICE_TOKEN_HEADERS),
        )
    if not secrets.compare_digest(presented, configured.get_secret_value()):
        raise HTTPException(
            status_code=401,
            detail="Invalid service token",
            headers=dict(_SERVICE_TOKEN_HEADERS),
        )
