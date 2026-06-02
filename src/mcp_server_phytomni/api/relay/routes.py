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

from fastapi import APIRouter, Depends, HTTPException

from ...config.defaults import ApiConfig

__all__ = ["create_relay_router", "relay_enabled_guard"]


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


def create_relay_router() -> APIRouter:
    """Build the ``/v1/relay`` router gated by the enable kill-switch.

    Returns:
        An ``APIRouter`` whose every route runs ``relay_enabled_guard``
        first, exposing a liveness probe at ``/v1/relay/healthz``.
    """
    router = APIRouter(
        prefix="/v1/relay",
        dependencies=[Depends(relay_enabled_guard)],
    )

    @router.get("/healthz")
    async def relay_healthz() -> dict[str, str]:
        """Return a relay liveness signal when the relay is enabled."""
        return {"status": "ok"}

    return router
