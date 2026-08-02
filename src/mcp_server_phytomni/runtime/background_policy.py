# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Canonical policy for detached generic background Agent runs."""

from __future__ import annotations

__all__ = [
    "BACKGROUND_SUBMISSION_AGENT_SLUGS",
    "is_detached_background_run",
]

BACKGROUND_SUBMISSION_AGENT_SLUGS: frozenset[str] = frozenset(
    {"analyst", "research", "network", "design"}
)


def is_detached_background_run(*, agent: str, origin: str) -> bool:
    """Return whether one run uses the generic detached-worker contract."""
    return origin == "remote" and agent in BACKGROUND_SUBMISSION_AGENT_SLUGS
