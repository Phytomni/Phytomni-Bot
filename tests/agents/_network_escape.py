# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared network-escape guard for offline agent tests.

The repo-root ``block_external_http`` fixture already blocks live
``httpx.*.send``, ``loop.create_connection``, and ``getaddrinfo``.
This helper still re-blocks ``httpx.*.send`` unconditionally and
re-labels the loop patches so a compiled-graph escape cannot depend
on the transport-type check. An escape surfaces as a fast, named
``RuntimeError`` instead of a hang.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest


def install_network_escape_guard(
    monkeypatch: pytest.MonkeyPatch,
    *,
    label: str,
) -> None:
    """Re-block send and label loop escapes for compiled-graph tests.

    The repo-root ``block_external_http`` autouse fixture already
    covers ``socket.create_connection``, ``httpx.*.request``, live
    ``httpx.*.send``, ``loop.create_connection``, and
    ``getaddrinfo``. This helper still re-blocks ``httpx.*.send``
    unconditionally so a compiled-graph escape cannot depend on the
    transport-type check, and keeps labeled loop raisers so the
    failing suite is identifiable. An escape surfaces as a fast,
    named ``RuntimeError`` instead of a hang that
    ``asyncio.wait_for`` cannot cancel once a connect blocks the
    loop.

    Args:
        monkeypatch: The test's ``MonkeyPatch`` fixture.
        label: Short label injected into each raised message so the
            failing test is identifiable (e.g. ``"preamble"``,
            ``"stream"``).
    """

    def _blocked_http(_self: Any, request: Any, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError(
            f"offline {label} test escaped to a live HTTP call "
            f"({request.method} {request.url}); a mock is missing"
        )

    def _blocked_connect(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(
            f"offline {label} test escaped to a raw async socket "
            "(loop.create_connection); a mock is missing"
        )

    def _blocked_dns(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(
            f"offline {label} test escaped to DNS resolution "
            "(loop.getaddrinfo); a mock is missing"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_http)
    monkeypatch.setattr(httpx.Client, "send", _blocked_http)
    monkeypatch.setattr(
        asyncio.base_events.BaseEventLoop,
        "create_connection",
        _blocked_connect,
    )
    monkeypatch.setattr(
        asyncio.base_events.BaseEventLoop, "getaddrinfo", _blocked_dns
    )
