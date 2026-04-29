# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for conducting in silico research based on
scientific literature.

It includes functions that extract research goals from scientific papers and
execute comprehensive computational research workflows to reproduce findings
or explore related hypotheses.
"""

from asyncio import gather
from json import loads
from typing import Dict, List, Optional, Union

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR

from .analyst_agents import retrieve_plan_submit
from .chat_agents import phyto_chat
from .config.defaults import InSilicoResearchConfig
from .config.settings import SensitiveConfig
from .utils import download_list_convert, get_prompt

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
    reasoning_effort: Optional[str] = isrc.REASONING_EFFORT,
    stream: bool = isrc.STREAM,
    temperature: float = isrc.TEMPERATURE,
    top_p: float = isrc.TOP_P,
    user: str = isrc.USER,
    obs_file_list: List[str] = [],
    server_dir: str = isrc.TEMP_DIR,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = isrc.OBS_SERVER,
    bucket_name: str = isrc.BUCKET_NAME,
    part_size: int = isrc.PART_SIZT,
    task_num: int = isrc.TASK_NUM,
    max_concurrency: int = isrc.MAX_CONCURRENCY,
    max_workers: int = isrc.MAX_WORKERS,
    timeout: float = isrc.TIMEOUT,
    retriable_codes: List[int] = isrc.RETRIABLE_CODES,
    max_retries: int = isrc.MAX_RETRIES,
    max_tokens: int = isrc.MAX_TOKENS,
) -> List[Dict[str, str]]:
    """Extract research goals and context from scientific paper text.

    This function analyzes scientific paper content using a language model to
    identify specific research objectives that can be reproduced
    computationally. Each extracted goal includes the complete workflow
    description and supporting contextual information from the original paper.

    Args:
        user_query: The scientific paper text or content to analyze for
            extracting research goals.
        prompt_file: Path to the YAML template file containing system prompts.
        prompt_path: Nested path within the template file to locate the
            specific system prompt (e.g., "system/ai4ps").
        api_key: API key for authenticating with the language model service.
        base_url: Base URL endpoint for the language model API service.
        model: Identifier of the specific language model to use for generation.
        frequency_penalty: Penalty applied to new tokens based on their
            frequency in the text so far, discouraging repetition of exact
            words/phrases. Values range from -2.0 to 2.0.
        n: Number of completion choices to generate for each input.
        presence_penalty: Penalty applied to new tokens based on their
            presence in the text so far, discouraging repetition of concepts.
            Values range from -2.0 to 2.0.
        reasoning_effort: Level of reasoning effort for the language model.
            Typically 'low', 'medium', or 'high'.
        stream: Flag to enable or disable streaming of responses from the
            language model. If True, responses are sent as a series of events.
        temperature: Sampling temperature for language model responses
            (controls randomness). Higher values mean more random responses.
        top_p: Nucleus sampling parameter for language model responses
            (controls diversity). Considers tokens with cumulative probability
            mass up to top_p.
        user: User identifier for API interactions, particularly for chat or
            language model services.
        obs_file_list: List of OBS object keys (file paths) to download and
            include as context in the query. Files are converted to markdown.
        server_dir: Local directory path for temporary file storage during
            file downloads and processing.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket containing the files.
        part_size: Size of each part for multipart downloads from OBS.
        task_num: Number of concurrent tasks for multipart downloads from OBS.
        max_concurrency: Maximum number of files to download from OBS
            concurrently.
        max_workers: Maximum number of worker processes to use for file
            conversion operations.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries for
            API calls.
        max_retries: Maximum number of retry attempts for API calls.
        max_tokens: Maximum number of tokens to generate in language model
            responses.

    Returns:
        A list of dictionaries, each containing:
        - 'goal': A comprehensive workflow description for reproducing a
          key finding or figure from the paper
        - 'context': Supporting text snippets from the original paper
          providing necessary details and parameters

    Raises:
        McpError: If the language model API call fails after all retry
            attempts.
        JSONDecodeError: If the response cannot be parsed as valid JSON.

    Examples:
        Extract goals from a paper:
            >>> paper_text = "This study investigated CRISPR-Cas9..."
            >>> goals = await extract_goals(paper_text)
            >>> for goal in goals:
            ...     print(f"Goal: {goal['goal']}")
            ...     print(f"Context: {goal['context']}")
    """
    total_length = 0
    if obs_file_list:
        upload_str_list = await download_list_convert(
            obs_file_list=obs_file_list,
            server_dir=server_dir,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
            part_size=part_size,
            task_num=task_num,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            max_workers=max_workers,
        )
        upload_results = []
        for i, doc in enumerate(upload_str_list):
            fragment = (
                f"[user upload file {i+1} begin]\n"
                f"{doc}\n[user upload file {i+1} end]"
            )
            if total_length + len(fragment) <= max_tokens:
                upload_results.append(fragment)
                total_length += len(fragment)
            else:
                break
        upload_context = "\n\n".join(upload_results)
        user_query = get_prompt(
            prompt_file,
            "user/in_silico_research_goals_file",
            {"upload_context": upload_context, "paper_text": user_query},
        )
    else:
        user_query = get_prompt(
            prompt_file,
            "user/in_silico_research_goals",
            {"paper_text": user_query},
        )

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
            "type": "json_schema",
            "json_schema": {
                "type": "array",
                "description": "A list of research objectives from the "
                "paper. Each objective is a dictionary containing a "
                "consolidated goal and its supporting context.",
                "items": {
                    "type": "object",
                    "description": "Represents a single, end-to-end research "
                    "objective.",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": "A comprehensive summary of the "
                            "workflow to reproduce a key finding, "
                            "detailing steps from data acquisition to "
                            "final analysis.",
                        },
                        "context": {
                            "type": "string",
                            "description": "Aggregated text from the paper "
                            "(e.g., Methods, Results, Figure Legends) "
                            "providing details and evidence for the goal.",
                        },
                    },
                    "required": ["goal", "context"],
                },
            },
        },
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    if phyto_response is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Failed to get response from language model API",
            )
        )
    return loads(phyto_response["choices"][0]["message"]["content"])


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
    reasoning_effort: Optional[str] = isrc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = isrc.RESPONSE_FORMAT,
    stream: bool = isrc.STREAM,
    temperature: float = isrc.TEMPERATURE,
    top_p: float = isrc.TOP_P,
    user: str = isrc.USER,
    obs_file_list: List[str] = [],
    server_dir: str = isrc.TEMP_DIR,
    execute_code: bool = isrc.EXECUTE_CODE,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = isrc.OBS_SERVER,
    bucket_name: str = isrc.BUCKET_NAME,
    part_size: int = isrc.PART_SIZT,
    task_num: int = isrc.TASK_NUM,
    max_concurrency: int = isrc.MAX_CONCURRENCY,
    max_workers: int = isrc.MAX_WORKERS,
    timeout: float = isrc.TIMEOUT,
    retriable_codes: List[int] = isrc.RETRIABLE_CODES,
    max_retries: int = isrc.MAX_RETRIES,
) -> List:
    """Conduct comprehensive in silico research based on scientific literature.

    This function orchestrates a complete computational research workflow:
    1. Extracts research goals from the provided scientific paper text
    2. For each goal, executes a retrieve-plan-submit workflow that includes:
       - Document retrieval for relevant knowledge
       - Analysis plan generation
       - Computational task submission and execution
    3. Returns results from all concurrent research workflows

    Args:
        user_query: The scientific paper text or content to analyze and
            reproduce computationally.
        data_list: Dictionary mapping data identifiers to their descriptions
            or file paths, providing the computational resources needed for
            the research workflows.
        user_id: Identifier for the user submitting the research tasks.
        is_create_dir: Flag indicating whether to create output directories
            for storing analysis results.
        output_dir: Output directory path for storing results of analysis
            or operations (e.g., an OBS path).
        repo_id_dict: Dictionary mapping repository IDs to associated integer
            values (e.g., page sizes or token limits).
        page_num: Page number for paginated results from retrieval services.
        filter_string: Optional filter criteria string for metadata filtering
            during retrieval.
        scope: Scope of search for retrieval operations. 'both' searches
            documents and keywords, 'doc' searches only documents, 'keyword'
            searches only keywords.
        extra_repo_ids: Optional list of additional repository IDs to include
            in retrieval.
        score_threshold: Minimum relevance score threshold for retrieved items.
            Results below this threshold are typically discarded.
        top_n: Number of top-scoring results to retrieve or consider.
        prompt_file: Path to the YAML template file containing system prompts.
        prompt_path: Nested path within the template file to locate the
            specific system prompt (e.g., "system/ai4ps").
        api_key: API key for authenticating with the language model service.
        base_url: Base URL endpoint for the language model API service.
        model: Identifier of the specific language model to use for generation.
        frequency_penalty: Penalty applied to new tokens based on their
            frequency in the text so far, discouraging repetition of exact
            words/phrases. Values range from -2.0 to 2.0.
        max_tokens: Maximum number of tokens to generate in language model
            responses.
        n: Number of completion choices to generate for each input.
        presence_penalty: Penalty applied to new tokens based on their
            presence in the text so far, discouraging repetition of concepts.
            Values range from -2.0 to 2.0.
        reasoning_effort: Level of reasoning effort for the language model.
            Typically 'low', 'medium', or 'high'.
        response_format: Desired response format from the language model.
            For example, {'type': 'json_object'} to request JSON response.
        stream: Flag to enable or disable streaming of responses from the
            language model. If True, responses are sent as a series of events.
        temperature: Sampling temperature for language model responses
            (controls randomness). Higher values mean more random responses.
        top_p: Nucleus sampling parameter for language model responses
            (controls diversity). Considers tokens with cumulative probability
            mass up to top_p.
        user: User identifier for API interactions, particularly for chat or
            language model services.
        execute_code: Flag indicating whether code execution is permitted
            during an analysis operation.
        obs_file_list: List of OBS object keys (file paths) to download and
            include as context in the query. Files are converted to markdown.
        server_dir: Local directory path for temporary file storage during
            file downloads and processing.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket containing the files.
        part_size: Size of each part for multipart downloads from OBS.
        task_num: Number of concurrent tasks for multipart downloads from OBS.
        max_concurrency: Maximum number of files to download from OBS
            concurrently.
        max_workers: Maximum number of worker processes to use for file
            conversion operations.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries for
            API calls.
        max_retries: Maximum number of retry attempts for API calls.

    Returns:
        A list containing the results from all executed research workflows.
        Each element corresponds to a research goal extracted from the input
        paper, containing the complete analysis results from the
        retrieve-plan-submit process.

    Raises:
        McpError: If any of the underlying API calls fail after all retry
            attempts.
        Exception: Various exceptions may be returned as list elements if
            individual research workflows fail during execution.

    Examples:
        Conduct research on a paper:
            >>> data_sources = {
            ...     "gene_expression": "/path/to/expression_data.csv",
            ...     "genome_annotation": "/path/to/annotation.gtf"
            ... }
            >>> paper_text = "This study analyzed gene expression..."
            >>> results = await in_silico_research(paper_text, data_sources)
            >>> for i, result in enumerate(results):
            ...     print(f"Research goal {i+1} result: {result}")

        Custom configuration:
            >>> results = await in_silico_research(
            ...     paper_text,
            ...     data_sources,
            ...     output_dir="/custom/output/path/",
            ...     execute_code=True,
            ...     top_n=15
            ... )
    """
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
        obs_file_list=obs_file_list,
        server_dir=server_dir,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        part_size=part_size,
        task_num=task_num,
        max_concurrency=max_concurrency,
        max_workers=max_workers,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    tasks = [
        retrieve_plan_submit(
            goal_description=goal_meta["goal"],
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
            meta_meta="\n\n" + goal_meta["context"],
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
        )
        for goal_meta in goal_list
    ]
    response = await gather(*tasks, return_exceptions=True)
    return response
