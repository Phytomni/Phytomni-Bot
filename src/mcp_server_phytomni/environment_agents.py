# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Environment agents for regional vegetation index analysis workflows."""

import re
from typing import Dict, List
from uuid import uuid1
from typing import Optional

from .analyst_agents import get_data_list, create_output_dir, submit
from .chat_agents import phyto_chat
from .config.defaults import EnvironmentConfig
from .config.settings import SensitiveConfig

from .utils import get_prompt, load_text_file

ENVIRONMENT_CONFIG = EnvironmentConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)


async def region_vci_analysis(
    query: str,
    user_id: str = ENVIRONMENT_CONFIG.USER_ID,
    batch: bool = False,
    prompt_file: str = ENVIRONMENT_CONFIG.PROMPT_FILE,
    environment_data: str = ENVIRONMENT_CONFIG.ENVIRONMENT_DATA,
    region_code: str = ENVIRONMENT_CONFIG.REGION_CODE,
    output_dir: str = ENVIRONMENT_CONFIG.OUTPUT_DIR,
    model_url: str = SENSITIVE_CONFIG.CODER_URL,
    model_name: str = SENSITIVE_CONFIG.CODER_MODEL,
    coder_api_key: str = SENSITIVE_CONFIG.CODER_API_KEY.get_secret_value(),
    access_key_id: str = DEFAULT_ACCESS_KEY_ID,
    secret_access_key: str = DEFAULT_SECRET_ACCESS_KEY,
    obs_server: str = ENVIRONMENT_CONFIG.OBS_SERVER,
    bucket_name: str = ENVIRONMENT_CONFIG.BUCKET_NAME,
    analysis_url: str = ENVIRONMENT_CONFIG.ANALYSIS_URL,
    region: str = ENVIRONMENT_CONFIG.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = ENVIRONMENT_CONFIG.RESOURCE,
    app_id_dict: Dict[str, str] = ENVIRONMENT_CONFIG.APP_ID,
    timeout: float = ENVIRONMENT_CONFIG.TIMEOUT,
    retriable_codes: List[int] = ENVIRONMENT_CONFIG.RETRIABLE_CODES,
    max_retries: int = ENVIRONMENT_CONFIG.MAX_RETRIES,
    max_poll: float = ENVIRONMENT_CONFIG.MAX_POLL,
    prompt_path: str = ENVIRONMENT_CONFIG.PROMPT_PATH,
    api_key: str = SENSITIVE_CONFIG.API_KEY.get_secret_value(),
    base_url: str = SENSITIVE_CONFIG.BASE_URL,
    model: str = SENSITIVE_CONFIG.MODEL_ID,
    frequency_penalty: float = ENVIRONMENT_CONFIG.FREQUENCY_PENALTY,
    n: int = ENVIRONMENT_CONFIG.N,
    presence_penalty: float = ENVIRONMENT_CONFIG.PRESENCE_PENALTY,
    reasoning_effort: Optional[str] = ENVIRONMENT_CONFIG.REASONING_EFFORT,
    stream: bool = ENVIRONMENT_CONFIG.STREAM,
    temperature: float = ENVIRONMENT_CONFIG.TEMPERATURE,
    top_p: float = ENVIRONMENT_CONFIG.TOP_P,
    user: str = ENVIRONMENT_CONFIG.USER,
) -> dict:
    """Run a regional VCI analysis workflow and return task results."""
    region_info = load_text_file(region_code)
    prompt = get_prompt(
        prompt_file,
        "user/environment/get_code_query",
        {"json_dict": region_info, "query": query},
    )
    phyto_response = await phyto_chat(
        user_query=prompt,
        prompt_file=prompt_file,
        prompt_path=prompt_path,
        api_key=api_key,
        base_url=base_url,
        model=model,
        frequency_penalty=frequency_penalty,
        n=n,
        presence_penalty=presence_penalty,
        reasoning_effort=reasoning_effort,
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    if phyto_response is None:
        return {"vci_analysis_task": None}

    content = phyto_response["choices"][0]["message"]["content"]
    try:
        code_info = re.findall(r"<result>(.*?)</result>", content)[0]
    except Exception:
        return {"vci_analysis_task": None}
    code_info = code_info.split("|")
    province_code, city_code, county_code = (code_info + [None] * 3)[:3]

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
        if not user_id:
            user_id = str(uuid1())
        output_dir = create_output_dir(user_id, "vci_analysis_task")
    meta = get_prompt(prompt_file, "user/environment/vci_analysis_meta")
    vci_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name="environment-agents-vci-task",
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource="large",
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {"vci_analysis_task": vci_task}
