# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Register and dispatch MCP schemas through domain tool handlers."""

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import asdict
from json import dumps
from typing import (
    Any,
    Unpack,
    cast,
)

from mcp.server import Server
from mcp.server.lowlevel.server import request_ctx
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, ErrorData, TextContent, Tool
from pydantic import BaseModel, ValidationError

from ..agents.brief_gene.agent import brief_gene_stream_seed
from ..agents.chat.service import stream_phyto_chat_chunks
from ..agents.data.agent import data_stream_seed
from ..agents.knowledge.agent import knowledge_stream_target
from ..agents.review.agent import review_stream_target
from ..agents.shared.citation_database import validate_citation_database
from ..agents.shared.citation_enrichment import enrich_cited_doc_list
from ..agents.shared.gauss import aclose_gauss_pool
from ..agents.shared.intermediate_state import merge_intermediate_state
from ..common.logging_config import configure_logging
from ..runtime.langgraph_runner import build_runnable_config
from ..runtime.outbound import (
    aclose_outbound_runtime,
    init_outbound_runtime,
)
from ..runtime.request_context import current_request_id
from ..runtime.resume import (
    aresume_graph,
    detect_interrupt,
    elicit_review_decision,
)
from ..storage.path_policy import IdFactory
from .handler_support import (
    chat_call_kwargs,
    load_chat_runtime,
)
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
    reset_private_agent_state,
    reset_private_agent_thread_id,
    reset_private_conversation_messages,
    scratch_server_dir,
    set_private_agent_state,
    set_private_agent_thread_id,
    set_private_conversation_messages,
)
from .progress_events import PROGRESS_KIND
from .result_formatting import (
    AguiEvent,
    ExecutionProjection,
    FormattedToolResult,
    ToolResultEnvelope,
    build_tool_result_envelope,
    custom,
    format_tool_result,
    is_cited_tool,
    resolve_debug,
    run_finished,
    run_started,
    step_started,
    text_message_content,
    text_message_end,
    text_message_start,
)
from .schemas import (
    AGENT_TOOL_DEFINITIONS,
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
from .stream_lifecycle import StreamLifecycleState, project_stream_failures
from .streaming_phases import (
    GRAPH_PROGRESS_TOOLS as _GRAPH_PROGRESS_TOOLS,
)
from .streaming_phases import (
    PrivateStreamKwargs as _PrivateStreamKwargs,
)
from .streaming_phases import StreamRunMeta as _StreamRunMeta
from .streaming_phases import (
    close_async_iterator as _close_async_iterator,
)
from .streaming_phases import iterate_owned, phase_for

ToolHandler = Callable[[Any], Awaitable[Any]]


TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
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

TOOL_HANDLERS: dict[str, ToolHandler] = {
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


def validate_tool_arguments(name: Any, arguments: dict[str, Any]) -> Any:
    """Validate one tool payload without invoking its handler."""
    tool_name = _tool_name(name)
    model = TOOL_ARGUMENT_MODELS.get(tool_name)
    handler = TOOL_HANDLERS.get(tool_name)
    if model is None or handler is None:
        raise _invalid_params(f"Unknown tool: {tool_name}")
    try:
        return model(**arguments)
    except ValidationError as exc:
        raise _invalid_params(
            _format_validation_error(tool_name, exc)
        ) from exc


async def invoke_tool_raw(
    name: Any,
    arguments: dict[str, Any],
    *,
    conversation_messages: Sequence[Mapping[str, str]] = (),
    agent_thread_id: str | None = None,
    private_agent_state: Mapping[str, Any] | None = None,
) -> Any:
    """Validate arguments and call a tool handler, returning its payload.

    This is the single shared invocation seam: the MCP dispatcher and the
    HTTP API both route through it so agent lookup, schema validation, and
    handler invocation live in exactly one place. It returns the raw
    handler payload without any MCP serialization.

    Args:
        name: Raw tool name supplied by the caller.
        arguments: JSON object passed to the selected tool.
        conversation_messages: Private native-role history held in a
            task-local handler context, never in public tool arguments.
        agent_thread_id: Private stable Chat thread held in a task-local
            handler context, never in public tool arguments.
        private_agent_state: Private handler-only state excluded from
            public schemas and result payloads.

    Returns:
        The unwrapped handler response payload.

    Raises:
        McpError: If the tool is unknown or arguments fail schema validation.
    """
    tool_name = _tool_name(name)
    args = validate_tool_arguments(tool_name, arguments)
    messages_token = set_private_conversation_messages(conversation_messages)
    thread_token = set_private_agent_thread_id(agent_thread_id)
    state_token = set_private_agent_state(private_agent_state)
    try:
        return await TOOL_HANDLERS[tool_name](args)
    finally:
        reset_private_agent_state(state_token)
        reset_private_agent_thread_id(thread_token)
        reset_private_conversation_messages(messages_token)


async def invoke_tool_formatted(
    name: Any, arguments: dict[str, Any]
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
    # Keep this explicitly legacy: callers that selected the historical
    # display-only shim still receive formatter metadata until the MCP and
    # HTTP surfaces migrate to the canonical execution block.
    return format_tool_result(
        _tool_name(name), envelope.raw, arguments=arguments
    )


async def invoke_tool_enveloped(
    name: Any,
    arguments: dict[str, Any],
    *,
    conversation_messages: Sequence[Mapping[str, str]] = (),
    agent_thread_id: str | None = None,
    private_agent_state: Mapping[str, Any] | None = None,
) -> ToolResultEnvelope:
    """Validate arguments, call a handler, and preserve raw payload.

    This is the shared full-result seam built on top of
    invoke_tool_raw. It keeps the formatted display payload beside the
    raw handler payload so MCP and HTTP clients can inspect all fields
    returned by the agent path.

    Args:
        name: Raw tool name supplied by the caller.
        arguments: JSON object passed to the selected tool.
        conversation_messages: Private native-role history for in-process
            invocation adapters; it is excluded from public MCP schemas.
        agent_thread_id: Private stable Chat thread for in-process V1
            invocation; it is excluded from public MCP schemas.
        private_agent_state: Private handler-only state for in-process
            adapters; it is excluded from public schemas.

    Returns:
        Full result envelope for the selected tool.

    Raises:
        McpError: If the tool is unknown or arguments fail validation.
    """
    raw = await invoke_tool_raw(
        name,
        arguments,
        conversation_messages=conversation_messages,
        agent_thread_id=agent_thread_id,
        private_agent_state=private_agent_state,
    )
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
    propagates to the payload the formatter reads. Non-dict elements are
    filtered out, so the returned list may be shorter than ``doc_list``.
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


def prepare_tool_stream(
    name: Any,
    arguments: dict[str, Any],
    *,
    run_id: str,
    dialogue_id: str | None,
    **private: Unpack[_PrivateStreamKwargs],
) -> AsyncIterator[AguiEvent]:
    """Validate and prepare a raw AG-UI event iterator synchronously.

    This is the eager setup seam for streamed tools. It performs public tool
    lookup and Pydantic validation, resolves the supported streaming branch,
    and builds graph targets before returning an async iterator. Runtime
    failures from the returned iterator are intentionally not caught here;
    the HTTP boundary owns their protocol projection.

    Args:
        name: Raw tool name supplied by the caller.
        arguments: JSON object passed to the selected tool.
        run_id: Registry run id carried on ``RunStarted``/``RunFinished``.
        dialogue_id: Optional chat-ai conversation id carried on
            ``RunStarted``.

    Returns:
        A raw async iterator for the selected supported streaming primitive.

    Raises:
        McpError: When the tool name is unknown or schema validation
            fails (mirrors :func:`invoke_tool_raw`).
        NotImplementedError: When the tool is registered but lacks a
            streaming primitive.
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
        return _stream_chat_events(
            cast(ChatAgent, args),
            run_id=run_id,
            dialogue_id=dialogue_id,
            conversation_messages=private.get("conversation_messages", ()),
        )
    if tool_name in {
        PhytomniAgents.KNOWLEDGE_AGENT.value,
        PhytomniAgents.REVIEW_AGENT.value,
        PhytomniAgents.BRIEF_GENE_AGENT.value,
    }:
        app, initial_state = _build_graph_stream_target(
            tool_name,
            args,
            conversation_messages=private.get("conversation_messages", ()),
            private_agent_state=private.get("private_agent_state"),
        )
        return _stream_graph_agent(
            app,
            initial_state,
            tool_name,
            tool_name,
            run_id=run_id,
            dialogue_id=dialogue_id,
        )
    raise NotImplementedError(f"streaming not supported for tool {tool_name}")


def invoke_tool_streamed(
    name: Any,
    arguments: dict[str, Any],
    *,
    run_id: str,
    dialogue_id: str | None,
) -> AsyncIterator[AguiEvent]:
    """Return an eagerly prepared AG-UI stream without awaiting it.

    The public call shape remains ``async for event in
    invoke_tool_streamed(...)``. Setup errors therefore surface before an
    HTTP response body is opened, while runtime errors remain in the raw
    iterator for the shared stream lifecycle projector.
    """
    raw_events = prepare_tool_stream(
        name,
        arguments,
        run_id=run_id,
        dialogue_id=dialogue_id,
    )
    return project_stream_failures(
        raw_events,
        state=StreamLifecycleState(),
        run_id=run_id,
        request_id=current_request_id() or "unknown",
    )


async def _stream_chat_events(
    args: ChatAgent,
    *,
    run_id: str,
    dialogue_id: str | None,
    conversation_messages: Sequence[Mapping[str, str]] = (),
) -> AsyncIterator[AguiEvent]:
    """Project provider chat deltas into raw AG-UI event frames."""
    yield run_started(run_id, dialogue_id)
    message_id = IdFactory().new_id("msg")
    started = False
    chunks = _stream_chat_agent(
        args, conversation_messages=conversation_messages
    )
    try:
        async for chunk in chunks:
            delta = _chunk_content_delta(chunk)
            if not delta:
                continue
            if not started:
                yield text_message_start(message_id)
                started = True
            yield text_message_content(message_id, delta)
    finally:
        await _close_async_iterator(chunks)
    if started:
        yield text_message_end(message_id)
    yield run_finished(run_id)


def _build_graph_stream_target(
    tool_name: str,
    args: BaseModel,
    *,
    conversation_messages: Sequence[Mapping[str, str]] = (),
    private_agent_state: Mapping[str, Any] | None = None,
) -> tuple[Any, Mapping[str, Any]]:
    """Acquire the cached compiled app + seeded state for a graph agent.

    Dispatches on ``tool_name`` to the co-located per-agent stream-target
    accessor (``knowledge_stream_target`` / ``review_stream_target`` /
    ``brief_gene_stream_seed``), each of which acquires the SAME cached
    agent its blocking wrapper uses with default config and returns
    ``(app, initial_state)``. Knowledge / Review request schemas expose
    ``user_query`` + ``obs_file_list``; BriefGene carries only
    ``user_query``.

    Args:
        tool_name: Public MCP tool name (KnowledgeAgent, ReviewAgent,
            or BriefGeneAgent).
        args: The validated request schema for that tool.

    Returns:
        Tuple of the compiled graph app and its initial state dict.
    """
    if tool_name == PhytomniAgents.KNOWLEDGE_AGENT.value:
        knowledge_args = cast(KnowledgeAgent, args)
        retrieval_query = (
            private_agent_state.get("retrieval_query")
            if private_agent_state is not None
            else None
        )
        return knowledge_stream_target(
            knowledge_args.user_query,
            obs_file_list=knowledge_args.obs_file_list,
            locale=knowledge_args.locale,
            conversation_messages=conversation_messages,
            retrieval_query=(
                retrieval_query
                if isinstance(retrieval_query, str)
                else knowledge_args.user_query
            ),
        )
    if tool_name == PhytomniAgents.BRIEF_GENE_AGENT.value:
        return brief_gene_stream_seed(cast(BriefGeneAgent, args))
    review_args = cast(ReviewAgent, args)
    return review_stream_target(
        review_args.user_query,
        obs_file_list=review_args.obs_file_list,
        locale=review_args.locale,
    )


async def _terminal_graph_events(
    tool_name: str, final_state: Mapping[str, Any] | None
) -> AsyncIterator[AguiEvent]:
    """Project the final graph state into terminal AG-UI frames.

    Mirrors the blocking cited path: runs the same ``_maybe_enrich_cited``
    bibliographic enrichment, then reuses ``build_tool_result_envelope``
    so the streamed terminal answer carries the same fields as the
    blocking response -- one one-shot TextMessage for the answer, plus
    Custom frames for references, public metadata, and follow-up questions.

    Args:
        tool_name: Public MCP tool name driving envelope formatting.
        final_state: The compiled graph's last ``values`` chunk, or
            ``None`` when the astream loop produced no terminal state.

    Yields:
        ``TextMessageStart``/``TextMessageContent``/``TextMessageEnd``
        around the formatted answer (only when non-empty), then
        ``Custom`` frames for references, citation degradation metadata,
        and follow-up questions
        (each only when non-empty).
    """
    if final_state is None:
        return
    merged = merge_intermediate_state(dict(final_state))
    await _maybe_enrich_cited(tool_name, merged)
    envelope = build_tool_result_envelope(tool_name, merged)
    formatted = envelope.formatted
    message_id = IdFactory().new_id("msg")
    if formatted.answer:
        yield text_message_start(message_id)
        yield text_message_content(message_id, formatted.answer)
        yield text_message_end(message_id)
    if formatted.references:
        yield custom(
            "phyto.references",
            {"doc_list": [dict(ref) for ref in formatted.references]},
        )
    if formatted.metadata.get("citation_metadata_degraded") is True:
        yield custom("phyto.metadata", {"citation_metadata_degraded": True})
    if formatted.follow_up_questions:
        yield custom("phyto.follow_up", list(formatted.follow_up_questions))


async def _stream_graph_agent(
    app: Any,
    initial_state: Mapping[str, Any],
    agent_name: str,
    tool_name: str,
    **run_meta: Unpack[_StreamRunMeta],
) -> AsyncIterator[AguiEvent]:
    """Drive a compiled graph, emitting stage events then a terminal answer.

    Walks ``app.astream`` in ``["custom", "updates", "values"]`` mode
    with ``subgraphs=True``, projecting whitelisted parent-only
    (``ns == ()``) node updates to deduped ``StepStarted`` frames,
    capturing the latest parent-only ``values`` chunk as the graph's
    final state, and forwarding ``custom`` ticks whose ``kind`` matches
    :data:`PROGRESS_KIND` from any namespace as ``phyto.progress``
    frames. Once the astream loop is exhausted, the captured state is
    projected into terminal answer/reference frames through
    :func:`_terminal_graph_events` before ``RunFinished`` closes the run.
    """
    run_id = run_meta["run_id"]
    yield run_started(run_id, run_meta["dialogue_id"])
    seen_phases: set[str] = set()
    final_state: Mapping[str, Any] | None = None
    graph_events = app.astream(
        initial_state,
        stream_mode=["custom", "updates", "values"],
        subgraphs=True,
        config=build_runnable_config(run_id),
    )
    try:
        async for ns, mode, chunk in iterate_owned(graph_events):
            if mode == "custom":
                if isinstance(chunk, Mapping) and chunk.get("kind") == (
                    PROGRESS_KIND
                ):
                    yield custom("phyto.progress", dict(chunk))
            elif mode == "updates" and ns == ():
                for node_name in chunk:
                    phase = phase_for(agent_name, node_name)
                    if phase and phase not in seen_phases:
                        seen_phases.add(phase)
                        yield step_started(phase)
            elif mode == "values" and ns == ():
                final_state = chunk
    finally:
        await _close_async_iterator(graph_events)
    async for event in _terminal_graph_events(tool_name, final_state):
        yield event
    yield run_finished(run_id)


def _stdio_progress_context() -> tuple[str | int | None, Any, str]:
    """Return (progress_token, session, run_id) from the MCP request ctx.

    Reads the low-level server's ``request_ctx`` contextvar. Outside an
    active MCP request (``LookupError``), returns
    ``(None, None, <fresh run id>)`` so the caller takes the blocking
    path. Inside an MCP request without ``progressToken``, returns the
    session with a ``None`` token so ReviewAgent can still elicit.
    """
    run_id = IdFactory().new_id("run")
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None, None, run_id
    meta = getattr(ctx, "meta", None)
    token = getattr(meta, "progressToken", None) if meta else None
    return token, ctx.session, run_id


def _graph_stream_target(
    tool_name: str, args: BaseModel
) -> tuple[Any, Mapping[str, Any]] | None:
    """Return (app, initial_state) for a graph tool, or None if not one.

    Knowledge/Review reuse their existing ``*_stream_target`` accessors;
    Data/BriefGene acquire their cached agent and seed the initial
    state inline (they have no SSE ``stream_target`` because they carry
    no public chat-completions model alias).

    Args:
        tool_name: Public MCP tool name.
        args: The validated request schema for that tool.

    Returns:
        Tuple of the compiled graph app and its initial state dict,
        or ``None`` when the tool is not a graph-progress candidate.
    """
    if tool_name in {
        PhytomniAgents.KNOWLEDGE_AGENT.value,
        PhytomniAgents.REVIEW_AGENT.value,
    }:
        return _build_graph_stream_target(tool_name, args)
    if tool_name == PhytomniAgents.DATA_AGENT.value:
        return data_stream_seed(cast(DataAgent, args))
    if tool_name == PhytomniAgents.BRIEF_GENE_AGENT.value:
        return brief_gene_stream_seed(cast(BriefGeneAgent, args))
    return None


async def _astream_progress_ticks(
    app: Any,
    initial_state: Mapping[str, Any],
    run_id: str,
    sink: list[Mapping[str, Any]],
) -> AsyncIterator[Mapping[str, Any]]:
    """Yield phyto.progress ticks from a graph walk; capture final state.

    Mirrors :func:`_stream_graph_agent`'s astream contract (custom /
    updates / values with ``subgraphs=True``) but emits only the
    progress ticks the stdio seam forwards as MCP progress
    notifications. The parent-level (``ns == ()``) ``values`` chunk is
    appended to ``sink`` so the caller can format the terminal payload
    without a second graph walk.

    Args:
        app: Compiled LangGraph application.
        initial_state: Seeded initial state dict.
        run_id: Registry run id for the LangGraph thread config.
        sink: Caller-owned list; cleared and repopulated with the
            latest parent-level ``values`` chunk on each iteration.

    Yields:
        Each ``phyto.progress`` custom tick emitted by the graph.
    """
    async for ns, mode, chunk in app.astream(
        initial_state,
        stream_mode=["custom", "updates", "values"],
        subgraphs=True,
        config=build_runnable_config(run_id),
    ):
        if mode == "custom":
            if isinstance(chunk, Mapping) and chunk.get("kind") == (
                PROGRESS_KIND
            ):
                yield chunk
        elif mode == "values" and ns == ():
            sink.clear()
            sink.append(chunk)


async def _stdio_terminal_payload(
    tool_name: str, final_state: Mapping[str, Any] | None
) -> list[TextContent]:
    """Format a captured graph final-state into the stdio text payload.

    Reuses the same enrichment + envelope path ``dispatch_tool`` uses so
    the terminal answer is byte-identical to the blocking response.

    Args:
        tool_name: Public MCP tool name driving envelope formatting.
        final_state: The captured last ``values`` chunk, or ``None``
            when the astream loop produced no terminal state.

    Returns:
        MCP text content containing the serialized formatted result.
    """
    if final_state is None:
        return _text_response(
            {
                "formatted": {},
                "execution": asdict(ExecutionProjection()),
            }
        )
    merged = merge_intermediate_state(dict(final_state))
    await _maybe_enrich_cited(tool_name, merged)
    envelope = build_tool_result_envelope(tool_name, merged)
    payload: dict[str, Any] = {
        "formatted": asdict(envelope.formatted),
        "execution": asdict(envelope.execution),
    }
    if resolve_debug(None):
        payload["raw"] = envelope.raw
    return _text_response(payload)


async def _drive_stdio_progress(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    progress_token: str | int | None,
    session: Any,
    run_id: str,
) -> list[TextContent]:
    """Drive a graph tool, notifying progress, returning terminal payload.

    Validates arguments, walks the graph via
    :func:`_astream_progress_ticks` forwarding each ``phyto.progress``
    tick to ``session.send_progress_notification`` when a token exists,
    then formats the captured final state through
    :func:`_stdio_terminal_payload`.

    Args:
        tool_name: Public MCP tool name.
        arguments: JSON object passed to the selected tool.
        progress_token: Token supplied by the MCP client, or ``None``.
        session: The MCP session carrying ``send_progress_notification``.
        run_id: Registry run id for the LangGraph thread config.

    Returns:
        MCP text content containing the serialized formatted result.

    Raises:
        NotImplementedError: If the tool has no graph progress support.
    """
    model = TOOL_ARGUMENT_MODELS.get(tool_name)
    if model is None:
        raise NotImplementedError(f"no graph progress for {tool_name}")
    args = model(**arguments)
    target = _graph_stream_target(tool_name, args)
    if target is None:
        raise NotImplementedError(f"no graph progress for {tool_name}")
    graph_app, initial_state = target
    sink: list[Mapping[str, Any]] = []
    async for tick in _astream_progress_ticks(
        graph_app, initial_state, run_id, sink
    ):
        if progress_token is not None:
            await session.send_progress_notification(
                progress_token=progress_token,
                progress=float(tick["current"]),
                total=(
                    float(tick["total"])
                    if tick.get("total") is not None
                    else None
                ),
                message=tick["phase"],
            )
    final_state = sink[0] if sink else None
    if (
        tool_name == PhytomniAgents.REVIEW_AGENT.value
        and final_state is not None
    ):
        interrupt = detect_interrupt(final_state, run_id)
        if interrupt is not None:
            decision = await elicit_review_decision(
                session, interrupt["draft"]
            )
            final_state = await aresume_graph(graph_app, run_id, decision)
    return await _stdio_terminal_payload(tool_name, final_state)


def _chunk_content_delta(chunk: Mapping[str, Any]) -> str:
    """Return the assistant content delta from one provider chunk."""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) else ""


async def _stream_chat_agent(
    args: ChatAgent,
    *,
    conversation_messages: Sequence[Mapping[str, str]] = (),
) -> AsyncIterator[dict[str, Any]]:
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
    chat_config, runtime = load_chat_runtime()
    call_kwargs = chat_call_kwargs(
        args, scratch_server_dir(chat_config, "chat"), chat_config, runtime
    )
    call_kwargs["conversation_messages"] = conversation_messages
    provider_chunks = stream_phyto_chat_chunks(**call_kwargs)
    try:
        async for chunk in provider_chunks:
            yield chunk
    finally:
        await _close_async_iterator(provider_chunks)


async def dispatch_tool(
    name: Any, arguments: dict[str, Any]
) -> list[TextContent]:
    """Validate arguments, call a tool handler, and serialize the result.

    When the MCP client supplied a ``progressToken`` and the tool is a
    graph agent, the call is driven through ``_drive_stdio_progress`` so
    the client receives in-band ``notifications/progress`` during the
    run. ReviewAgent also uses that path whenever an MCP session exists,
    even without a token, so approval interrupts can elicit or degrade.
    Every other call takes the blocking ``invoke_tool_enveloped`` path.

    Args:
        name: Raw MCP tool name supplied by the client.
        arguments: JSON object passed to the selected MCP tool.

    Returns:
        MCP text content containing the serialized formatted result.

    Raises:
        McpError: If the tool is unknown or arguments fail validation.
    """
    tool_name = _tool_name(name)
    token, session, run_id = _stdio_progress_context()
    should_drive_graph = (
        token is not None and tool_name in _GRAPH_PROGRESS_TOOLS
    )
    should_drive_review = (
        tool_name == PhytomniAgents.REVIEW_AGENT.value and session is not None
    )
    if should_drive_graph or should_drive_review:
        model = TOOL_ARGUMENT_MODELS.get(tool_name)
        if model is None:
            raise _invalid_params(f"Unknown tool: {tool_name}")
        try:
            model(**arguments)
        except ValidationError as exc:
            raise _invalid_params(
                _format_validation_error(tool_name, exc)
            ) from exc
        return await _drive_stdio_progress(
            tool_name,
            arguments,
            progress_token=token,
            session=session,
            run_id=run_id,
        )
    envelope = await invoke_tool_enveloped(name, arguments)
    payload: dict[str, Any] = {
        "formatted": asdict(envelope.formatted),
        "execution": asdict(envelope.execution),
    }
    if resolve_debug(None):
        payload["raw"] = envelope.raw
    return _text_response(payload)


async def serve() -> None:
    """Initialize and run the Phytomni MCP stdio server.

    Configures package-level logging, builds a ``Server("Phytomni-Server")``
    instance, wires the ``list_tools`` and ``call_tool`` handlers, owns the
    process-wide outbound runtime (initialised before stdio comes up,
    closed in ``finally`` so a stdio crash never leaks resources), and
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
    validate_citation_database()
    server = Server("Phytomni-Server")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Return public MCP tool definitions.

        Returns:
            Tool metadata and JSON schemas for every registered Phytomni tool.
        """
        tools = [
            Tool(
                name=name,
                description=description,
                inputSchema=model.model_json_schema(),
            )
            for name, description, model in AGENT_TOOL_DEFINITIONS
        ]
        tools.append(
            Tool(
                name=PhytomniAgents.GET_TASK_STATUS,
                description=PhytomniAgents.GET_TASK_STATUS_DESCRIPTION,
                inputSchema=GetTaskStatus.model_json_schema(),
            )
        )
        return tools

    @server.call_tool()
    async def call_tool(name, arguments: dict[str, Any]) -> list[TextContent]:
        """Dispatch one MCP tool call.

        Args:
            name: Raw tool name supplied by the MCP client.
            arguments: JSON object supplied by the MCP client.

        Returns:
            Serialized MCP text response from `dispatch_tool`.
        """
        return await dispatch_tool(name, arguments)

    options = server.create_initialization_options()
    await init_outbound_runtime()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, options, raise_exceptions=True
            )
    finally:
        await aclose_outbound_runtime()
        await aclose_gauss_pool()
