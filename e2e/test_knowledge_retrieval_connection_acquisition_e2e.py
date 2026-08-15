# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Failure-safe ownership tests for Knowledge SSE connection acquisition."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import cast

import pytest

from . import test_knowledge_retrieval_resilience_e2e as resilience_e2e

pytestmark = pytest.mark.integration


async def test_dual_sse_acquisition_resets_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer-connect failure resets the stream that already opened."""
    first_acquired = asyncio.Event()
    reset_calls = 0

    async def reset() -> None:
        nonlocal reset_calls
        reset_calls += 1

    connection = cast(
        resilience_e2e.LiveSseConnection,
        SimpleNamespace(reset=reset),
    )

    async def fake_open(
        _server: resilience_e2e.ApiServer,
        *,
        query: str,
    ) -> resilience_e2e.LiveSseConnection:
        if query == "first":
            first_acquired.set()
            return connection
        await first_acquired.wait()
        raise OSError("synthetic peer connect failure")

    monkeypatch.setattr(
        sys.modules[resilience_e2e.__name__],
        "_open_live_sse_connection",
        fake_open,
    )

    with pytest.raises(ExceptionGroup) as excinfo:
        await resilience_e2e.open_live_sse_connections(
            cast(resilience_e2e.ApiServer, object()),
            queries=("first", "second"),
        )

    assert any(
        isinstance(exc, OSError)
        and str(exc) == "synthetic peer connect failure"
        for exc in excinfo.value.exceptions
    )
    assert reset_calls == 1
