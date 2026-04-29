# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for protein design and computational
structural analysis.

It includes functions that leverage computational biology and bioinformatics
tools to analyze protein structures, predict protein properties, and perform
digital design workflows for protein engineering applications.
"""
import re
from typing import Dict, List
from uuid import uuid1
from typing import Any, List, Literal, Dict, Optional, Union

from .analyst_agents import get_data_list, create_output_dir, submit
from .chat_agents import phyto_chat
from .config.defaults import EnvironmentConfig
from .config.settings import SensitiveConfig
# from .config.defaults import AnalystConfig
from .utils import get_prompt

ec = EnvironmentConfig()
sc = SensitiveConfig().load()
# ac = AnalystConfig()


async def region_vci_analysis(
    query: str,
    user_id: str = ec.USER_ID,
    batch: bool = False,
    prompt_file: str = ec.PROMPT_FILE,
    environment_data: str = ec.ENVIRONMENT_DATA,
    region_code: str = ec.REGION_CODE,
    output_dir: str = ec.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ec.OBS_SERVER,
    bucket_name: str = ec.BUCKET_NAME,
    analysis_url: str = ec.ANALYSIS_URL,
    region: str = ec.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = ec.RESOURCE,
    app_id_dict: Dict[str, str] = ec.APP_ID,
    timeout: float = ec.TIMEOUT,
    retriable_codes: List[int] = ec.RETRIABLE_CODES,
    max_retries: int = ec.MAX_RETRIES,
    max_poll: float = ec.MAX_POLL,
    prompt_path: str = ec.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = ec.FREQUENCY_PENALTY,
    n: int = ec.N,
    presence_penalty: float = ec.PRESENCE_PENALTY,
    reasoning_effort: Optional[str] = ec.REASONING_EFFORT,
    stream: bool = ec.STREAM,
    temperature: float = ec.TEMPERATURE,
    top_p: float = ec.TOP_P,
    user: str = ec.USER,
) -> dict:
    with open(region_code) as fi:
        region_info = fi.read()
    prompt = get_prompt(prompt_file, 'user/environment/get_code_query',
                        {'json_dict': region_info, 'query': query})
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
    content = phyto_response['choices'][0]['message']['content']
    try:
        code_info = re.findall(r'<result>(.*?)</result>', content)[0]
    except Exception as e:
        return {'vci_analysis_task': None}
    code_info = code_info.split('|')
    province_code, city_code, county_code = (code_info + [None] * 3)[:3]

    goal_description = get_prompt(prompt_file, 'user/environment/vci_analysis',
                                  {'province_code': province_code,
                                   'city_code': city_code,
                                   'county_code': county_code})
    data_list = get_data_list(environment_data, 'environment_analysis', 'vci_analysis')
    if not batch:
        if not user_id:
            user_id = str(uuid1())
        output_dir = create_output_dir(user_id, 'vci_analysis_task')
    meta = get_prompt(prompt_file, 'user/environment/vci_analysis_meta')
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
        task_name='environment-agents-vci-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='large',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'vci_analysis_task': vci_task}
