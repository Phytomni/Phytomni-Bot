# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for shared query-resolution primitives."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from mcp_server_phytomni.agents.shared.query_resolution import (
    ResolverFailure,
    invoke_resolver,
    normalize_confidence,
)

pytestmark = pytest.mark.unit


async def test_invoke_resolver_returns_mapping() -> None:
    """A successful resolver result is returned unchanged."""
    result = await invoke_resolver(
        lambda: _return_result({"gene_id": "AT1G01010"}),
        timeout_seconds=1.0,
    )

    assert result == {"gene_id": "AT1G01010"}


async def test_invoke_resolver_rejects_none_result() -> None:
    """A missing resolver result becomes a typed shared failure."""
    with pytest.raises(ResolverFailure, match="no result"):
        await invoke_resolver(_return_none, timeout_seconds=1.0)


async def test_invoke_resolver_translates_timeout() -> None:
    """Only a wall-clock timeout is translated into ResolverFailure."""
    with pytest.raises(
        ResolverFailure,
        match=r"resolver timeout after 0\.01 s",
    ):
        await invoke_resolver(_never_finishes, timeout_seconds=0.01)


async def test_invoke_resolver_propagates_cancellation() -> None:
    """Cancellation remains visible to the caller for task cleanup."""

    async def cancelled() -> Mapping[str, Any] | None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await invoke_resolver(cancelled, timeout_seconds=1.0)


@pytest.mark.parametrize("value, expected", [(-0.5, 0.0), (1.5, 1.0)])
def test_normalize_confidence_clamps_bounds(
    value: float,
    expected: float,
) -> None:
    """Confidence values outside the protocol range are clamped."""
    assert normalize_confidence(value) == expected


@pytest.mark.parametrize("value", [object(), True, None])
def test_normalize_confidence_rejects_non_numeric_values(value: Any) -> None:
    """Non-numeric confidence values fail closed."""
    with pytest.raises(ResolverFailure, match="confidence must be numeric"):
        normalize_confidence(value)


async def _return_result(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a mapping from an awaitable factory used by the tests."""
    return result


async def _return_none() -> None:
    """Return no resolver result."""
    return None


async def _never_finishes() -> Mapping[str, Any]:
    """Sleep until the invocation timeout cancels this coroutine."""
    await asyncio.sleep(60)
    return {}
