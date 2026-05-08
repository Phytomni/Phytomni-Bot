# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""MCP server entrypoint for registering and dispatching Phytomni tools.

The module owns JSON schema definitions, MCP-compliant error mapping, and
dispatch to the domain-specific tool handler layer while keeping public tool
names stable for existing clients.
"""

import asyncio
from enum import Enum
from json import dumps
from typing import Annotated, Any, Awaitable, Callable, Dict, List

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, ErrorData, TextContent, Tool
from pydantic import BaseModel, Field

from .tool_handlers import (
    handle_analyst_agent,
    handle_brief_gene_agent,
    handle_chat_agent,
    handle_data_agent,
    handle_deep_genome_agent,
    handle_digital_design_agent,
    handle_gene_network_agent,
    handle_in_silico_research_agent,
    handle_knowledge_agent,
    handle_review_agent,
)


class ChatAgent(BaseModel):
    """Input parameters for general ChatAgent Q&A and file summarization."""

    user_query: Annotated[
        str,
        Field(
            description="Full user question or instruction for a general "
            "plant-science answer, explanation, or uploaded-file summary. "
            "Preserve the user's original intent and include any requested "
            "output format.",
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="OBS paths for optional user-uploaded context files "
            "to summarize or use while answering. Use complete OBS paths "
            "exactly as provided, pass [] when no files are supplied, and do "
            "not invent file paths. Supported file types: PPTX, DOCX, XLSX, "
            "XLS, PDF, Outlook.",
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
    """Input parameters for retrieval-backed KnowledgeAgent answers."""

    user_query: Annotated[
        str,
        Field(
            description="Focused plant-science question to answer with "
            "retrieved literature, patent, or book evidence. Include key "
            "species, genes, traits, methods, or constraints from the user."
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="OBS paths for optional user-uploaded documents to "
            "combine with retrieved evidence. Use complete OBS paths exactly "
            "as provided, pass [] when no files are supplied, and do not "
            "invent file paths. Supported file types: PPTX, DOCX, XLSX, XLS, "
            "PDF, Outlook.",
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
    """Input parameters for natural-language database querying."""

    user_query: Annotated[
        str,
        Field(
            description="Natural-language database question that needs "
            "structured SQL-backed results, such as counts, tables, "
            "statistics, rankings, or filtered records. Keep it as a user "
            "question rather than raw SQL, and include species, trait, gene, "
            "or filter constraints when available.",
        ),
    ]


class AnalystAgent(BaseModel):
    """Input parameters for bioinformatics workflow planning and submission."""

    goal_description: Annotated[
        str,
        Field(
            description=(
                "Bioinformatics analysis objective to plan and submit. "
                "Describe the biological question, desired analysis type, "
                "target organism, comparisons, and expected outputs; put "
                "dataset paths in data_list instead of this text."
            ),
        ),
    ]
    data_list: Annotated[
        Dict[str, str],
        Field(
            description="Dictionary of input analysis datasets. Each key must "
            "be a complete OBS path to a data file, and each value must "
            "describe that file's role, format, sequencing or omics type, "
            "organism, sample group or condition, quality notes, and intended "
            "analysis use. Use {} only when the workflow truly has no input "
            "datasets.",
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
            description="OBS paths for optional supporting documents, such as "
            "protocols, papers, or requirement files, that provide context "
            "for workflow planning. Do not duplicate raw analysis datasets "
            "already listed in data_list. Pass [] when no files are supplied.",
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
    """Input parameters for comprehensive deep genome gene analysis."""

    species_code: Annotated[
        str,
        Field(
            description=(
                """Three-letter species_code for the target gene. Fill this
                with one supported code, not a Latin name or common name.
                Supported species_code values are the keys of this dict: {
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
                'zma': 'maize (Zea mays)'}"""
            ),
        ),
    ]
    gene_id: Annotated[
        str,
        Field(
            description="Single target gene identifier in the selected "
            "species_code, such as a locus ID or accepted database gene "
            "ID. Do not include multiple genes, trait IDs, or free-form "
            "questions.",
        ),
    ]


class ReviewAgent(BaseModel):
    """Input parameters for broad literature review and report generation."""

    user_query: Annotated[
        str,
        Field(
            description="Broad review or report topic. Include the crop or "
            "species, biological process, trait, method, scope, comparison, "
            "and desired report angle when provided by the user."
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="OBS paths for optional user-uploaded papers, notes, "
            "or source documents to consider during review generation. Use "
            "complete OBS paths exactly as provided, pass [] when no files "
            "are supplied, and do not invent file paths. Supported file "
            "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook.",
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
    """Input parameters for a concise single-gene function report."""

    user_query: Annotated[
        str,
        Field(
            description="One plant gene ID or transcript ID to summarize. "
            "Prefer the exact identifier supplied by the user and do not use "
            "gene symbols, species names, multiple genes, or a full research "
            "question as this value.",
        ),
    ]


class InSilicoResearchAgent(BaseModel):
    """Input parameters for paper-driven in silico research task submission."""

    user_query: Annotated[
        str,
        Field(
            description="Scientific paper text, abstract, methods/results "
            "context, or study description to decompose into computational "
            "research objectives. If the paper is uploaded as a file, include "
            "the user's instruction or brief context here."
        ),
    ]
    data_list: Annotated[
        Dict[str, str],
        Field(
            description="Dictionary of datasets available for the extracted "
            "in silico tasks. Each key must be a complete OBS path to a data "
            "file, and each value must describe the file's biological source, "
            "format, assay or sequencing type, sample condition, quality "
            "notes, and intended use in reproducibility analysis. Use {} "
            "only when no datasets are supplied.",
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
            description="OBS paths for optional uploaded paper or context "
            "files used to extract research objectives. Use complete OBS "
            "paths exactly as provided, pass [] when no files are supplied, "
            "and do not invent file paths. Supported file types: PPTX, "
            "DOCX, XLSX, XLS, PDF, Outlook.",
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
    """Input parameters for protein and promoter design task submission."""

    species: Annotated[
        str,
        Field(
            description="Target species for design analysis as a Latin name "
            "in lowercase with spaces, not a species_code. Examples: "
            "'arabidopsis thaliana', 'oryza sativa', 'zea mays', "
            "'glycine max', 'triticum aestivum', 'hordeum vulgare', "
            "'solanum lycopersicum', 'solanum tuberosum', 'brassica napus', "
            "'gossypium hirsutum', 'sorghum bicolor'.",
        ),
    ]
    gene_id: Annotated[
        str,
        Field(
            description="Single target gene identifier for protein and "
            "promoter design in the selected species. Do not include multiple "
            "genes, trait IDs, or a general function-analysis question.",
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="OBS paths for optional uploaded context files. Use "
            "complete OBS paths exactly as provided, pass [] when no files "
            "are supplied, and do not invent file paths. Supported file "
            "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook.",
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
    """Input parameters for trait-associated gene network task submission."""

    species: Annotated[
        str,
        Field(
            description="Target species for trait-associated gene network "
            "analysis as a Latin name in lowercase with spaces, not a "
            "species_code. Examples: 'arabidopsis thaliana', 'oryza sativa', "
            "'zea mays', 'glycine max', 'triticum aestivum', "
            "'hordeum vulgare', 'solanum lycopersicum', 'solanum tuberosum', "
            "'brassica napus', 'gossypium hirsutum', 'sorghum bicolor'.",
        ),
    ]
    to_id: Annotated[
        str,
        Field(
            description="Trait Ontology identifier for the target phenotype, "
            "formatted like 'TO:0000207'. Fill this with a TO ID, not a gene "
            "ID or free-text trait name. If the user did not provide a "
            "reliable TO ID, ask for clarification instead of guessing.",
        ),
    ]
    obs_file_list: Annotated[
        List[str],
        Field(
            description="OBS paths for optional uploaded context files. Use "
            "complete OBS paths exactly as provided, pass [] when no files "
            "are supplied, and do not invent file paths. Supported file "
            "types: PPTX, DOCX, XLSX, XLS, PDF, Outlook.",
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
        CHAT_AGENT: Core language model interface for fundamental Q&A.
            Usage: Basic conceptual queries, single-domain problem solving.
            Limitations: Avoid for multi-factor agricultural optimizations.
        KNOWLEDGE_AGENT: Evidence-based literature synthesis system.
            Usage: Cross-referenced answers from curated scientific sources.
        DATA_AGENT: Structured data query interface.
            Usage: Precise numerical/statistical retrieval from databases.
        ANALYST_AGENT: Genomic workflow orchestration system.
            Usage: Automated execution of bioinformatics pipelines.

    Descriptions provide guidance on appropriate application scenarios and
    technical constraints for each agent type. All agents implement
    standardized JSON schema for parameter validation.
    """

    CHAT_AGENT = "ChatAgent"
    CHAT_AGENT_DESCRIPTION = (
        "Use for general plant-science Q&A, conceptual explanations, and "
        "summaries of user-uploaded files using the base LLM. Do not use "
        "when the request needs literature retrieval, SQL/database results, "
        "or bioinformatics workflow execution."
    )
    KNOWLEDGE_AGENT = "KnowledgeAgent"
    KNOWLEDGE_AGENT_DESCRIPTION = (
        "Use for evidence-backed answers that require retrieval from plant "
        "science literature, patents, or books, optionally combined with "
        "uploaded files. Do not use for broad review writing or structured "
        "database queries."
    )
    DATA_AGENT = "DataAgent"
    DATA_AGENT_DESCRIPTION = (
        "Use for natural-language questions that need structured botanical "
        "database or SQL results, including counts, tables, statistics, and "
        "other precise database-backed facts."
    )
    ANALYST_AGENT = "AnalystAgent"
    ANALYST_AGENT_DESCRIPTION = (
        "Use for bioinformatics workflow planning and task submission from a "
        "research goal plus input datasets, such as sequence, omics, or other "
        "computational biology analyses that should run on the analysis "
        "platform."
    )
    REVIEW_AGENT = "ReviewAgent"
    REVIEW_AGENT_DESCRIPTION = (
        "Use for broad literature review or report generation that requires "
        "multi-dimension planning, retrieval, drafting, critique, revision, "
        "and summary. Prefer KnowledgeAgent for targeted evidence-backed Q&A."
    )
    BRIEF_GENE_AGENT = "BriefGeneAgent"
    BRIEF_GENE_AGENT_DESCRIPTION = (
        "Use for a concise function report about one plant gene, transcript, "
        "or accepted database gene ID, using BI annotations when available "
        "and retrieved literature as fallback or supporting evidence. Do not "
        "route gene-symbol-only inputs here unless the user provides an ID."
    )
    DEEP_GENOME_AGENT = "DeepGenomeAgent"
    DEEP_GENOME_AGENT_DESCRIPTION = (
        "Use for comprehensive gene-function analysis from species_code plus "
        "gene_id, including annotations, ortholog/paralog/interaction "
        "context, deep computational analyses, experiment recommendations, "
        "protocols, and final report sections."
    )
    IN_SILICO_RESEARCH_AGENT = "InSilicoResearchAgent"
    IN_SILICO_RESEARCH_AGENT_DESCRIPTION = (
        "Use to extract computational research objectives from a paper or "
        "research context, then submit reproducibility-style analysis tasks "
        "against the provided datasets."
    )
    DIGITAL_DESIGN_AGENT = "DigitalDesignAgent"
    DIGITAL_DESIGN_AGENT_DESCRIPTION = (
        "Use to submit both protein design and promoter design analyses for a "
        "specific species plus gene_id. This is for computational design task "
        "execution, not general gene-function explanation."
    )
    GENE_NETWORK_AGENT = "GeneNetworkAgent"
    GENE_NETWORK_AGENT_DESCRIPTION = (
        "Use to submit trait-associated gene network analysis for a species "
        "plus Trait Ontology ID. Do not use for generic free-form gene "
        "interaction Q&A or broad network explanation."
    )


ToolHandler = Callable[[Any], Awaitable[Any]]

TOOL_ARGUMENT_MODELS: Dict[str, type[BaseModel]] = {
    PhytomniAgents.CHAT_AGENT.value: ChatAgent,
    PhytomniAgents.KNOWLEDGE_AGENT.value: KnowledgeAgent,
    PhytomniAgents.DATA_AGENT.value: DataAgent,
    PhytomniAgents.ANALYST_AGENT.value: AnalystAgent,
    PhytomniAgents.REVIEW_AGENT.value: ReviewAgent,
    PhytomniAgents.BRIEF_GENE_AGENT.value: BriefGeneAgent,
    PhytomniAgents.DEEP_GENOME_AGENT.value: DeepGenomeAgent,
    PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value: InSilicoResearchAgent,
    PhytomniAgents.DIGITAL_DESIGN_AGENT.value: DigitalDesignAgent,
    PhytomniAgents.GENE_NETWORK_AGENT.value: GeneNetworkAgent,
}

TOOL_HANDLERS: Dict[str, ToolHandler] = {
    PhytomniAgents.CHAT_AGENT.value: handle_chat_agent,
    PhytomniAgents.KNOWLEDGE_AGENT.value: handle_knowledge_agent,
    PhytomniAgents.DATA_AGENT.value: handle_data_agent,
    PhytomniAgents.ANALYST_AGENT.value: handle_analyst_agent,
    PhytomniAgents.REVIEW_AGENT.value: handle_review_agent,
    PhytomniAgents.BRIEF_GENE_AGENT.value: handle_brief_gene_agent,
    PhytomniAgents.DEEP_GENOME_AGENT.value: handle_deep_genome_agent,
    PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value: (
        handle_in_silico_research_agent
    ),
    PhytomniAgents.DIGITAL_DESIGN_AGENT.value: handle_digital_design_agent,
    PhytomniAgents.GENE_NETWORK_AGENT.value: handle_gene_network_agent,
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
                name=PhytomniAgents.CHAT_AGENT,
                description=PhytomniAgents.CHAT_AGENT_DESCRIPTION,
                inputSchema=ChatAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.KNOWLEDGE_AGENT,
                description=PhytomniAgents.KNOWLEDGE_AGENT_DESCRIPTION,
                inputSchema=KnowledgeAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.DATA_AGENT,
                description=PhytomniAgents.DATA_AGENT_DESCRIPTION,
                inputSchema=DataAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.ANALYST_AGENT,
                description=PhytomniAgents.ANALYST_AGENT_DESCRIPTION,
                inputSchema=AnalystAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.REVIEW_AGENT,
                description=PhytomniAgents.REVIEW_AGENT_DESCRIPTION,
                inputSchema=ReviewAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.BRIEF_GENE_AGENT,
                description=PhytomniAgents.BRIEF_GENE_AGENT_DESCRIPTION,
                inputSchema=BriefGeneAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.DEEP_GENOME_AGENT,
                description=PhytomniAgents.DEEP_GENOME_AGENT_DESCRIPTION,
                inputSchema=DeepGenomeAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.IN_SILICO_RESEARCH_AGENT,
                description=(
                    PhytomniAgents.IN_SILICO_RESEARCH_AGENT_DESCRIPTION
                ),
                inputSchema=InSilicoResearchAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.DIGITAL_DESIGN_AGENT,
                description=PhytomniAgents.DIGITAL_DESIGN_AGENT_DESCRIPTION,
                inputSchema=DigitalDesignAgent.model_json_schema(),
            ),
            Tool(
                name=PhytomniAgents.GENE_NETWORK_AGENT,
                description=PhytomniAgents.GENE_NETWORK_AGENT_DESCRIPTION,
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
