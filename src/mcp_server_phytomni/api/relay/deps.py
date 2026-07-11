# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared FastAPI dependencies for the relay routers.

Functions: relay_enabled_guard, read_relay_body, require_relay_access.
These live in their own module so both ``routes.py`` (OpenAI/platform
families) and ``obs.py`` (object-storage family) import them without a
circular import between the two route modules.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException
from starlette.requests import Request

from ...config.defaults import ApiConfig
from ..auth import ApiPrincipal, relay_scope_satisfied, require_principal
from ..ratelimit import make_rate_limiter

__all__ = [
    "relay_enabled_guard",
    "read_relay_body",
    "require_relay_access",
]

# One relay-specific limiter per worker, kept separate from the agent
# budget so relay calls (which spend the operator's metered upstream
# credentials) have their own cost ceiling. Per-worker in-memory state,
# like the agent limiter; a shared store is needed before multi-worker.
_relay_rate_limit = make_rate_limiter()


async def relay_enabled_guard() -> None:
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
