# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Upstream-facing /v1/relay router for the credential-injecting relay.

The router is always mounted; a per-request guard re-reads RELAY_ENABLED
so the surface is hidden by default and an operator can disable it
mid-incident without restarting a worker. Forwarding routes land in
later steps; this module currently exposes only the liveness probe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from ...auth.iam import get_token
from ...config.defaults import ApiConfig, DeepGenomeConfig
from ...config.settings import get_sensitive_config
from ..auth import ApiPrincipal, relay_scope_satisfied, require_principal
from ..ratelimit import make_rate_limiter
from .audit import get_audit_store
from .forward import (
    RelayErrorMode,
    RelayInjectionStrategy,
    RelayUpstream,
    forward_relay_request,
)

__all__ = [
    "create_relay_router",
    "relay_enabled_guard",
    "require_relay_access",
    "read_relay_body",
]

# OpenAI-family relay services: each fronts an upstream the operator
# reaches with an Authorization: Bearer key, with the SDK path appended
# to the configured base URL. The customer query string is never carried
# onto the operator-credentialed call (the upstream URL is config-only).
_OPENAI_RELAYS = (
    ("llm", "chat/completions", "BASE_URL", "API_KEY"),
    ("coder", "chat/completions", "CODER_URL", "CODER_API_KEY"),
    ("embed", "embeddings", "EMBED_URL", "EMBED_API_KEY"),
)

# Platform-family relay services (ENVELOPE mode): the configured URL is
# the POST target verbatim (no path appended). inject kind is one of
# "none" (upstream is unauthenticated), "iam" (X-Auth-Token via
# get_token, with the optional region attr), or "bi" (static BI_TOKEN).
_PLATFORM_RELAYS = (
    ("retrieve", "search", "RETRIEVE_URL", "none", None),
    ("rerank", "rank", "RERANK_URL", "none", None),
    ("database", "nl2sql", "DATABASE_URL", "iam", None),
    ("analysis", "tasks", "ANALYSIS_URL", "iam", "ANALYSIS_REGION"),
    ("bi", "query", "BI_URL", "bi", None),
    ("task", "create", "CREATE_TASK_URL", "none", None),
    ("task", "update", "UPDATE_TASK_URL", "none", None),
)


async def _relay_no_inject() -> dict[str, str]:
    """Inject no operator credential (the upstream is unauthenticated)."""
    return {}


def _build_platform_inject(
    kind: str, region: Optional[str]
) -> RelayInjectionStrategy:
    """Build the per-service injection strategy for a platform route.

    ``none`` injects nothing, ``bi`` injects the static BI_TOKEN under the
    lowercase ``token`` header, and ``iam`` mints an IAM ``X-Auth-Token``
    via ``get_token`` (with the service's region when one applies).
    """
    if kind == "none":
        return _relay_no_inject
    if kind == "bi":

        async def _bi_inject() -> dict[str, str]:
            token = get_sensitive_config().BI_TOKEN.get_secret_value()
            return {"token": token}

        return _bi_inject

    async def _iam_inject() -> dict[str, str]:
        token = await get_token(region=region) if region else await get_token()
        return {"X-Auth-Token": token}

    return _iam_inject


# One relay-specific limiter per worker, kept separate from the agent
# budget so relay calls (which spend the operator's metered upstream
# credentials) have their own cost ceiling. Per-worker in-memory state,
# like the agent limiter; a shared store is needed before multi-worker.
_relay_rate_limit = make_rate_limiter()


def relay_enabled_guard() -> None:
    """Reject relay requests when the relay surface is disabled.

    ``ApiConfig()`` is constructed per call so ``RELAY_ENABLED`` is
    re-read on every request: the relay is a security kill-switch, not a
    boot-time feature gate, so flipping the flag to False stops serving
    in-flight workers immediately rather than only after a restart.

    Raises:
        HTTPException: 404 when the relay surface is disabled, so a
            stock deployment exposes no relay route at all.
    """
    if not ApiConfig().RELAY_ENABLED:
        raise HTTPException(status_code=404, detail="relay disabled")


async def read_relay_body(request: Request, max_bytes: int) -> bytes:
    """Drain the relay request body, rejecting an over-budget read.

    Streams ``request.stream()`` so a chunked body with no (or a
    falsified) Content-Length cannot exhaust worker memory: the buffer is
    rejected the moment it exceeds ``max_bytes``, capping peak memory at
    ``max_bytes`` plus one transport chunk rather than the whole body.

    Args:
        request: The inbound relay request.
        max_bytes: Inclusive byte ceiling for the body.

    Returns:
        The accumulated request body within budget.

    Raises:
        HTTPException: 413 when the body exceeds ``max_bytes``.
    """
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise HTTPException(
                status_code=413, detail="relay request body too large"
            )
    return bytes(buffer)


def require_relay_access(
    service: str,
) -> Callable[..., Awaitable[ApiPrincipal]]:
    """Build the admission dependency for a relay ``service`` route.

    Runs after authentication, then applies the relay-specific per-key
    rate limit (429, distinct from the agent budget) and the strict
    relay scope check (403; an empty-scope key is denied here, unlike on
    agent routes). The ordering keeps 401 (auth) / 429 (budget) / 403
    (scope) distinct.

    Args:
        service: The relay service the route fronts (e.g. ``llm``).

    Returns:
        A dependency yielding the authorized principal.
    """

    async def _gated(
        principal: ApiPrincipal = Depends(require_principal),
    ) -> ApiPrincipal:
        retry_after = _relay_rate_limit(
            principal.key_prefix, ApiConfig().RELAY_RATE_LIMIT_PER_MIN
        )
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="relay rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        if not relay_scope_satisfied(principal.scopes, service):
            raise HTTPException(status_code=403, detail="insufficient scope")
        return principal

    return _gated


def _openai_relay_handler(
    name: str, path: str, url_field: str, key_field: str
) -> Callable[..., Awaitable[Response]]:
    """Build a TRANSPARENT relay handler for one OpenAI-family service.

    The handler is scope-gated, reads the body under the byte budget,
    injects the operator Bearer key, and forwards to the config base URL
    with ``path`` appended (the client query is dropped — the upstream
    URL is config-resolved only).
    """

    async def _handler(
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access(name)),
    ) -> Response:
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        sensitive = get_sensitive_config()
        base = getattr(sensitive, url_field).rstrip("/")
        key = getattr(sensitive, key_field).get_secret_value()

        async def _inject() -> dict[str, str]:
            return {"Authorization": f"Bearer {key}"}

        upstream = RelayUpstream(
            url=f"{base}/{path}",
            error_mode=RelayErrorMode.TRANSPARENT,
            service=name,
            inject_headers=_inject,
        )
        return await forward_relay_request(
            request=request,
            body=body,
            upstream=upstream,
            principal=principal,
            audit_store=get_audit_store(config.RELAY_AUDIT_DB_PATH),
        )

    return _handler


def _platform_relay_handler(
    name: str, url_attr: str, inject_kind: str, region_attr: Optional[str]
) -> Callable[..., Awaitable[Response]]:
    """Build an ENVELOPE relay handler for one platform-family service.

    The handler is scope-gated, reads the body under the byte budget,
    forwards it verbatim to the config-resolved URL (used as-is, no path
    appended, no client query), and injects the per-service credential.
    """

    async def _handler(
        request: Request,
        principal: ApiPrincipal = Depends(require_relay_access(name)),
    ) -> Response:
        config = ApiConfig()
        body = await read_relay_body(request, config.RELAY_REQUEST_MAX_BYTES)
        platform = DeepGenomeConfig()
        region = getattr(platform, region_attr) if region_attr else None
        upstream = RelayUpstream(
            url=getattr(platform, url_attr),
            error_mode=RelayErrorMode.ENVELOPE,
            service=name,
            inject_headers=_build_platform_inject(inject_kind, region),
        )
        return await forward_relay_request(
            request=request,
            body=body,
            upstream=upstream,
            principal=principal,
            audit_store=get_audit_store(config.RELAY_AUDIT_DB_PATH),
        )

    return _handler


def create_relay_router() -> APIRouter:
    """Build the ``/v1/relay`` router gated by the enable kill-switch.

    Returns:
        An ``APIRouter`` whose every route runs ``relay_enabled_guard``
        first, exposing a liveness probe and the OpenAI-family relay
        routes (llm / coder / embed).
    """
    router = APIRouter(
        prefix="/v1/relay",
        dependencies=[Depends(relay_enabled_guard)],
    )

    @router.get("/healthz")
    async def relay_healthz() -> dict[str, str]:
        """Return a relay liveness signal when the relay is enabled."""
        return {"status": "ok"}

    for name, path, url_field, key_field in _OPENAI_RELAYS:
        router.add_api_route(
            f"/{name}/{path}",
            _openai_relay_handler(name, path, url_field, key_field),
            methods=["POST"],
        )

    for name, path, url_attr, inject_kind, region_attr in _PLATFORM_RELAYS:
        router.add_api_route(
            f"/{name}/{path}",
            _platform_relay_handler(name, url_attr, inject_kind, region_attr),
            methods=["POST"],
        )

    return router
