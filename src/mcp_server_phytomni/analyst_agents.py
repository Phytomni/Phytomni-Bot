# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyichao (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""
This module provides a suite of asynchronous functions for interacting with a
bioinformatics analysis platform. It enables submitting analysis tasks,
monitoring their status, and managing them programmatically. The module
leverages a combination of HTTP requests for API communication, object storage
for data handling, and large language models for generating analysis plans.

Key functionalities include:
- Submitting complex bioinformatics tasks with specified parameters and data.
- Generating analysis plans using language models, with or without retrieval
  augmentation.
- Monitoring the lifecycle of submitted tasks (e.g., pending, running,
  completed, failed).
- Handling asynchronous operations with retries and timeouts for robustness.
- Uploading and deleting data from an Object Storage Service (OBS).

The module is designed to be used in scenarios where automated, reproducible,
and scalable bioinformatics analyses are required. It abstracts away the
complexities of direct API and service interactions, providing a simplified
interface for developers and researchers.
"""
import asyncio
import datetime
import json
import time
from pathlib import Path
from random import uniform
from traceback import format_exc
from typing import Any, List, Literal, Dict, Optional, Union
from uuid import uuid1

from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from obs import GetObjectHeader, PutObjectHeader, ObsClient

from .chat_agents import phyto_chat
from .config.defaults import AnalystConfig
from .config.settings import SensitiveConfig
from .knowledge_agents import multi_retrieve
from .utils import get_prompt, get_token

ac = AnalystConfig()
sc = SensitiveConfig().load()


async def submit(
    goal_description: str,
    data_list: Dict[str, str],
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    meta: str = '',
    execute_code: bool = ac.EXECUTE_CODE,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    task_name: str = ac.TASK_NAME,
    resource_dict: Dict[str, Dict[str, int]] = ac.RESOURCE,
    app_id_dict: Dict[str, str] = ac.APP_ID,
    compute_resource: Literal[
        'small', 'medium', 'large'
    ] = ac.COMPUTE_RESOURCE,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
    max_poll: float = ac.MAX_POLL,
) -> Dict[str, str]:
    """
    Submits an analysis task to the Bioinformatics Agents platform.

    This function constructs and sends a request to initiate a new analysis
    task based on the provided parameters. It handles the creation of a JSON
    payload, uploads it to object storage, and then triggers the analysis
    workflow.

    Args:
        goal_description: A natural language description of the analysis goals.
        data_list: A dictionary of input data sources, where keys are
            identifiers and values are their descriptions or paths.
        output_dir: The OBS path for storing analysis results.
        meta: Step-by-step instructions for processing.
        execute_code: A boolean flag to enable or disable automated code
            execution within the workflow.
        model_url: The URL of the coding model service.
        model_name: The name of the coding model to be used.
        coder_api_key: The API key for the coding model service.
        access_key_id: The access key ID for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The server endpoint for the OBS.
        bucket_name: The name of the OBS bucket.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        task_name: The name assigned to the task on the AI4S platform.
        resource_dict: A dictionary defining the computational resources
            (CPU, memory) for different resource levels.
        app_id_dict: A dictionary mapping compute resource levels to
            application IDs.
        compute_resource: The level of compute resources to allocate for the
            task ('small', 'medium', or 'large').
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.
        max_poll: The maximum total duration in seconds to monitor the task.

    Returns:
        A dictionary containing the submission response, which includes the
        task ID, output directory, job name, and compute resource details.

    Raises:
        McpError: If the task submission fails after all retries.
        OSError: If uploading the data information to OBS fails.
    """
    if not user_id:
        user_id = uuid1()
    if is_create_dir:
        output_dir = create_output_dir(
            user_id=user_id,
            task='analysis_agents_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta += '\nlast step, compress the output folder into a zip file '
    meta += '(zip -r $output_dir.zip $output_dir).'
    data = {
        'goal_description': goal_description,
        'data_list': data_list,
        'output_dir': output_dir,
        'meta': meta,
        'execute_code': execute_code,
        'model_url': model_url,
        'model_name': model_name,
        'api_key': coder_api_key,
    }
    josn_file = Path(f'{uuid1()}.json')
    try:
        with open(josn_file, 'w', encoding='utf-8') as open_json:
            json.dump(data, open_json)
        data_path = upload_analyst_agents_data(
            analyst_agents_datapath=str(josn_file),
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
            )
    except OSError as exc:
        raise OSError('Data information upload obs error.') from exc
    finally:
        if josn_file.exists():
            josn_file.unlink()

    job_headers = {'Content-Type': 'application/json',
                   'X-Auth-Token': await get_token(timeout=timeout,
                                                   region=region)}
    task_name = task_name.replace('_', '-')
    time_stamp = datetime.datetime.now().strftime('%H%M%S-%f')
    job_name = f'{task_name}-{time_stamp}'
    job_data = {
        'name': job_name,
        'labels': [],
        'description': '',
        'timeout': max_poll,
        'output_dir': '',
        'tasks': [{
            'task_name': f'analyst-agents-{compute_resource}',
            'display_name': job_name,
            'inputs': [
                {
                    'name': 'obs-mount',
                    'type': 'DIRECTORY',
                    'description': '',
                    'required': True,
                    'pattern': '',
                    'values': ['phytomni:/agent_data/'],
                    'enum': [],
                    'concurrent': '',
                },
                {
                    'name': 'meta-file',
                    'type': 'FILE',
                    'description': '',
                    'required': True,
                    'pattern': '',
                    'values': [data_path],
                    'enum': [],
                    'concurrent': '',
                },
            ],
            'outputs': [],
            'output_dir': '',
            'resources': {
                'cpu': f"{resource_dict[compute_resource]['cpu']}C",
                'cpu_type': 'X86',
                'gpu': '0',
                'gpu_type': '',
                'memory': f"{resource_dict[compute_resource]['memory']}G"
            },
            'summary': '',
            'labels': [],
        }],
        'io_acc_id': '',
        'ioType': '',
        'priority': 0,
        'automatic': True,
        'node_labels': [],
        'tool_id': app_id_dict[compute_resource],
        'tool_type': 'app',
    }
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    analysis_url,
                    headers=job_headers,
                    json=job_data,
                )
                if response.status_code == 201:
                    return {
                        'task_id': json.loads(response.text)['id'],
                        'output_dir': output_dir,
                        'job_name': job_name,
                        'compute_resource': compute_resource,
                    }
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message='Failed to submit task'))

            except HTTPStatusError as e:
                if (
                    hasattr(e, 'response') and
                    e.response is not None and
                    e.response.status_code in retriable_codes and
                    attempt < max_retries
                ):
                    wait_time = (2 ** attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Failed to submit task: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}',
                )) from e


async def task_delete(task_id: str,
                      analysis_url: str = ac.ANALYSIS_URL,
                      region: str = ac.ANALYSIS_REGION,
                      timeout: float = ac.TIMEOUT,
                      retriable_codes: List[int] = ac.RETRIABLE_CODES,
                      max_retries: int = ac.MAX_RETRIES,
                      ) -> str:
    """
    Deletes a specified task from the analysis platform.

    This function sends a request to terminate and delete a task using its
    unique ID. It includes retry logic for transient network or server issues.

    Args:
        task_id: The unique identifier of the task to be deleted.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A confirmation message indicating that the task was successfully
        deleted.

    Raises:
        McpError: If the task deletion fails after all retries.
    """
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url=f'{analysis_url}/{task_id}/terminate',
                    headers={'Content-Type': 'application/json',
                             'X-Auth-Token': await get_token(timeout=timeout,
                                                             region=region)},
                    json={'force': True},
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return f'Delete task {task_id} success.'
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message='Failed to delete task'))

            except HTTPStatusError as e:
                if (
                    hasattr(e, 'response') and
                    e.response is not None and
                    e.response.status_code in retriable_codes and
                    attempt < max_retries
                ):
                    wait_time = (2 ** attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Failed to delete task: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}',
                )) from e


async def task_status(task_id: str,
                      analysis_url: str = ac.ANALYSIS_URL,
                      region: str = ac.ANALYSIS_REGION,
                      timeout: float = ac.TIMEOUT,
                      retriable_codes: List[int] = ac.RETRIABLE_CODES,
                      max_retries: int = ac.MAX_RETRIES,
                      ) -> dict:
    """
    Checks the execution status of a specified task.

    This function queries the analysis platform for the current status of a
    task identified by its ID. It provides details such as whether the task is
    pending, running, completed, or failed.

    Args:
        task_id: The unique identifier of the task to check.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A dictionary containing the task's status details, including its ID,
        current status, and potentially results or error information.

    Raises:
        McpError: If checking the task status fails after all retries.
    """
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(
                    f'{analysis_url}/{task_id}',
                    headers={'Content-Type': 'application/json',
                             'X-Auth-Token': await get_token(timeout=timeout,
                                                             region=region)},
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return response.json()
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Check task {task_id} status failed.'))

            except HTTPStatusError as e:
                if (
                    hasattr(e, 'response') and
                    e.response is not None and
                    e.response.status_code in retriable_codes and
                    attempt < max_retries
                ):
                    wait_time = (2 ** attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Failed to delete task: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}',
                )) from e


async def task_log(task_id: str,
                   analysis_url: str = ac.ANALYSIS_URL,
                   compute_resource: Literal[
                       'small', 'medium', 'large'
                   ] = ac.COMPUTE_RESOURCE,
                   region: str = ac.ANALYSIS_REGION,
                   timeout: float = ac.TIMEOUT,
                   retriable_codes: List[int] = ac.RETRIABLE_CODES,
                   max_retries: int = ac.MAX_RETRIES,
                   ) -> dict:
    """
    Retrieves the execution log for a specified task.

    This function fetches the logs generated by a task during its execution,
    which can be useful for debugging or monitoring progress.

    Args:
        task_id: The unique identifier of the task.
        analysis_url: The URL for the analysis submission API.
        compute_resource: The level of compute resources used by the task.
        region: The geographical region of the analysis service.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A dictionary containing the task's log data.

    Raises:
        McpError: If fetching the task log fails after all retries.
    """
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(
                    f'{analysis_url}/{task_id}/logs'
                    f'?task_name=analyst-agents-{compute_resource}',
                    headers={'Content-Type': 'application/json',
                             'X-Auth-Token': await get_token(timeout=timeout,
                                                             region=region)},
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return response.json()
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Check task {task_id} log failed.'))

            except HTTPStatusError as e:
                if (
                    hasattr(e, 'response') and
                    e.response is not None and
                    e.response.status_code in retriable_codes and
                    attempt < max_retries
                ):
                    wait_time = (2 ** attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Failed to delete task: {str(e)}',
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f'Network error: {str(e)}',
                )) from e


async def plan_submit(
    goal_description: str,
    data_list: Dict[str, str],
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    prompt_file: str = ac.PROMPT_FILE,
    prompt_path: str = ac.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = ac.FREQUENCY_PENALTY,
    n: int = ac.N,
    presence_penalty: float = ac.PRESENCE_PENALTY,
    reasoning_effort: str = ac.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = ac.RESPONSE_FORMAT,
    stream: bool = ac.STREAM,
    temperature: float = ac.TEMPERATURE,
    top_p: float = ac.TOP_P,
    user: str = ac.USER,
    execute_code: bool = ac.EXECUTE_CODE,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    task_name: str = ac.TASK_NAME + '-plan',
    resource_dict: Dict[str, Dict[str, int]] = ac.RESOURCE,
    app_id_dict: Dict[str, str] = ac.APP_ID,
    compute_resource: Literal[
        'small', 'medium', 'large'
    ] = ac.COMPUTE_RESOURCE,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
) -> Dict[str, str]:
    """
    Generates a plan using a language model and submits it for execution.

    This function first utilizes the `phyto_chat` service to process the
    `goal_description` and generate a structured plan. This plan, along with
    the original goal and data, is then passed to the `submit` function for
    execution.

    Args:
        goal_description: A natural language description of the analysis goals.
        data_list: A dictionary of input data sources, where keys are
            identifiers and values are their descriptions or paths.
        output_dir: The OBS path for storing analysis results.
        prompt_file: The path to the prompt template file.
        prompt_path: The path or key for the specific system prompt.
        api_key: The API key for the language model.
        base_url: The base URL of the language model API.
        model: The identifier of the language model.
        frequency_penalty: The penalty for token repetition.
        n: The number of plan choices to generate.
        presence_penalty: The penalty for new tokens.
        reasoning_effort: The reasoning effort for the language model.
        response_format: The desired output format from the language model.
        stream: A flag to enable real-time token streaming.
        temperature: The randomness control for generation.
        top_p: The nucleus sampling threshold.
        user: A unique session identifier for the user.
        execute_code: A boolean flag to enable or disable automated code
            execution within the workflow.
        model_url: The URL of the coding model service.
        model_name: The name of the coding model to be used.
        coder_api_key: The API key for the coding model service.
        access_key_id: The access key ID for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The server endpoint for the OBS.
        bucket_name: The name of the OBS bucket.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        task_name: The name assigned to the task on the AI4S platform.
        resource_dict: A dictionary defining the computational resources.
        app_id_dict: A dictionary mapping compute resources to app IDs.
        compute_resource: The level of compute resources to allocate.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A dictionary with the response from the `submit` function, typically
        containing submission status information.

    Raises:
        McpError: If plan generation or submission fails.
    """
    phyto_response = await phyto_chat(
        user_query=get_prompt(prompt_file, 'user/analysis',
                              {'user_query': goal_description}),
        prompt_file=prompt_file,
        prompt_path=prompt_path,
        api_key=api_key,
        base_url=base_url,
        model=model,
        frequency_penalty=frequency_penalty,
        n=n,
        presence_penalty=presence_penalty,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    task_dict = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        meta=phyto_response['choices'][0]['message']['content'],
        execute_code=execute_code,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name=task_name,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource=compute_resource,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    return task_dict


async def retrieve_plan_submit(
    goal_description: str,
    data_list: Dict[str, str],
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    retrieve_url: str = ac.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = ac.REPO_ID_DICT,
    page_num: int = ac.PAGE_NUM,
    filter_string: Optional[str] = ac.FILTER_STRING,
    scope: str = ac.SCOPE,
    extra_repo_ids: Optional[List[str]] = ac.EXTRA_REPO_IDS,
    rerank_url: str = ac.RERANK_URL,
    rerank_batch_size: int = ac.RERANK_BATCH_SIZE,
    score_threshold: float = ac.SCORE_THRESHOLD,
    top_n: int = ac.TOP_N,
    prompt_file: str = ac.PROMPT_FILE,
    prompt_path: str = ac.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = ac.FREQUENCY_PENALTY,
    max_tokens: int = ac.MAX_TOKENS,
    n: int = ac.N,
    presence_penalty: float = ac.PRESENCE_PENALTY,
    reasoning_effort: str = ac.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = ac.RESPONSE_FORMAT,
    stream: bool = ac.STREAM,
    temperature: float = ac.TEMPERATURE,
    top_p: float = ac.TOP_P,
    user: str = ac.USER,
    execute_code: bool = ac.EXECUTE_CODE,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    task_name: str = ac.TASK_NAME + '-retrieve-plan',
    resource_dict: Dict[str, Dict[str, int]] = ac.RESOURCE,
    app_id_dict: Dict[str, str] = ac.APP_ID,
    compute_resource: Literal[
        'small', 'medium', 'large'
    ] = ac.COMPUTE_RESOURCE,
    meta_meta: Optional[str] = None,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
) -> Dict[str, str]:
    """
    Performs retrieval-augmented plan generation and submits it for execution.

    This function first retrieves relevant documents, uses them to augment the
    goal description, generates a plan with a language model, and then submits
    this plan for execution.

    Args:
        goal_description: A natural language description of the analysis goals.
        data_list: A dictionary of input data sources, where keys are
            identifiers and values are their descriptions or paths.
        output_dir: The OBS path for storing analysis results.
        retrieve_url: The URL for the document retrieval service.
        repo_id_dict: A dictionary of repository IDs for retrieval.
        page_num: The page number for retrieval results.
        filter_string: A string for filtering retrieval results.
        scope: The scope of the retrieval ('doc', 'keyword', 'both').
        extra_repo_ids: A list of additional repository IDs.
        rerank_url: The URL for the reranking service.
        rerank_batch_size: The batch size for reranking.
        score_threshold: The minimum score for retrieved documents.
        top_n: The number of top documents to retrieve.
        prompt_file: The path to the prompt template file.
        prompt_path: The path or key for the system prompt.
        api_key: The API key for the language model.
        base_url: The base URL of the language model API.
        model: The identifier of the language model.
        frequency_penalty: The penalty for token repetition.
        max_tokens: The maximum number of tokens to generate.
        n: The number of plan choices to generate.
        presence_penalty: The penalty for new tokens.
        reasoning_effort: The reasoning effort for the language model.
        response_format: The desired output format.
        stream: A flag for real-time token streaming.
        temperature: The randomness control for generation.
        top_p: The nucleus sampling threshold.
        user: A unique session identifier for the user.
        execute_code: A boolean flag to enable or disable automated code
            execution within the workflow.
        model_url: The URL of the coding model service.
        model_name: The name of the coding model.
        coder_api_key: The API key for the coding model.
        access_key_id: The access key ID for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The server endpoint for OBS.
        bucket_name: The name of the OBS bucket.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        task_name: The name of the task.
        resource_dict: A dictionary of computational resources.
        app_id_dict: A dictionary mapping compute resources to app IDs.
        compute_resource: The level of compute resources to allocate.
        meta_meta: Additional metadata to append to the generated plan.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.

    Returns:
        A dictionary with the response from the `submit` function.

    Raises:
        McpError: If retrieval, plan generation, or submission fails.
    """
    if not repo_id_dict:
        repo_id_dict = ac.REPO_ID_DICT
    retrieve_response = await multi_retrieve(
        user_query=goal_description,
        retrieve_url=retrieve_url,
        repo_id_dict=repo_id_dict,
        page_num=page_num,
        filter_string=filter_string,
        scope=scope,
        extra_repo_ids=extra_repo_ids,
        rerank_url=rerank_url,
        rerank_batch_size=rerank_batch_size,
        score_threshold=score_threshold,
        top_n=top_n,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    retrieve_results = []
    total_length = 0
    for file_id, eachdoc in enumerate(retrieve_response['doc_list']):
        if eachdoc['subtitle']:
            current_fragment = (
                f"[document {file_id+1} begin] {eachdoc['title']}\n"
                f"{eachdoc['subtitle']}\n{eachdoc['content']} "
                f'[document {file_id+1} end]')
        else:
            current_fragment = (
                f"[document {file_id+1} begin] {eachdoc['title']}\n"
                f"{eachdoc['content']} [document {file_id+1} end]")
        if total_length + len(current_fragment) <= max_tokens:
            retrieve_results.append(current_fragment)
            total_length += len(current_fragment)
        else:
            break
    retrieve_results = '\n\n'.join(retrieve_results)
    user_query = get_prompt(
        prompt_file, 'user/analysis_retrieve',
        {'retrieve_results': retrieve_results, 'user_query': goal_description})
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
        response_format=response_format,
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    meta = (
        phyto_response['choices'][0]['message']['content'] + meta_meta
        if meta_meta else phyto_response['choices'][0]['message']['content']
    )
    task_dict = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        meta=meta,
        execute_code=execute_code,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name=task_name,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource=compute_resource,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    return task_dict


async def wait_for_completion(
    task_id: str,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
) -> Dict[str, Any]:
    """
    Asynchronously monitors a task until it completes or times out.

    This function repeatedly polls the status of a task until it reaches a
    terminal state (e.g., 'SUCCEEDED', 'FAILED', 'CANCELLED') or the
    maximum polling time is exceeded.

    Args:
        task_id: The unique identifier of the task to monitor.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.
        poll_interval: The interval in seconds between polling for task status.
        max_poll: The maximum total duration in seconds to monitor the task.

    Returns:
        A dictionary containing the final status of the task.

    Raises:
        McpError: If the task enters a 'FAILED' or 'CANCELLED' state, or if
            an unexpected status is returned.
        asyncio.TimeoutError: If the maximum polling duration is exceeded.
    """
    start_time = time.time()
    while (time.time() - start_time) < max_poll:
        status_data = await task_status(
            task_id,
            analysis_url=analysis_url,
            region=region,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
        )
        match status_data.get('status'):
            case 'CANCELLED':
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message='Task cancelled',
                ))
            case 'FAILED':
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message='Task failed',
                ))
            case 'PENDING':
                await asyncio.sleep(poll_interval)
            case 'RUNNING':
                await asyncio.sleep(poll_interval)
            case 'SUCCEEDED':
                return status_data
            case _:
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message='Task status error',
                ))
    raise asyncio.TimeoutError(
        f'Exceeded max polling time {max_poll/60} minutes')


async def submit_wait(
    goal_description: str,
    data_list: Dict[str, str],
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    meta: str = '',
    execute_code: bool = ac.EXECUTE_CODE,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    task_name: str = ac.TASK_NAME,
    resource_dict: Dict[str, Dict[str, int]] = ac.RESOURCE,
    app_id_dict: Dict[str, str] = ac.APP_ID,
    compute_resource: Literal[
        'small', 'medium', 'large'
    ] = ac.COMPUTE_RESOURCE,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
) -> Dict[str, Any]:
    """
    Submits an analysis task and waits for its completion.

    This function combines the functionality of `submit` and
    `wait_for_completion`. It first submits a task and then monitors it
    until it finishes or times out.

    Args:
        goal_description: A natural language description of the analysis goals.
        data_list: A dictionary of input data sources, where keys are
            identifiers and values are their descriptions or paths.
        output_dir: The OBS path for storing analysis results.
        meta: Step-by-step instructions for processing.
        execute_code: A boolean flag to enable or disable automated code
            execution within the workflow.
        model_url: The URL of the coding model service.
        model_name: The name of the coding model.
        coder_api_key: The API key for the coding model.
        access_key_id: The access key ID for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The server endpoint for OBS.
        bucket_name: The name of the OBS bucket.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        task_name: The name of the task.
        resource_dict: A dictionary of computational resources.
        app_id_dict: A dictionary mapping compute resources to app IDs.
        compute_resource: The level of compute resources to allocate.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.
        poll_interval: The interval in seconds between polling for task status.
        max_poll: The maximum total duration in seconds to monitor the task.

    Returns:
        A dictionary containing the final status and results of the task.

    Raises:
        McpError: If the task fails or is cancelled.
        asyncio.TimeoutError: If the polling duration is exceeded.
    """
    task_dict = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        meta=meta,
        execute_code=execute_code,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name=task_name,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource=compute_resource,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    response = await wait_for_completion(
        task_id=task_dict['task_id'],
        analysis_url=analysis_url,
        region=region,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        poll_interval=poll_interval,
        max_poll=max_poll,
    )
    return response


async def plan_submit_wait(
    goal_description: str,
    data_list: Dict[str, str],
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    prompt_file: str = ac.PROMPT_FILE,
    prompt_path: str = ac.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = ac.FREQUENCY_PENALTY,
    n: int = ac.N,
    presence_penalty: float = ac.PRESENCE_PENALTY,
    reasoning_effort: str = ac.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = ac.RESPONSE_FORMAT,
    stream: bool = ac.STREAM,
    temperature: float = ac.TEMPERATURE,
    top_p: float = ac.TOP_P,
    user: str = ac.USER,
    execute_code: bool = ac.EXECUTE_CODE,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    task_name: str = ac.TASK_NAME + '-plan',
    resource_dict: Dict[str, Dict[str, int]] = ac.RESOURCE,
    app_id_dict: Dict[str, str] = ac.APP_ID,
    compute_resource: Literal[
        'small', 'medium', 'large'
    ] = ac.COMPUTE_RESOURCE,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
) -> Dict[str, Any]:
    """
    Generates a plan, submits it, and waits for completion.

    This function orchestrates a multi-step process: generating a plan based
    on the goal description, submitting it for asynchronous execution, and then
    polling until the task is finished or times out.

    Args:
        goal_description: A natural language description of the analysis goals.
        data_list: A dictionary of input data sources, where keys are
            identifiers and values are their descriptions or paths.
        output_dir: The OBS path for storing analysis results.
        prompt_file: The path to the prompt template file.
        prompt_path: The path or key for the system prompt.
        api_key: The API key for the language model.
        base_url: The base URL of the language model API.
        model: The identifier of the language model.
        frequency_penalty: The penalty for token repetition.
        n: The number of plan choices to generate.
        presence_penalty: The penalty for new tokens.
        reasoning_effort: The reasoning effort for the language model.
        response_format: The desired output format.
        stream: A flag for real-time token streaming.
        temperature: The randomness control for generation.
        top_p: The nucleus sampling threshold.
        user: A unique session identifier for the user.
        execute_code: A boolean flag to enable or disable automated code
            execution within the workflow.
        model_url: The URL of the coding model service.
        model_name: The name of the coding model.
        coder_api_key: The API key for the coding model.
        access_key_id: The access key ID for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The server endpoint for OBS.
        bucket_name: The name of the OBS bucket.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        task_name: The name of the task.
        resource_dict: A dictionary of computational resources.
        app_id_dict: A dictionary mapping compute resources to app IDs.
        compute_resource: The level of compute resources to allocate.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.
        poll_interval: The interval in seconds between polling for task status.
        max_poll: The maximum total duration in seconds to monitor the task.

    Returns:
        A dictionary with the final result or status of the completed task.

    Raises:
        McpError: If the submission fails or the task encounters an error.
    """
    task_dict = await plan_submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        prompt_file=prompt_file,
        prompt_path=prompt_path,
        api_key=api_key,
        base_url=base_url,
        model=model,
        frequency_penalty=frequency_penalty,
        n=n,
        presence_penalty=presence_penalty,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        execute_code=execute_code,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name=task_name,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource=compute_resource,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    response = await wait_for_completion(
        task_id=task_dict['task_id'],
        analysis_url=analysis_url,
        region=region,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        poll_interval=poll_interval,
        max_poll=max_poll,
    )
    return response


async def retrieve_plan_submit_wait(
    goal_description: str,
    data_list: Dict[str, str],
    user_id: str = ac.USER_ID,
    is_create_dir: bool = ac.CREATE_DIR,
    output_dir: str = ac.OUTPUT_DIR,
    retrieve_url: str = ac.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = ac.REPO_ID_DICT,
    page_num: int = ac.PAGE_NUM,
    filter_string: Optional[str] = ac.FILTER_STRING,
    scope: str = ac.SCOPE,
    extra_repo_ids: Optional[List[str]] = ac.EXTRA_REPO_IDS,
    rerank_url: str = ac.RERANK_URL,
    rerank_batch_size: int = ac.RERANK_BATCH_SIZE,
    score_threshold: float = ac.SCORE_THRESHOLD,
    top_n: int = ac.TOP_N,
    prompt_file: str = ac.PROMPT_FILE,
    prompt_path: str = ac.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = ac.FREQUENCY_PENALTY,
    max_tokens: int = ac.MAX_TOKENS,
    n: int = ac.N,
    presence_penalty: float = ac.PRESENCE_PENALTY,
    reasoning_effort: str = ac.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = ac.RESPONSE_FORMAT,
    stream: bool = ac.STREAM,
    temperature: float = ac.TEMPERATURE,
    top_p: float = ac.TOP_P,
    user: str = ac.USER,
    execute_code: bool = ac.EXECUTE_CODE,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
    analysis_url: str = ac.ANALYSIS_URL,
    region: str = ac.ANALYSIS_REGION,
    task_name: str = ac.TASK_NAME + '-retrieve-plan',
    resource_dict: Dict[str, Dict[str, int]] = ac.RESOURCE,
    app_id_dict: Dict[str, str] = ac.APP_ID,
    compute_resource: Literal[
        'small', 'medium', 'large'
    ] = ac.COMPUTE_RESOURCE,
    meta_meta: Optional[str] = None,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
) -> Dict[str, Any]:
    """
    Retrieves documents, generates a plan, submits it, and waits for
    completion.

    This function orchestrates a retrieval-augmented planning and execution
    workflow. It retrieves relevant documents, generates an augmented plan,
    submits it for execution, and then waits for the task to complete.

    Args:
        goal_description: A natural language description of the analysis goals.
        data_list: A dictionary of input data sources, where keys are
            identifiers and values are their descriptions or paths.
        output_dir: The OBS path for storing analysis results.
        retrieve_url: The URL for the document retrieval service.
        repo_id_dict: A dictionary of repository IDs for retrieval.
        page_num: The page number for retrieval results.
        filter_string: A string for filtering retrieval results.
        scope: The scope of the retrieval.
        extra_repo_ids: A list of additional repository IDs.
        rerank_url: The URL for the reranking service.
        rerank_batch_size: The batch size for reranking.
        score_threshold: The minimum score for retrieved documents.
        top_n: The number of top documents to retrieve.
        prompt_file: The path to the prompt template file.
        prompt_path: The path or key for the system prompt.
        api_key: The API key for the language model.
        base_url: The base URL of the language model API.
        model: The identifier of the language model.
        frequency_penalty: The penalty for token repetition.
        max_tokens: The maximum number of tokens to generate.
        n: The number of plan choices to generate.
        presence_penalty: The penalty for new tokens.
        reasoning_effort: The reasoning effort for the language model.
        response_format: The desired output format.
        stream: A flag for real-time token streaming.
        temperature: The randomness control for generation.
        top_p: The nucleus sampling threshold.
        user: A unique session identifier for the user.
        execute_code: A boolean flag to enable or disable automated code
            execution within the workflow.
        model_url: The URL of the coding model service.
        model_name: The name of the coding model.
        coder_api_key: The API key for the coding model.
        access_key_id: The access key ID for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The server endpoint for OBS.
        bucket_name: The name of the OBS bucket.
        analysis_url: The URL for the analysis submission API.
        region: The geographical region of the analysis service.
        task_name: The name of the task.
        resource_dict: A dictionary of computational resources.
        app_id_dict: A dictionary mapping compute resources to app IDs.
        compute_resource: The level of compute resources to allocate.
        meta_meta: Additional metadata to append to the plan.
        timeout: The total request timeout in seconds for API calls.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retry attempts for a failed request.
        poll_interval: The interval in seconds between polling for task status.
        max_poll: The maximum total duration in seconds to monitor the task.

    Returns:
        A dictionary with the final result or status of the completed task.

    Raises:
        McpError: If the submission fails or the task encounters an error.
    """
    if not repo_id_dict:
        repo_id_dict = ac.REPO_ID_DICT
    task_dict = await retrieve_plan_submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=is_create_dir,
        output_dir=output_dir,
        retrieve_url=retrieve_url,
        repo_id_dict=repo_id_dict,
        page_num=page_num,
        filter_string=filter_string,
        scope=scope,
        extra_repo_ids=extra_repo_ids,
        rerank_url=rerank_url,
        rerank_batch_size=rerank_batch_size,
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
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name=task_name,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource=compute_resource,
        meta_meta=meta_meta,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    response = await wait_for_completion(
        task_id=task_dict['task_id'],
        analysis_url=analysis_url,
        region=region,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        poll_interval=poll_interval,
        max_poll=max_poll,
    )
    return response


def upload_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
) -> str:
    """
    Uploads data to an Object Storage Service (OBS) bucket.

    This function takes a local file path and uploads the file to a specified
    OBS bucket. It handles the connection and authentication with the OBS
    service.

    Args:
        analyst_agents_datapath: The local path to the file to be uploaded.
        access_key_id: The access key ID for the OBS bucket.
        secret_access_key: The secret access key for the OBS bucket.
        obs_server: The server address of the OBS.
        bucket_name: The name of the OBS bucket.

    Returns:
        The OBS path of the uploaded file, in the format
        'bucket_name:/object_key'.

    Raises:
        OSError: If the file upload to OBS fails.
    """
    obsclient = ObsClient(access_key_id=access_key_id,
                          secret_access_key=secret_access_key,
                          server=obs_server)
    try:
        headers = PutObjectHeader()
        headers.contentType = 'text/plain'
        object_file = analyst_agents_datapath.split('/')[-1]
        object_key = f'agent_data/tmp_data/{object_file}'
        response = obsclient.putFile(
            bucketName=bucket_name,
            objectKey=object_key,
            file_path=object_file,
            metadata={'meta1': 'value1', 'meta2': 'value2'},
            headers=headers)
        if response.status < 300:
            return f'{bucket_name}:/{object_key}'
        raise OSError(f'Put File Failed\nrequestId: {response.requestId}\n'
                      f'errorCode: {response.errorCode}\n'
                      f'errorMessage: {response.errorMessage}')
    except Exception as exc:
        raise OSError(f'Put File Failed\n{format_exc()}') from exc


def delete_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
) -> str:
    """
    Deletes data from an Object Storage Service (OBS) bucket.

    This function removes a specified object from an OBS bucket using its path.
    It handles the connection and authentication required for the deletion.

    Args:
        analyst_agents_datapath: The OBS path of the file to be deleted.
        access_key_id: The access key ID for the OBS bucket.
        secret_access_key: The secret access key for the OBS bucket.
        obs_server: The server address of the OBS.
        bucket_name: The name of the OBS bucket.

    Returns:
        A confirmation message indicating the successful deletion of the
        object, including details like the request ID.

    Raises:
        OSError: If the file deletion from OBS fails.
    """
    obsclient = ObsClient(access_key_id=access_key_id,
                          secret_access_key=secret_access_key,
                          server=obs_server)
    try:
        object_key = analyst_agents_datapath
        response = obsclient.deleteObject(bucket_name, object_key)
        if response.status < 300:
            return (f'Delete Object Succeeded\n'
                    f'requestId: {response.requestId}\n'
                    f'deleteMarker: {response.body.deleteMarker}\n'
                    f'versionId: {response.body.versionId}')
        raise OSError(f'Delete Object Failed\n'
                      f'requestId: {response.requestId}\n'
                      f'errorCode: {response.errorCode}\n'
                      f'errorMessage: {response.errorMessage}')
    except Exception as exc:
        raise OSError(f'Delete Object Failed\n{format_exc()}') from exc


def get_data_list(data_file: str,
                  analysis_type: str,
                  species: str) -> list:
    """Generate ready-to-use prompt from template components.

    Combines template loading and rendering in one workflow:
    1. Load base template from YAML file
    2. Apply parameter substitutions

    Args:
        data_file: data_list_file for json format
        analysis_type: analysis_type[evolution_analysis, deepgo2_analysis,
                                     structure_analysis, prompter_analysis,
                                     protein_design_analysis,
                                     gene_expression_analysis, ppi_analysis]
        species: 65 species ...

    Returns:
        data_list for analysis
    """
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f'Data file not found: {data_file}') from exc
    try:
        analysis_data_list = data[analysis_type]
    except KeyError as exc:
        raise KeyError(f'Analysis type not found: {analysis_type}') from exc
    try:
        data_list = analysis_data_list[species]
    except KeyError as exc:
        raise KeyError(f'Species not found: {species}') from exc
    return data_list


def create_output_dir(
    user_id: str,
    task: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
) -> str:
    obs_client = ObsClient(access_key_id=access_key_id,
                           secret_access_key=secret_access_key,
                           server=obs_server)
    try:
        output_dir = (f'agent_data/user_data/{user_id}/output/'
                      f'{task}_{int(time.time())}_{uuid1()}/')
        response = obs_client.putContent(bucketName=bucket_name,
                                         objectKey=output_dir,
                                         content=None)
        if response.status < 300:
            return f'/obs/{bucket_name}/{output_dir}'
        raise OSError(f'Put File Failed\nrequestId: {response.requestId}\n'
                      f'errorCode: {response.errorCode}\n'
                      f'errorMessage: {response.errorMessage}')
    except Exception as exc:
        raise OSError(f'Put File Failed\n{format_exc()}') from exc


def download_obs_out(
    task_dir: str,
    obs_output_path: str,
    download_path: str = ac.DOWNLOAD_PATH,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    target_file_feature: List[str] = ac.TARGET_FILE_FEATURE,
    bucket_name: str = ac.BUCKET_NAME,
    marker: Optional[str] = ac.DOWNLOAD_MARKER,
    max_keys: int = ac.DOWNLOAD_MAX_KEYS,
    if_download_all: bool = ac.IF_DOWNLOAD_ALL,
):
    output_path = Path(f'{download_path}/{task_dir}')
    output_path.mkdir(parents=True, exist_ok=True)
    headers = GetObjectHeader()
    headers.if_modified_since = 'date'
    obs_client = ObsClient(access_key_id=access_key_id,
                           secret_access_key=secret_access_key,
                           server=obs_server)
    try:
        while True:
            file_response = obs_client.listObjects(bucketName=bucket_name,
                                                   prefix=obs_output_path,
                                                   marker=marker,
                                                   max_keys=max_keys,
                                                   encoding_type='url')
            if file_response.status < 300:
                for content in file_response.body.contents:
                    obj_file = content.key
                    if obj_file.endswith('/'):
                        continue
                    output_file = obj_file.split('/')[-1]
                    if not if_download_all and not any(
                        output_file.endswith(suffix)
                        for suffix in target_file_feature
                    ):
                        continue
                    full_path = str(output_path / output_file)
                    download_response = obs_client.getObject(
                        bucketName=bucket_name,
                        objectKey=obj_file,
                        downloadPath=full_path,
                        headers=headers,
                    )
                    if download_response.status > 300:
                        yield f'{output_file} download failed.'
                        continue
                    else:
                        yield f'{output_file} download succeed.'
                        continue
                if file_response.body.is_truncated is True:
                    marker = file_response.body.next_marker
                else:
                    break
            else:
                raise OSError(f'Get File List Failed\n'
                              f'requestId: {file_response.requestId}\n'
                              f'errorCode: {file_response.errorCode}\n'
                              f'errorMessage: {file_response.errorMessage}')
    except Exception as exc:
        raise OSError(f'Download File Failed\n{format_exc()}') from exc
