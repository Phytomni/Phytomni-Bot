# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Reusable routing closures for shared LangGraph subgraphs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def make_after_router(
    *,
    pending_post_key: str,
    default: str | None,
    label: str,
) -> Callable[[Any], str]:
    """Return a router for a staged post-subgraph node."""

    def _router(state: Any) -> str:
        pending = state.get(pending_post_key)
        if isinstance(pending, str) and pending:
            return pending
        if default is not None:
            return default
        raise ValueError(
            f"{label} after-router has no branch: "
            f"state[{pending_post_key!r}] is unset or empty and no "
            "default was configured"
        )

    return _router
