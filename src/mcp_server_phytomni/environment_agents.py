# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Environment agents for regional vegetation index analysis workflows."""

import re
from typing import Any
from uuid import uuid1

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


def _resource_dict(value: Any, default: dict) -> dict:
    """Return a copied nested resource dictionary."""
    source = default if value is None else value
    return {key: dict(item) for key, item in source.items()}


def _environment_chat_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return chat kwargs for environment code extraction."""
    retriable_codes = kwargs.get("retriable_codes")
    return {
        "prompt_file": kwargs.get(
            "prompt_file", ENVIRONMENT_CONFIG.PROMPT_FILE
        ),
        "prompt_path": kwargs.get(
            "prompt_path", ENVIRONMENT_CONFIG.PROMPT_PATH
        ),
        "api_key": kwargs.get(
            "api_key", SENSITIVE_CONFIG.API_KEY.get_secret_value()
        ),
        "base_url": kwargs.get("base_url", SENSITIVE_CONFIG.BASE_URL),
        "model": kwargs.get("model", SENSITIVE_CONFIG.MODEL_ID),
        "frequency_penalty": kwargs.get(
            "frequency_penalty", ENVIRONMENT_CONFIG.FREQUENCY_PENALTY
        ),
        "n": kwargs.get("n", ENVIRONMENT_CONFIG.N),
        "presence_penalty": kwargs.get(
            "presence_penalty", ENVIRONMENT_CONFIG.PRESENCE_PENALTY
        ),
        "reasoning_effort": kwargs.get(
            "reasoning_effort", ENVIRONMENT_CONFIG.REASONING_EFFORT
        ),
        "stream": kwargs.get("stream", ENVIRONMENT_CONFIG.STREAM),
        "temperature": kwargs.get(
            "temperature", ENVIRONMENT_CONFIG.TEMPERATURE
        ),
        "top_p": kwargs.get("top_p", ENVIRONMENT_CONFIG.TOP_P),
        "user": kwargs.get("user", ENVIRONMENT_CONFIG.USER),
        "timeout": kwargs.get("timeout", ENVIRONMENT_CONFIG.TIMEOUT),
        "retriable_codes": (
            list(ENVIRONMENT_CONFIG.RETRIABLE_CODES)
            if retriable_codes is None
            else list(retriable_codes)
        ),
        "max_retries": kwargs.get(
            "max_retries", ENVIRONMENT_CONFIG.MAX_RETRIES
        ),
    }


def _environment_submit_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return Analyst submit kwargs for the environment workflow."""
    return {
        "execute_code": True,
        "model_url": kwargs.get("model_url", SENSITIVE_CONFIG.CODER_URL),
        "model_name": kwargs.get("model_name", SENSITIVE_CONFIG.CODER_MODEL),
        "coder_api_key": kwargs.get(
            "coder_api_key", SENSITIVE_CONFIG.CODER_API_KEY.get_secret_value()
        ),
        "access_key_id": kwargs.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
        "secret_access_key": kwargs.get(
            "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
        ),
        "obs_server": kwargs.get("obs_server", ENVIRONMENT_CONFIG.OBS_SERVER),
        "bucket_name": kwargs.get(
            "bucket_name", ENVIRONMENT_CONFIG.BUCKET_NAME
        ),
        "analysis_url": kwargs.get(
            "analysis_url", ENVIRONMENT_CONFIG.ANALYSIS_URL
        ),
        "region": kwargs.get("region", ENVIRONMENT_CONFIG.ANALYSIS_REGION),
        "task_name": "environment-agents-vci-task",
        "resource_dict": _resource_dict(
            kwargs.get("resource_dict"), ENVIRONMENT_CONFIG.RESOURCE
        ),
        "app_id_dict": dict(
            kwargs.get("app_id_dict") or ENVIRONMENT_CONFIG.APP_ID
        ),
        "compute_resource": "large",
        "timeout": kwargs.get("timeout", ENVIRONMENT_CONFIG.TIMEOUT),
        "retriable_codes": _environment_chat_kwargs(kwargs)["retriable_codes"],
        "max_retries": kwargs.get(
            "max_retries", ENVIRONMENT_CONFIG.MAX_RETRIES
        ),
        "max_poll": kwargs.get("max_poll", ENVIRONMENT_CONFIG.MAX_POLL),
    }


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
