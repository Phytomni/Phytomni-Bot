# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared input-fingerprint dedup helpers for analyst submissions.

Both analyst entry points share this module: the top-level
``retrieve_plan_submit`` wrapper and the ``submit_analyst_via_subgraph``
dispatch seam every sub-agent funnels through. It owns the identity
digest and the status-column reuse gate.

Functions: analyst_task_fingerprint, should_reuse_prior_task.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Optional

__all__ = [
    "analyst_task_fingerprint",
    "should_reuse_prior_task",
]

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


def analyst_task_fingerprint(
    goal_description: str,
    data_list: Dict[str, str],
    obs_file_list: Optional[List[str]],
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
