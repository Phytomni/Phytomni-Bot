# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for conducting deep research and generating
comprehensive literature reviews.

It includes functions that leverage retrieval-augmented generation (RAG) to
expand user queries into multiple research dimensions, retrieve relevant
documents for each dimension, and synthesize the findings into a cohesive
research report.
"""
from asyncio import gather
from json import loads
from typing import Any, Dict, List, Optional, Union

from .chat_agents import phyto_chat
from .config.defaults import ReviewConfig
from .config.settings import SensitiveConfig
from .knowledge_agents import multi_retrieve
from .utils import download_list_convert, get_prompt

rc = ReviewConfig()
sc = SensitiveConfig().load()


async def deep_research(
    user_query: str,
    prompt_file: str = rc.PROMPT_FILE,
    prompt_path: str = rc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = rc.FREQUENCY_PENALTY,
    n: int = rc.N,
    presence_penalty: float = rc.PRESENCE_PENALTY,
    reasoning_effort: Optional[str] = rc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = rc.RESPONSE_FORMAT,
    stream: bool = rc.STREAM,
    temperature: float = rc.TEMPERATURE,
    top_p: float = rc.TOP_P,
    user: str = rc.USER,
    retrieve_url: str = rc.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = rc.REPO_ID_DICT,
    page_num: int = rc.PAGE_NUM,
    filter_string: Optional[str] = rc.FILTER_STRING,
    scope: str = rc.SCOPE,
    extra_repo_ids: Optional[List[str]] = rc.EXTRA_REPO_IDS,
    rerank_url: str = rc.RERANK_URL,
    rerank_batch_size: int = rc.RERANK_BATCH_SIZE,
    score_threshold: float = rc.SCORE_THRESHOLD,
    top_n: int = rc.TOP_N,
    obs_file_list: List[str] = [],
    server_dir: str = rc.TEMP_DIR,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = rc.OBS_SERVER,
    bucket_name: str = rc.BUCKET_NAME,
    part_size: int = rc.PART_SIZT,
    task_num: int = rc.TASK_NUM,
    max_concurrency: int = rc.MAX_CONCURRENCY,
    max_workers: int = rc.MAX_WORKERS,
    timeout: float = rc.TIMEOUT,
    retriable_codes: List[int] = rc.RETRIABLE_CODES,
    max_retries: int = rc.MAX_RETRIES,
    max_tokens: int = rc.MAX_TOKENS,
) -> Dict[str, Any]:
    """Performs an in-depth research process based on a user query.

    This function orchestrates a multi-step process:
    1. It first uses a large language model (phyto_chat) to expand the
       user_query into several distinct research dimensions.
    2. For each identified research dimension, it retrieves relevant documents
       using multi_retrieve.
    3. The retrieved documents for each dimension are formatted and then used
       as context (knowledge) along with the dimensions themselves to generate
       a comprehensive research report via another phyto_chat call.
    4. The final report is augmented with a flat list of all retrieved
       documents.

    Args:
        user_query: The initial query from the user to conduct deep research
            on.
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
        retrieve_url: URL for the document retrieval service.
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
        rerank_url: URL for the document reranking service.
        rerank_batch_size: Batch size for reranking operations, if reranking
            is applied to retrieved documents.
        score_threshold: Minimum relevance score threshold for retrieved items.
            Results below this threshold are typically discarded.
        top_n: Number of top-scoring results to retrieve or consider.
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
        A dictionary containing the generated research report and supporting
        documents. Specifically, it is the response from the final phyto_chat
        call, augmented with:
        - "doc_list" (List[Dict]): A flat list containing all document
          dictionaries retrieved by multi_retrieve across all research
          dimensions.
        - "total" (int): A hardcoded integer value of 10000.
        The main content of the report is typically found within
        response['choices'][0]['message']['content'].

    Raises:
        McpError: If any of the underlying phyto_chat or multi_retrieve
            calls fail after all retry attempts.
        JSONDecodeError: If the first phyto_chat response (for query
            expansion) is not valid JSON or does not conform to the expected
            structure for extracting research dimensions.
        KeyError: If expected keys are missing from intermediate API responses
            or data structures.

    Examples:
        Basic deep research:
            >>> result = await deep_research(
            ...     "photosynthesis mechanisms in C4 plants"
            ... )
            >>> print(result['choices'][0]['message']['content'])

        Custom parameters:
            >>> result = await deep_research(
            ...     "CRISPR applications in plant breeding",
            ...     top_n=20,
            ...     temperature=0.2
            ... )
    """
    original_user_query = user_query
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
            fragment = (f'[user upload file {i+1} begin]\n'
                        f'{doc}\n[user upload file {i+1} end]')
            if total_length + len(fragment) <= max_tokens:
                upload_results.append(fragment)
                total_length += len(fragment)
            else:
                break
        upload_context = '\n\n'.join(upload_results)
        user_query = get_prompt(
            prompt_file, 'user/deep_research_query_file',
            {'upload_context': upload_context, 'user_query': user_query})
    else:
        user_query = get_prompt(prompt_file,
                                'user/deep_research_query',
                                {'user_query': user_query})

    query_response = await phyto_chat(
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
                "type": "object",
                "properties": {
                    "Research_dimensions": {
                        "type": "array",
                        "description":
                            "Four logically interconnected and "
                            "thematically coherent research aspects",
                        "items": {
                            "type": "string"
                        }
                    }
                },
                "required": ["Research_dimensions"]
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

    if (not query_response or
            'choices' not in query_response or
            not query_response['choices'] or
            not query_response['choices'][0] or
            'message' not in query_response['choices'][0] or
            not query_response['choices'][0]['message'] or
            'content' not in query_response['choices'][0]['message']):
        raise ValueError("Invalid response structure from phyto_chat")

    dimensions_str = query_response['choices'][0]['message']['content']
    start_index = dimensions_str.find('{')
    end_index = dimensions_str.rfind('}') + 1
    json_part = dimensions_str[start_index:end_index]
    dimensions = loads(json_part)['Research_dimensions']
    tasks = [
        multi_retrieve(
            user_query=dimension,
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
        for dimension in dimensions
    ]
    results = await gather(*tasks, return_exceptions=True)
    dimensions_retrieval = []
    all_doc_list = []
    file_id = 0
    upload_length = total_length
    dimension_length = (max_tokens - upload_length) / 4
    for di, dimension_result in enumerate(results):
        retrieve_results = []
        if isinstance(dimension_result, BaseException):
            dimensions_retrieval.append('')
            continue

        for doc in dimension_result.get('doc_list', []):
            all_doc_list.append(doc)
            header = f"[document {file_id+1} begin] {doc['title']}"
            content_field = (doc.get('big_content') if 'big_content' in doc
                             else doc.get('content', ''))
            body = (f"{doc['subtitle']}\n{content_field}"
                    if doc.get('subtitle') else doc.get('content', ''))
            fragment = f'{header}\n{body} [document {file_id+1} end]'
            if total_length + len(fragment) <= (
                    upload_length + dimension_length * (di + 1)):
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break
            file_id += 1
        dimensions_retrieval.append('\n\n'.join(retrieve_results))
    prompt_parameters = {
        f'research_point_{dimension_id+1}': dimension
        for dimension_id, dimension in enumerate(dimensions)}
    prompt_parameters.update({
        f'knowledge_{dimension_id+1}': retrieval
        for dimension_id, retrieval in enumerate(dimensions_retrieval)
    })
    prompt_parameters.update({'user_query': original_user_query})
    report_response = await phyto_chat(
        user_query=get_prompt(prompt_file,
                              'user/deep_research_report',
                              prompt_parameters),
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

    if (report_response and
            'choices' in report_response and
            report_response['choices'] and
            report_response['choices'][0] and
            'message' in report_response['choices'][0] and
            report_response['choices'][0]['message']):
        report_response['choices'][0]['message'].update(
            {'doc_list': all_doc_list, 'total': 10000})
        if 'content' in report_response['choices'][0]['message']:
            system_response_content = (
                report_response['choices'][0]['message']['content'])
        else:
            raise ValueError(
                'Invalid response structure for follow-up questions generation')
    else:
        raise ValueError(
            'Invalid response structure from phyto_chat in report generation')

    follow_up_response = await phyto_chat(
        user_query=get_prompt(
            prompt_file, 'system/follow_up_questions',
            {
                'user_query': original_user_query,
                'system_response': system_response_content
            }),
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
    follow_up_content = ''
    if (follow_up_response and 'choices' in follow_up_response and
            len(follow_up_response['choices']) > 0 and
            'message' in follow_up_response['choices'][0] and
            follow_up_response['choices'][0]['message'] is not None and
            'content' in follow_up_response['choices'][0]['message']):
        follow_up_content = (
            follow_up_response['choices'][0]['message']['content'])
    follow_up_list = []
    if follow_up_content:
        start_index = follow_up_content.find('[')
        end_index = follow_up_content.rfind(']') + 1
        if start_index != -1 and end_index > start_index:
            try:
                json_part = follow_up_content[start_index:end_index]
                follow_up_list = loads(json_part)
            except (ValueError, TypeError):
                follow_up_list = []
    report_response['choices'][0]['message'].update(
        {'follow_up_questions': follow_up_list})
    return report_response
