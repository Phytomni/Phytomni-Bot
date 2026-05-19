# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""In-process per-key sliding-window rate limiter.

Functions: make_rate_limiter.

Single-process only: state lives in memory, so each uvicorn worker
keeps its own counters. A reverse proxy or shared store is required
before scaling to multiple workers.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable, Optional

__all__ = ["make_rate_limiter"]

RateLimiter = Callable[[str, int], Optional[int]]


def make_rate_limiter(
    window_seconds: float = 60.0,
    now: Callable[[], float] = time.monotonic,
) -> RateLimiter:
    """Build a stateful per-key sliding-window limiter.

    A closure (not a class) keeps the rolling-hit state so the limiter
    exposes a single call without tripping the too-few-public-methods
    lint, mirroring the closure-based ASGI middleware.

    Args:
        window_seconds: Rolling window width in seconds.
        now: Monotonic clock callable (injectable for tests).

    Returns:
        ``check(key, limit) -> Optional[int]``: None when the request is
        allowed (and recorded), otherwise the integer seconds to wait
        before retrying. A ``limit`` <= 0 disables limiting.
    """
    hits: dict[str, list[float]] = defaultdict(list)

    def check(key: str, limit: int) -> Optional[int]:
        """Record an attempt and report whether it is over budget."""
        if limit <= 0:
            return None
        current = now()
        cutoff = current - window_seconds
        recent = [stamp for stamp in hits[key] if stamp > cutoff]
        if len(recent) >= limit:
            hits[key] = recent
            return max(int(recent[0] + window_seconds - current), 1)
        recent.append(current)
        hits[key] = recent
        return None

    return check
