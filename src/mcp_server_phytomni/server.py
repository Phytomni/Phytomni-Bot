# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
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
    2024-2026. All rights reserved.
"""

import asyncio
from enum import Enum
from json import dumps
from typing import Annotated, Any, Awaitable, Callable, Dict, List

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, TextContent, Tool, INVALID_PARAMS
from pydantic import BaseModel, Field

from .tool_handlers import handle_analyst_agent, handle_brief_gene_agent
from .tool_handlers import handle_chat_agent, handle_data_agent
from .tool_handlers import handle_deep_genome_agent
from .tool_handlers import handle_digital_design_agent
from .tool_handlers import handle_gene_network_agent
from .tool_handlers import handle_in_silico_research_agent
from .tool_handlers import handle_knowledge_agent, handle_review_agent


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
                    "/obs/phytomni/path/to/reference.fasta": "Reference "
                    "genome assembly for target organism.",
                    "/obs/phytomni/path/to/sequence.fastq": "Illumina WGS "
                    "data, 150bp read, for SNP/InDel detection.",
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


class BriefGeneAgent(BaseModel):
    """Parameters for generating a brief gene function report."""

    user_query: Annotated[
        str,
        Field(
            description="A plant gene ID, transcript ID, or gene symbol to "
            "summarize using BI database annotations and literature evidence.",
        ),
    ]


class InSilicoResearchAgent(BaseModel):
    """Parameters for conducting in silico research."""

    user_query: Annotated[
        str,
        Field(description="The user's paper context."),
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
                    "/obs/phytomni/path/to/reference.fasta": "Reference "
                    "genome assembly for target organism.",
                    "/obs/phytomni/path/to/sequence.fastq": "Illumina WGS "
                    "data, 150bp read, for SNP/InDel detection.",
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


class DigitalDesignAgent(BaseModel):
    """Parameters for protein and promoter design analysis."""

    species: Annotated[
        str,
        Field(
            description="The species name in Latin lowercase format with "
            "spaces (e.g., 'arabidopsis thaliana', "
            "'oryza sativa', 'zea mays'). Examples: "
            "'arabidopsis thaliana' (thale cress), "
            "'oryza sativa' (rice), 'zea mays' (maize), "
            "'glycine max' (soybean), "
            "'triticum aestivum' (wheat), "
            "'hordeum vulgare' (barley), "
            "'solanum lycopersicum' (tomato), "
            "'solanum tuberosum' (potato), "
            "'brassica napus' (oilseed), "
            "'gossypium hirsutum' (cotton), "
            "'sorghum bicolor' (sorghum).",
        ),
    ]
    gene_id: Annotated[
        str,
        Field(
            description="The specific gene identifier to analyze for design.",
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


class GeneNetworkAgent(BaseModel):
    """Parameters for gene network analysis."""

    species: Annotated[
        str,
        Field(
            description="The species name in Latin lowercase format with "
            "spaces (e.g., 'arabidopsis thaliana', "
            "'oryza sativa', 'zea mays'). Examples: "
            "'arabidopsis thaliana' (thale cress), "
            "'oryza sativa' (rice), 'zea mays' (maize), "
            "'glycine max' (soybean), "
            "'triticum aestivum' (wheat), "
            "'hordeum vulgare' (barley), "
            "'solanum lycopersicum' (tomato), "
            "'solanum tuberosum' (potato), "
            "'brassica napus' (oilseed), "
            "'gossypium hirsutum' (cotton), "
            "'sorghum bicolor' (sorghum).",
        ),
    ]
    to_id: Annotated[
        str,
        Field(
            description="The Trait Ontology identifier for network analysis "
            "(e.g., 'TO:0000207' for plant height trait). Trait "
            "Ontologies (TO) are standardized controlled "
            "vocabularies that describe plant phenotypic traits "
            "and characteristics.",
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
    BRIEFGENEAGENT = "BriefGeneAgent"
    BRIEFGENEAGENT_DESCRIPTION = (
        "Generates a concise, evidence-supported gene function report for a "
        "plant gene ID or alias by combining BI database annotations with "
        "retrieved literature context."
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
    DIGITALDESIGNAGENT = "DigitalDesignAgent"
    DIGITALDESIGNAGENT_DESCRIPTION = (
        "Performs comprehensive protein and promoter design analysis for "
        "specific genes, including protein structure prediction, property "
        "analysis, design optimization, and promoter modification prediction. "
        "This agent automatically runs both protein design and promoter "
        "design analyses and returns combined results."
    )
    GENENETWORKAGENT = "GeneNetworkAgent"
    GENENETWORKAGENT_DESCRIPTION = (
        "Analyzes gene networks including interaction prediction, "
        "co-expression analysis, and regulatory network characterization. "
        "Identifies functional modules and constructs comprehensive gene "
        "relationship networks for plant genomics research."
    )


ToolHandler = Callable[[Any], Awaitable[Any]]

TOOL_ARGUMENT_MODELS: Dict[str, type[BaseModel]] = {
    PhytomniAgents.CHATAGENT.value: ChatAgent,
    PhytomniAgents.KNOWLEDGEAGENT.value: KnowledgeAgent,
    PhytomniAgents.DATAAGENT.value: DataAgent,
    PhytomniAgents.ANALYSTAGENT.value: AnalystAgent,
    PhytomniAgents.REVIEWAGENT.value: ReviewAgent,
    PhytomniAgents.BRIEFGENEAGENT.value: BriefGeneAgent,
    PhytomniAgents.DEEPGENOMEAGENT.value: DeepGenomeAgent,
    PhytomniAgents.INSILICORESEARCHAGENT.value: InSilicoResearchAgent,
    PhytomniAgents.DIGITALDESIGNAGENT.value: DigitalDesignAgent,
    PhytomniAgents.GENENETWORKAGENT.value: GeneNetworkAgent,
}

TOOL_HANDLERS: Dict[str, ToolHandler] = {
    PhytomniAgents.CHATAGENT.value: handle_chat_agent,
    PhytomniAgents.KNOWLEDGEAGENT.value: handle_knowledge_agent,
    PhytomniAgents.DATAAGENT.value: handle_data_agent,
    PhytomniAgents.ANALYSTAGENT.value: handle_analyst_agent,
    PhytomniAgents.REVIEWAGENT.value: handle_review_agent,
    PhytomniAgents.BRIEFGENEAGENT.value: handle_brief_gene_agent,
    PhytomniAgents.DEEPGENOMEAGENT.value: handle_deep_genome_agent,
    PhytomniAgents.INSILICORESEARCHAGENT.value: (
        handle_in_silico_research_agent
    ),
    PhytomniAgents.DIGITALDESIGNAGENT.value: handle_digital_design_agent,
    PhytomniAgents.GENENETWORKAGENT.value: handle_gene_network_agent,
}


def _tool_name(name: Any) -> str:
    """Return the string tool name from a raw MCP name value."""
    if isinstance(name, PhytomniAgents):
        return name.value
    return str(name)


def _invalid_params(message: str) -> McpError:
    """Build an MCP invalid-params error."""
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def _text_response(response: Any) -> list[TextContent]:
    """Serialize a handler response into MCP text content."""
    return [TextContent(type="text", text=dumps(response))]


async def dispatch_tool(
    name: Any, arguments: Dict[str, Any]
) -> list[TextContent]:
    """Validate arguments, call a tool handler, and serialize the response."""
    tool_name = _tool_name(name)
    model = TOOL_ARGUMENT_MODELS.get(tool_name)
    handler = TOOL_HANDLERS.get(tool_name)
    if model is None or handler is None:
        raise _invalid_params(f"Unknown tool: {tool_name}")

    try:
        args = model(**arguments)
    except ValueError as exc:
        raise _invalid_params(str(exc)) from exc

    response = await handler(args)
    return _text_response(response)


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
    - DigitalDesignAgent: Protein and promoter design analysis with
        computational modeling
    - GeneNetworkAgent: Gene interaction and regulatory network analysis

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
                inputSchema=ChatAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.KNOWLEDGEAGENT,
                description=PhytomniAgents.KNOWLEDGEAGENT_DESCRIPTION,
                inputSchema=KnowledgeAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.DATAAGENT,
                description=PhytomniAgents.DATAAGENT_DESCRIPTION,
                inputSchema=DataAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.ANALYSTAGENT,
                description=PhytomniAgents.ANALYSTAGENT_DESCRIPTION,
                inputSchema=AnalystAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.REVIEWAGENT,
                description=PhytomniAgents.REVIEWAGENT_DESCRIPTION,
                inputSchema=ReviewAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.BRIEFGENEAGENT,
                description=PhytomniAgents.BRIEFGENEAGENT_DESCRIPTION,
                inputSchema=BriefGeneAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.DEEPGENOMEAGENT,
                description=PhytomniAgents.DEEPGENOMEAGENT_DESCRIPTION,
                inputSchema=DeepGenomeAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.INSILICORESEARCHAGENT,
                description=PhytomniAgents.INSILICORESEARCHAGENT_DESCRIPTION,
                inputSchema=InSilicoResearchAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.DIGITALDESIGNAGENT,
                description=PhytomniAgents.DIGITALDESIGNAGENT_DESCRIPTION,
                inputSchema=DigitalDesignAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.GENENETWORKAGENT,
                description=PhytomniAgents.GENENETWORKAGENT_DESCRIPTION,
                inputSchema=GeneNetworkAgent.model_json_schema(),
            ),
        ]

    @server.call_tool()
    async def call_tool(name, arguments: Dict[str, Any]) -> list[TextContent]:
        return await dispatch_tool(name, arguments)

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, options, raise_exceptions=True
        )


if __name__ == "__main__":
    asyncio.run(serve())
