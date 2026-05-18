# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared offline test doubles for ``tests/unit``.

Hosts the scripted fake httpx ``AsyncClient`` builder and the
instant-retry sleep patch reused by the HTTP-retry and IAM-token
suites, so the scaffolding is defined once.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest


@pytest.fixture
def instant_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``asyncio.sleep`` to a no-op so retry backoff is instant.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    async def _no_sleep(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


@pytest.fixture
def fake_client_factory() -> Callable[[list[Any], dict[str, int]], type]:
    """Return a builder for a scripted fake async HTTP client.

    The built class is an async context manager whose ``post`` replays
    one scripted behavior per call: an exception instance is raised,
    anything else is returned. ``calls["n"]`` counts POST attempts.

    Returns:
        ``make(behaviors, calls) -> type`` client-class builder.
    """

    def _make(behaviors: list[Any], calls: dict[str, int]) -> type:
        script = list(behaviors)

        class _FakeClient:
            """Async context-manager HTTP client stub."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                """Ignore client construction arguments."""
                del args, kwargs

            async def __aenter__(self) -> "_FakeClient":
                """Enter the async context."""
                return self

            async def __aexit__(self, *args: Any) -> None:
                """Exit the async context."""
                del args

            async def post(self, *args: Any, **kwargs: Any) -> Any:
                """Replay the next scripted behavior for one POST."""
                del args, kwargs
                calls["n"] += 1
                behavior = script.pop(0)
                if isinstance(behavior, BaseException):
                    raise behavior
                return behavior

        return _FakeClient

    return _make
