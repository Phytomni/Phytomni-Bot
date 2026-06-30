# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""MCP server entrypoint for registering and dispatching Phytomni tools.

The module wires schema definitions, MCP-compliant error mapping, and dispatch
to the domain-specific tool handler layer while keeping public tool names
stable for existing clients.
"""

from collections.abc import AsyncIterator
from dataclasses import asdict
from json import dumps
from typing import Any, Awaitable, Callable, Dict, Mapping, Sequence, cast

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, ErrorData, TextContent, Tool
from pydantic import BaseModel, ValidationError

from ..agents.chat.service import stream_phyto_chat_chunks
from ..agents.shared.citation_enrichment import enrich_cited_doc_list
from ..agents.shared.gauss import aclose_gauss_pool
from ..common.httpx_client import aclose_shared_client, init_shared_client
from ..common.logging_config import configure_logging
from ..config.defaults import ChatConfig
from .handler_support import chat_kwargs, load_handler_runtime, obs_kwargs
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
    scratch_server_dir,
)
from .result_formatting import (
    FormattedToolChunk,
    FormattedToolResult,
    ToolResultEnvelope,
    build_tool_result_envelope,
    format_tool_chunk,
    is_cited_tool,
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
    await _maybe_enrich_cited(_tool_name(name), raw)
    return build_tool_result_envelope(
        _tool_name(name), raw, arguments=arguments
    )


async def _maybe_enrich_cited(tool_name: str, raw: Any) -> None:
    """Enrich a cited tool's doc list with bibliographic metadata.

    No-op for non-cited tools or payloads without a doc list; the
    enricher itself degrades silently on any BI failure.
    """
    if not is_cited_tool(tool_name):
        return
    docs = _raw_doc_list(raw)
    if docs:
        await enrich_cited_doc_list(docs)


def _raw_doc_list(raw: Any) -> list[dict[str, Any]]:
    """Return the cited payload's doc dicts (the live objects).

    Returns the actual dict objects from
    ``raw['choices'][0]['message']['doc_list']`` so in-place enrichment
    propagates to the payload the formatter reads.
    """
    if not isinstance(raw, Mapping):
        return []
    choices = raw.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, str):
        return []
    if not choices or not isinstance(choices[0], Mapping):
        return []
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        return []
    docs = message.get("doc_list")
    if not isinstance(docs, list):
        return []
    return [doc for doc in docs if isinstance(doc, dict)]


async def invoke_tool_streamed(
    name: Any, arguments: Dict[str, Any]
) -> AsyncIterator[FormattedToolChunk]:
    """Stream a tool's response chunk-by-chunk through a typed seam.

    Fourth invocation seam, parallel to :func:`invoke_tool_raw` /
    :func:`invoke_tool_formatted` / :func:`invoke_tool_enveloped`.
    v1 wires only :class:`ChatAgent` to
    :func:`stream_phyto_chat_chunks`; every other registered tool
    raises :class:`NotImplementedError` so callers receive a clear
    "streaming not supported for X" signal instead of a silent
    fallback to non-streaming aggregation.

    The function is an async generator — argument validation,
    unknown-tool detection, and the not-implemented branch all raise
    on the first ``__anext__`` call, not when the generator object is
    constructed. Callers must iterate (or call ``__anext__`` once) to
    surface those errors.

    Args:
        name: Raw tool name supplied by the caller.
        arguments: JSON object passed to the selected tool.

    Yields:
        :class:`FormattedToolChunk` per provider chunk; the chunk's
        ``payload`` is one OpenAI ``chat.completion.chunk`` dict with
        unknown provider fields intact for downstream SSE shaping.

    Raises:
        McpError: When the tool name is unknown or schema validation
            fails (mirrors :func:`invoke_tool_raw`).
        NotImplementedError: When the tool is registered but lacks a
            streaming primitive (every tool except ChatAgent in v1).
    """
    tool_name = _tool_name(name)
    model = TOOL_ARGUMENT_MODELS.get(tool_name)
    if model is None:
        raise _invalid_params(f"Unknown tool: {tool_name}")
    try:
        args = model(**arguments)
    except ValidationError as exc:
        raise _invalid_params(
            _format_validation_error(tool_name, exc)
        ) from exc
    if tool_name == PhytomniAgents.CHAT_AGENT.value:
        async for chunk in _stream_chat_agent(cast(ChatAgent, args)):
            yield format_tool_chunk(chunk)
        return
    raise NotImplementedError(f"streaming not supported for tool {tool_name}")


async def _stream_chat_agent(
    args: ChatAgent,
) -> AsyncIterator[Dict[str, Any]]:
    """Stream phyto-chat chunks using the chat handler's standard kwargs.

    Mirrors :func:`handle_chat_agent` by composing the same
    ``chat_kwargs`` + ``obs_kwargs`` + scratch ``server_dir`` so the
    streaming path sends identical config / sensitive / OBS wiring to
    the provider. Yields each chunk dict produced by
    :func:`stream_phyto_chat_chunks` for the outer seam to wrap.

    Args:
        args: Validated :class:`ChatAgent` request schema.

    Yields:
        One OpenAI ``chat.completion.chunk`` dict per upstream chunk.
    """
    chat_config = ChatConfig()
    runtime = load_handler_runtime()
    async for chunk in stream_phyto_chat_chunks(
        user_query=args.user_query,
        obs_file_list=args.obs_file_list,
        server_dir=scratch_server_dir(chat_config, "chat"),
        **chat_kwargs(chat_config, runtime.sensitive),
        **obs_kwargs(chat_config, runtime.obs_credentials),
    ):
        yield chunk


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
        await aclose_gauss_pool()
