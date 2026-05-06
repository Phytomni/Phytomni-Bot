# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared helpers for Analyst-backed LangGraph task workflows."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid1

from langgraph.types import Send

from .agent_registry import agent_fingerprint_values, get_cached_agent
from .analyst_agents import (
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
    create_output_dir,
)
from .config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .langgraph_runner import ainvoke_graph, capture_workflow_boundary


@dataclass(frozen=True)
class AnalysisAgentCacheSpec:
    """Inputs needed to resolve a cached Analyst-backed agent."""

    agent_name: str
    config_name: str
    base_config: Any
    field_map: Mapping[str, str]
    user_id: str | None


def route_analysis_tasks(
    node_name: str,
    target_key: str,
    tasks_key: str,
    state: Mapping[str, Any],
) -> list[Send]:
    """Dispatch analysis tasks in parallel using LangGraph Send."""
    return [
        Send(
            node_name,
            {
                "task_index": index,
                "species": state["species"],
                target_key: state[target_key],
                "output_dir": state.get("output_dir"),
                **task,
            },
        )
        for index, task in enumerate(state.get(tasks_key, []))
    ]


def ensure_analysis_output_dir(
    config: Any,
    sensitive_config: Any,
    analysis_type: str,
    output_dir: str | None,
) -> str:
    """Return an existing or newly created analysis output directory."""
    if output_dir:
        return output_dir
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return create_output_dir(
        user_id=config.USER_ID or str(uuid1()),
        task=f"{analysis_type}_task",
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=config.OBS_SERVER,
        bucket_name=config.BUCKET_NAME,
    )


async def submit_analyst_analysis(
    analyst_agent: Any,
    config: Any,
    sensitive_config: Any,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Submit one prepared analysis task through AnalystAgent."""
    analysis_type = str(request["analysis_type"])
    target_id = str(request["target_id"])
    output_dir = ensure_analysis_output_dir(
        config,
        sensitive_config,
        analysis_type,
        request.get("output_dir"),
    )
    goal_description, meta, data_list = request["prompt_parts"]
    print(f"  → Submitting {analysis_type} task via AnalystAgent...")
    result = await analyst_agent.arun(
        query=None,
        goal_description=goal_description,
        preset_data_list=data_list,
        preset_plan=meta,
        output_dir=output_dir,
        compute_resource=request["compute_resource"],
        is_auto_select=False,
        is_polling=False,
        thread_id=f"{target_id}_{analysis_type}_{uuid1()}",
    )
    print(
        f"=>{analysis_type} task completed "
        f"(task_id: {result.get('task_id')})"
    )
    return result


async def capture_analysis_result(
    state: Mapping[str, Any],
    analysis_type: str,
    submit_call: Callable[[], Awaitable[dict[str, Any]]],
    result_key: str,
    result_list_key: str | None = None,
) -> dict[str, Any]:
    """Capture one dispatched analysis result as LangGraph state updates."""

    async def run_task() -> dict[str, Any]:
        """Run the task and merge task id/result updates."""
        task_result = await submit_call()
        existing_task_ids = dict(state.get("task_ids", {}))
        task_id = task_result.get("task_id")
        if task_id is not None:
            existing_task_ids[analysis_type.replace("_analysis", "")] = str(
                task_id
            )
        updates: dict[str, Any] = {
            "task_ids": existing_task_ids,
            "completed_count": 1,
        }
        if result_list_key is not None:
            task_results = list(state.get(result_list_key, []))
            task_results.append(task_result)
            updates[result_list_key] = task_results
        else:
            updates[result_key] = task_result
        return updates

    def failure_state(exc: Exception) -> dict[str, Any]:
        """Preserve partial task progress when dispatch fails."""
        return {
            "task_ids": state.get("task_ids", {}),
            "completed_count": 1,
            "error": str(exc),
        }

    return await capture_workflow_boundary(run_task, failure_state)


async def capture_dispatched_analysis(
    state: Mapping[str, Any],
    analysis_type: str,
    target_key: str,
    dispatch_call: Callable[
        [str, str, str, str | None], Awaitable[dict[str, Any]]
    ],
    result_keys: tuple[str, str | None],
) -> dict[str, Any]:
    """Capture an analysis dispatched by target-key based state."""
    return await capture_analysis_result(
        state,
        analysis_type,
        lambda: dispatch_call(
            analysis_type,
            state["species"],
            state[target_key],
            state.get("output_dir"),
        ),
        result_keys[0],
        result_list_key=result_keys[1],
    )


def base_analysis_state(
    base_state: Mapping[str, Any],
    result_key: str,
    tasks_key: str,
    kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the common initial state for Analyst-backed workflows."""
    return {
        **base_state,
        "user_id": kwargs.get("user_id"),
        "batch": kwargs.get("batch", False),
        "output_dir": kwargs.get("output_dir"),
        result_key: [] if result_key.endswith("_result") else {},
        tasks_key: [],
        "task_ids": {},
        "completed_count": 0,
        "error": None,
    }


async def invoke_analysis_agent(
    app: Any,
    initial_state: Mapping[str, Any],
    thread_id: str | None,
    result_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Invoke an analysis graph and return selected result fields."""
    result = await ainvoke_graph(
        app,
        initial_state,
        thread_id=thread_id,
    )
    return {key: result.get(key) for key in result_keys}


def copy_analyst_sensitive_config(
    base_config: Any,
    kwargs: Mapping[str, Any],
) -> Any:
    """Return sensitive config overrides shared by Analyst-backed agents."""
    return copy_sensitive_config_with_overrides(
        base_config,
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )


def copy_user_analysis_config(
    base_config: Any,
    kwargs: Mapping[str, Any],
    field_map: Mapping[str, str],
    user_id: str | None,
) -> Any:
    """Return analysis config overrides plus the optional user id."""
    return copy_config_with_overrides(
        base_config,
        kwargs,
        field_map,
        fixed_updates={"USER_ID": user_id},
    )


async def run_analysis_graph(
    app: Any,
    base_state: Mapping[str, Any],
    state_keys: tuple[str, str],
    kwargs: Mapping[str, Any],
    result_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Build initial state, invoke the graph, and return selected fields."""
    initial_state = base_analysis_state(
        base_state,
        state_keys[0],
        state_keys[1],
        kwargs,
    )
    return await invoke_analysis_agent(
        app,
        initial_state,
        kwargs.get("thread_id"),
        result_keys,
    )


def get_cached_analysis_agent(
    agent_name: str,
    factory: Callable[[], Any],
    config_name: str,
    config: Any,
    sensitive_config: Any,
) -> Any:
    """Return a cached Analyst-backed agent with a stable fingerprint."""
    return get_cached_agent(
        agent_name,
        factory,
        agent_fingerprint_values(
            **{config_name: config, "sensitive_config": sensitive_config}
        ),
    )


def get_configured_analysis_agent(
    spec: AnalysisAgentCacheSpec,
    kwargs: Mapping[str, Any],
    base_sensitive_config: Any,
    factory_builder: Callable[[Any, Any], Any],
) -> Any:
    """Resolve overrides and return a cached Analyst-backed agent."""
    config = copy_user_analysis_config(
        spec.base_config,
        kwargs,
        spec.field_map,
        spec.user_id,
    )
    sensitive_config = copy_analyst_sensitive_config(
        base_sensitive_config,
        kwargs,
    )
    return get_cached_analysis_agent(
        spec.agent_name,
        lambda: factory_builder(config, sensitive_config),
        spec.config_name,
        config,
        sensitive_config,
    )
