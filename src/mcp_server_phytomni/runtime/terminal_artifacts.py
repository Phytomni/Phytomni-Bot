# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Collect a terminal-state artifact index from reconciled task rows.

``collect_terminal_artifacts`` emits one descriptor per succeeded task
that carries an ``output_dir``. The ``paths`` field is intentionally
empty: a real glob would require synchronous OBS / obsfs I/O on the
terminal-write hot path, so the index ships the path-key skeleton and
clients list ``output_dir`` themselves until an async worker takes
that scan off the polling loop.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

__all__ = ["collect_terminal_artifacts"]

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})


def collect_terminal_artifacts(
    task_results: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return one artifact descriptor per succeeded task with output_dir.

    Args:
        task_results: Iterable of reconciled task rows. Each row is the
            shape ``reconcile_task`` returns at terminal time:
            ``{"task_id", "status", "output_dir", ...}``.

    Returns:
        List of ``{"task_id", "output_dir", "paths"}`` dicts. ``paths``
        is always an empty list at this layer; a future indexer will
        populate it without changing the descriptor shape.
    """
    artifacts: List[Dict[str, Any]] = []
    for row in task_results:
        status = (row.get("status") or "").lower()
        if status not in _SUCCESS_STATUSES:
            continue
        output_dir = row.get("output_dir")
        if not output_dir:
            continue
        artifacts.append(
            {
                "task_id": str(row.get("task_id", "")),
                "output_dir": str(output_dir),
                "paths": [],
            }
        )
    return artifacts
