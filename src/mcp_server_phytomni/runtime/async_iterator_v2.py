# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared lifecycle helpers for transport-owned asynchronous iterators."""

from __future__ import annotations

import inspect
from typing import Any


async def close_async_iterator(iterator: Any) -> None:
    """Close an asynchronous iterator when its implementation supports it."""
    closer = getattr(iterator, "aclose", None)
    if not callable(closer):
        return
    close_result = closer()
    if inspect.isawaitable(close_result):
        await close_result
