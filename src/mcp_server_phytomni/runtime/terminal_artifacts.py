# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Collect a terminal-state artifact index from reconciled task rows.

``collect_terminal_artifacts`` emits one descriptor per succeeded task
that carries an ``output_dir``. Its ``paths`` field is populated from a
prior ``enumerate_artifact_paths`` pass — an async OBS / obsfs glob run
once at the run-level settle transition. When that pass has not run
(e.g. the single-task ``GetTaskStatus`` surface), ``paths`` stays empty
and clients list ``output_dir`` themselves.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any, Protocol

from ..config.defaults import ServerConfig
from ..storage.artifact_listing import list_artifact_paths

logger = logging.getLogger(__name__)

__all__ = [
    "ArtifactLister",
    "collect_terminal_artifacts",
    "enumerate_artifact_paths",
]

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_DEFAULT_PATH_CAP = 200


class ArtifactLister(Protocol):
    """Async callable listing object paths under one output directory."""

    async def __call__(self, output_dir: str) -> list[str]: ...


async def _default_artifact_lister(output_dir: str) -> list[str]:
    """List artifact paths via the storage helper, off the event loop.

    Resolves bucket / endpoint from ``ServerConfig`` lazily so the
    registry layer needs no config threading, and runs the synchronous
    OBS / obsfs walk in a worker thread.

    Args:
        output_dir: OBS-style directory written by the run.

    Returns:
        Public ``/obs/<bucket>/<key>`` paths under ``output_dir``.
    """
    config = ServerConfig()
    return await asyncio.to_thread(
        list_artifact_paths,
        output_dir,
        bucket_name=config.BUCKET_NAME,
        obs_server=config.OBS_SERVER,
    )


async def enumerate_artifact_paths(
    live: list[dict[str, Any]],
    *,
    lister: ArtifactLister | None = None,
    cap: int = _DEFAULT_PATH_CAP,
) -> list[dict[str, Any]]:
    """Attach ``artifact_paths`` to each succeeded row with an output_dir.

    Best-effort: a per-row listing failure logs a warning and leaves that
    row's ``artifact_paths`` empty (degrading to the directory-prefix-only
    behaviour) rather than aborting the terminal settle. Over-``cap``
    results are truncated with a warning so the cap is never silent.

    Args:
        live: Reconciled child task rows (mutated in place and returned).
        lister: Override the default OBS lister (e.g. a future async
            worker); defaults to ``_default_artifact_lister``.
        cap: Maximum paths retained per row.

    Returns:
        The ``live`` rows with ``artifact_paths`` set on eligible rows.
    """
    use = lister or _default_artifact_lister
    for row in live:
        status = (row.get("status") or "").lower()
        output_dir = row.get("output_dir")
        if status not in _SUCCESS_STATUSES or not output_dir:
            continue
        try:
            paths = await use(str(output_dir))
        except (OSError, ValueError) as exc:
            logger.warning(
                "artifact listing failed for task %s (%s): %s",
                row.get("task_id"),
                output_dir,
                exc,
            )
            row["artifact_paths"] = []
            continue
        if len(paths) > cap:
            logger.warning(
                "artifact listing for task %s truncated: %d of %d kept",
                row.get("task_id"),
                cap,
                len(paths),
            )
            paths = paths[:cap]
        row["artifact_paths"] = paths
    return live


def collect_terminal_artifacts(
    task_results: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one artifact descriptor per succeeded task with output_dir.

    Args:
        task_results: Iterable of reconciled task rows. Each row is the
            shape ``reconcile_task`` returns at terminal time:
            ``{"task_id", "status", "output_dir", ...}``, optionally
            carrying an ``artifact_paths`` list from a prior
            ``enumerate_artifact_paths`` pass.

    Returns:
        List of ``{"task_id", "output_dir", "paths"}`` dicts. ``paths``
        is the row's ``artifact_paths`` when present, else an empty list.
    """
    artifacts: list[dict[str, Any]] = []
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
                "paths": row.get("artifact_paths", []),
            }
        )
    return artifacts
