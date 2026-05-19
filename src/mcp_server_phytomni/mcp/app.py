# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""MCP server entrypoint for registering and dispatching Phytomni tools.

The module wires schema definitions, MCP-compliant error mapping, and dispatch
to the domain-specific tool handler layer while keeping public tool names
stable for existing clients.
"""

from dataclasses import asdict
from json import dumps
from typing import Any, Awaitable, Callable, Dict

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, ErrorData, TextContent, Tool
from pydantic import BaseModel

from .handlers import (
    handle_analyst_agent,
    handle_brief_gene_agent,
    handle_chat_agent,
    handle_data_agent,
    handle_deep_genome_agent,
    handle_digital_design_agent,
    handle_gene_network_agent,
    handle_get_task_status,
    handle_in_silico_research_agent,
    handle_knowledge_agent,
    handle_review_agent,
)
from .result_formatting import FormattedToolResult, format_tool_result
from .schemas import (
    AnalystAgent,
    BriefGeneAgent,
    ChatAgent,
    DataAgent,
    DeepGenomeAgent,
    DigitalDesignAgent,
    GeneNetworkAgent,
    GetTaskStatus,
    InSilicoResearchAgent,
    KnowledgeAgent,
    PhytomniAgents,
    ReviewAgent,
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
    PhytomniAgents.GET_TASK_STATUS.value: GetTaskStatus,
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
    PhytomniAgents.GET_TASK_STATUS.value: handle_get_task_status,
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


async def invoke_tool_raw(name: Any, arguments: Dict[str, Any]) -> Any:
    """Validate arguments and call a tool handler, returning its payload.

    This is the single shared invocation seam: the MCP dispatcher and the
    HTTP API both route through it so agent lookup, schema validation, and
    handler invocation live in exactly one place. It returns the raw
    handler payload without any MCP serialization.

    Args:
        name: Raw tool name supplied by the caller.
        arguments: JSON object passed to the selected tool.

    Returns:
        The unwrapped handler response payload.

    Raises:
        McpError: If the tool is unknown or arguments fail schema validation.
    """
    tool_name = _tool_name(name)
    model = TOOL_ARGUMENT_MODELS.get(tool_name)
    handler = TOOL_HANDLERS.get(tool_name)
    if model is None or handler is None:
        raise _invalid_params(f"Unknown tool: {tool_name}")

    try:
        args = model(**arguments)
    except ValueError as exc:
        raise _invalid_params(str(exc)) from exc

    return await handler(args)


async def invoke_tool_formatted(
    name: Any, arguments: Dict[str, Any]
) -> FormattedToolResult:
    """Validate arguments, call a handler, and format its payload.

    This is the shared formatted seam built on top of invoke_tool_raw:
    the MCP dispatcher and the HTTP API both route through it so the
    stdio and HTTP surfaces emit the same normalized result. The raw
    seam stays untouched and keeps returning the unwrapped payload.

    Args:
        name: Raw tool name supplied by the caller.
        arguments: JSON object passed to the selected tool.

    Returns:
        The normalized client-facing result for the selected tool.

    Raises:
        McpError: If the tool is unknown or arguments fail validation.
    """
    raw = await invoke_tool_raw(name, arguments)
    return format_tool_result(_tool_name(name), raw, arguments=arguments)


async def dispatch_tool(
    name: Any, arguments: Dict[str, Any]
) -> list[TextContent]:
    """Validate arguments, call a tool handler, and serialize the result.

    Args:
        name: Raw MCP tool name supplied by the client.
        arguments: JSON object passed to the selected MCP tool.

    Returns:
        MCP text content containing the serialized formatted result.

    Raises:
        McpError: If the tool is unknown or arguments fail validation.
    """
    formatted = await invoke_tool_formatted(name, arguments)
    return _text_response(asdict(formatted))


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
        """Return public MCP tool definitions.

        Returns:
            Tool metadata and JSON schemas for every registered Phytomni tool.
        """
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
            Tool(
                name=PhytomniAgents.GET_TASK_STATUS,
                description=PhytomniAgents.GET_TASK_STATUS_DESCRIPTION,
                inputSchema=GetTaskStatus.model_json_schema(),
            ),
        ]

    @server.call_tool()
    async def call_tool(name, arguments: Dict[str, Any]) -> list[TextContent]:
        """Dispatch one MCP tool call.

        Args:
            name: Raw tool name supplied by the MCP client.
            arguments: JSON object supplied by the MCP client.

        Returns:
            Serialized MCP text response from `dispatch_tool`.
        """
        return await dispatch_tool(name, arguments)

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, options, raise_exceptions=True
        )
