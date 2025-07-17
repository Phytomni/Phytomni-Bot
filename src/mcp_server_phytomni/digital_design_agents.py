# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
from typing import Dict, List
from uuid import uuid1

from .analyst_agents import get_data_list, create_output_dir, submit
from .config.defaults import DigitalDesignConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt

ddc = DigitalDesignConfig()
sc = SensitiveConfig().load()


async def protein_design_analysis(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = ddc.PROMPT_FILE,
    deepgenome_data: str = ddc.DEEPGENOME_DATA,
    output_dir: str = ddc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ddc.OBS_SERVER,
    bucket_name: str = ddc.BUCKET_NAME,
    analysis_url: str = ddc.ANALYSIS_URL,
    region: str = ddc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = ddc.RESOURCE,
    app_id_dict: Dict[str, str] = ddc.APP_ID,
    timeout: float = ddc.TIMEOUT,
    retriable_codes: List[int] = ddc.RETRIABLE_CODES,
    max_retries: int = ddc.MAX_RETRIES,
    max_poll: float = ddc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(prompt_file, 'user/protein_design_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'protein_design_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'protein_design_task')
    meta = get_prompt(prompt_file, 'user/protein_design_analysis_meta')
    pr_design_task = await submit(
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
        task_name='deepgenome-agents-prdesign-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='medium',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'protein_design_task': pr_design_task}


async def design_module(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = True,
    prompt_file: str = ddc.PROMPT_FILE,
    deepgenome_data: str = ddc.DEEPGENOME_DATA,
    output_dir: str = ddc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ddc.OBS_SERVER,
    bucket_name: str = ddc.BUCKET_NAME,
    analysis_url: str = ddc.ANALYSIS_URL,
    region: str = ddc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = ddc.RESOURCE,
    app_id_dict: Dict[str, str] = ddc.APP_ID,
    timeout: float = ddc.TIMEOUT,
    retriable_codes: List[int] = ddc.RETRIABLE_CODES,
    max_retries: int = ddc.MAX_RETRIES,
    max_poll: float = ddc.MAX_POLL,
) -> dict:
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='design_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    protein_design_task = await protein_design_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return protein_design_task
