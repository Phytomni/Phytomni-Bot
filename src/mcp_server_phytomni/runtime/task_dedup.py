# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared input-fingerprint dedup helpers for analyst submissions.

Both analyst entry points share this module: the top-level
``retrieve_plan_submit`` wrapper and the ``submit_analyst_via_subgraph``
dispatch seam every sub-agent funnels through.

Functions: analyst_task_fingerprint, should_reuse_prior_task,
    verify_live_status, record_dispatch_submission.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

from ..storage.path_policy import IdFactory
from .fingerprint_jobs import mark_job_terminal
from .task_manager import Submission, TaskManager, resolve_tasks_db_path

__all__ = [
    "analyst_task_fingerprint",
    "mint_caller_owned_task_id",
    "record_dispatch_submission",
    "should_reuse_prior_task",
    "verify_live_status",
]

logger = logging.getLogger(__name__)

# Statuses (lowercased) that keep a prior row eligible for the cheap
# column-level reuse gate before any live probe runs.
_REUSE_STATUSES = frozenset(
    {
        "submitted",
        "running",
        "pending",
        "succeeded",
        "success",
        "completed",
        "done",
    }
)

# Remote analysis-platform live statuses (UPPERCASE) the probe maps to a
# reuse decision (see agents/analyst/task_ops.wait_for_completion).
_LIVE_SUCCESS = "SUCCEEDED"
_LIVE_IN_FLIGHT = frozenset({"RUNNING", "PENDING"})
_LIVE_DEAD = frozenset({"FAILED", "CANCELLED"})


def analyst_task_fingerprint(
    goal_description: str,
    data_list: dict[str, str],
    obs_file_list: list[str] | None,
) -> str:
    """Return a stable identity digest for one analyst submission.

    Identity is the user-visible question and its referenced data only:
    ``goal_description`` verbatim, ``data_list`` normalized to a sorted
    ``[path, description]`` list (dict insertion order ignored, the
    description kept because differing sub-questions are distinct
    tasks), and ``obs_file_list`` sorted (upload order ignored). Compute
    tier and user id are intentionally excluded so identical questions
    dedupe across tiers and tenants.

    Args:
        goal_description: Research goal or analysis objective.
        data_list: Data files and descriptions for the submission.
        obs_file_list: Optional OBS files attached to the request.

    Returns:
        Hex digest string; equal inputs MUST yield equal digests.
    """
    canonical = {
        "goal_description": goal_description,
        "data_list": sorted(data_list.items()),
        "obs_file_list": sorted(obs_file_list or []),
    }
    encoded = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def should_reuse_prior_task(prior_status: str) -> bool:
    """Cheap column gate: in-flight / succeeded reuse, else resubmit.

    ``TaskManager.get_task_by_fingerprint`` already filters terminal-
    failed rows at SQL; this rejects any unrecognized status (fail-safe
    resubmit) before the more expensive live probe runs.

    Args:
        prior_status: Status string from the tasks registry row.

    Returns:
        True to keep the prior row a reuse candidate; False to resubmit.
    """
    return prior_status.lower() in _REUSE_STATUSES


def verify_live_status(
    prior: dict[str, str],
    *,
    live_status: str | None,
    require_terminal_success: bool,
) -> bool:
    """Decide if a prior task is reusable from its probed live status.

    The local ``tasks.status`` column is written once (``"submitted"``)
    and never advanced, so a dead remote task would otherwise be reused
    forever. The caller probes the platform via
    ``agents.analyst.task_ops.probe_live_status`` (kept there to avoid
    the ``runtime.task_dedup`` import cycle) and passes the upper-cased
    status here:

    - ``SUCCEEDED`` -> reuse (terminal output ready).
    - ``RUNNING`` / ``PENDING`` -> reuse only when the caller polls
      elsewhere (``require_terminal_success`` False); a polling caller
      (deep_genome) needs a terminal task, so resubmit.
    - ``FAILED`` / ``CANCELLED`` -> never reuse; write the dead status
      back to the local row so the SQL dead-status filter self-heals.
    - ``None`` / unknown status -> resubmit (fail-safe).

    Args:
        prior: Row dict from ``get_task_by_fingerprint`` carrying at
            least ``task_id`` / ``status`` / ``analysis_id`` /
            ``output_dir``.
        live_status: Upper-cased remote status from
            ``probe_live_status``, or ``None`` when the probe failed.
        require_terminal_success: True when the caller needs a terminal
            task (is_polling), so only ``SUCCEEDED`` is reusable.

    Returns:
        True to reuse the prior ``task_id``; False to submit fresh.
    """
    if live_status == _LIVE_SUCCESS:
        return True
    if live_status in _LIVE_IN_FLIGHT:
        return not require_terminal_success
    if live_status in _LIVE_DEAD:
        _write_back_dead(prior)
    return False


def _write_back_dead(prior: dict[str, str]) -> None:
    """Persist a confirmed-dead remote status onto the local row.

    Turns the otherwise-inert dead-status SQL filter live so a later
    identical fingerprint hit is filtered at SQL without another remote
    probe. Best-effort: a write failure must not break the fresh submit
    that follows.

    Args:
        prior: Row dict whose ``task_id`` is flipped to ``"failed"``.
    """
    try:
        TaskManager(resolve_tasks_db_path()).update_task(
            prior["task_id"],
            "failed",
            prior.get("analysis_id", "") or "",
            prior.get("output_dir", "") or "",
        )
    except (sqlite3.Error, OSError):
        logger.warning(
            "Failed to write back dead status for %s", prior["task_id"]
        )
    ei_task_id = prior.get("source_task_id") or prior.get("analysis_id")
    if not ei_task_id:
        ei_task_id = prior.get("task_id")
    if not ei_task_id:
        return
    try:
        mark_job_terminal(
            resolve_tasks_db_path(),
            str(ei_task_id),
            "failed",
        )
    except (sqlite3.Error, OSError, ValueError):
        logger.warning(
            "Failed to mark fingerprint job dead for %s", prior["task_id"]
        )


def mint_caller_owned_task_id(agent: str) -> str:
    """Return a fresh caller-owned task id for a dedup-reuse result.

    A dedup hit must never hand the caller the prior tenant's task id;
    the caller polls this fresh id, and the prior remote id is kept
    server-side as ``source_task_id`` for the live-status probe.

    Args:
        agent: Public agent alias recorded in the readable id segment.

    Returns:
        A fresh ``IdFactory`` task id distinct from any prior tenant's.
    """
    return IdFactory().new_id("task", agent)


def record_dispatch_submission(
    task_id: str,
    output_dir: str,
    fingerprint: str,
    source_task_id: str | None = None,
) -> None:
    """Persist a dispatch-seam submission row carrying its dedup key.

    The seam writes its own row (run columns left ``NULL``) so all six
    dispatch consumers populate ``tasks.input_fingerprint`` uniformly,
    independent of the per-tool ``records_submission`` recorder whose
    coverage is uneven (it never extracts deep_genome sub-task ids).
    Best-effort: a write failure must not break an already-successful
    remote submission.

    Args:
        task_id: Remote task id returned by the analyst subgraph, or the
            caller-owned id minted on a dedup reuse.
        output_dir: Output directory reported by the submission.
        fingerprint: Deterministic identity digest from
            ``analyst_task_fingerprint``.
        source_task_id: On a dedup reuse, the prior tenant's remote task
            id kept server-side for the live-status probe; ``None`` on a
            fresh submission.
    """
    try:
        TaskManager(resolve_tasks_db_path()).record(
            Submission(
                task_id=task_id,
                status="submitted",
                output_dir=output_dir,
                input_fingerprint=fingerprint,
                source_task_id=source_task_id,
            )
        )
    except (sqlite3.Error, OSError):
        logger.warning("Failed to persist dispatch dedup row for %s", task_id)
