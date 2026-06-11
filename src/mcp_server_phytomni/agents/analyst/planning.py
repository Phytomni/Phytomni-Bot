# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Analyst retrieve→plan→submit wrapper with input-identity dedup.

Owns ``retrieve_plan_submit``: looks up the local task registry by an
input-identity fingerprint before launching the LangGraph workflow so a
duplicate 30min-3h job collapses into a constant-time reuse hit. The
``.agent`` shim re-exports this so existing
``analyst.agent`` import paths still resolve.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...runtime.task_dedup import (
    analyst_task_fingerprint,
    should_reuse_prior_task,
)
from ...runtime.task_manager import TaskManager, resolve_tasks_db_path
from .submission import _build_submit_agent, _shared_arun_kwargs


async def retrieve_plan_submit(
    goal_description: str,
    data_list: Dict[str, str],
    obs_file_list: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve context, plan, and submit an analysis through AnalystAgent.

    Computes an input-identity fingerprint from the stable user-supplied
    inputs and queries ``TaskManager.get_task_by_fingerprint`` before
    launching the LangGraph workflow. If a reusable prior task exists
    (per ``should_reuse_prior_task``), the prior ``task_id`` /
    ``output_dir`` are returned without running ``agent.arun``, so a
    duplicate 30min–3h submission collapses into a constant-time lookup.

    Args:
        goal_description: Research goal or analysis objective.
        data_list: Data files and descriptions passed directly to the agent.
        obs_file_list: Optional OBS files to include in retrieval context.
        **kwargs: Optional config, credential, output, compute-resource,
            metadata, user, thread, retry, and model overrides.

    Returns:
        AnalystAgent result payload, optionally augmented with
        ``meta_meta``. Always includes ``input_fingerprint`` so the
        submit chokepoint can persist the dedup key on the new task row
        (or carries the prior row's identity on a reuse hit). The reuse
        branch also sets ``dedup_hit=True`` so downstream chokepoints
        can detect a transparent passthrough and skip the registry
        write that would otherwise overwrite the prior row's ``run_id``
        with a freshly minted one and orphan the original run.
    """
    meta_meta = kwargs.get("meta_meta")
    compute_resource = kwargs.get("compute_resource", "small")
    fingerprint = analyst_task_fingerprint(
        goal_description=goal_description,
        data_list=data_list,
        obs_file_list=obs_file_list,
    )
    prior = TaskManager(resolve_tasks_db_path()).get_task_by_fingerprint(
        fingerprint
    )
    if prior is not None and should_reuse_prior_task(prior["status"] or ""):
        reused: Dict[str, Any] = {
            "task_id": prior["task_id"],
            "output_dir": prior["output_dir"],
            "job_name": "",
            "compute_resource": compute_resource,
            "input_fingerprint": fingerprint,
            "dedup_hit": True,
        }
        if meta_meta:
            reused["meta_meta"] = meta_meta
        return reused

    agent, output_dir, compute_resource, thread_id = _build_submit_agent(
        kwargs,
        "analyst-retrieve-plan-submit",
        "retrieve-plan-submit",
        "AnalystAgent.retrieve_plan_submit",
    )
    result = await agent.arun(
        **_shared_arun_kwargs(
            goal_description=goal_description,
            output_dir=output_dir,
            compute_resource=compute_resource,
            data_list=data_list,
        ),
        obs_file_list=obs_file_list or [],
        thread_id=thread_id,
        is_auto_select=True,
        is_polling=False,
    )
    if meta_meta:
        result["meta_meta"] = meta_meta
    result["input_fingerprint"] = fingerprint
    return result
