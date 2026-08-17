# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Asyncio helpers for synchronous CLI-entry-point tests."""

from __future__ import annotations

import asyncio
from typing import Any

try:
    from langgraph.errors import NodeCancelledError
except ImportError:  # pragma: no cover - older langgraph
    NodeCancelledError = asyncio.CancelledError

GRAPH_CANCELLATION = (asyncio.CancelledError, NodeCancelledError)

__all__ = ["GRAPH_CANCELLATION", "run_coroutine_on_owned_loop"]


def run_coroutine_on_owned_loop(coro: Any) -> Any:
    """Run a coroutine on a loop owned and closed by the test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
