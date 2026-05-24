# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Analyst submission wrapper, dedup helpers, and per-call config copies.

Exports the ``submit`` compatibility wrapper, the input-fingerprint /
reuse-decision helpers shared with ``retrieve_plan_submit``, and the
analyst-specific override builders. ``AnalystAgent`` comes from ``.core``
to keep the import graph acyclic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Optional

from ...config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import get_sensitive_config
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...storage.path_policy import RunIdentity
from .core import AnalystAgent
from .defaults import (
    ANALYST_CONFIG,
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
)

# ``_analyst_config_with_overrides`` consumes these as explicit parameters;
# strip them from the splatted kwargs or Python raises "got multiple values".
_CONFIG_EXPLICIT_KEYS = frozenset(
    {"user_id", "is_create_dir", "output_dir", "compute_resource"}
)


def _shared_arun_kwargs(
    goal_description: str,
    output_dir: str,
    compute_resource: str,
    data_list: Any,
) -> Dict[str, Any]:
    """Return the AnalystAgent.arun kwarg block shared by submit wrappers.

    ``submit`` (here) and ``retrieve_plan_submit`` (agent.py) both call
    ``agent.arun`` with the same first five kwargs in the same order.
    Centralizing that block kills the pylint R0801 duplicate-code warning
    and gives one seam to update when arun's signature evolves.
    """
    return {
        "query": goal_description,
        "goal_description": goal_description,
        "output_dir": output_dir,
        "compute_resource": compute_resource,
        "preset_data_list": data_list,
    }


def _analyst_config_with_overrides(
    user_id: str,
    is_create_dir: bool,
    output_dir: str,
    compute_resource: Literal["small", "medium", "large"],
    **kwargs: Any,
):
    """Build an AnalystConfig copy from compatibility wrapper arguments."""
    return copy_config_with_overrides(
        ANALYST_CONFIG,
        kwargs,
        ANALYST_CONFIG_FIELD_MAP,
        fixed_updates={
            "USER_ID": user_id,
            "CREATE_DIR": is_create_dir,
            "OUTPUT_DIR": output_dir,
            "COMPUTE_RESOURCE": compute_resource,
        },
    )


def _sensitive_config_with_overrides(**kwargs: Any):
    """Build a SensitiveConfig copy from compatibility wrapper arguments."""
    return copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )


def _submit_user_and_thread_id(
    user_id: Any,
    scope: str,
    operation: str,
) -> tuple[str, str]:
    """Return compatible user IDs and run-scoped fallback thread IDs."""
    if user_id:
        resolved_user_id = str(user_id)
        return resolved_user_id, resolved_user_id

    run_identity = RunIdentity.create(user_id=None, scope=scope)
    return run_identity.user_id, run_identity.scoped_id("thread", operation)


def _build_submit_agent(
    kwargs: Dict[str, Any],
    scope: str,
    operation: str,
    cache_label: str,
) -> tuple[Any, str, str, str]:
    """Resolve wrapper kwargs into a cached collision-safe AnalystAgent."""
    user_id = kwargs.get("user_id", ANALYST_CONFIG.USER_ID)
    user_id, thread_id = _submit_user_and_thread_id(
        user_id,
        scope,
        operation,
    )
    is_create_dir = kwargs.get("is_create_dir", ANALYST_CONFIG.CREATE_DIR)
    output_dir = kwargs.get("output_dir", ANALYST_CONFIG.OUTPUT_DIR)
    compute_resource = kwargs.get(
        "compute_resource",
        ANALYST_CONFIG.COMPUTE_RESOURCE,
    )
    analyst_config = _analyst_config_with_overrides(
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        compute_resource=compute_resource,
        **{k: v for k, v in kwargs.items() if k not in _CONFIG_EXPLICIT_KEYS},
    )
    sensitive_config = _sensitive_config_with_overrides(**kwargs)
    agent = get_cached_agent(
        cache_label,
        lambda: AnalystAgent(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
    )
    return agent, output_dir, compute_resource, thread_id


async def submit(
    goal_description: str,
    data_list: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Submit a prepared analysis plan through AnalystAgent.

    Args:
        goal_description: Research goal or analysis objective.
        data_list: Data files and descriptions passed directly to the agent.
        **kwargs: Optional config, credential, output, compute-resource,
            metadata, user, thread, retry, and auto-selection overrides.

    Returns:
        AnalystAgent result payload with task id, output directory, job name,
        and compute resource.
    """
    meta = kwargs.get("meta", "")
    enable_auto_select = kwargs.get("enable_auto_select", True)
    meta_meta = kwargs.get("meta_meta")
    agent, output_dir, compute_resource, thread_id = _build_submit_agent(
        kwargs,
        "analyst-submit",
        "submit",
        "AnalystAgent.submit",
    )
    return await agent.arun(
        **_shared_arun_kwargs(
            goal_description=goal_description,
            output_dir=output_dir,
            compute_resource=compute_resource,
            data_list=data_list,
        ),
        preset_plan=meta + (meta_meta or ""),
        thread_id=thread_id,
        is_auto_select=enable_auto_select,
        is_polling=False,
    )


def _analyst_task_fingerprint(
    goal_description: str,
    data_list: Dict[str, str],
    obs_file_list: Optional[List[str]],
) -> str:
    """Return a stable identity digest for one analyst submission.

    Identity is defined by the **user-visible question and its
    referenced data** only:

    - ``goal_description`` is the research goal verbatim.
    - ``data_list`` is normalized to a sorted list of ``[path,
      description]`` pairs so dict insertion order is ignored. The
      description text is included because two analyses pointing at
      the same files but asking different sub-questions through
      descriptions are distinct tasks.
    - ``obs_file_list`` is sorted so upload order is ignored.

    Compute tier (``compute_resource``) and authenticated user
    (``user_id``) are intentionally **excluded** so identical
    scientific questions dedupe across the small/medium/large tiers
    and across tenants. A user-supplied requirement: the same
    analysis should reuse a prior remote task even if the new caller
    asked for a different compute tier or comes from a different
    user account.

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


def _should_reuse_prior_task(prior_status: str) -> bool:
    """Decide whether a prior task row may short-circuit a fresh submit.

    The user-supplied decision the dedup contract hinges on:

    - **In-flight** (``submitted`` / ``running`` / ``pending``) → reuse
      avoids launching a duplicate 30min–3h job; the caller polls the
      already-running remote id.
    - **Succeeded** (``succeeded`` / ``success`` / ``completed`` /
      ``done``) → reuse hands the caller the finished output directly.
    - **Unknown** → fail safe and resubmit; ``get_task_by_fingerprint``
      already filters out the dead-status set at the SQL layer, so an
      unrecognized status here is a sign the prior row is in an
      unexpected state and shouldn't be trusted.

    Args:
        prior_status: Status string from the tasks registry row.

    Returns:
        True to short-circuit and return the prior task; False to
        submit a fresh analysis job.
    """
    return prior_status.lower() in {
        "submitted",
        "running",
        "pending",
        "succeeded",
        "success",
        "completed",
        "done",
    }
