# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
from uuid import uuid1
from typing import List, Dict

from .analyst_agents import create_output_dir, get_data_list, submit
from .config.defaults import GeneNetworkConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt

gnc = GeneNetworkConfig()
sc = SensitiveConfig().load()


async def network_analysis(
    species: str,
    to_id: str,
    user_id: str = gnc.USER_ID,
    batch: bool = False,
    prompt_file: str = gnc.PROMPT_FILE,
    deepgenome_data: str = gnc.DEEPGENOME_DATA,
    output_dir: str = gnc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = gnc.OBS_SERVER,
    bucket_name: str = gnc.BUCKET_NAME,
    analysis_url: str = gnc.ANALYSIS_URL,
    region: str = gnc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = gnc.RESOURCE,
    app_id_dict: Dict[str, str] = gnc.APP_ID,
    timeout: float = gnc.TIMEOUT,
    retriable_codes: List[int] = gnc.RETRIABLE_CODES,
    max_retries: int = gnc.MAX_RETRIES,
    max_poll: float = gnc.MAX_POLL,
) -> dict:
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='network_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )

    goal_description = get_prompt(
        prompt_file, 'user/gene_network_analysis',
        {'to_id': to_id})
    data_list = get_data_list(deepgenome_data, 
                              'gene_network_analysis',
                              species)
    meta = get_prompt(prompt_file, 
                      'user/gene_network_analysis_meta')
    gene_network_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id, 
        is_create_dir=False, 
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
        task_name='gene-network-agents-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'network_task': gene_network_task}
