# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Backward-compatible re-export hub for the analyst public surface.

Imports the AnalystAgent orchestrator from ``.core``, the submit / dedup
helpers from ``.submission``, and the task-platform wrappers from
``.task_ops``. Keeps ``retrieve_plan_submit`` here for now (Step 10.2
relocates it) so existing ``from mcp_server_phytomni.agents.analyst.agent
import ...`` callers continue to resolve every public name unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...runtime.task_manager import TaskManager, resolve_tasks_db_path
from ..shared.analysis_storage import (
    ObsAccessOptions,
    create_output_dir,
    get_data_list,
)
from .core import AnalystAgent, AnalystAgentsState
from .defaults import (
    ANALYST_CONFIG,
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
)
from .storage import (
    ObsDownloadOptions,
    delete_analyst_agents_data,
    download_obs_out,
    upload_analyst_agents_data,
)
from .submission import (
    _analyst_task_fingerprint,
    _build_submit_agent,
    _shared_arun_kwargs,
    _should_reuse_prior_task,
    submit,
)
from .task_ops import task_delete, task_log, task_status, wait_for_completion

__all__ = [
    "ANALYST_CONFIG",
    "ANALYST_CONFIG_FIELD_MAP",
    "ANALYST_SECRET_FIELD_MAP",
    "ANALYST_SENSITIVE_FIELD_MAP",
    "AnalystAgent",
    "AnalystAgentsState",
    "ObsAccessOptions",
    "ObsDownloadOptions",
    "create_output_dir",
    "delete_analyst_agents_data",
    "download_obs_out",
    "get_data_list",
    "retrieve_plan_submit",
    "submit",
    "task_delete",
    "task_log",
    "task_status",
    "upload_analyst_agents_data",
    "wait_for_completion",
]


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
    (per ``_should_reuse_prior_task``), the prior ``task_id`` /
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
    fingerprint = _analyst_task_fingerprint(
        goal_description=goal_description,
        data_list=data_list,
        obs_file_list=obs_file_list,
    )
    prior = TaskManager(resolve_tasks_db_path()).get_task_by_fingerprint(
        fingerprint
    )
    if prior is not None and _should_reuse_prior_task(prior["status"] or ""):
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
