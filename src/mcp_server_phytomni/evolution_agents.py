import asyncio
from collections import deque
from json import dumps, loads
from pathlib import Path
from random import randint
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid1

from mcp.shared.exceptions import McpError

from .analyst_agents import create_output_dir, download_obs_out
from .analyst_agents import upload_analyst_agents_data, get_data_list
from .analyst_agents import submit, wait_for_completion
from .chat_agents import phyto_chat
from .config.defaults import DeepGenomeConfig
from .config.settings import SensitiveConfig
from .data_agents import nl2sql
from .knowledge_agents import multi_retrieve, retrieve_generate
from .task_manager import create_task, TaskManager, update_task
from .utils import get_prompt, get_token
import requests


dgc = DeepGenomeConfig()
sc = SensitiveConfig().load()
_manager_cache = {}


async def evo_test_analysis(
    query: str,
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
    batch: bool = False,
    enable_auto_select: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
    prompt_path: str = dgc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = dgc.FREQUENCY_PENALTY,
    n: int = dgc.N,
    presence_penalty: float = dgc.PRESENCE_PENALTY,
    reasoning_effort: Optional[str] = dgc.REASONING_EFFORT,
    stream: bool = dgc.STREAM,
    temperature: float = dgc.TEMPERATURE,
    top_p: float = dgc.TOP_P,
    user: str = dgc.USER,
) -> dict:
    async def find_spa_taxid(spa_names):

        result = []
        repo_id = "4a533117-9416-4e8b-b7cc-27b448a90095"
        endpoint = "http://1.95.74.240:8000"
        url = f"{endpoint}/v1/koosearch/repos/{repo_id}/faqs"
        headers = {
            "X-Auth-Token": await get_token(),
            "Content-Type": "application/json"
        }
        params = {
            "question": spa_names,
            "page_size": 10,
            "page_num": 1
        }
        proxy = {'http': None, 'https': None}
        response = requests.get(url, headers=headers, params=params, proxies=proxy)
        if response.status_code == 200:
            response_taxid_data = response.json()
            if response_taxid_data['total'] > 0:
                for faq in response_taxid_data['records']:
                    tax_id = faq['answer'].split('.')[0]
                    result.append((tax_id))
        return result

    prompt = get_prompt(prompt_file, 'user/get_taxid_meta',
                        {'user_query': query})
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
    target_spa_list = loads(content.replace("'", '"'))
    target_spa_taxid_list = []
    if target_spa_list['target_spa_list'][0] != 'All':
        for spa in target_spa_list['target_spa_list']:
            spa_list = await find_spa_taxid(spa)
            target_spa_taxid_list += spa_list
        target_spa_taxids = ','.join(target_spa_taxid_list)
    else:
        target_spa_taxids = 'All'

    goal_description = get_prompt(prompt_file, 'user/evolution_agents_analysis',
                                  {'gene_id': gene_id, 'target_taxid': target_spa_taxids})
    data_list = get_data_list(deepgenome_data, 'evolution_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = str(uuid1())
        output_dir = create_output_dir(
            user_id=user_id,
            task='evolution_agents_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/evolution_agents_meta')
    evo_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        enable_auto_select=enable_auto_select,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='evolution-agents-evo-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='medium',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'evolution_agents_task': evo_task}
