# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Handlers for MCP tool execution."""

from typing import Any

from ..analyst_agents import retrieve_plan_submit
from ..brief_gene_agents import brief_gene_function
from ..chat_agents import phyto_chat_with_follow
from ..config.defaults import (
    AnalystConfig,
    BriefGeneConfig,
    ChatConfig,
    DataConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    GeneNetworkConfig,
    InSilicoResearchConfig,
    KnowledgeConfig,
    ReviewConfig,
)
from ..config.settings import SensitiveConfig
from ..data_agents import rewrite_nl2sql
from ..deep_genome_agents import gene_function
from ..digital_design_agents import design_module
from ..gene_network_agents import network_analysis
from ..in_silico_research_agents import in_silico_research
from ..knowledge_agents import multi_retrieve_generate
from ..review_agents import deep_research


async def handle_chat_agent(args: Any) -> Any:
    """Execute ChatAgent with default runtime configuration."""
    chat_config = ChatConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await phyto_chat_with_follow(
        user_query=args.user_query,
        obs_file_list=args.obs_file_list,
        prompt_file=chat_config.PROMPT_FILE,
        prompt_path=chat_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=chat_config.FREQUENCY_PENALTY,
        n=chat_config.N,
        presence_penalty=chat_config.PRESENCE_PENALTY,
        reasoning_effort=chat_config.REASONING_EFFORT,
        response_format=chat_config.RESPONSE_FORMAT,
        stream=chat_config.STREAM,
        temperature=chat_config.TEMPERATURE,
        top_p=chat_config.TOP_P,
        user=chat_config.USER,
        server_dir=chat_config.TEMP_DIR,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=chat_config.OBS_SERVER,
        bucket_name=chat_config.BUCKET_NAME,
        part_size=chat_config.PART_SIZE,
        task_num=chat_config.TASK_NUM,
        timeout=chat_config.TIMEOUT,
        retriable_codes=chat_config.RETRIABLE_CODES,
        max_retries=chat_config.MAX_RETRIES,
        max_concurrency=chat_config.MAX_CONCURRENCY,
        max_workers=chat_config.MAX_WORKERS,
        max_tokens=chat_config.MAX_TOKENS,
    )


async def handle_knowledge_agent(args: Any) -> Any:
    """Execute KnowledgeAgent with default runtime configuration."""
    knowledge_config = KnowledgeConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await multi_retrieve_generate(
        user_query=args.user_query,
        retrieve_url=knowledge_config.RETRIEVE_URL,
        repo_id_dict=knowledge_config.REPO_ID_DICT,
        page_num=knowledge_config.PAGE_NUM,
        filter_string=knowledge_config.FILTER_STRING,
        scope=knowledge_config.SCOPE,
        extra_repo_ids=knowledge_config.EXTRA_REPO_IDS,
        rerank_url=knowledge_config.RERANK_URL,
        rerank_batch_size=knowledge_config.RERANK_BATCH_SIZE,
        score_threshold=knowledge_config.SCORE_THRESHOLD,
        top_n=knowledge_config.TOP_N,
        prompt_file=knowledge_config.PROMPT_FILE,
        prompt_path=knowledge_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=knowledge_config.FREQUENCY_PENALTY,
        max_tokens=knowledge_config.MAX_TOKENS,
        n=knowledge_config.N,
        presence_penalty=knowledge_config.PRESENCE_PENALTY,
        reasoning_effort=knowledge_config.REASONING_EFFORT,
        response_format=knowledge_config.RESPONSE_FORMAT,
        stream=knowledge_config.STREAM,
        temperature=knowledge_config.TEMPERATURE,
        top_p=knowledge_config.TOP_P,
        user=knowledge_config.USER,
        obs_file_list=args.obs_file_list,
        server_dir=knowledge_config.TEMP_DIR,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=knowledge_config.OBS_SERVER,
        bucket_name=knowledge_config.BUCKET_NAME,
        part_size=knowledge_config.PART_SIZE,
        task_num=knowledge_config.TASK_NUM,
        max_concurrency=knowledge_config.MAX_CONCURRENCY,
        max_workers=knowledge_config.MAX_WORKERS,
        timeout=knowledge_config.TIMEOUT,
        retriable_codes=knowledge_config.RETRIABLE_CODES,
        max_retries=knowledge_config.MAX_RETRIES,
    )


async def handle_data_agent(args: Any) -> Any:
    """Execute DataAgent with default runtime configuration."""
    data_config = DataConfig()
    sensitive_config = SensitiveConfig.load()
    return await rewrite_nl2sql(
        user_query=args.user_query,
        retrieve_url=data_config.RETRIEVE_URL,
        data_repo_id=data_config.DATA_REPO_ID,
        page_num=data_config.PAGE_NUM,
        page_size=data_config.DATA_PAGE_SIZE,
        filter_string=data_config.FILTER_STRING,
        scope=data_config.SCOPE,
        rerank_url=data_config.RERANK_URL,
        rerank_batch_size=data_config.RERANK_BATCH_SIZE,
        score_threshold=data_config.SCORE_THRESHOLD,
        prompt_file=data_config.PROMPT_FILE,
        prompt_path=data_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=data_config.FREQUENCY_PENALTY,
        n=data_config.N,
        presence_penalty=data_config.PRESENCE_PENALTY,
        reasoning_effort=data_config.REASONING_EFFORT,
        response_format=data_config.RESPONSE_FORMAT,
        stream=data_config.STREAM,
        temperature=data_config.TEMPERATURE,
        top_p=data_config.TOP_P,
        user=data_config.USER,
        database_url=data_config.DATABASE_URL,
        workspace_id=data_config.WORKSPACE_ID,
        subject_id=data_config.SUBJECT_ID,
        dialog_id=data_config.DIALOG_ID,
        need_insight=data_config.NEED_INSIGHT,
        simplify_response=data_config.SIMPLIFY_RESPONSE,
        timeout=data_config.TIMEOUT,
        retriable_codes=data_config.RETRIABLE_CODES,
        max_retries=data_config.MAX_RETRIES,
        max_tokens=data_config.MAX_TOKENS,
    )


async def handle_analyst_agent(args: Any) -> Any:
    """Execute AnalystAgent with default runtime configuration."""
    analyst_config = AnalystConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await retrieve_plan_submit(
        goal_description=args.goal_description,
        data_list=args.data_list,
        user_id=analyst_config.USER_ID,
        is_create_dir=analyst_config.CREATE_DIR,
        output_dir=analyst_config.OUTPUT_DIR,
        retrieve_url=analyst_config.RETRIEVE_URL,
        repo_id_dict=analyst_config.REPO_ID_DICT,
        page_num=analyst_config.PAGE_NUM,
        filter_string=analyst_config.FILTER_STRING,
        scope=analyst_config.SCOPE,
        extra_repo_ids=analyst_config.EXTRA_REPO_IDS,
        rerank_url=analyst_config.RERANK_URL,
        rerank_batch_size=analyst_config.RERANK_BATCH_SIZE,
        score_threshold=analyst_config.SCORE_THRESHOLD,
        top_n=analyst_config.TOP_N,
        prompt_file=analyst_config.PROMPT_FILE,
        prompt_path=analyst_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=analyst_config.FREQUENCY_PENALTY,
        max_tokens=analyst_config.MAX_TOKENS,
        n=analyst_config.N,
        presence_penalty=analyst_config.PRESENCE_PENALTY,
        reasoning_effort=analyst_config.REASONING_EFFORT,
        response_format=analyst_config.RESPONSE_FORMAT,
        stream=analyst_config.STREAM,
        temperature=analyst_config.TEMPERATURE,
        top_p=analyst_config.TOP_P,
        user=analyst_config.USER,
        obs_file_list=args.obs_file_list,
        server_dir=analyst_config.TEMP_DIR,
        execute_code=analyst_config.EXECUTE_CODE,
        model_url=sensitive_config.CODER_URL,
        model_name=sensitive_config.CODER_MODEL,
        coder_api_key=sensitive_config.CODER_API_KEY.get_secret_value(),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=analyst_config.OBS_SERVER,
        bucket_name=analyst_config.BUCKET_NAME,
        part_size=analyst_config.PART_SIZE,
        task_num=analyst_config.TASK_NUM,
        max_concurrency=analyst_config.MAX_CONCURRENCY,
        max_workers=analyst_config.MAX_WORKERS,
        analysis_url=analyst_config.ANALYSIS_URL,
        region=analyst_config.ANALYSIS_REGION,
        task_name=analyst_config.TASK_NAME + "-retrieve-plan",
        resource_dict=analyst_config.RESOURCE,
        app_id_dict=analyst_config.APP_ID,
        compute_resource=analyst_config.COMPUTE_RESOURCE,
        meta_meta=None,
        timeout=analyst_config.TIMEOUT,
        retriable_codes=analyst_config.RETRIABLE_CODES,
        max_retries=analyst_config.MAX_RETRIES,
    )


async def handle_review_agent(args: Any) -> Any:
    """Execute ReviewAgent with default runtime configuration."""
    review_config = ReviewConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await deep_research(
        user_query=args.user_query,
        prompt_file=review_config.PROMPT_FILE,
        prompt_path=review_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=review_config.FREQUENCY_PENALTY,
        n=review_config.N,
        presence_penalty=review_config.PRESENCE_PENALTY,
        reasoning_effort=review_config.REASONING_EFFORT,
        response_format=review_config.RESPONSE_FORMAT,
        stream=review_config.STREAM,
        temperature=review_config.TEMPERATURE,
        top_p=review_config.TOP_P,
        user=review_config.USER,
        retrieve_url=review_config.RETRIEVE_URL,
        repo_id_dict=review_config.REPO_ID_DICT,
        page_num=review_config.PAGE_NUM,
        filter_string=review_config.FILTER_STRING,
        scope=review_config.SCOPE,
        extra_repo_ids=review_config.EXTRA_REPO_IDS,
        rerank_url=review_config.RERANK_URL,
        rerank_batch_size=review_config.RERANK_BATCH_SIZE,
        score_threshold=review_config.SCORE_THRESHOLD,
        top_n=review_config.TOP_N,
        obs_file_list=args.obs_file_list,
        server_dir=review_config.TEMP_DIR,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=review_config.OBS_SERVER,
        bucket_name=review_config.BUCKET_NAME,
        part_size=review_config.PART_SIZE,
        task_num=review_config.TASK_NUM,
        max_concurrency=review_config.MAX_CONCURRENCY,
        max_workers=review_config.MAX_WORKERS,
        timeout=review_config.TIMEOUT,
        retriable_codes=review_config.RETRIABLE_CODES,
        max_retries=review_config.MAX_RETRIES,
        max_tokens=review_config.MAX_TOKENS,
    )


async def handle_brief_gene_agent(args: Any) -> Any:
    """Execute BriefGeneAgent with default runtime configuration."""
    brief_config = BriefGeneConfig()
    sensitive_config = SensitiveConfig.load()
    return await brief_gene_function(
        user_query=args.user_query,
        prompt_file=brief_config.PROMPT_FILE,
        prompt_path=brief_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=brief_config.FREQUENCY_PENALTY,
        n=brief_config.N,
        presence_penalty=brief_config.PRESENCE_PENALTY,
        reasoning_effort=brief_config.REASONING_EFFORT,
        response_format=brief_config.RESPONSE_FORMAT,
        stream=brief_config.STREAM,
        temperature=brief_config.TEMPERATURE,
        top_p=brief_config.TOP_P,
        user=brief_config.USER,
        retrieve_url=brief_config.RETRIEVE_URL,
        repo_id_dict=brief_config.REPO_ID_DICT,
        page_num=brief_config.PAGE_NUM,
        filter_string=brief_config.FILTER_STRING,
        scope=brief_config.SCOPE,
        extra_repo_ids=brief_config.EXTRA_REPO_IDS,
        rerank_url=brief_config.RERANK_URL,
        rerank_batch_size=brief_config.RERANK_BATCH_SIZE,
        score_threshold=brief_config.SCORE_THRESHOLD,
        top_n=brief_config.TOP_N,
        bi_url=brief_config.BI_URL,
        bi_token=sensitive_config.BI_TOKEN.get_secret_value(),
        max_concurrency=brief_config.MAX_CONCURRENCY,
        timeout=brief_config.TIMEOUT,
        retriable_codes=brief_config.RETRIABLE_CODES,
        max_retries=brief_config.MAX_RETRIES,
        max_tokens=brief_config.MAX_TOKENS,
    )


async def handle_deep_genome_agent(args: Any) -> Any:
    """Execute DeepGenomeAgent with default runtime configuration."""
    deep_genome_config = DeepGenomeConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await gene_function(
        species_code=args.species_code,
        gene_id=args.gene_id,
        user_id=deep_genome_config.USER_ID,
        batch=deep_genome_config.BATCH,
        epic_type=deep_genome_config.EPIC_TYPE,
        create_task_url=deep_genome_config.CREATE_TASK_URL,
        update_task_url=deep_genome_config.UPDATE_TASK_URL,
        database_url=deep_genome_config.DATABASE_URL,
        workspace_id=deep_genome_config.WORKSPACE_ID,
        subject_id=deep_genome_config.SUBJECT_ID,
        dialog_id=deep_genome_config.DIALOG_ID,
        need_insight=deep_genome_config.NEED_INSIGHT,
        prompt_file=deep_genome_config.PROMPT_FILE,
        deepgenome_data=deep_genome_config.DEEPGENOME_DATA,
        output_dir=deep_genome_config.OUTPUT_DIR,
        model_url=sensitive_config.CODER_URL,
        model_name=sensitive_config.CODER_MODEL,
        coder_api_key=sensitive_config.CODER_API_KEY.get_secret_value(),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=deep_genome_config.OBS_SERVER,
        bucket_name=deep_genome_config.BUCKET_NAME,
        analysis_url=deep_genome_config.ANALYSIS_URL,
        region=deep_genome_config.ANALYSIS_REGION,
        resource_dict=deep_genome_config.RESOURCE,
        app_id_dict=deep_genome_config.APP_ID,
        retrieve_url=deep_genome_config.RETRIEVE_URL,
        repo_id_dict=deep_genome_config.REPO_ID_DICT,
        page_num=deep_genome_config.PAGE_NUM,
        filter_string=deep_genome_config.FILTER_STRING,
        extra_repo_ids=deep_genome_config.EXTRA_REPO_IDS,
        rerank_url=deep_genome_config.RERANK_URL,
        rerank_batch_size=deep_genome_config.RERANK_BATCH_SIZE,
        score_threshold=deep_genome_config.SCORE_THRESHOLD,
        top_n=deep_genome_config.TOP_N,
        prompt_path=deep_genome_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=deep_genome_config.FREQUENCY_PENALTY,
        max_tokens=deep_genome_config.MAX_TOKENS,
        n=deep_genome_config.N,
        presence_penalty=deep_genome_config.PRESENCE_PENALTY,
        reasoning_effort=deep_genome_config.REASONING_EFFORT,
        response_format=deep_genome_config.RESPONSE_FORMAT,
        stream=deep_genome_config.STREAM,
        temperature=deep_genome_config.TEMPERATURE,
        top_p=deep_genome_config.TOP_P,
        user=deep_genome_config.USER,
        deepgenome_out=deep_genome_config.DEEPGENOME_OUT,
        download_path=deep_genome_config.DOWNLOAD_PATH,
        marker=deep_genome_config.DOWNLOAD_MARKER,
        max_keys=deep_genome_config.DOWNLOAD_MAX_KEYS,
        timeout=deep_genome_config.TIMEOUT,
        retriable_codes=deep_genome_config.RETRIABLE_CODES,
        max_retries=deep_genome_config.MAX_RETRIES,
        max_concurrency=deep_genome_config.MAX_CONCURRENCY,
        max_poll=deep_genome_config.MAX_POLL,
    )


async def handle_in_silico_research_agent(args: Any) -> Any:
    """Execute InSilicoResearchAgent with default runtime configuration."""
    in_silico_config = InSilicoResearchConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await in_silico_research(
        user_query=args.user_query,
        data_list=args.data_list,
        output_dir=in_silico_config.OUTPUT_DIR,
        repo_id_dict=in_silico_config.REPO_ID_DICT,
        page_num=in_silico_config.PAGE_NUM,
        filter_string=in_silico_config.FILTER_STRING,
        scope=in_silico_config.SCOPE,
        extra_repo_ids=in_silico_config.EXTRA_REPO_IDS,
        score_threshold=in_silico_config.SCORE_THRESHOLD,
        top_n=in_silico_config.TOP_N,
        prompt_file=in_silico_config.PROMPT_FILE,
        prompt_path=in_silico_config.PROMPT_PATH,
        api_key=sensitive_config.API_KEY.get_secret_value(),
        base_url=sensitive_config.BASE_URL,
        model=sensitive_config.MODEL_ID,
        frequency_penalty=in_silico_config.FREQUENCY_PENALTY,
        max_tokens=in_silico_config.MAX_TOKENS,
        n=in_silico_config.N,
        presence_penalty=in_silico_config.PRESENCE_PENALTY,
        reasoning_effort=in_silico_config.REASONING_EFFORT,
        response_format=in_silico_config.RESPONSE_FORMAT,
        stream=in_silico_config.STREAM,
        temperature=in_silico_config.TEMPERATURE,
        top_p=in_silico_config.TOP_P,
        user=in_silico_config.USER,
        obs_file_list=args.obs_file_list,
        server_dir=in_silico_config.TEMP_DIR,
        execute_code=in_silico_config.EXECUTE_CODE,
        model_url=sensitive_config.CODER_URL,
        model_name=sensitive_config.CODER_MODEL,
        coder_api_key=sensitive_config.CODER_API_KEY.get_secret_value(),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=in_silico_config.OBS_SERVER,
        bucket_name=in_silico_config.BUCKET_NAME,
        part_size=in_silico_config.PART_SIZE,
        task_num=in_silico_config.TASK_NUM,
        max_concurrency=in_silico_config.MAX_CONCURRENCY,
        max_workers=in_silico_config.MAX_WORKERS,
        timeout=in_silico_config.TIMEOUT,
        retriable_codes=in_silico_config.RETRIABLE_CODES,
        max_retries=in_silico_config.MAX_RETRIES,
    )


async def handle_digital_design_agent(args: Any) -> Any:
    """Execute DigitalDesignAgent with default runtime configuration."""
    design_config = DigitalDesignConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await design_module(
        species=args.species,
        gene_id=args.gene_id,
        user_id=design_config.USER_ID,
        batch=True,
        enable_auto_select=False,
        prompt_file=design_config.PROMPT_FILE,
        deepgenome_data=design_config.DEEPGENOME_DATA,
        output_dir=design_config.OUTPUT_DIR,
        model_url=sensitive_config.CODER_URL,
        model_name=sensitive_config.CODER_MODEL,
        coder_api_key=sensitive_config.CODER_API_KEY.get_secret_value(),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=design_config.OBS_SERVER,
        bucket_name=design_config.BUCKET_NAME,
        analysis_url=design_config.ANALYSIS_URL,
        region=design_config.ANALYSIS_REGION,
        resource_dict=design_config.RESOURCE,
        app_id_dict=design_config.APP_ID,
        timeout=design_config.TIMEOUT,
        retriable_codes=design_config.RETRIABLE_CODES,
        max_retries=design_config.MAX_RETRIES,
        max_poll=design_config.MAX_POLL,
    )


async def handle_gene_network_agent(args: Any) -> Any:
    """Execute GeneNetworkAgent with default runtime configuration."""
    network_config = GeneNetworkConfig()
    sensitive_config = SensitiveConfig.load()
    access_key_id, secret_access_key = sensitive_config.obs_credentials()
    return await network_analysis(
        species=args.species,
        to_id=args.to_id,
        user_id=network_config.USER_ID,
        batch=False,
        prompt_file=network_config.PROMPT_FILE,
        deepgenome_data=network_config.DEEPGENOME_DATA,
        output_dir=network_config.OUTPUT_DIR,
        model_url=sensitive_config.CODER_URL,
        model_name=sensitive_config.CODER_MODEL,
        coder_api_key=sensitive_config.CODER_API_KEY.get_secret_value(),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=network_config.OBS_SERVER,
        bucket_name=network_config.BUCKET_NAME,
        analysis_url=network_config.ANALYSIS_URL,
        region=network_config.ANALYSIS_REGION,
        resource_dict=network_config.RESOURCE,
        app_id_dict=network_config.APP_ID,
        timeout=network_config.TIMEOUT,
        retriable_codes=network_config.RETRIABLE_CODES,
        max_retries=network_config.MAX_RETRIES,
        max_poll=network_config.MAX_POLL,
    )
