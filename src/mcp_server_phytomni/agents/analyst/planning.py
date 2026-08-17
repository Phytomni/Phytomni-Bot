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

from typing import Any

from ...runtime.result_run_layout import is_unallocated_default_output_dir
from ...runtime.task_dedup import (
    analyst_task_fingerprint,
    should_reuse_prior_task,
)
from ...runtime.task_manager import TaskManager, resolve_tasks_db_path
from ..shared.options import resolve_agent_locale
from .defaults import ANALYST_CONFIG
from .submission import _build_submit_agent, _shared_arun_kwargs
from .task_ops import verified_reuse_task_ids


async def _reuse_live_prior_task(
    prior: dict[str, Any],
    *,
    fingerprint: str,
    compute_resource: object,
    meta_meta: object,
) -> dict[str, Any] | None:
    """Return a reuse payload when the prior remote task is still live."""
    prior_output = str(prior.get("output_dir") or "")
    if is_unallocated_default_output_dir(
        prior_output,
        ANALYST_CONFIG.OUTPUT_DIR,
    ):
        return None
    reuse_ids = await verified_reuse_task_ids(
        prior,
        require_terminal_success=False,
    )
    if reuse_ids is None:
        return None
    caller_task_id, source_task_id = reuse_ids
    reused: dict[str, Any] = {
        "task_id": caller_task_id,
        "output_dir": prior["output_dir"],
        "job_name": "",
        "compute_resource": compute_resource,
        "input_fingerprint": fingerprint,
        "source_task_id": source_task_id,
    }
    if meta_meta:
        reused["meta_meta"] = meta_meta
    return reused


async def retrieve_plan_submit(
    goal_description: str,
    data_list: dict[str, str],
    obs_file_list: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Retrieve context, plan, and submit an analysis through AnalystAgent.

    Computes an input-identity fingerprint from the stable user-supplied
    inputs and queries ``TaskManager.get_task_by_fingerprint`` before
    launching the LangGraph workflow. A candidate that passes the cheap
    status gate is then verified against the live remote status (the
    local ``tasks.status`` column never advances past ``submitted``, so
    a dead remote task must not be reused). If the prior task is still
    live or succeeded, its ``task_id`` / ``output_dir`` are returned
    without running ``agent.arun``, so a duplicate 30min–3h submission
    collapses into a constant-time lookup.

    Args:
        goal_description: Research goal or analysis objective.
        data_list: Data files and descriptions passed directly to the agent.
        obs_file_list: Optional OBS files to include in retrieval context.
        **kwargs: Optional config, credential, output, compute-resource,
            metadata, user, thread, retry, and model overrides.

    Returns:
        AnalystAgent result payload, optionally augmented with
        ``meta_meta``. Always includes ``input_fingerprint`` so the
        submit chokepoint can persist the dedup key on the new task row.
        On a reuse hit the wrapper mints a fresh caller-owned
        ``task_id`` (never the prior tenant's id) and carries the prior
        remote id under ``source_task_id`` for the server-side
        live-status probe, so the caller polls a row they own while the
        prior remote task stays the data source.
    """
    compute_resource = kwargs.get(
        "compute_resource", ANALYST_CONFIG.COMPUTE_RESOURCE
    )
    fingerprint = analyst_task_fingerprint(
        goal_description=goal_description,
        data_list=data_list,
        obs_file_list=obs_file_list,
    )
    prior = TaskManager(resolve_tasks_db_path()).get_task_by_fingerprint(
        fingerprint
    )
    if prior is not None and should_reuse_prior_task(prior["status"] or ""):
        reused = await _reuse_live_prior_task(
            prior,
            fingerprint=fingerprint,
            compute_resource=compute_resource,
            meta_meta=kwargs.get("meta_meta"),
        )
        if reused is not None:
            return reused

    agent, output_dir, compute_resource, thread_id = _build_submit_agent(
        kwargs,
        "analyst-retrieve-plan-submit",
        "retrieve-plan-submit",
        "AnalystAgent.retrieve_plan_submit",
    )
    shared_kwargs = _shared_arun_kwargs(
        goal_description,
        output_dir,
        compute_resource,
        data_list,
    )
    result = await agent.arun(
        **shared_kwargs,
        obs_file_list=obs_file_list or [],
        thread_id=thread_id,
        is_auto_select=True,
        is_polling=False,
        input_fingerprint=fingerprint,
        locale=resolve_agent_locale(kwargs.get("locale")),
    )
    if kwargs.get("meta_meta"):
        result["meta_meta"] = kwargs["meta_meta"]
    result["input_fingerprint"] = fingerprint
    return result
