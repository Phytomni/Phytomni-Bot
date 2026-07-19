# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared async HTTP client honouring ServerConfig TLS settings.

``init_shared_client`` / ``aclose_shared_client`` are owned by the
API lifespan and MCP serve loop; ``get_async_client`` yields the shared
client (keep-alive pool) when the caller passes only ``timeout`` /
``config``. Any other kwarg (e.g. ``trust_env=False`` on a bare-IP
corporate URL) opts out and gets an ephemeral client instead so the
pool's TLS / proxy posture is never mutated mid-request.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from httpx import AsyncClient, Limits

from ..config.defaults import ServerConfig

__all__ = [
    "VerifyArg",
    "aclose_shared_client",
    "get_async_client",
    "init_shared_client",
    "resolve_verify",
    "shared_client_initialised",
]

VerifyArg = bool | ssl.SSLContext

# Single-key dict so ``init`` / ``aclose`` can mutate the slot without a
# ``global`` statement (pylint W0603) and without renaming the slot to
# an UPPER_CASE constant (pylint C0103) — neither lint is suppressed.
# The container identity stays fixed; only the ``client`` entry rebinds.
_HTTPX_STATE: dict[str, AsyncClient | None] = {"client": None}


def resolve_verify(config: ServerConfig | None = None) -> VerifyArg:
    """Return the ``verify`` argument value httpx should use.

    Resolution order:
        1. ``TLS_VERIFY=False`` disables verification regardless of bundle.
        2. ``CA_BUNDLE`` is wrapped in an ``ssl.SSLContext`` so callers
           get the new ``verify=<SSLContext>`` shape (string bundle
           paths are deprecated in httpx ≥0.27).
        3. Otherwise verification falls back to the system trust store
           via ``verify=True``.

    Args:
        config: Resolved ServerConfig instance. Defaults to a freshly
            constructed one so callers can stay one-liner; tests supply
            an explicit config to avoid re-reading os.environ.

    Returns:
        ``False`` when verification is disabled, an ``ssl.SSLContext``
        loaded from ``CA_BUNDLE`` when one is configured, or ``True``
        for the system trust store.
    """
    resolved = config if config is not None else ServerConfig()
    if not resolved.TLS_VERIFY:
        return False
    if resolved.CA_BUNDLE:
        return ssl.create_default_context(cafile=resolved.CA_BUNDLE)
    return True


def init_shared_client(*, config: ServerConfig | None = None) -> AsyncClient:
    """Initialise the process-wide shared ``AsyncClient``.

    Idempotent: a second call returns the existing client unchanged so
    a duplicated lifespan event (e.g. test reuse) does not leak a
    second pool. The client uses ``timeout=None`` so per-request
    ``timeout=`` arguments (passed by ``_send_retry_request`` and by
    every direct ``client.get/post`` call site) stay authoritative;
    httpx would otherwise impose its 5 s default at the client level
    and silently shorten long-running tool calls.

    Args:
        config: ServerConfig override (mainly tests). Defaults to a
            fresh ``ServerConfig()`` so the production lifespan does
            not have to thread one in.

    Returns:
        The shared ``AsyncClient`` instance.
    """
    existing = _HTTPX_STATE["client"]
    if existing is not None:
        return existing
    resolved = config if config is not None else ServerConfig()
    client = AsyncClient(
        verify=resolve_verify(resolved),
        timeout=None,
        limits=Limits(
            max_connections=resolved.HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=resolved.HTTP_MAX_KEEPALIVE,
        ),
    )
    _HTTPX_STATE["client"] = client
    return client


async def aclose_shared_client() -> None:
    """Close the shared ``AsyncClient`` and clear the slot.

    Safe to call when no shared client is active so a lifespan teardown
    can run unconditionally. The slot is cleared even on close failure
    so a flaky ``aclose`` cannot pin a stale client across restarts.
    """
    client = _HTTPX_STATE["client"]
    if client is None:
        return
    _HTTPX_STATE["client"] = None
    await client.aclose()


def shared_client_initialised() -> bool:
    """Return whether the shared client slot is currently populated.

    Exposed so tests can assert lifespan wiring without poking the
    module state directly.
    """
    return _HTTPX_STATE["client"] is not None


@asynccontextmanager
async def get_async_client(
    *,
    timeout: Any = None,  # noqa: ASYNC109
    config: ServerConfig | None = None,
    **client_kwargs: Any,
) -> AsyncGenerator[AsyncClient, None]:
    """Yield the shared keep-alive client, or an ephemeral fallback.

    Yields the shared client (keep-alive connection pool) when the
    caller passes only ``timeout`` / ``config``. Any other kwarg
    (e.g. ``trust_env``, ``headers``, ``proxies``, transport
    overrides) opts out and gets an ephemeral client so the shared
    pool's TLS / proxy posture is never mutated mid-request. The
    shared client is also bypassed when the lifespan has not
    initialised one (tests, scripts), keeping the legacy per-call
    factory shape working without fixture changes.

    The caller's ``timeout=`` is dropped on the shared path because
    the shared client uses ``timeout=None`` and per-request timeouts
    are passed by ``_send_retry_request`` (and by every direct
    ``client.get/post`` call site) — see ``init_shared_client``.

    Args:
        timeout: Request timeout used when constructing an ephemeral
            ``AsyncClient``. Ignored on the shared path.
        config: ServerConfig override (tests inject a built instance).
            Ignored on the shared path (which uses the config passed
            to ``init_shared_client``).
        **client_kwargs: Additional ``AsyncClient`` keyword arguments;
            any non-empty value forces an ephemeral client.

    Yields:
        An ``httpx.AsyncClient``. Shared clients are NOT closed on
        context exit (the lifespan owns them); ephemeral clients are
        closed normally.

    Raises:
        TypeError: When ``verify`` is supplied via ``client_kwargs``;
            verification is centrally managed and a per-call override
            would defeat the audit invariant.
    """
    if "verify" in client_kwargs:
        raise TypeError(
            "get_async_client manages verify via ServerConfig; pass "
            "PHYTOMNI_TLS_VERIFY / PHYTOMNI_CA_BUNDLE instead."
        )
    shared = _HTTPX_STATE["client"]
    if shared is not None and not client_kwargs:
        yield shared
        return
    async with AsyncClient(
        verify=resolve_verify(config),
        timeout=timeout,
        **client_kwargs,
    ) as client:
        yield client
