# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared network-escape guard for offline agent tests.

Patches the remaining async paths the repo-root
``block_external_http`` fixture does not cover so an un-mocked
escape surfaces as a named ``RuntimeError`` instead of a 20s hang.
Shared by the brief_gene preamble fan-in test and the graph astream
primitive test.
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
    """Patch the async paths ``block_external_http`` still leaves open.

    The repo-root ``block_external_http`` autouse fixture now covers
    ``socket.create_connection``, ``httpx.*.request``, and live
    ``httpx.*.send``. Two async paths can still reach a real socket
    and wedge an offline test: the event loop's ``create_connection``
    and DNS via ``getaddrinfo``. This helper also re-blocks
    ``httpx.*.send`` unconditionally so a compiled-graph escape
    cannot depend on the transport-type check. An escape surfaces as
    a fast, named ``RuntimeError`` instead of a 20s hang that
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
