# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
from asyncio import gather
from json import loads
from typing import Dict, List, Optional, Union

from .analyst_agents import retrieve_plan_submit
from .chat_agents import phyto_chat
from .config.defaults import InSilicoResearchConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt

isrc = InSilicoResearchConfig()
sc = SensitiveConfig()


async def extract_goals(
    user_query: str,
    prompt_file: str = isrc.PROMPT_FILE,
    prompt_path: str = isrc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = isrc.FREQUENCY_PENALTY,
    n: int = isrc.N,
    presence_penalty: float = isrc.PRESENCE_PENALTY,
    reasoning_effort: str = isrc.REASONING_EFFORT,
    stream: bool = isrc.STREAM,
    temperature: float = isrc.TEMPERATURE,
    top_p: float = isrc.TOP_P,
    user: str = isrc.USER,
    timeout: float = isrc.TIMEOUT,
    retriable_codes: List[int] = isrc.RETRIABLE_CODES,
    max_retries: int = isrc.MAX_RETRIES
) -> List[Dict[str, str]]:
    user_query = get_prompt(
        prompt_file, 'user/in_silico_research_goals',
        {'paper_text': user_query})
    phyto_response = await phyto_chat(
        user_query=user_query,
        prompt_file=prompt_file,
        prompt_path=prompt_path,
        api_key=api_key,
        base_url=base_url,
        model=model,
        frequency_penalty=frequency_penalty,
        n=n,
        presence_penalty=presence_penalty,
        reasoning_effort=reasoning_effort,
        response_format={
            'type': 'json_schema',
            'json_schema': {
                'type': 'array',
                'description':
                    'A list of research objectives derived from the paper. '
                    'Each objective is a dictionary containing a consolidated '
                    'goal and its supporting context.',
                'items': {
                    'type': 'object',
                    'description':
                        'Represents a single, end-to-end research objective.',
                    'properties': {
                        'goal': {
                            'type': 'string',
                            'description':
                                'A comprehensive, single-string summary of '
                                'the entire workflow required to reproduce a '
                                'key finding or figure, detailing all major '
                                'steps from data acquisition to final '
                                'analysis.',
                        },
                        'context': {
                            'type': 'string',
                            'description':
                                'Aggregated text snippets from the original '
                                'paper (e.g., Methods, Results, Figure '
                                'Legends) that provide the necessary details, '
                                'parameters, and evidence for executing the '
                                'specified goal.',
                        }
                    },
                    'required': ['goal', 'context']
                }
            }
        },
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    return loads(phyto_response['choices'][0]['message']['content'])


async def in_silico_research(
    user_query: str,
    data_list: Dict[str, str],
    user_id: str = isrc.USER_ID,
    is_create_dir: bool = isrc.CREATE_DIR,
    output_dir: str = isrc.OUTPUT_DIR,
    repo_id_dict: Optional[Dict[str, int]] = isrc.REPO_ID_DICT,
    page_num: int = isrc.PAGE_NUM,
    filter_string: Optional[str] = isrc.FILTER_STRING,
    scope: str = isrc.SCOPE,
    extra_repo_ids: Optional[List[str]] = isrc.EXTRA_REPO_IDS,
    score_threshold: float = isrc.SCORE_THRESHOLD,
    top_n: int = isrc.TOP_N,
    prompt_file: str = isrc.PROMPT_FILE,
    prompt_path: str = isrc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = isrc.FREQUENCY_PENALTY,
    max_tokens: int = isrc.MAX_TOKENS,
    n: int = isrc.N,
    presence_penalty: float = isrc.PRESENCE_PENALTY,
    reasoning_effort: str = isrc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = isrc.RESPONSE_FORMAT,
    stream: bool = isrc.STREAM,
    temperature: float = isrc.TEMPERATURE,
    top_p: float = isrc.TOP_P,
    user: str = isrc.USER,
    execute_code: bool = isrc.EXECUTE_CODE,
    timeout: float = isrc.TIMEOUT,
    retriable_codes: List[int] = isrc.RETRIABLE_CODES,
    max_retries: int = isrc.MAX_RETRIES,
) -> List:
    goal_list = await extract_goals(
        user_query=user_query,
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
        max_retries=max_retries
    )
    tasks = [
        retrieve_plan_submit(
            goal_description=goal_meta['goal'],
            data_list=data_list,
            user_id=user_id,
            is_create_dir=is_create_dir,
            output_dir=output_dir,
            repo_id_dict=repo_id_dict,
            page_num=page_num,
            filter_string=filter_string,
            scope=scope,
            extra_repo_ids=extra_repo_ids,
            score_threshold=score_threshold,
            top_n=top_n,
            prompt_file=prompt_file,
            prompt_path=prompt_path,
            api_key=api_key,
            base_url=base_url,
            model=model,
            frequency_penalty=frequency_penalty,
            max_tokens=max_tokens,
            n=n,
            presence_penalty=presence_penalty,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            stream=stream,
            temperature=temperature,
            top_p=top_p,
            user=user,
            execute_code=execute_code,
            meta_meta='\n\n'+goal_meta['context'],
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
        )
        for goal_meta in goal_list
    ]
    response = await gather(*tasks, return_exceptions=True)
    return response
