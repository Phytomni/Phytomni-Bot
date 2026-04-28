# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for protein design and computational
structural analysis.

It includes functions that leverage computational biology and bioinformatics
tools to analyze protein structures, predict protein properties, and perform
digital design workflows for protein engineering applications.
"""
from typing import Dict, List
from uuid import uuid1

from .analyst_agents import get_data_list, create_output_dir, submit
from .config.defaults import DigitalDesignConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt
from json import loads, dumps

ddc = DigitalDesignConfig()
sc = SensitiveConfig().load()


async def protein_design_analysis(
    species: str,
    gene_id: str,
    user_id: str = ddc.USER_ID,
    batch: bool = False,
    enable_auto_select: bool = False,
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
    """Perform protein design analysis for a specific gene.

    This function conducts comprehensive protein design analysis by generating
    analysis goals, retrieving relevant genomic data, and submitting
    computational tasks for protein structure prediction, property analysis,
    and design optimization.

    Args:
        species: The species name for which protein design analysis is
            performed (e.g., "Arabidopsis_thaliana").
        gene_id: The specific gene identifier to analyze for protein design.
        user_id: Identifier for the user submitting the analysis task.
        batch: Flag indicating whether this is part of a batch processing
            workflow. If False, a new output directory will be created.
        prompt_file: Path to the YAML template file containing system prompts.
        deepgenome_data: Path to the species data configuration file containing
            genome and annotation information.
        output_dir: Output directory path for storing results of analysis
            or operations (e.g., an OBS path).
        model_url: Base URL endpoint for the coding model API service.
        model_name: Identifier of the specific coding model to use for
            generating analysis code.
        coder_api_key: API key for authenticating with the coding model
            service.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket for storing analysis results.
        analysis_url: URL for the workflow analysis service.
        region: Cloud service region for analysis operations.
        resource_dict: Dictionary mapping compute resource types to their
            CPU and memory specifications.
        app_id_dict: Dictionary mapping compute resource types to their
            corresponding application IDs.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries for
            API calls.
        max_retries: Maximum number of retry attempts for API calls.
        max_poll: Maximum duration in seconds for polling the status of
            long-running tasks.

    Returns:
        A dictionary containing the protein design task results with the key
        'protein_design_task' mapping to the complete analysis results.

    Raises:
        McpError: If any of the underlying API calls or file operations fail
            after all retry attempts.
        FileNotFoundError: If the deepgenome_data file or other required
            resources cannot be found.

    Examples:
        Analyze protein design for a specific gene:
            >>> result = await protein_design_analysis(
            ...     species="Arabidopsis_thaliana",
            ...     gene_id="AT1G01010"
            ... )
            >>> print(result['protein_design_task'])

        Custom output directory:
            >>> result = await protein_design_analysis(
            ...     species="Zea_mays",
            ...     gene_id="GRMZM2G000001",
            ...     output_dir="/custom/output/path/",
            ...     batch=True
            ... )
    """
    goal_description = get_prompt(prompt_file, 'user/protein_design_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'protein_design_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = str(uuid1())
        output_dir = create_output_dir(user_id, 'protein_design_task')
    meta = get_prompt(prompt_file, 'user/protein_design_analysis_meta')
    pr_design_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
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


async def promoter_design_analysis(
    species: str,
    gene_id: str,
    user_id: str = ddc.USER_ID,
    batch: bool = False,
    enable_auto_select: bool = False,
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
    """Perform promoter design analysis for a specific gene.

    This function conducts comprehensive promoter design analysis by generating
    analysis goals, retrieving relevant genomic data, and submitting
    computational tasks for promoter epicmodification prediction, property analysis,
    and design optimization.

    Args:
        species: The species name for which promoter design analysis is
            performed (e.g., "Arabidopsis_thaliana").
        gene_id: The specific gene identifier to analyze for promoter design.
        user_id: Identifier for the user submitting the analysis task.
        batch: Flag indicating whether this is part of a batch processing
            workflow. If False, a new output directory will be created.
        prompt_file: Path to the YAML template file containing system prompts.
        deepgenome_data: Path to the species data configuration file containing
            genome and annotation information.
        output_dir: Output directory path for storing results of analysis
            or operations (e.g., an OBS path).
        model_url: Base URL endpoint for the coding model API service.
        model_name: Identifier of the specific coding model to use for
            generating analysis code.
        coder_api_key: API key for authenticating with the coding model
            service.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket for storing analysis results.
        analysis_url: URL for the workflow analysis service.
        region: Cloud service region for analysis operations.
        resource_dict: Dictionary mapping compute resource types to their
            CPU and memory specifications.
        app_id_dict: Dictionary mapping compute resource types to their
            corresponding application IDs.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries for
            API calls.
        max_retries: Maximum number of retry attempts for API calls.
        max_poll: Maximum duration in seconds for polling the status of
            long-running tasks.

    Returns:
        A dictionary containing the promoter design task results with the key
        'promoter_design_task' mapping to the complete analysis results.

    Raises:
        McpError: If any of the underlying API calls or file operations fail
            after all retry attempts.
        FileNotFoundError: If the deepgenome_data file or other required
            resources cannot be found.

    Examples:
        Analyze promoter design for a specific gene:
            >>> result = await promoter_design_analysis(
            ...     species="Arabidopsis_thaliana",
            ...     gene_id="AT1G01010"
            ... )
            >>> print(result['promoter_design_task'])

        Custom output directory:
            >>> result = await promoter_design_analysis(
            ...     species="Zea_mays",
            ...     gene_id="GRMZM2G000001",
            ...     output_dir="/custom/output/path/",
            ...     batch=True
            ... )
    """
    goal_description = get_prompt(prompt_file, 'user/promoter_design_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'promoter_design_analysis',
                              species)
    data_list = loads(dumps(data_list).replace('/gene_id', f'/{gene_id}'))
    if not batch:
        if not user_id:
            user_id = str(uuid1())
        output_dir = create_output_dir(user_id, 'promoter_design_task')
    meta = get_prompt(prompt_file, 'user/promoter_design_analysis_meta')
    dna_design_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
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
        task_name='deepgenome-agents-dnadesign-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'promoter_design_task': dna_design_task}


async def design_module(
    species: str,
    gene_id: str,
    user_id: str = ddc.USER_ID,
    batch: bool = True,
    enable_auto_select: bool = False,
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
    """Execute a complete protein design workflow module.

    This function serves as a high-level interface for protein design tasks,
    orchestrating the entire workflow including output directory management
    and protein design analysis execution. It acts as a wrapper around
    protein_design_analysis with enhanced directory management capabilities.

    Args:
        species: The species name for which protein design analysis is
            performed (e.g., "arabidopsis thaliana").
        gene_id: The specific gene identifier to analyze for protein design.
        user_id: Identifier for the user submitting the analysis task.
        batch: Flag indicating whether this is part of a batch processing
            workflow. If False, a new output directory will be created
            automatically.
        prompt_file: Path to the YAML template file containing system prompts.
        deepgenome_data: Path to the species data configuration file containing
            genome and annotation information.
        output_dir: Output directory path for storing results of analysis
            or operations (e.g., an OBS path).
        model_url: Base URL endpoint for the coding model API service.
        model_name: Identifier of the specific coding model to use for
            generating analysis code.
        coder_api_key: API key for authenticating with the coding model
            service.
        access_key_id: Access key ID for OBS authentication.
        secret_access_key: Secret access key for OBS authentication.
        obs_server: Server endpoint URL for the Object Storage Service.
        bucket_name: Name of the OBS bucket for storing analysis results.
        analysis_url: URL for the workflow analysis service.
        region: Cloud service region for analysis operations.
        resource_dict: Dictionary mapping compute resource types to their
            CPU and memory specifications.
        app_id_dict: Dictionary mapping compute resource types to their
            corresponding application IDs.
        timeout: General request timeout in seconds for API calls.
        retriable_codes: List of HTTP status codes that trigger retries for
            API calls.
        max_retries: Maximum number of retry attempts for API calls.
        max_poll: Maximum duration in seconds for polling the status of
            long-running tasks.

    Returns:
        A dictionary containing the complete protein design task results,
        typically with the same structure as returned by
        protein_design_analysis.

    Raises:
        McpError: If any of the underlying API calls or file operations fail
            after all retry attempts.
        FileNotFoundError: If the deepgenome_data file or other required
            resources cannot be found.

    Examples:
        Execute protein design module:
            >>> result = await design_module(
            ...     species="arabidopsis thaliana",
            ...     gene_id="AT1G01010"
            ... )
            >>> print(result)

        Non-batch processing with custom user:
            >>> result = await design_module(
            ...     species="zea mays",
            ...     gene_id="GRMZM2G000001",
            ...     user_id="researcher_001",
            ...     batch=False
            ... )

    Note:
        This function is designed to be the primary entry point for protein
        design workflows, providing simplified parameter management and
        automatic directory creation when needed.
    """
    if not batch:
        if not user_id:
            user_id = str(uuid1())
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
        enable_auto_select=enable_auto_select,
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
    promoter_design_task = await promoter_design_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        enable_auto_select=enable_auto_select,
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

    return {**protein_design_task, **promoter_design_task}
