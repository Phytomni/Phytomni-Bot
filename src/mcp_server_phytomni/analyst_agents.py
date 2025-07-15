# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyichao (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
import asyncio
import datetime
import json
import time
from pathlib import Path
from random import uniform
from traceback import format_exc
from typing import Any, List, Literal, Dict, Optional, Union
from uuid import uuid1

from httpx import AsyncClient, ConnectError, HTTPError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from obs import PutObjectHeader, ObsClient

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
    output_dir: str = ac.OUTPUT_DIR,
    meta: Optional[str] = None,
    execute_code: bool = ac.EXECUTE_CODE,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    api_key: str = sc.CODER_API_KEY.get_secret_value(),
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
) -> Dict[str, str]:
    """Submit analysis task to Bioinformatics Agents.

    Args:
        goal_description: Natural language description of analysis goals
            - Must include task execution steps using meta_info
        data_list: List of input data sources with:
            - obs_url: OBS path to input files (required)
            - description: Brief explanation of data source
        output_dir: OBS path for storing analysis results
        meta: Step-by-step instructions for processing
            - Format: "step1, operation; step2, operation..."
        execute_code: Enable automated code execution in workflow
        timeout: Total request timeout (min 5s connect timeout)
        task_name: task name in ai4s platform.
        compute_resource: the compute resource in this submit task.

    Returns:
        Task submission response with:
            - task_id: Unique identifier for tracking
            - output_dir: The output results dirctory
            - job_name: The job name in ai4s platform
            - compute_resource: The compute resouce in this task
            - error: Error message if failed

    Raises:
        McpError: On submission failure with error code and message
    """
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
        'api_key': api_key,
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

    job_headers = {"Content-Type": "application/json",
                   "X-Auth-Token": await get_token(timeout=timeout,
                                                   region=region)}
    task_name = task_name.replace('_', '-')
    time_stamp = datetime.datetime.now().strftime('%H%M%S-%f')
    job_name = f'{task_name}-{time_stamp}'
    job_data = {
        "name": job_name,
        "labels": [],
        "description": "",
        "timeout": 10080,
        "output_dir": "",
        "tasks": [{
            "task_name": f"analyst-agents-{compute_resource}",
            "display_name": job_name,
            "inputs": [
                {
                    "name": "obs-mount",
                    "type": "DIRECTORY",
                    "description": "",
                    "required": True,
                    "pattern": "",
                    "values": ["phytomni:/agent_data/"],
                    "enum": [],
                    "concurrent": "",
                },
                {
                    "name": "meta-file",
                    "type": "FILE",
                    "description": "",
                    "required": True,
                    "pattern": "",
                    "values": [data_path],
                    "enum": [],
                    "concurrent": "",
                },
            ],
            "outputs": [],
            "output_dir": "",
            "resources": {
                "cpu": f"{resource_dict[compute_resource]['cpu']}C",
                "cpu_type": "X86",
                "gpu": "0",
                "gpu_type": "",
                "memory": f"{resource_dict[compute_resource]['memory']}G"
            },
            "summary": "",
            "labels": [],
        }],
        "io_acc_id": "",
        "ioType": "",
        "priority": 0,
        "automatic": True,
        "node_labels": [],
        "tool_id": app_id_dict[compute_resource],
        "tool_type": "app",
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
                    message=f"Failed to submit task: {str(e)}")) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e


async def task_delete(task_id: str,
                      analysis_url: str = ac.ANALYSIS_URL,
                      region: str = ac.ANALYSIS_REGION,
                      timeout: float = ac.TIMEOUT,
                      retriable_codes: List[int] = ac.RETRIABLE_CODES,
                      max_retries: int = ac.MAX_RETRIES,
                      ) -> str:
    """Check task execution status.

    Args:
        task_id: Unique identifier from submit response
        timeout: Total request timeout (min 5s connect timeout)

    Returns:
        Task status details with:
            - task_id: Confirmation of requested ID
            - status: One of 'pending', 'running', 'completed', 'failed'
            - result: Analysis output (if completed)
            - error: Error details (if failed)

    Raises:
        McpError: On status check failure with error code and message
    """
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url=f'{analysis_url}/{task_id}/terminate',
                    headers={"Content-Type": "application/json",
                             "X-Auth-Token": await get_token(timeout=timeout,
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
                    message=f"Failed to delete task: {str(e)}")) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e


async def task_status(task_id: str,
                      analysis_url: str = ac.ANALYSIS_URL,
                      region: str = ac.ANALYSIS_REGION,
                      timeout: float = ac.TIMEOUT,
                      retriable_codes: List[int] = ac.RETRIABLE_CODES,
                      max_retries: int = ac.MAX_RETRIES,
                      ) -> str:
    """Check task execution status.

    Args:
        task_id: Unique identifier from submit response
        timeout: Total request timeout (min 5s connect timeout)

    Returns:
        Task status details with:
            - task_id: Confirmation of requested ID
            - status: One of 'pending', 'running', 'completed', 'failed'
            - result: Analysis output (if completed)
            - error: Error details (if failed)

    Raises:
        McpError: On status check failure with error code and message
    """
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(
                    f'{analysis_url}/{task_id}',
                    headers={"Content-Type": "application/json",
                             "X-Auth-Token": await get_token(timeout=timeout,
                                                             region=region)},
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return response
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
                    message=f"Failed to delete task: {str(e)}")) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
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
                   ) -> str:
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(
                    f'{analysis_url}/{task_id}/logs'
                    f'?task_name=analyst-agents-{compute_resource}',
                    headers={"Content-Type": "application/json",
                             "X-Auth-Token": await get_token(timeout=timeout,
                                                             region=region)},
                    timeout=timeout,
                )
                if response.status_code == 200:
                    return response
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
                    message=f"Failed to delete task: {str(e)}")) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e


async def plan_submit(
    goal_description: str,
    data_list: Dict[str, str],
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
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
) -> Dict[str, str]:
    """Generate a plan using a language model and submit it for execution.

    This function first utilizes the `phyto_chat` service to process the
    `goal_description`, likely to generate a structured plan or metadata
    for an analysis task. This generated metadata, along with the original
    `goal_description` and `data_list`, is then passed to the `submit`
    function for further processing or execution.

    Args:
        goal_description: A natural language description of the goal or task
            to be achieved. This is used to generate a plan via `phyto_chat`.
        data_list: A list of dictionaries, where each dictionary represents
            a data item or source relevant to the goal.
        output_dir: Directory path where outputs from the `submit` function
            (e.g., execution results, generated files) should be stored.
            Defaults to `OUTPUT_DIR`.
        prompt_file: Path to the prompt template file used for constructing
            the prompt for the plan generation step via `phyto_chat`.
            Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the prompt file to retrieve the
            specific system prompt for plan generation.
            Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model for plan
            generation. Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service for plan generation.
            Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use for plan generation.
            Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) in the
            plan generation step. Defaults to `FREQUENCY_PENALTY`.
        n: Number of plan choices to generate by the Phyto model.
            Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) in the plan
            generation step. Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for compatible Phyto
            models during plan generation. Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for the Phyto
            model during plan generation. Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for the plan generation
            step. Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for the plan generation
            step. Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for the plan generation
            step. Defaults to `TOP_P`.
        user: Unique session identifier for the end-user, passed to the
            Phyto model. Defaults to `USER`.
        execute_code: Flag indicating whether any code generated or referenced
            in the plan should be executed by the `submit` function.
            Defaults to `EXECUTE_CODE`.
        timeout: Total request timeout in seconds for each underlying API call
            (both `phyto_chat` plan generation and the `submit` call).
            Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying API calls. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying API calls.
            Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary containing the response from the `submit` function,
        which typically includes information about the submission status or
        execution results. The exact structure depends on the `submit`
        function's implementation.

    Raises:
        McpError: If either the `phyto_chat` plan generation step or the
            subsequent `submit` call fails after all retry attempts.
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
    response = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=phyto_response['choices'][0]['message']['content'],
        execute_code=execute_code,
        task_name="plan-submit-task", 
        timeout=timeout
    )
    return response


async def retrieve_plan_submit(
    goal_description: str,
    data_list: List[Dict[str, str]],
    output_dir: str = ac.OUTPUT_DIR,
    repo_id_dict: Optional[Dict[str, int]] = ac.REPO_ID_DICT,
    page_num: int = ac.PAGE_NUM,
    filter_string: Optional[str] = ac.FILTER_STRING,
    scope: str = ac.SCOPE,
    extra_repo_ids: Optional[List[str]] = ac.EXTRA_REPO_IDS,
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
    meta_meta: Optional[str] = None,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
) -> Dict[str, str]:
    """Perform retrieval-augmented plan generation and submit for execution.

    This function first retrieves relevant documents using `multi_retrieve`
    based on the `goal_description`. These documents are then used to augment
    the `goal_description` into a new prompt. This augmented prompt is passed
    to `phyto_chat` to generate a plan or metadata. Finally, the original
    `goal_description`, `data_list`, the generated metadata, and other
    parameters are passed to the `submit` function for processing or execution.

    Args:
        goal_description: A natural language description of the goal or task.
            It is used for document retrieval and as the basis for the
            augmented prompt for plan generation.
        data_list: A list of dictionaries, where each dictionary represents
            a data item or source relevant to the goal, passed to the `submit`
            function.
        output_dir: Directory path where outputs from the `submit` function
            (e.g., execution results, generated files) should be stored.
            Defaults to `OUTPUT_DIR`.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for document retrieval. If None,
            defaults to `REPO_ID_DICT`.
        page_num: Pagination page number for retrieval results from each
            repository. Defaults to `PAGE_NUM`.
        filter_string: Optional filter criteria string for metadata filtering
            during document retrieval from each repository.
            Defaults to `FILTER_STRING`.
        scope: Search scope for each retrieval, can be 'doc', 'keyword', or
            'both'. Defaults to `SCOPE`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in the document retrieval. Defaults to `EXTRA_REPO_IDS`.
        score_threshold: Minimum relevance score threshold applied during
            document retrieval. Defaults to `SCORE_THRESHOLD`.
        top_n: The total number of top-scoring documents to retrieve and use
            as context for plan generation. Defaults to `TOP_N`.
        prompt_file: Path to the prompt template file used for constructing
            the prompt for the plan generation step (after retrieval) via
            `phyto_chat`. Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the prompt file to retrieve the
            specific system prompt for plan generation.
            Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model for plan
            generation. Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service for plan generation.
            Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use for plan generation.
            Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) in the
            plan generation step. Defaults to `FREQUENCY_PENALTY`.
        max_tokens: Maximum number of tokens to generate in the plan by the
            Phyto model. Defaults to `MAX_TOKENS`.
        n: Number of plan choices to generate by the Phyto model.
            Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) in the plan
            generation step. Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for compatible Phyto
            models during plan generation. Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for the Phyto
            model during plan generation. Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for the plan generation
            step. Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for the plan generation
            step. Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for the plan generation
            step. Defaults to `TOP_P`.
        user: Unique session identifier for the end-user, passed to the
            Phyto model. Defaults to `USER`.
        execute_code: Flag indicating whether any code generated or referenced
            in the plan should be executed by the `submit` function.
            Defaults to `EXECUTE_CODE`.
        timeout: Total request timeout in seconds for each underlying API call
            (`multi_retrieve`, `phyto_chat`, and `submit`).
            Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for `multi_retrieve` and `phyto_chat` API calls.
            Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for `multi_retrieve` and
            `phyto_chat` API calls. Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary containing the response from the `submit` function,
        which typically includes information about the submission status or
        execution results. The exact structure depends on the `submit`
        function's implementation.

    Raises:
        McpError: If the `multi_retrieve` step, the `phyto_chat` plan
            generation step, or the subsequent `submit` call fails after
            all retry attempts.
    """
    if not repo_id_dict:
        repo_id_dict = ac.REPO_ID_DICT
    retrieve_response = await multi_retrieve(
        user_query=goal_description,
        repo_id_dict=repo_id_dict,
        page_num=page_num,
        filter_string=filter_string,
        scope=scope,
        extra_repo_ids=extra_repo_ids,
        score_threshold=score_threshold,
        top_n=top_n,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    retrieve_results = []
    total_length = 0
    for file_id, eachdoc in enumerate(retrieve_response['doc_list']):
        if eachdoc["subtitle"]:
            current_fragment = (
                f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                f'{eachdoc["subtitle"]}\n{eachdoc["content"]} '
                f'[document {file_id+1} end]')
        else:
            current_fragment = (
                f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                f'{eachdoc["content"]} [document {file_id+1} end]')
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
    response = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=phyto_response['choices'][0]['message']['content'] + meta_meta if meta_meta else phyto_response['choices'][0]['message']['content'],
        execute_code=execute_code,
        task_name="retrieve-plan-submit",
        timeout=timeout,
    )
    return response


async def wait_for_completion(
    task_id: str,
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
    timeout: float = ac.TIMEOUT,
) -> Dict[str, Any]:
    """Asynchronously monitor task execution until completion or timeout.

    Args:
        task_id: Unique identifier from task submission
            - Must match existing task in processing queue
        poll_interval: Status check frequency in seconds
            - Minimum 2s recommended to avoid rate limiting
        max_poll: Maximum total monitoring duration in seconds
            - Default 1800s (30 minutes)
        timeout: Total request timeout (min 5s connect timeout)

    Returns:
        Final task status containing:
            - status: 'completed' state confirmation
            - result: Full execution output data

    Raises:
        McpError: When task enters failed state
        asyncio.TimeoutError: If exceeds max_poll duration
        RuntimeError: On unexpected status response format
    """
    start_time = time.time()
    while (time.time() - start_time) < max_poll:
        status_data = await task_status(task_id, timeout)
        if status_data.get('status') == 'FAILED':
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message="Task failed"))
        if status_data.get('status') == 'FINISH':
            return status_data
        await asyncio.sleep(poll_interval)
    raise asyncio.TimeoutError(
        f"Exceeded max polling time {max_poll/60} minutes")


async def submit_wait(goal_description: str,
                      data_list: List[Dict[str, str]],
                      output_dir: str = ac.OUTPUT_DIR,
                      meta: str = '',
                      use_meta: Optional[bool] = None,
                      execute_code: bool = ac.EXECUTE_CODE,
                      poll_interval: float = ac.POLL_INTERVAL,
                      max_poll: float = ac.MAX_POLL,
                      timeout: float = ac.TIMEOUT,
                      ) -> Dict[str, Any]:
    """Submit analysis task to Bioinformatics Agents and
        monitor task execution until completion or timeout.

    Args:
        goal_description: Natural language description of analysis goals
            - Must include task execution steps using meta_info
        data_list: List of input data sources with:
            - obs_url: OBS path to input files (required)
            - description: Brief explanation of data source
        output_dir: OBS path for storing analysis results
        meta: Step-by-step instructions for processing
            - Format: "step1, operation; step2, operation..."
        use_meta:
            - Auto-determined if not provided (enabled if meta exists)
            - True: Use provided meta instructions
            - False: Ignore meta and use goal_description only
        execute_code: Enable automated code execution in workflow
        poll_interval: Status check frequency in seconds
            - Minimum 2s recommended to avoid rate limiting
        max_poll: Maximum total monitoring duration in seconds
            - Default 1800s (30 minutes)
        timeout: Total request timeout (min 5s connect timeout)

    Returns:
        Final task status containing:
            - status: 'completed' state confirmation
            - result: Full execution output data
    """
    task_id = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=execute_code,
        timeout=timeout,
    )
    response = await wait_for_completion(
        task_id=task_id,
        poll_interval=poll_interval,
        max_poll=max_poll,
        timeout=timeout,
    )
    return response


async def plan_submit_wait(
    goal_description: str,
    data_list: List[Dict[str, str]],
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
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
) -> Dict[str, Any]:
    """Generate a plan, submit it for asynchronous execution, and wait for
        completion.

    This function orchestrates a multi-step process:
    1. It calls `plan_submit` to generate a plan based on `goal_description`
       (using `phyto_chat`) and submit this plan along with `data_list` for
       asynchronous processing. The `plan_submit` function returns a task ID.
    2. It then calls `wait_for_completion` with the obtained task ID,
       polling until the task is finished or a timeout (`max_poll`) occurs.

    Args:
        goal_description: A natural language description of the goal or task
            to be achieved. This is used by `plan_submit` to generate a plan.
        data_list: A list of dictionaries, where each dictionary represents
            a data item or source relevant to the goal,
            passed to `plan_submit`.
        output_dir: Directory path where outputs from the task execution
            (e.g., execution results, generated files) should be stored.
            Used by `plan_submit`. Defaults to `OUTPUT_DIR`.
        prompt_file: Path to the prompt template file used for constructing
            the prompt for the plan generation step within `plan_submit`.
            Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the prompt file to retrieve the
            specific system prompt for plan generation within `plan_submit`.
            Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model for plan
            generation within `plan_submit`. Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service for plan generation
            within `plan_submit`. Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use for plan generation
            within `plan_submit`. Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) in the
            plan generation step within `plan_submit`.
            Defaults to `FREQUENCY_PENALTY`.
        max_tokens: Maximum number of tokens to generate in the plan by the
            Phyto model within `plan_submit`. Defaults to `MAX_TOKENS`.
        n: Number of plan choices to generate by the Phyto model within
            `plan_submit`. Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) in the plan
            generation step within `plan_submit`.
            Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for compatible Phyto
            models during plan generation within `plan_submit`.
            Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for the Phyto
            model during plan generation within `plan_submit`.
            Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for the plan generation
            step within `plan_submit`. Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for the plan generation
            step within `plan_submit`. Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for the plan generation
            step within `plan_submit`. Defaults to `TOP_P`.
        user: Unique session identifier for the end-user, passed to the
            Phyto model within `plan_submit`. Defaults to `USER`.
        execute_code: Flag indicating whether any code generated or referenced
            in the plan should be executed. Passed to `plan_submit`.
            Defaults to `EXECUTE_CODE`.
        poll_interval: Time in seconds to wait between polling attempts for
            task completion by `wait_for_completion`.
            Defaults to `POLL_INTERVAL`.
        max_poll: Maximum total time in seconds to wait for task completion
            by polling in `wait_for_completion`. Defaults to `MAX_POLL`.
        timeout: Total request timeout in seconds for API calls made during
            the `plan_submit` phase and for individual polling requests made
            by `wait_for_completion`. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for API calls made during the `plan_submit` phase.
            Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for API calls made
            during the `plan_submit` phase. Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary containing the final result or status of the completed
        task, as returned by `wait_for_completion`. The exact structure
        depends on the implementation of the task processing and
        `wait_for_completion`.

    Raises:
        McpError: If the `plan_submit` step fails or if `wait_for_completion`
            encounters an unrecoverable error or times out based on `max_poll`.
    """
    task_id = await plan_submit(
        goal_description=goal_description,
        data_list=data_list,
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
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    response = await wait_for_completion(
        task_id=task_id,
        poll_interval=poll_interval,
        max_poll=max_poll,
        timeout=timeout,
    )
    return response


async def retrieve_plan_submit_wait(
    goal_description: str,
    data_list: List[Dict[str, str]],
    output_dir: str = ac.OUTPUT_DIR,
    repo_id_dict: Optional[Dict[str, int]] = ac.REPO_ID_DICT,
    page_num: int = ac.PAGE_NUM,
    filter_string: Optional[str] = ac.FILTER_STRING,
    scope: str = ac.SCOPE,
    extra_repo_ids: Optional[List[str]] = ac.EXECUTE_CODE,
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
    poll_interval: float = ac.POLL_INTERVAL,
    max_poll: float = ac.MAX_POLL,
    timeout: float = ac.TIMEOUT,
    retriable_codes: List[int] = ac.RETRIABLE_CODES,
    max_retries: int = ac.MAX_RETRIES,
) -> Dict[str, Any]:
    """Retrieve documents, generate an augmented plan, submit for execution,
        and wait.

    This function orchestrates a retrieval-augmented planning and execution
        workflow:
    1. It calls `retrieve_plan_submit` which:
        a. Retrieves relevant documents based on `goal_description` using
           `multi_retrieve`.
        b. Augments the `goal_description` with these documents to create a
           new prompt.
        c. Uses `phyto_chat` with this augmented prompt to generate a
           plan/metadata.
        d. Submits this plan, original `goal_description`, and `data_list` for
           asynchronous processing via the `submit` function. This step returns
           a task ID.
    2. It then calls `wait_for_completion` with the obtained task ID,
       polling until the task is finished or a timeout (`max_poll`) occurs.

    Args:
        goal_description: A natural language description of the goal or task.
            It is used by `retrieve_plan_submit` for document retrieval and
            as the basis for the augmented prompt for plan generation.
        data_list: A list of dictionaries, where each dictionary represents
            a data item or source relevant to the goal. Passed to
            `retrieve_plan_submit`.
        output_dir: Directory path where outputs from the task execution
            (e.g., execution results, generated files) should be stored.
            Used by `retrieve_plan_submit`. Defaults to `OUTPUT_DIR`.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for document retrieval. Used by
            `retrieve_plan_submit`. If None, defaults to `REPO_ID_DICT`.
        page_num: Pagination page number for retrieval results from each
            repository. Used by `retrieve_plan_submit`. Defaults to `PAGE_NUM`.
        filter_string: Optional filter criteria string for metadata filtering
            during document retrieval. Used by `retrieve_plan_submit`.
            Defaults to `FILTER_STRING`.
        scope: Search scope for each retrieval ('doc', 'keyword', or 'both').
            Used by `retrieve_plan_submit`. Defaults to `SCOPE`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in the document retrieval. Used by `retrieve_plan_submit`.
            Defaults to `EXECUTE_CODE`.
        score_threshold: Minimum relevance score threshold applied during
            document retrieval. Used by `retrieve_plan_submit`.
            Defaults to `SCORE_THRESHOLD`.
        top_n: The total number of top-scoring documents to retrieve for
            augmenting the plan generation. Used by `retrieve_plan_submit`.
            Defaults to `TOP_N`.
        prompt_file: Path to the prompt template file used for constructing
            the prompt for the plan generation step within
            `retrieve_plan_submit`. Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the prompt file to retrieve the
            specific system prompt for plan generation within
            `retrieve_plan_submit`. Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model for plan
            generation within `retrieve_plan_submit`. Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service for plan generation
            within `retrieve_plan_submit`. Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use for plan generation
            within `retrieve_plan_submit`. Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) in the
            plan generation step within `retrieve_plan_submit`.
            Defaults to `FREQUENCY_PENALTY`.
        max_tokens: Maximum number of tokens to generate in the plan by the
            Phyto model within `retrieve_plan_submit`.
            Defaults to `MAX_TOKENS`.
        n: Number of plan choices to generate by the Phyto model within
            `retrieve_plan_submit`. Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) in the plan
            generation step within `retrieve_plan_submit`.
            Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for compatible Phyto
            models during plan generation within `retrieve_plan_submit`.
            Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for the Phyto
            model during plan generation within `retrieve_plan_submit`.
            Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for the plan generation
            step within `retrieve_plan_submit`. Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for the plan generation
            step within `retrieve_plan_submit`. Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for the plan generation
            step within `retrieve_plan_submit`. Defaults to `TOP_P`.
        user: Unique session identifier for the end-user, passed to the
            Phyto model within `retrieve_plan_submit`. Defaults to `USER`.
        execute_code: Flag indicating whether any code generated or referenced
            in the plan should be executed. Passed to `retrieve_plan_submit`.
            Defaults to `EXECUTE_CODE`.
        poll_interval: Time in seconds to wait between polling attempts for
            task completion by `wait_for_completion`.
            Defaults to `POLL_INTERVAL`.
        max_poll: Maximum total time in seconds to wait for task completion
            by polling in `wait_for_completion`. Defaults to `MAX_POLL`.
        timeout: Total request timeout in seconds. This applies to API calls
            made during the `retrieve_plan_submit` phase (including document
            retrieval, plan generation, and initial submission) and also to
            individual polling requests made by `wait_for_completion`.
            Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for API calls made during the `retrieve_plan_submit` phase
            (e.g., document retrieval, plan generation).
            Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for API calls made
            during the `retrieve_plan_submit` phase. Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary containing the final result or status of the completed
        task, as returned by `wait_for_completion`. The exact structure
        depends on the implementation of the task processing and
        `wait_for_completion`.

    Raises:
        McpError: If the `retrieve_plan_submit` step fails or if
            `wait_for_completion` encounters an unrecoverable error or
            times out based on `max_poll`.
    """
    if not repo_id_dict:
        repo_id_dict = ac.REPO_ID_DICT
    task_id = await retrieve_plan_submit(
        goal_description=goal_description,
        data_list=data_list,
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
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    response = await wait_for_completion(
        task_id=task_id,
        poll_interval=poll_interval,
        max_poll=max_poll,
        timeout=timeout,
    )
    return response


def upload_analyst_agents_data(
    analyst_agents_datapath: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = ac.OBS_SERVER,
    bucket_name: str = ac.BUCKET_NAME,
) -> str:
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
