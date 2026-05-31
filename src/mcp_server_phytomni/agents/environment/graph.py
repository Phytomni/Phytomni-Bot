# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Node functions and conditional router for the environment LangGraph.

Two nodes mirror ``region_vci_analysis``:
``extract_region_codes_node`` runs the chat extraction (province /
city / county codes) and ``submit_vci_task_node`` issues the
analyst submission. Nodes route every external call through the
``agent`` module namespace so existing wrapper-level
``monkeypatch.setattr`` still intercepts the dependencies.
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from . import agent
from .agent import (
    ENVIRONMENT_CONFIG,
    environment_output_dir,
    environment_region_codes,
    environment_submit_kwargs,
)
from .state import EnvironmentState


async def extract_region_codes_node(
    state: EnvironmentState,
) -> Dict[str, Any]:
    """Extract province / city / county codes from the user query.

    Delegates to :func:`agents.environment.agent.environment_region_codes`
    so the chat completion and ``<result>...</result>`` parsing stay
    in one place. Returns the codes triple as a list; the
    conditional router downstream branches on whether the value is
    ``None``. Always sets ``vci_analysis_task`` so the schema-filtered
    output keeps a consistent shape across happy and failure paths.
    """
    codes = await environment_region_codes(
        state["query"], state.get("kwargs") or {}
    )
    if codes is None:
        return {"region_codes": None, "vci_analysis_task": None}
    return {"region_codes": list(codes)}


async def submit_vci_task_node(
    state: EnvironmentState,
) -> Dict[str, Any]:
    """Submit the VCI analysis task for the parsed region codes.

    Mirrors the post-extraction body of
    :func:`agents.environment.agent.region_vci_analysis`: builds the
    goal prompt + data list, materialises the output directory when
    ``batch=False``, and forwards the submission to the analyst
    ``submit``. Re-routes ``get_prompt`` / ``get_data_list`` /
    ``submit`` through the ``agent`` module namespace so the
    existing wrapper-level ``monkeypatch.setattr`` calls still
    intercept the dependencies.
    """
    kwargs = state.get("kwargs") or {}
    batch = state.get("batch", False)
    codes = state.get("region_codes")
    if codes is None:
        raise RuntimeError(
            "submit_vci_task_node reached with region_codes=None; "
            "route_after_extract should have short-circuited to END"
        )
    province_code, city_code, county_code = codes
    user_id = kwargs.get("user_id", ENVIRONMENT_CONFIG.USER_ID)
    prompt_file = kwargs.get("prompt_file", ENVIRONMENT_CONFIG.PROMPT_FILE)
    environment_data = kwargs.get(
        "environment_data", ENVIRONMENT_CONFIG.ENVIRONMENT_DATA
    )
    output_dir = kwargs.get("output_dir", ENVIRONMENT_CONFIG.OUTPUT_DIR)
    goal_description = agent.get_prompt(
        prompt_file,
        "user/environment/vci_analysis",
        {
            "province_code": province_code,
            "city_code": city_code,
            "county_code": county_code,
        },
    )
    data_list = agent.get_data_list(
        environment_data, "environment_analysis", "vci_analysis"
    )
    if not batch:
        output_dir = environment_output_dir(user_id, kwargs)
    meta = agent.get_prompt(prompt_file, "user/environment/vci_analysis_meta")
    vci_task = await agent.submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        **environment_submit_kwargs(kwargs),
    )
    return {"vci_analysis_task": vci_task}


def route_after_extract(
    state: EnvironmentState,
) -> Literal["submit_vci_task_node", "__end__"]:
    """Route to submit when codes resolved, else short-circuit to END.

    Mirrors the legacy wrapper's early-return on
    ``environment_region_codes`` returning ``None``: the failure
    path produces no VCI task and skips the analyst submission.
    """
    if state.get("region_codes") is None:
        return "__end__"
    return "submit_vci_task_node"
