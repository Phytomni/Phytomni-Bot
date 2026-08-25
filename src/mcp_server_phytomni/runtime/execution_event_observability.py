# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded process metrics for execution-event rollout decisions."""

from __future__ import annotations

from collections import Counter
from threading import Lock

_LABELS = (
    "append_committed",
    "append_failed",
    "tracking_recovered",
    "production_disabled",
)
_counts: Counter[str] = Counter({label: 0 for label in _LABELS})
_lock = Lock()


def observe_execution_event(label: str) -> None:
    """Increment one fixed-cardinality metric and reject dynamic labels."""
    if label not in _LABELS:
        raise ValueError("unknown execution-event observation")
    with _lock:
        _counts[label] += 1


def execution_event_observations() -> dict[str, int]:
    """Return a detached deterministic snapshot for metrics exporters."""
    with _lock:
        return {label: _counts[label] for label in _LABELS}


def reset_execution_event_observations() -> None:
    """Reset process-local observations for deterministic tests."""
    with _lock:
        for label in _LABELS:
            _counts[label] = 0
