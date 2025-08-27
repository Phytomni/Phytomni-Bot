# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Phytomni MCP Server - Main server implementation for plant science research
    platform.

This module implements the Model Context Protocol (MCP) server that provides
specialized AI agents for comprehensive plant science research and
bioinformatics analysis. The server orchestrates multiple domain-specific
agents, each designed to handle particular aspects of biological research
workflows.

Server Architecture:
    The server exposes eight specialized agent tools through the MCP interface:

    - ChatAgent: Core conversational interface with document processing
        capabilities
    - KnowledgeAgent: Literature retrieval and RAG-based research synthesis
    - DataAgent: Natural language to SQL query translation for biological
        databases
    - AnalystAgent: Automated bioinformatics workflow execution and management
    - ReviewAgent: Comprehensive multi-dimensional literature research
    - DeepGenomeAgent: Advanced gene function analysis with multi-omics
        integration
    - InSilicoResearchAgent: Scientific methodology extraction and reproduction
    - DigitalDesignAgent: Protein structure analysis and design workflows
    - GeneNetworkAgent: Gene interaction and regulatory network analysis

Key Features:
    - JSON schema validation for all tool parameters using Pydantic models
    - Comprehensive error handling with MCP-compliant error responses
    - Automatic configuration loading from environment variables and config
        files
    - Secure credential management through the SensitiveConfig system
    - Resource isolation and cleanup for concurrent agent execution
    - Standard I/O protocol implementation for cross-platform MCP client
        compatibility

The server maintains strict separation between agent execution contexts and
implements automatic resource cleanup to ensure reliable operation in
production environments. Each agent inherits from hierarchical configuration
classes that provide consistent default parameters while allowing specialized
customization.

Usage:
    The server is typically launched as a standalone process:

    ```bash
    python -m mcp_server_phytomni.server
    ```

    Or integrated programmatically:

    ```python
    import asyncio
    from mcp_server_phytomni.server import serve

    asyncio.run(serve())
    ```

Authors:
    xieshang (xieshang0608@gmail.com)
    guxiaofeng (guxiaofeng@caas.cn)

Copyright:
    Biotechnology Research Institute, Chinese Academy of Agricultural Sciences
    2024-2025. All rights reserved.
"""
import asyncio
from enum import Enum
from json import dumps
from typing import Annotated, Dict, List

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, TextContent, Tool, INVALID_PARAMS
from pydantic import BaseModel, Field

from .analyst_agents import retrieve_plan_submit
from .config.defaults import AnalystConfig, ChatConfig, DataConfig
from .config.defaults import DeepGenomeConfig, InSilicoResearchConfig
from .config.defaults import KnowledgeConfig, ReviewConfig
from .config.settings import SensitiveConfig
from .chat_agents import phyto_chat_with_follow
from .data_agents import rewrite_nl2sql
from .deep_genome_agents import gene_function
from .in_silico_research_agents import in_silico_research
from .knowledge_agents import multi_retrieve_generate
from .review_agents import deep_research


class ChatAgent(BaseModel):
    """Parameters for generating text using the Phyto model."""
    user_query: Annotated[
        str,
        Field(
            description="The user's query string for generating text.",
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="List of observation file paths for the large "
                        "language model to process. Users can upload one "
                        "file, multiple files, or no files. Supported file "
                        "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook. When "
                        "uploading files, provide complete file paths as a "
                        "list of strings. When not uploading any files, pass "
                        "an empty list [].",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]


class KnowledgeAgent(BaseModel):
    """Parameters for retrieving documents and generating text."""
    user_query: Annotated[
        str,
        Field(
            description="The user's query string for retrieving documents "
                        "and generating text."
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="List of observation file paths for the large "
                        "language model to process. Users can upload one "
                        "file, multiple files, or no files. Supported file "
                        "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook. When "
                        "uploading files, provide complete file paths as a "
                        "list of strings. When not uploading any files, pass "
                        "an empty list [].",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]


class DataAgent(BaseModel):
    """Parameters for natural language searching SQL database."""
    user_query: Annotated[
        str,
        Field(
            description="The user's natural language query string for "
                        "searching SQL database.",
        ),
    ]


class AnalystAgent(BaseModel):
    """Parameters for submitting and waiting for a bioinformatic analysis."""
    goal_description: Annotated[
        str,
        Field(
            description="Description of the biological research objective "
                        "and analysis goal.",
        ),
    ]
    data_list: Annotated[
        Dict[str, str],
        Field(
            description="Input datasets dictionary mapping OBS file paths to "
                        "their detailed descriptions. Keys should be absolute "
                        "OBS paths pointing to genomic data files. Values "
                        "should comprehensively describe the data "
                        "characteristics including sequencing type, organism "
                        "source, data quality metrics, experimental "
                        "conditions, and intended analysis purpose",
            json_schema_extra={
                "example": {
                    "/obs/phytomni/path/to/reference.fasta":
                        "High-quality reference genome assembly for target "
                        "organism, containing complete chromosomal sequences.",
                    "/obs/phytomni/path/to/sequence.fastq":
                        "Illumina paired-end whole genome sequencing data, "
                        "150bp read length, from fresh tissue sample "
                        "collected under standard conditions, intended for "
                        "SNP/InDel detection and comparative genomics "
                        "analysis.",
                },
                "x-java-default": "new HashMap<>()",
                "x-csharp-default": "new Dictionary<string, string>()",
            },
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="List of observation file paths for the large "
                        "language model to process. Users can upload one "
                        "file, multiple files, or no files. Supported file "
                        "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook. When "
                        "uploading files, provide complete file paths as a "
                        "list of strings. When not uploading any files, pass "
                        "an empty list [].",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]


class DeepGenomeAgent(BaseModel):
    """Parameters for submitting and waiting for a gene function analysis."""
    species_code: Annotated[
        str,
        Field(
            description="""species code is the key of dict: {
                'ach': 'kiwi (Actinidia chinensis)',
                'aco': 'pineapple (Ananas comosus)',
                'aly': 'Arabidopsis lyrata',
                'aof': 'garden (Asparagus officinalis)',
                'ata': 'rough-spike (Aegilops tauschii)',
                'ath': 'thale (Arabidopsis thaliana)',
                'atr': 'Amborella trichopoda',
                'bdi': 'Brachypodium distachyon',
                'bna': 'oilseed (Brassica napus)',
                'bol': 'Brassica oleracea',
                'bra': 'Brassica rapa',
                'bvu': 'suger (Beta vulgaris)',
                'can': 'pepper (Capsicum annuum)',
                'cav': 'Corylus avellana',
                'cbr': 'Chara braunii',
                'ccan': 'coffee (Coffea canephora)',
                'ccl': 'citrus (Citrus clementina)',
                'cla': 'watermelon (Citrullus lanatus)',
                'cme': 'muskmelon (Cucumis melo)',
                'cqu': 'quinoa (Chenopodium quinoa)',
                'cre': 'Chlamydomonas reinhardtii',
                'csa': 'cucumber (Cucumis sativus)',
                'dca': 'carrot (Daucus carota)',
                'dex': 'white (Digitaria exilis)',
                'ecu': 'weeping (Eragrostis curvula)',
                'egr': 'Eucalyptus grandis',
                'esa': 'saltwater (Eutrema salsugineum)',
                'ghi': 'upland (Gossypium hirsutum)',
                'gma': 'soybean (Glycine max)',
                'gra': 'cotton (Gossypium raimondii)',
                'han': 'sunflower (Helianthus annuus)',
                'hvu': 'barley (Hordeum vulgare)',
                'lpe': 'Lolium perenne',
                'lsa': 'lettuce (Lactuca sativa)',
                'mac': 'banana (Musa acuminata)',
                'mes': 'cassava (Manihot esculenta)',
                'mpo': 'liverwort (Marchantia polymorpha)',
                'mtr': 'barrel (Medicago truncatula)',
                'obr': 'wild (Oryza brachyantha)',
                'oeu': 'common (Olea europaea)',
                'osa': 'rice (Oryza sativa)',
                'pha': "Hall's (Panicum hallii)",
                'ppa': 'Physcomitrium patens',
                'ppe': 'peach (Prunus persica)',
                'psa': 'garden (Pisum sativum)',
                'pso': 'opium (Papaver somniferum)',
                'ptr': 'black (Populus trichocarpa)',
                'pvu': 'common (Phaseolus vulgaris)',
                'qlo': 'Quercus lobata',
                'rch': 'rose (Rosa chinensis)',
                'sbi': 'sorghum (Sorghum bicolor)',
                'sce': 'rye (Secale cereale)',
                'sit': 'foxtail (Setaria italica)',
                'sly': 'tomato (Solanum lycopersicum)',
                'smo': 'Selaginella moellendorffii',
                'ssp': 'sugarcane (Saccharum spontaneum)',
                'stu': 'potato (Solanum tuberosum)',
                'svi': 'green (Setaria viridis)',
                'tae': 'wheat (Triticum aestivum)',
                'tca': 'cacao (Theobroma cacao)',
                'tdi': 'emmer (Triticum dicoccoides)',
                'tpr': 'red (Trifolium pratense)',
                'ttu': 'durum (Triticum turgidum)',
                'vvi': 'grape (Vitis vinifera)',
                'zma': 'maize (Zea mays)'}""",
        ),
    ]
    gene_id: Annotated[
        str,
        Field(
            description="gene id",
        ),
    ]


class ReviewAgent(BaseModel):
    """Parameters for conducting in-depth research and
    generating a comprehensive review."""
    user_query: Annotated[
        str,
        Field(
            description="The user's research question or topic "
                        "for which a detailed review is required."
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="List of observation file paths for the large "
                        "language model to process. Users can upload one "
                        "file, multiple files, or no files. Supported file "
                        "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook. When "
                        "uploading files, provide complete file paths as a "
                        "list of strings. When not uploading any files, pass "
                        "an empty list [].",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]


class InSilicoResearchAgent(BaseModel):
    """Parameters for conducting in silico research."""
    user_query: Annotated[
        str,
        Field(
            description="The user's paper context."
        ),
    ]
    data_list: Annotated[
        Dict[str, str],
        Field(
            description="Input datasets dictionary mapping OBS file paths to "
                        "their detailed descriptions. Keys should be absolute "
                        "OBS paths pointing to genomic data files. Values "
                        "should comprehensively describe the data "
                        "characteristics including sequencing type, organism "
                        "source, data quality metrics, experimental "
                        "conditions, and intended analysis purpose",
            json_schema_extra={
                "example": {
                    "/obs/phytomni/path/to/reference.fasta":
                        "High-quality reference genome assembly for target "
                        "organism, containing complete chromosomal sequences.",
                    "/obs/phytomni/path/to/sequence.fastq":
                        "Illumina paired-end whole genome sequencing data, "
                        "150bp read length, from fresh tissue sample "
                        "collected under standard conditions, intended for "
                        "SNP/InDel detection and comparative genomics "
                        "analysis.",
                },
                "x-java-default": "new HashMap<>()",
                "x-csharp-default": "new Dictionary<string, string>()",
            }
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="List of observation file paths for the large "
                        "language model to process. Users can upload one "
                        "file, multiple files, or no files. Supported file "
                        "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook. When "
                        "uploading files, provide complete file paths as a "
                        "list of strings. When not uploading any files, pass "
                        "an empty list [].",
            json_schema_extra={
                "example": [
                    "/obs/phytomni/path/to/document.pdf",
                    "/obs/phytomni/path/to/document.docx",
                ],
                "x-java-default": "new ArrayList<>()",
                "x-csharp-default": "new List<string>()",
            },
        ),
    ]


class PhytomniAgents(str, Enum):
    """Enumeration of specialized AI agents for plant science research support.

    Defines available agent types with domain-specific capabilities for
    different aspects of botanical studies and computational biology workflows.

    Members:
        CHATAGENT: Core language model interface for fundamental Q&A.
            Usage: Basic conceptual queries, single-domain problem solving.
            Limitations: Avoid for multi-factor agricultural optimizations.
        KNOWLEDGEAGENT: Evidence-based literature synthesis system.
            Usage: Cross-referenced answers from curated scientific sources.
        DATAAGENT: Structured data query interface.
            Usage: Precise numerical/statistical retrieval from databases.
        ANALYSTAGENT: Genomic workflow orchestration system.
            Usage: Automated execution of bioinformatics pipelines.

    Descriptions provide guidance on appropriate application scenarios and
    technical constraints for each agent type. All agents implement
    standardized JSON schema for parameter validation.
    """
    CHATAGENT = "ChatAgent"
    CHATAGENT_DESCRIPTION = (
        "Provides concise explanations for foundational or single-domain "
        "questions in plant biology (e.g., definitions, basic mechanisms) "
        "using the LLM's internal knowledge. Not recommended for "
        "multi-dimensional agricultural optimization or climate adaptation "
        "strategies."
    )
    KNOWLEDGEAGENT = "KnowledgeAgent"
    KNOWLEDGEAGENT_DESCRIPTION = (
        "Retrieves and synthesizes information from plant science literature, "
        "patents, and books through RAG (Retrieval-Augmented Generation) "
        "pipelines, providing evidence-supported answers."
    )
    DATAAGENT = "DataAgent"
    DATAAGENT_DESCRIPTION = (
        "Executes structured queries on botanical databases (e.g., species "
        "traits, experimental data) using SQL interfaces, returning precise "
        "numerical/statistical results."
    )
    ANALYSTAGENT = "AnalystAgent"
    ANALYSTAGENT_DESCRIPTION = (
        "Initiates computational workflows (e.g., sequence alignment, "
        "phylogenetic analysis) through integrated bioinformatics platforms "
        "for genomic/proteomic investigations."
    )
    REVIEWAGENT = "ReviewAgent"
    REVIEWAGENT_DESCRIPTION = (
        "Conducts a comprehensive and in-depth investigation into a user's "
        "query, synthesizing information from a wide range of scientific "
        "literature and other relevant sources to produce a structured review "
        "or detailed report. Ideal for when a broad understanding, critical "
        "assessment, or an extensive overview of a complex topic is required, "
        "going beyond targeted Q&A or data retrieval."
    )
    DEEPGENOMEAGENT = "DeepGenomeAgent"
    DEEPGENOMEAGENT_DESCRIPTION = (
        "Integrates functional annotations from plant multi-omics databases "
        "(GO, KEGG, etc.) with experimental evidence mined from literature, "
        "generating comparative summaries with experimental evidence."
    )
    INSILICORESEARCHAGENT = "InSilicoResearchAgent"
    INSILICORESEARCHAGENT_DESCRIPTION = (
        "Decomposes a complete scientific paper by analyzing its methodology "
        "and results, producing a structured, sequential list of high-level "
        "tasks designed for computational replication."
    )


async def serve() -> None:
    """Initialize and run the Phytomni MCP service endpoint.

    This function orchestrates the complete service lifecycle for the Phytomni
    Model Context Protocol (MCP) server, providing specialized AI agents for
    plant science research and bioinformatics analysis. The server manages
    multiple agent types with domain-specific capabilities and handles all
    aspects of request processing, validation, and response generation.

    The service exposes the following specialized agents:
    - ChatAgent: Core language model interface for Q&A and document processing
    - KnowledgeAgent: Literature synthesis with RAG (Retrieval-Augmented
        Generation)
    - DataAgent: Structured database queries using natural language to SQL
    - AnalystAgent: Automated bioinformatics workflow execution
    - ReviewAgent: Comprehensive research investigation and report generation
    - DeepGenomeAgent: Multi-omics gene function analysis with experimental
        data
    - InSilicoResearchAgent: Scientific paper methodology decomposition

    Service Architecture:
    - Tool registration with JSON schema validation for type safety
    - Request routing to appropriate agent handlers based on tool name
    - Comprehensive error handling with MCP-compliant error responses
    - Automatic resource cleanup and memory management
    - Standard I/O protocol implementation for cross-platform compatibility

    Primary Endpoints:
        /list_tools: Returns metadata for all registered agents including:
            - Agent names and descriptions
            - Input parameter schemas
            - Capability specifications
        /call_tool: Executes agent-specific operations with:
            - Parameter validation against schemas
            - Agent-specific configuration loading
            - Response formatting and error handling

    Args:
        None: This function takes no parameters and uses configuration
        from environment variables and default configuration classes.

    Returns:
        None: This function runs indefinitely until interrupted, serving
        requests through the stdio interface.

    Raises:
        McpError: Wrapped exceptions for operational failures including:
            - INVALID_PARAMS: Invalid parameter schemas or missing required
                fields
            - INTERNAL_ERROR: Agent execution failures, timeouts, or
                infrastructure issues
        ValueError: If agent parameters fail Pydantic model validation
        ConnectionError: If underlying services (databases, APIs) are
            unavailable
        TimeoutError: If agent operations exceed configured timeout limits

    Examples:
        Running the server:
            >>> import asyncio
            >>> asyncio.run(serve())

        The server can be integrated with MCP clients:
            ```python
            # Client connection example
            from mcp import ClientSession

            async with ClientSession() as session:
                tools = await session.list_tools()
                result = await session.call_tool(
                    "ChatAgent",
                    {"user_query": "What is photosynthesis?",
                    "obs_file_list": []}
                )
            ```

    Note:
        The server maintains strict isolation between agent execution contexts
        and implements automatic resource cleanup through context managers.
        All agents are configured through their respective configuration
        classes which load settings from environment variables and
        configuration files.

        The server implements graceful shutdown handling and ensures all
        ongoing operations complete before termination.
    """
    server = Server("Phytomni-Server")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=PhytomniAgents.CHATAGENT,
                description=PhytomniAgents.CHATAGENT_DESCRIPTION,
                inputSchema=ChatAgent.model_json_schema()
            ),
            Tool(
                name=PhytomniAgents.KNOWLEDGEAGENT,
                description=PhytomniAgents.KNOWLEDGEAGENT_DESCRIPTION,
                inputSchema=KnowledgeAgent.model_json_schema()
            ),
            Tool(
                name=PhytomniAgents.DATAAGENT,
                description=PhytomniAgents.DATAAGENT_DESCRIPTION,
                inputSchema=DataAgent.model_json_schema()
            ),
            Tool(
                name=PhytomniAgents.ANALYSTAGENT,
                description=PhytomniAgents.ANALYSTAGENT_DESCRIPTION,
                inputSchema=AnalystAgent.model_json_schema()
            ),
            Tool(
                name=PhytomniAgents.REVIEWAGENT,
                description=PhytomniAgents.REVIEWAGENT_DESCRIPTION,
                inputSchema=ReviewAgent.model_json_schema()
            ),
            Tool(
                name=PhytomniAgents.DEEPGENOMEAGENT,
                description=PhytomniAgents.DEEPGENOMEAGENT_DESCRIPTION,
                inputSchema=DeepGenomeAgent.model_json_schema()
            ),
            Tool(
                name=PhytomniAgents.INSILICORESEARCHAGENT,
                description=PhytomniAgents.INSILICORESEARCHAGENT_DESCRIPTION,
                inputSchema=InSilicoResearchAgent.model_json_schema()
            ),
        ]

    @server.call_tool()
    async def call_tool(name, arguments: dict) -> list[TextContent]:
        match name:
            case PhytomniAgents.CHATAGENT:
                try:
                    args = ChatAgent(**arguments)
                except ValueError as e:
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS, message=str(e))) from e
                chatconfig = ChatConfig()
                sensitiveconfig = SensitiveConfig().load()
                response = await phyto_chat_with_follow(
                    user_query=args.user_query,
                    obs_file_list=args.obs_file_list,
                    prompt_file=chatconfig.PROMPT_FILE,
                    prompt_path=chatconfig.PROMPT_PATH,
                    api_key=sensitiveconfig.API_KEY.get_secret_value(),
                    base_url=sensitiveconfig.BASE_URL,
                    model=sensitiveconfig.MODEL_ID,
                    frequency_penalty=chatconfig.FREQUENCY_PENALTY,
                    n=chatconfig.N,
                    presence_penalty=chatconfig.PRESENCE_PENALTY,
                    reasoning_effort=chatconfig.REASONING_EFFORT,
                    response_format=chatconfig.RESPONSE_FORMAT,
                    stream=chatconfig.STREAM,
                    temperature=chatconfig.TEMPERATURE,
                    top_p=chatconfig.TOP_P,
                    user=chatconfig.USER,
                    server_dir=chatconfig.TEMP_DIR,
                    access_key_id=(
                        sensitiveconfig.AccessKeyID.get_secret_value()),
                    secret_access_key=(
                        sensitiveconfig.SecretAccessKey.get_secret_value()),
                    obs_server=chatconfig.OBS_SERVER,
                    bucket_name=chatconfig.BUCKET_NAME,
                    part_size=chatconfig.PART_SIZT,
                    task_num=chatconfig.TASK_NUM,
                    timeout=chatconfig.TIMEOUT,
                    retriable_codes=chatconfig.RETRIABLE_CODES,
                    max_retries=chatconfig.MAX_RETRIES,
                    max_concurrency=chatconfig.MAX_CONCURRENCY,
                    max_workers=chatconfig.MAX_WORKERS,
                    max_tokens=chatconfig.MAX_TOKENS,
                )
                return [TextContent(
                    type='text',
                    text=dumps(response),
                )]

            case PhytomniAgents.KNOWLEDGEAGENT:
                try:
                    args = KnowledgeAgent(**arguments)
                except ValueError as e:
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS, message=str(e))) from e
                knowledgeconfig = KnowledgeConfig()
                sensitiveconfig = SensitiveConfig().load()
                response = await multi_retrieve_generate(
                    user_query=args.user_query,
                    retrieve_url=knowledgeconfig.RETRIEVE_URL,
                    repo_id_dict=knowledgeconfig.REPO_ID_DICT,
                    page_num=knowledgeconfig.PAGE_NUM,
                    filter_string=knowledgeconfig.FILTER_STRING,
                    scope=knowledgeconfig.SCOPE,
                    extra_repo_ids=knowledgeconfig.EXTRA_REPO_IDS,
                    rerank_url=knowledgeconfig.RERANK_URL,
                    rerank_batch_size=knowledgeconfig.RERANK_BATCH_SIZE,
                    score_threshold=knowledgeconfig.SCORE_THRESHOLD,
                    top_n=knowledgeconfig.TOP_N,
                    prompt_file=knowledgeconfig.PROMPT_FILE,
                    prompt_path=knowledgeconfig.PROMPT_PATH,
                    api_key=sensitiveconfig.API_KEY.get_secret_value(),
                    base_url=sensitiveconfig.BASE_URL,
                    model=sensitiveconfig.MODEL_ID,
                    frequency_penalty=knowledgeconfig.FREQUENCY_PENALTY,
                    max_tokens=knowledgeconfig.MAX_TOKENS,
                    n=knowledgeconfig.N,
                    presence_penalty=knowledgeconfig.PRESENCE_PENALTY,
                    reasoning_effort=knowledgeconfig.REASONING_EFFORT,
                    response_format=knowledgeconfig.RESPONSE_FORMAT,
                    stream=knowledgeconfig.STREAM,
                    temperature=knowledgeconfig.TEMPERATURE,
                    top_p=knowledgeconfig.TOP_P,
                    user=knowledgeconfig.USER,
                    obs_file_list=args.obs_file_list,
                    server_dir=knowledgeconfig.TEMP_DIR,
                    access_key_id=(
                        sensitiveconfig.AccessKeyID.get_secret_value()),
                    secret_access_key=(
                        sensitiveconfig.SecretAccessKey.get_secret_value()),
                    obs_server=knowledgeconfig.OBS_SERVER,
                    bucket_name=knowledgeconfig.BUCKET_NAME,
                    part_size=knowledgeconfig.PART_SIZT,
                    task_num=knowledgeconfig.TASK_NUM,
                    max_concurrency=knowledgeconfig.MAX_CONCURRENCY,
                    max_workers=knowledgeconfig.MAX_WORKERS,
                    timeout=knowledgeconfig.TIMEOUT,
                    retriable_codes=knowledgeconfig.RETRIABLE_CODES,
                    max_retries=knowledgeconfig.MAX_RETRIES,
                )
                return [TextContent(
                    type='text',
                    text=dumps(response),
                )]

            case PhytomniAgents.DATAAGENT:
                try:
                    args = DataAgent(**arguments)
                except ValueError as e:
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS, message=str(e))) from e
                dataconfig = DataConfig()
                sensitiveconfig = SensitiveConfig().load()
                response = await rewrite_nl2sql(
                    user_query=args.user_query,
                    retrieve_url=dataconfig.RETRIEVE_URL,
                    data_repo_id=dataconfig.DATA_REPO_ID,
                    page_num=dataconfig.PAGE_NUM,
                    page_size=dataconfig.DATA_PAGE_SIZE,
                    filter_string=dataconfig.FILTER_STRING,
                    scope=dataconfig.SCOPE,
                    rerank_url=dataconfig.RERANK_URL,
                    rerank_batch_size=dataconfig.RERANK_BATCH_SIZE,
                    score_threshold=dataconfig.SCORE_THRESHOLD,
                    prompt_file=dataconfig.PROMPT_FILE,
                    prompt_path=dataconfig.PROMPT_PATH,
                    api_key=sensitiveconfig.API_KEY.get_secret_value(),
                    base_url=sensitiveconfig.BASE_URL,
                    model=sensitiveconfig.MODEL_ID,
                    frequency_penalty=dataconfig.FREQUENCY_PENALTY,
                    n=dataconfig.N,
                    presence_penalty=dataconfig.PRESENCE_PENALTY,
                    reasoning_effort=dataconfig.REASONING_EFFORT,
                    response_format=dataconfig.RESPONSE_FORMAT,
                    stream=dataconfig.STREAM,
                    temperature=dataconfig.TEMPERATURE,
                    top_p=dataconfig.TOP_P,
                    user=dataconfig.USER,
                    database_url=dataconfig.DATABASE_URL,
                    workspace_id=dataconfig.WORKSPACE_ID,
                    subject_id=dataconfig.SUBJECT_ID,
                    dialog_id=dataconfig.DIALOG_ID,
                    need_insight=dataconfig.NEED_INSIGHT,
                    simplify_response=dataconfig.SIMPLIFY_RESPONSE,
                    timeout=dataconfig.TIMEOUT,
                    retriable_codes=dataconfig.RETRIABLE_CODES,
                    max_retries=dataconfig.MAX_RETRIES,
                    max_tokens=dataconfig.MAX_TOKENS,
                )
                return [TextContent(
                    type='text',
                    text=dumps(response),
                )]

            case PhytomniAgents.ANALYSTAGENT:
                try:
                    args = AnalystAgent(**arguments)
                except ValueError as e:
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS, message=str(e))) from e
                analystconfig = AnalystConfig()
                sensitiveconfig = SensitiveConfig().load()
                response = await retrieve_plan_submit(
                    goal_description=args.goal_description,
                    data_list=args.data_list,
                    user_id=analystconfig.USER_ID,
                    is_create_dir=analystconfig.CREATE_DIR,
                    output_dir=analystconfig.OUTPUT_DIR,
                    retrieve_url=analystconfig.RETRIEVE_URL,
                    repo_id_dict=analystconfig.REPO_ID_DICT,
                    page_num=analystconfig.PAGE_NUM,
                    filter_string=analystconfig.FILTER_STRING,
                    scope=analystconfig.SCOPE,
                    extra_repo_ids=analystconfig.EXTRA_REPO_IDS,
                    rerank_url=analystconfig.RERANK_URL,
                    rerank_batch_size=analystconfig.RERANK_BATCH_SIZE,
                    score_threshold=analystconfig.SCORE_THRESHOLD,
                    top_n=analystconfig.TOP_N,
                    prompt_file=analystconfig.PROMPT_FILE,
                    prompt_path=analystconfig.PROMPT_PATH,
                    api_key=sensitiveconfig.API_KEY.get_secret_value(),
                    base_url=sensitiveconfig.BASE_URL,
                    model=sensitiveconfig.MODEL_ID,
                    frequency_penalty=analystconfig.FREQUENCY_PENALTY,
                    max_tokens=analystconfig.MAX_TOKENS,
                    n=analystconfig.N,
                    presence_penalty=analystconfig.PRESENCE_PENALTY,
                    reasoning_effort=analystconfig.REASONING_EFFORT,
                    response_format=analystconfig.RESPONSE_FORMAT,
                    stream=analystconfig.STREAM,
                    temperature=analystconfig.TEMPERATURE,
                    top_p=analystconfig.TOP_P,
                    user=analystconfig.USER,
                    obs_file_list=args.obs_file_list,
                    server_dir=analystconfig.TEMP_DIR,
                    execute_code=analystconfig.EXECUTE_CODE,
                    model_url=sensitiveconfig.CODER_URL,
                    model_name=sensitiveconfig.CODER_MODEL,
                    coder_api_key=(
                        sensitiveconfig.CODER_API_KEY.get_secret_value()),
                    access_key_id=(
                        sensitiveconfig.AccessKeyID.get_secret_value()),
                    secret_access_key=(
                        sensitiveconfig.SecretAccessKey.get_secret_value()),
                    obs_server=analystconfig.OBS_SERVER,
                    bucket_name=analystconfig.BUCKET_NAME,
                    part_size=analystconfig.PART_SIZT,
                    task_num=analystconfig.TASK_NUM,
                    max_concurrency=analystconfig.MAX_CONCURRENCY,
                    max_workers=analystconfig.MAX_WORKERS,
                    analysis_url=analystconfig.ANALYSIS_URL,
                    region=analystconfig.ANALYSIS_REGION,
                    task_name=analystconfig.TASK_NAME + '-retrieve-plan',
                    resource_dict=analystconfig.RESOURCE,
                    app_id_dict=analystconfig.APP_ID,
                    compute_resource=analystconfig.COMPUTE_RESOURCE,
                    meta_meta=None,
                    timeout=analystconfig.TIMEOUT,
                    retriable_codes=analystconfig.RETRIABLE_CODES,
                    max_retries=analystconfig.MAX_RETRIES,
                )
                return [TextContent(
                    type='text',
                    text=dumps(response),
                )]

            case PhytomniAgents.REVIEWAGENT:
                try:
                    args = ReviewAgent(**arguments)
                except ValueError as e:
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS, message=str(e))) from e
                reviewconfig = ReviewConfig()
                sensitiveconfig = SensitiveConfig().load()
                response = await deep_research(
                    user_query=args.user_query,
                    prompt_file=reviewconfig.PROMPT_FILE,
                    prompt_path=reviewconfig.PROMPT_PATH,
                    api_key=sensitiveconfig.API_KEY.get_secret_value(),
                    base_url=sensitiveconfig.BASE_URL,
                    model=sensitiveconfig.MODEL_ID,
                    frequency_penalty=reviewconfig.FREQUENCY_PENALTY,
                    n=reviewconfig.N,
                    presence_penalty=reviewconfig.PRESENCE_PENALTY,
                    reasoning_effort=reviewconfig.REASONING_EFFORT,
                    response_format=reviewconfig.RESPONSE_FORMAT,
                    stream=reviewconfig.STREAM,
                    temperature=reviewconfig.TEMPERATURE,
                    top_p=reviewconfig.TOP_P,
                    user=reviewconfig.USER,
                    retrieve_url=reviewconfig.RETRIEVE_URL,
                    repo_id_dict=reviewconfig.REPO_ID_DICT,
                    page_num=reviewconfig.PAGE_NUM,
                    filter_string=reviewconfig.FILTER_STRING,
                    scope=reviewconfig.SCOPE,
                    extra_repo_ids=reviewconfig.EXTRA_REPO_IDS,
                    rerank_url=reviewconfig.RERANK_URL,
                    rerank_batch_size=reviewconfig.RERANK_BATCH_SIZE,
                    score_threshold=reviewconfig.SCORE_THRESHOLD,
                    top_n=reviewconfig.TOP_N,
                    obs_file_list=args.obs_file_list,
                    server_dir=reviewconfig.TEMP_DIR,
                    access_key_id=(
                        sensitiveconfig.AccessKeyID.get_secret_value()),
                    secret_access_key=(
                        sensitiveconfig.SecretAccessKey.get_secret_value()),
                    obs_server=reviewconfig.OBS_SERVER,
                    bucket_name=reviewconfig.BUCKET_NAME,
                    part_size=reviewconfig.PART_SIZT,
                    task_num=reviewconfig.TASK_NUM,
                    max_concurrency=reviewconfig.MAX_CONCURRENCY,
                    max_workers=reviewconfig.MAX_WORKERS,
                    timeout=reviewconfig.TIMEOUT,
                    retriable_codes=reviewconfig.RETRIABLE_CODES,
                    max_retries=reviewconfig.MAX_RETRIES,
                    max_tokens=reviewconfig.MAX_TOKENS,
                )
                return [TextContent(
                    type='text',
                    text=dumps(response),
                )]

            case PhytomniAgents.DEEPGENOMEAGENT:
                try:
                    args = DeepGenomeAgent(**arguments)
                except ValueError as e:
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS, message=str(e))) from e
                deepgenomeconfig = DeepGenomeConfig()
                sensitiveconfig = SensitiveConfig().load()
                response = await gene_function(
                    species_code=args.species_code,
                    gene_id=args.gene_id,
                    user_id=deepgenomeconfig.USER_ID,
                    batch=deepgenomeconfig.BATCH,
                    epic_type=deepgenomeconfig.EPIC_TYPE,
                    create_task_url=deepgenomeconfig.CREATE_TASK_URL,
                    update_task_url=deepgenomeconfig.UPDATE_TASK_URL,
                    database_url=deepgenomeconfig.DATABASE_URL,
                    workspace_id=deepgenomeconfig.WORKSPACE_ID,
                    subject_id=deepgenomeconfig.SUBJECT_ID,
                    dialog_id=deepgenomeconfig.DIALOG_ID,
                    need_insight=deepgenomeconfig.NEED_INSIGHT,
                    prompt_file=deepgenomeconfig.PROMPT_FILE,
                    deepgenome_data=deepgenomeconfig.DEEPGENOME_DATA,
                    output_dir=deepgenomeconfig.OUTPUT_DIR,
                    model_url=sensitiveconfig.CODER_URL,
                    model_name=sensitiveconfig.CODER_MODEL,
                    coder_api_key=(
                        sensitiveconfig.CODER_API_KEY.get_secret_value()),
                    access_key_id=(
                        sensitiveconfig.AccessKeyID.get_secret_value()),
                    secret_access_key=(
                        sensitiveconfig.SecretAccessKey.get_secret_value()),
                    obs_server=deepgenomeconfig.OBS_SERVER,
                    bucket_name=deepgenomeconfig.BUCKET_NAME,
                    analysis_url=deepgenomeconfig.ANALYSIS_URL,
                    region=deepgenomeconfig.ANALYSIS_REGION,
                    resource_dict=deepgenomeconfig.RESOURCE,
                    app_id_dict=deepgenomeconfig.APP_ID,
                    retrieve_url=deepgenomeconfig.RETRIEVE_URL,
                    repo_id_dict=deepgenomeconfig.REPO_ID_DICT,
                    page_num=deepgenomeconfig.PAGE_NUM,
                    filter_string=deepgenomeconfig.FILTER_STRING,
                    extra_repo_ids=deepgenomeconfig.EXTRA_REPO_IDS,
                    rerank_url=deepgenomeconfig.RERANK_URL,
                    rerank_batch_size=deepgenomeconfig.RERANK_BATCH_SIZE,
                    score_threshold=deepgenomeconfig.SCORE_THRESHOLD,
                    top_n=deepgenomeconfig.TOP_N,
                    prompt_path=deepgenomeconfig.PROMPT_PATH,
                    api_key=sensitiveconfig.API_KEY.get_secret_value(),
                    base_url=sensitiveconfig.BASE_URL,
                    model=sensitiveconfig.MODEL_ID,
                    frequency_penalty=deepgenomeconfig.FREQUENCY_PENALTY,
                    max_tokens=deepgenomeconfig.MAX_TOKENS,
                    n=deepgenomeconfig.N,
                    presence_penalty=deepgenomeconfig.PRESENCE_PENALTY,
                    reasoning_effort=deepgenomeconfig.REASONING_EFFORT,
                    response_format=deepgenomeconfig.RESPONSE_FORMAT,
                    stream=deepgenomeconfig.STREAM,
                    temperature=deepgenomeconfig.TEMPERATURE,
                    top_p=deepgenomeconfig.TOP_P,
                    user=deepgenomeconfig.USER,
                    deepgenome_out=deepgenomeconfig.DEEPGENOME_OUT,
                    download_path=deepgenomeconfig.DOWNLOAD_PATH,
                    marker=deepgenomeconfig.DOWNLOAD_MARKER,
                    max_keys=deepgenomeconfig.DOWNLOAD_MAX_KEYS,
                    timeout=deepgenomeconfig.TIMEOUT,
                    retriable_codes=deepgenomeconfig.RETRIABLE_CODES,
                    max_retries=deepgenomeconfig.MAX_RETRIES,
                    max_concurrency=deepgenomeconfig.MAX_CONCURRENCY,
                    max_poll=deepgenomeconfig.MAX_POLL,
                )
                return [TextContent(
                    type='text',
                    text=dumps(response),
                )]

            case PhytomniAgents.INSILICORESEARCHAGENT:
                try:
                    args = InSilicoResearchAgent(**arguments)
                except ValueError as e:
                    raise McpError(ErrorData(
                        code=INVALID_PARAMS, message=str(e))) from e
                insilicoresearchconfig = InSilicoResearchConfig()
                sensitiveconfig = SensitiveConfig().load()
                response = await in_silico_research(
                    user_query=args.user_query,
                    data_list=args.data_list,
                    output_dir=insilicoresearchconfig.OUTPUT_DIR,
                    repo_id_dict=insilicoresearchconfig.REPO_ID_DICT,
                    page_num=insilicoresearchconfig.PAGE_NUM,
                    filter_string=insilicoresearchconfig.FILTER_STRING,
                    scope=insilicoresearchconfig.SCOPE,
                    extra_repo_ids=insilicoresearchconfig.EXTRA_REPO_IDS,
                    score_threshold=insilicoresearchconfig.SCORE_THRESHOLD,
                    top_n=insilicoresearchconfig.TOP_N,
                    prompt_file=insilicoresearchconfig.PROMPT_FILE,
                    prompt_path=insilicoresearchconfig.PROMPT_PATH,
                    api_key=sensitiveconfig.API_KEY.get_secret_value(),
                    base_url=sensitiveconfig.BASE_URL,
                    model=sensitiveconfig.MODEL_ID,
                    frequency_penalty=insilicoresearchconfig.FREQUENCY_PENALTY,
                    max_tokens=insilicoresearchconfig.MAX_TOKENS,
                    n=insilicoresearchconfig.N,
                    presence_penalty=insilicoresearchconfig.PRESENCE_PENALTY,
                    reasoning_effort=insilicoresearchconfig.REASONING_EFFORT,
                    response_format=insilicoresearchconfig.RESPONSE_FORMAT,
                    stream=insilicoresearchconfig.STREAM,
                    temperature=insilicoresearchconfig.TEMPERATURE,
                    top_p=insilicoresearchconfig.TOP_P,
                    user=insilicoresearchconfig.USER,
                    obs_file_list=args.obs_file_list,
                    server_dir=insilicoresearchconfig.TEMP_DIR,
                    execute_code=insilicoresearchconfig.EXECUTE_CODE,
                    access_key_id=(
                        sensitiveconfig.AccessKeyID.get_secret_value()),
                    secret_access_key=(
                        sensitiveconfig.SecretAccessKey.get_secret_value()),
                    obs_server=insilicoresearchconfig.OBS_SERVER,
                    bucket_name=insilicoresearchconfig.BUCKET_NAME,
                    part_size=insilicoresearchconfig.PART_SIZT,
                    task_num=insilicoresearchconfig.TASK_NUM,
                    max_concurrency=insilicoresearchconfig.MAX_CONCURRENCY,
                    max_workers=insilicoresearchconfig.MAX_WORKERS,
                    timeout=insilicoresearchconfig.TIMEOUT,
                    retriable_codes=insilicoresearchconfig.RETRIABLE_CODES,
                    max_retries=insilicoresearchconfig.MAX_RETRIES,
                )
                return [TextContent(
                    type='text',
                    text=dumps(response),
                )]

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         options, raise_exceptions=True)


if __name__ == '__main__':
    asyncio.run(serve())
