# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Nonblocking durable locks used by conversation-context adapters."""

from __future__ import annotations

import asyncio
from typing import Any

from .store import ConversationContextStore, ReviewMutationLockTimeoutError


async def acquire_review_mutation_lock(
    store: ConversationContextStore, *, wait_seconds: float = 30.0
) -> Any:
    """Poll a durable Review lock without blocking the event loop."""
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        try:
            return store.acquire_review_mutation_lock(timeout=0)
        except ReviewMutationLockTimeoutError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.01)


__all__ = ["acquire_review_mutation_lock"]
