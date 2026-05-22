# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared async HTTP client factory honouring ServerConfig TLS settings.

``get_async_client`` yields an ``httpx.AsyncClient`` whose ``verify``
argument is resolved from ``PHYTOMNI_TLS_VERIFY`` / ``PHYTOMNI_CA_BUNDLE``
(default verify=True). Transitional context-manager shape; a future
revision will replace it with a lifecycle-managed shared client.
"""

from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional, Union

from httpx import AsyncClient

from ..config.defaults import ServerConfig

__all__ = ["VerifyArg", "get_async_client", "resolve_verify"]

VerifyArg = Union[bool, ssl.SSLContext]


def resolve_verify(config: Optional[ServerConfig] = None) -> VerifyArg:
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


@asynccontextmanager
async def get_async_client(
    *,
    timeout: Any = None,
    config: Optional[ServerConfig] = None,
    **client_kwargs: Any,
) -> AsyncGenerator[AsyncClient, None]:
    """Open an ``httpx.AsyncClient`` with deployment TLS settings applied.

    The ``verify`` keyword is centrally resolved by ``resolve_verify``;
    callers should NOT pass their own ``verify``. Any other
    ``httpx.AsyncClient`` keyword (``trust_env``, ``headers``, ``proxies``,
    transport overrides, ...) flows through unchanged.

    Args:
        timeout: Request timeout passed verbatim to ``AsyncClient``.
        config: ServerConfig override (tests inject a built instance).
        **client_kwargs: Additional ``AsyncClient`` keyword arguments.

    Yields:
        An open ``httpx.AsyncClient`` that closes on context exit.

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
    async with AsyncClient(
        verify=resolve_verify(config),
        timeout=timeout,
        **client_kwargs,
    ) as client:
        yield client
