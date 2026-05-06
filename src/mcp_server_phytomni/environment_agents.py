# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Environment agents for regional vegetation index analysis workflows."""

import re
from typing import Any
from uuid import uuid1

from .agent_option_helpers import (
    SubmitKwargsSpec,
    build_chat_kwargs,
    build_submit_kwargs,
)
from .analyst_agents import create_output_dir, get_data_list, submit
from .chat_agents import phyto_chat
from .config.defaults import EnvironmentConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt, load_text_file

ENVIRONMENT_CONFIG = EnvironmentConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)


def _environment_chat_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return chat kwargs for environment code extraction."""
    return build_chat_kwargs(kwargs, ENVIRONMENT_CONFIG, SENSITIVE_CONFIG)


def _environment_submit_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return Analyst submit kwargs for the environment workflow."""
    return build_submit_kwargs(
        kwargs,
        ENVIRONMENT_CONFIG,
        SENSITIVE_CONFIG,
        (DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY),
        SubmitKwargsSpec(
            task_name="environment-agents-vci-task",
            compute_resource="large",
        ),
    )


async def _environment_region_codes(
    query: str,
    kwargs: dict[str, Any],
) -> tuple[str | None, str | None, str | None] | None:
    """Return province, city, and county codes extracted from a query."""
    prompt_file = kwargs.get("prompt_file", ENVIRONMENT_CONFIG.PROMPT_FILE)
    region_info = load_text_file(
        kwargs.get("region_code", ENVIRONMENT_CONFIG.REGION_CODE)
    )
    prompt = get_prompt(
        prompt_file,
        "user/environment/get_code_query",
        {"json_dict": region_info, "query": query},
    )
    phyto_response = await phyto_chat(
        user_query=prompt,
        **_environment_chat_kwargs(kwargs),
    )
    if phyto_response is None:
        return None
    content = phyto_response["choices"][0]["message"]["content"]
    try:
        code_info = re.findall(r"<result>(.*?)</result>", content)[0]
    except IndexError:
        return None
    return tuple((code_info.split("|") + [None] * 3)[:3])


def _environment_output_dir(
    user_id: str | None, kwargs: dict[str, Any]
) -> str:
    """Create the output directory for a non-batch VCI task."""
    return create_output_dir(
        user_id or str(uuid1()),
        "vci_analysis_task",
        access_key_id=kwargs.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
        secret_access_key=kwargs.get(
            "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
        ),
        obs_server=kwargs.get("obs_server", ENVIRONMENT_CONFIG.OBS_SERVER),
        bucket_name=kwargs.get("bucket_name", ENVIRONMENT_CONFIG.BUCKET_NAME),
    )


async def region_vci_analysis(
    query: str,
    batch: bool = False,
    **kwargs: Any,
) -> dict:
    """Run a regional VCI analysis workflow and return task results."""
    user_id = kwargs.get("user_id", ENVIRONMENT_CONFIG.USER_ID)
    prompt_file = kwargs.get("prompt_file", ENVIRONMENT_CONFIG.PROMPT_FILE)
    environment_data = kwargs.get(
        "environment_data", ENVIRONMENT_CONFIG.ENVIRONMENT_DATA
    )
    output_dir = kwargs.get("output_dir", ENVIRONMENT_CONFIG.OUTPUT_DIR)
    region_codes = await _environment_region_codes(query, kwargs)
    if region_codes is None:
        return {"vci_analysis_task": None}
    province_code, city_code, county_code = region_codes
    goal_description = get_prompt(
        prompt_file,
        "user/environment/vci_analysis",
        {
            "province_code": province_code,
            "city_code": city_code,
            "county_code": county_code,
        },
    )
    data_list = get_data_list(
        environment_data, "environment_analysis", "vci_analysis"
    )
    if not batch:
        output_dir = _environment_output_dir(user_id, kwargs)
    meta = get_prompt(prompt_file, "user/environment/vci_analysis_meta")
    vci_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        **_environment_submit_kwargs(kwargs),
    )
    return {"vci_analysis_task": vci_task}
