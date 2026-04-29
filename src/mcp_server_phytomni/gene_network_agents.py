# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Gene network analysis agents for plant bioinformatics research.

This module provides specialized agents for analyzing gene networks in plant
genomics, focusing on identifying and characterizing relationships between
genes and their regulatory networks. It leverages computational analysis
workflows to examine gene interactions, co-expression patterns, and functional
associations.

Key functionalities include:
- Network topology analysis and visualization
- Gene interaction prediction and validation
- Co-expression network construction
- Functional module identification
- Integration with plant-specific databases and resources

The module is designed for researchers studying plant gene regulatory networks,
metabolic pathways, and systems-level genomics approaches.

Examples:
    Basic gene network analysis:
        >>> result = await network_analysis(
        ...     species='rice',
        ...     to_id='TO:0000207'
        ... )
        >>> print(f"Network task: {result['network_task']}")

    Batch processing multiple traits:
        >>> result = await network_analysis(
        ...     species='arabidopsis',
        ...     to_id='TO:0000207',
        ...     batch=True,
        ...     user_id='batch_user_001'
        ... )
"""

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
    """Perform comprehensive gene network analysis for a target trait.

    This function initiates a computational workflow to analyze gene networks
    associated with a specific phenotypic trait, including interaction
    prediction, co-expression analysis, and functional module identification.
    It leverages plant-specific databases and advanced network analysis
    algorithms to characterize gene relationships and regulatory patterns.

    Args:
        species: The target species for analysis in Latin lowercase format
            with spaces (e.g., 'oryza sativa', 'arabidopsis thaliana').
        to_id: The Trait Ontology identifier for network analysis. Trait
            Ontology (TO) is a controlled vocabulary for plant phenotypic
            traits (e.g., 'TO:0000207' for plant height, 'TO:0000136' for
            drought resistance).
        user_id: Unique identifier for the user submitting the analysis task.
        batch: Flag indicating whether the operation is part of a batch
            processing workflow. When True, skips individual output directory
            creation.
        prompt_file: Path to the YAML template file containing system prompts
            for guiding the analysis workflow.
        deepgenome_data: Path to the comprehensive genomic dataset file
            containing species-specific reference data and analysis
            configurations.
        output_dir: Output directory path for storing results of analysis or
            operations (e.g., an OBS path). Used when batch=False.
        model_url: Base URL endpoint for the language model API service used
            for analysis interpretation and report generation.
        model_name: Identifier of the specific language model to use for
            generating analysis summaries and interpretations.
        coder_api_key: API key for authenticating with the language model
            service used for computational analysis tasks.
        access_key_id: Access key identifier for Object Storage Service (OBS)
            authentication, required for data upload and retrieval operations.
        secret_access_key: Secret access key for OBS authentication, paired
            with access_key_id for secure storage operations.
        obs_server: Base URL endpoint for the Object Storage Service where
            analysis results and intermediate data are stored.
        bucket_name: Name of the OBS bucket designated for storing analysis
            results and associated data files.
        analysis_url: Base URL endpoint for the bioinformatics analysis
            platform where computational workflows are executed.
        region: Geographical region identifier for the analysis service,
            affecting data locality and service availability.
        resource_dict: Dictionary mapping computational resource levels
            ('small', 'medium', 'large') to their respective CPU and memory
            allocations for analysis job scheduling.
        app_id_dict: Dictionary mapping computational resource levels to their
            corresponding application identifiers on the analysis platform.
        timeout: General request timeout in seconds for API calls, preventing
            indefinite blocking on network operations.
        retriable_codes: List of HTTP status codes that trigger retries for
            API calls, enabling resilient operation under transient failures.
        max_retries: Maximum number of retry attempts for API calls before
            raising an exception and terminating the operation.
        max_poll: Maximum total duration in seconds to monitor the analysis
            task before timing out, ensuring bounded execution time.

    Returns:
        A dictionary containing the network analysis task information with the
        following structure:
        {
            'network_task': {
                'task_id': str,          # Unique task identifier
                'output_dir': str,       # Analysis results directory
                'job_name': str,         # Analysis job name
                'compute_resource': str  # Allocated resource level
            }
        }

    Raises:
        McpError: If the analysis task submission fails after all retry
            attempts.
        FileNotFoundError: If the prompt file or deepgenome data file cannot
            be located at the specified paths.
        ValueError: If invalid species or trait ontology identifiers are
            provided.
        OSError: If output directory creation or OBS operations fail.

    Examples:
        Basic network analysis for plant height trait:
            >>> result = await network_analysis(
            ...     species='oryza sativa',
            ...     to_id='TO:0000207'
            ... )
            >>> print(f"Task ID: {result['network_task']['task_id']}")

        Batch processing with custom configuration:
            >>> result = await network_analysis(
            ...     species='arabidopsis thaliana',
            ...     to_id='TO:0000136',
            ...     batch=True,
            ...     user_id='batch_user_001',
            ...     timeout=3600.0,
            ...     max_retries=5
            ... )
            >>> print(f"Output: {result['network_task']['output_dir']}")

    Note:
        This function creates output directories automatically when
        batch=False. For batch operations, ensure output_dir is properly
        configured before calling this function. The analysis includes network
        topology metrics, functional enrichment analysis, and visualization
        outputs for genes associated with the specified trait ontology.
    """
    if not batch:
        if not user_id:
            user_id = str(uuid1())
        output_dir = create_output_dir(
            user_id=user_id,
            task="network_task",
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )

    goal_description = get_prompt(
        prompt_file, "user/gene_network_analysis", {"to_id": to_id}
    )
    data_list = get_data_list(
        deepgenome_data, "gene_network_analysis", species
    )
    meta = get_prompt(prompt_file, "user/gene_network_analysis_meta")
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
        task_name="gene-network-agents-task",
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource="small",
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {"network_task": gene_network_task}
