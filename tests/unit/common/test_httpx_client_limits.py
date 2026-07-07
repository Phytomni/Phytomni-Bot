# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pin the shared AsyncClient's connection-pool limits.

init_shared_client must apply ServerConfig-driven Limits so the
multi_retrieve x rerank x relay fan-out keeps enough keep-alive
connections instead of httpx's default cap of 20.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from mcp_server_phytomni.common import httpx_client
from mcp_server_phytomni.common.httpx_client import (
    aclose_shared_client,
    init_shared_client,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
async def _isolate_shared_client() -> AsyncIterator[None]:
    """Tear the shared client down so the slot never leaks between tests.

    Yields:
        ``None``; teardown clears the module-level slot.
    """
    yield
    await aclose_shared_client()


async def test_shared_client_applies_configured_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared client's pool limits come from ServerConfig.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap AsyncClient
            so the Limits kwarg can be captured without TLS setup.
    """
    constructed: list[dict[str, Any]] = []

    class _RecordingClient(AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx_client, "AsyncClient", _RecordingClient)

    init_shared_client()

    assert len(constructed) == 1
    limits = constructed[0]["limits"]
    assert limits.max_connections == 100
    assert limits.max_keepalive_connections == 50
