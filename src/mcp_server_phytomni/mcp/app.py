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
from pydantic import BaseModel, ValidationError

from ..common.httpx_client import aclose_shared_client, init_shared_client
from ..common.logging_config import configure_logging
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
from .result_formatting import (
    FormattedToolResult,
    ToolResultEnvelope,
    build_tool_result_envelope,
    resolve_debug,
)
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


def _format_validation_error(tool_name: str, exc: ValidationError) -> str:
    """Render a pydantic ValidationError as a sanitized message.

    Default `str(exc)` embeds the offending `input_value`, which leaks
    request bodies (and occasionally credentials) back to MCP clients.
    This helper emits only field paths and error categories so the
    response cannot echo caller payloads.
    """
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()) if part != "")
        kind = err.get("type") or "invalid"
        parts.append(f"{loc}: {kind}" if loc else kind)
    summary = "; ".join(parts) if parts else "invalid arguments"
    return f"Invalid arguments for {tool_name}: {summary}"


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
    except ValidationError as exc:
        raise _invalid_params(
            _format_validation_error(tool_name, exc)
        ) from exc

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
    envelope = await invoke_tool_enveloped(name, arguments)
    return envelope.formatted


async def invoke_tool_enveloped(
    name: Any, arguments: Dict[str, Any]
) -> ToolResultEnvelope:
    """Validate arguments, call a handler, and preserve raw payload.

    This is the shared full-result seam built on top of
    invoke_tool_raw. It keeps the formatted display payload beside the
    raw handler payload so MCP and HTTP clients can inspect all fields
    returned by the agent path.

    Args:
        name: Raw tool name supplied by the caller.
        arguments: JSON object passed to the selected tool.

    Returns:
        Full result envelope for the selected tool.

    Raises:
        McpError: If the tool is unknown or arguments fail validation.
    """
    raw = await invoke_tool_raw(name, arguments)
    return build_tool_result_envelope(
        _tool_name(name), raw, arguments=arguments
    )


async def dispatch_tool(
    name: Any, arguments: Dict[str, Any]
) -> list[TextContent]:
    """Validate arguments, call a tool handler, and serialize the result.

    In default mode the response contains only ``formatted``; set
    ``PHYTOMNI_DEBUG=1`` to include the sanitized ``raw`` handler
    payload alongside it.

    Args:
        name: Raw MCP tool name supplied by the client.
        arguments: JSON object passed to the selected MCP tool.

    Returns:
        MCP text content containing the serialized formatted result.

    Raises:
        McpError: If the tool is unknown or arguments fail validation.
    """
    envelope = await invoke_tool_enveloped(name, arguments)
    payload: Dict[str, Any] = {
        "formatted": asdict(envelope.formatted),
    }
    if resolve_debug(None):
        payload["raw"] = envelope.raw
    return _text_response(payload)


async def serve() -> None:
    """Initialize and run the Phytomni MCP stdio server.

    Configures package-level logging, builds a ``Server("Phytomni-Server")``
    instance, wires the ``list_tools`` and ``call_tool`` handlers, owns the
    process-wide shared ``AsyncClient`` (initialised before stdio comes
    up, closed in ``finally`` so a stdio crash never leaks the pool), and
    runs the server over stdio with ``raise_exceptions=True``. The
    function blocks until the stdio streams close (interrupt or client
    disconnect); there is no graceful shutdown drain — in-flight handler
    coroutines are cancelled abruptly by ``asyncio`` when the surrounding
    task is cancelled, and any resource cleanup must already be handled
    by each handler's own ``async with`` / ``try / finally`` blocks.

    The registered tools are listed in ``TOOL_ARGUMENT_MODELS`` /
    ``TOOL_HANDLERS``; see ``mcp/schemas.py`` for their public schemas.

    Raises:
        McpError: Wrapped invalid-params / internal errors surface here
            only when a handler raises them; serve itself does not raise.
    """
    configure_logging()
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
    init_shared_client()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, options, raise_exceptions=True
            )
    finally:
        await aclose_shared_client()
