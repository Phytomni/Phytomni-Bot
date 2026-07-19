# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed, domain-neutral primitives for bounded query resolution."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

__all__ = [
    "ResolverFailure",
    "invoke_resolver",
    "normalize_confidence",
]


class ResolverFailureError(ValueError):
    """Raised when a shared resolver primitive cannot produce valid output."""


ResolverFailure = ResolverFailureError


def normalize_confidence(value: Any) -> float:
    """Convert a numeric confidence to the inclusive 0.0-1.0 range.

    Args:
        value: Candidate confidence supplied by a model response.

    Returns:
        A finite confidence value clamped to the protocol range.

    Raises:
        ResolverFailure: If value is not a finite numeric value.
    """
    if isinstance(value, bool):
        raise ResolverFailure("confidence must be numeric")
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ResolverFailure("confidence must be numeric") from exc
    if not math.isfinite(confidence):
        raise ResolverFailure("confidence must be numeric")
    return max(0.0, min(1.0, confidence))


async def invoke_resolver(
    call: Callable[[], Awaitable[Mapping[str, Any] | None]],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    """Run a resolver call under a wall-clock timeout.

    Args:
        call: Zero-argument awaitable factory for the resolver request.
        timeout_seconds: Maximum time allowed for the request.

    Returns:
        The resolver's non-empty mapping response.

    Raises:
        ResolverFailure: If the call times out or returns None.
        asyncio.CancelledError: Propagated unchanged for task cleanup.
    """
    try:
        result = await asyncio.wait_for(call(), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise ResolverFailure(
            f"resolver timeout after {timeout_seconds:.2f} s"
        ) from exc
    if result is None:
        raise ResolverFailure("resolver returned no result")
    return result
