# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
from asyncio import gather
from json import loads
from typing import Any, Dict, List, Optional, Union

from .chat_agents import phyto_chat
from .config.defaults import ReviewConfig
from .config.settings import SensitiveConfig
from .knowledge_agents import multi_retrieve
from .utils import get_prompt

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
    reasoning_effort: str = rc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = rc.RESPONSE_FORMAT,
    stream: bool = rc.STREAM,
    temperature: float = rc.TEMPERATURE,
    top_p: float = rc.TOP_P,
    user: str = rc.USER,
    repo_id_dict: Optional[Dict[str, int]] = rc.REPO_ID_DICT,
    page_num: int = rc.PAGE_NUM,
    filter_string: Optional[str] = rc.FILTER_STRING,
    scope: str = rc.SCOPE,
    extra_repo_ids: Optional[List[str]] = rc.EXTRA_REPO_IDS,
    score_threshold: float = rc.SCORE_THRESHOLD,
    top_n: int = rc.TOP_N,
    timeout: float = rc.TIMEOUT,
    retriable_codes: List[int] = rc.RETRIABLE_CODES,
    max_retries: int = rc.MAX_RETRIES,
) -> Dict[str, Any]:
    """Performs an in-depth research process based on a user query.

    This function orchestrates a multi-step process:
    1.  It first uses a large language model (`phyto_chat`) to expand the
        `user_query` into several distinct research dimensions.
    2.  For each identified research dimension, it retrieves relevant documents
        using `multi_retrieve`.
    3.  The retrieved documents for each dimension are formatted and then used
        as context (knowledge) along with the dimensions themselves to generate
        a comprehensive research report via another `phyto_chat` call.
    4.  The final report is augmented with a flat list of all retrieved
        documents.

    Args:
        user_query: The initial query from the user to conduct deep research
            on.
        prompt_file: Path to the prompt template file used by `phyto_chat` for
            generating research dimensions and the final report.
            Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the `prompt_file` to retrieve specific
            prompts for `phyto_chat`. Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model.
            Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service (`phyto_chat`).
            Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use via `phyto_chat`.
            Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) for
            `phyto_chat`. Defaults to `FREQUENCY_PENALTY`.
        n: Number of choices to generate by `phyto_chat` for query expansion
            and report generation. Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) for
            `phyto_chat`. Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for `phyto_chat`.
            Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for `phyto_chat`.
            Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for `phyto_chat`.
            Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for `phyto_chat`.
            Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for `phyto_chat`.
            Defaults to `TOP_P`.
        user: Unique session identifier for the end-user,
            passed to `phyto_chat`. Defaults to `USER`.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for document retrieval, passed to
            `multi_retrieve`. Defaults to `REPO_ID_DICT`.
        page_num: Pagination page number for retrieval results from each
            repository, passed to `multi_retrieve`. Defaults to `PAGE_NUM`.
        filter_string: Optional filter criteria string for metadata filtering
            during document retrieval, passed to `multi_retrieve`.
            Defaults to `FILTER_STRING`.
        scope: Scope for document retrieval by `multi_retrieve` (e.g., 'title',
            'content', 'both'), applied for each research dimension.
            Defaults to `SCOPE`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in document retrieval, passed to `multi_retrieve`.
            Defaults to `EXTRA_REPO_IDS`.
        score_threshold: Minimum relevance score threshold applied during
            document retrieval by `multi_retrieve` for each dimension.
            Defaults to `SCORE_THRESHOLD`.
        top_n: The number of top-scoring documents to retrieve by
            `multi_retrieve` for each identified research dimension.
            Defaults to `TOP_N`.
        timeout: Request timeout in seconds for the underlying `phyto_chat` and
            `multi_retrieve` API calls. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `phyto_chat` and `multi_retrieve` API calls.
            Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying
            `phyto_chat` and `multi_retrieve` API calls.
            Defaults to `MAX_RETRIES`.

    Returns:
        A dictionary containing the generated research report and supporting
        documents. Specifically, it is the response from the final `phyto_chat`
        call, augmented with:
        - "doc_list" (List[Dict]): A flat list containing all document
          dictionaries retrieved by `multi_retrieve` across all research
          dimensions. The order is based on the processing order of
          dimensions and then the order within each dimension's retrieval
          results.
        - "total" (int): A hardcoded integer value of `10000`.
        The main content of the report is typically found within
        `response['choices'][0]['message']['content']`.

    Raises:
        McpError: If any of the underlying `phyto_chat` or `multi_retrieve`
            calls fail after all retry attempts.
        JSONDecodeError: If the first `phyto_chat` response (for query
            expansion) is not valid JSON or does not conform to the expected
            structure for extracting research dimensions.
        KeyError: If expected keys (e.g., 'choices', 'message', 'content',
            'doc_list', 'Research_dimensions') are missing from intermediate
            API responses or data structures.
        TypeError: If an operation is attempted on an object of an
            inappropriate type, for example, if a `multi_retrieve` call
            returns an exception that is not handled before attempting to
            access its `['doc_list']` attribute.
    """
    query_response = await phyto_chat(
        user_query=get_prompt(prompt_file,
                              'user/deep_research_query',
                              {'user_query': user_query}),
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
    dimensions_str = query_response['choices'][0]['message']['content']
    start_index = dimensions_str.find('{')
    end_index = dimensions_str.rfind('}') + 1
    json_part = dimensions_str[start_index:end_index]
    dimensions = loads(json_part)['Research_dimensions']
    tasks = [
        multi_retrieve(
            user_query=dimension,
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
        for dimension in dimensions
    ]
    results = await gather(*tasks, return_exceptions=True)
    dimensions_retrieval = []
    all_doc_list = []
    file_id = 0
    for dimension_result in results:
        retrieve_results = []
        for eachdoc in dimension_result['doc_list']:
            all_doc_list.append(eachdoc)
            if eachdoc["subtitle"]:
                retrieve_results.append(
                    f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                    f'{eachdoc["subtitle"]}\n{eachdoc["content"]} '
                    f'[document {file_id+1} end]')
            else:
                retrieve_results.append(
                    f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                    f'{eachdoc["content"]} [document {file_id+1} end]')
            file_id += 1
        dimensions_retrieval.append('\n\n'.join(retrieve_results))
    prompt_parameters = {
        f'research_point_{dimension_id+1}': dimension
        for dimension_id, dimension in enumerate(dimensions)}
    prompt_parameters.update({
        f'knowledge_{dimension_id+1}': retrieval
        for dimension_id, retrieval in enumerate(dimensions_retrieval)
    })
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
    report_response['choices'][0]['message'].update(
        {'doc_list': all_doc_list, 'total': 10000})
    return report_response
