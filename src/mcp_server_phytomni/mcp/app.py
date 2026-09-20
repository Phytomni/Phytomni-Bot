# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Register and dispatch MCP schemas through domain tool handlers."""

import asyncio
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import asdict, dataclass
from json import dumps
from typing import (
    Any,
    NotRequired,
    TypedDict,
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
from ..public_agent_catalog import PUBLIC_AGENT_CATALOG
from ..runtime.execution_entrypoint_v2 import (
    invoke_public_agent,
    invoke_public_agent_stream_response,
)
from ..runtime.execution_event_sink import (
    emit_execution_event,
    event_intent,
    set_todos,
)
from ..runtime.execution_identity_v2 import new_execution_id
from ..runtime.execution_instrumentation_v2 import instrument_tool_invocation
from ..runtime.execution_journal_v2 import ExecutionStatus
from ..runtime.langgraph_runner import build_runnable_config, stream_graph
from ..runtime.outbound import (
    aclose_outbound_runtime,
    init_outbound_runtime,
)
from ..runtime.request_context import current_request_id, current_request_user
from ..runtime.resume import (
    aresume_graph,
    detect_interrupt,
    elicit_review_decision,
)
from ..runtime.task_manager import resolve_tasks_db_path
from ..storage.path_policy import IdFactory
from . import handlers as _handlers
from . import schemas as _schemas
from .handler_support import (
    chat_call_kwargs,
    load_chat_runtime,
)
from .handlers import (
    handle_get_task_status,
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
    BriefGeneAgent,
    ChatAgent,
    DataAgent,
    GetTaskStatus,
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
from .streaming_phases import (
    iterate_owned,
    phase_for,
    todo_snapshot_for_phase,
)

ToolHandler = Callable[[Any], Awaitable[Any]]


class _ToolInvocationOptions(TypedDict):
    runtime_arguments: NotRequired[dict[str, Any] | None]
    conversation_messages: NotRequired[Sequence[Mapping[str, str]]]
    agent_thread_id: NotRequired[str | None]
    private_agent_state: NotRequired[Mapping[str, Any] | None]
    execution_id: NotRequired[str | None]
    transport: NotRequired[str | None]
    db_path: NotRequired[str | None]
    fingerprint_version: NotRequired[int]
    fingerprint: NotRequired[str | None]


_TOOL_INVOCATION_OPTION_NAMES = frozenset(
    _ToolInvocationOptions.__annotations__
)


@dataclass(slots=True)
class _StreamCarrier:
    """AG-UI stream plus its typed terminal observation for Runtime."""

    body_iterator: AsyncIterator[AguiEvent]
    answer: str = ""
    terminal_status: str | None = None

    def runtime_terminal_ready(self) -> bool:
        """Return whether the stream observed a terminal Runtime status."""
        return self.terminal_status is not None

    def runtime_terminal_status(self) -> str:
        """Return the observed status or a fail-closed fallback."""
        return self.terminal_status or "failed"

    def runtime_terminal_result(self) -> dict[str, Any]:
        """Build the transport-neutral terminal result from streamed data."""
        return {
            "formatted": {"answer": self.answer},
            "stream": True,
            "partial": self.terminal_status != "succeeded",
        }


def _tracked_stream_carrier(
    raw_events: AsyncIterator[AguiEvent],
) -> _StreamCarrier:
    """Accumulate one MCP/A2A stream without adding another run writer."""
    carrier = _StreamCarrier(raw_events)

    async def tracked() -> AsyncIterator[AguiEvent]:
        async for event in raw_events:
            if event.type == "TextMessageContent":
                content = event.data.get("delta", event.data.get("content"))
                if isinstance(content, str):
                    carrier.answer += content
            elif event.type == "RunError":
                carrier.terminal_status = "failed"
            elif event.type == "RunFinished":
                carrier.terminal_status = "succeeded"
            yield event

    carrier.body_iterator = tracked()
    return carrier


TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    item.tool: cast(type[BaseModel], getattr(_schemas, item.schema))
    for item in PUBLIC_AGENT_CATALOG
}
TOOL_ARGUMENT_MODELS[PhytomniAgents.GET_TASK_STATUS.value] = GetTaskStatus

_PUBLIC_AGENT_BY_TOOL = {item.tool: item for item in PUBLIC_AGENT_CATALOG}

TOOL_HANDLERS: dict[str, ToolHandler] = {
    item.tool: cast(ToolHandler, getattr(_handlers, item.handler))
    for item in PUBLIC_AGENT_CATALOG
}
TOOL_HANDLERS[PhytomniAgents.GET_TASK_STATUS.value] = handle_get_task_status


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


def _validate_tool_invocation_options(
    options: Mapping[str, object],
) -> None:
    unexpected = set(options).difference(_TOOL_INVOCATION_OPTION_NAMES)
    if unexpected:
        name = min(unexpected)
        raise TypeError(
            "invoke_tool_raw() got an unexpected keyword argument " f"'{name}'"
        )


def _runtime_tool_arguments(
    options: _ToolInvocationOptions,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    runtime_arguments = options.get("runtime_arguments")
    return arguments if runtime_arguments is None else runtime_arguments


async def invoke_tool_raw(
    name: Any,
    arguments: dict[str, Any],
    **options: Unpack[_ToolInvocationOptions],
) -> Any:
    """Validate arguments and invoke one handler through Runtime V2."""
    _validate_tool_invocation_options(options)
    tool_name = _tool_name(name)
    messages_token = set_private_conversation_messages(
        options.get("conversation_messages", ())
    )
    thread_token = set_private_agent_thread_id(options.get("agent_thread_id"))
    state_token = set_private_agent_state(options.get("private_agent_state"))
    call_id = IdFactory().new_id("tool-call")
    started = time.perf_counter()
    try:

        async def call_handler() -> Any:
            args = validate_tool_arguments(tool_name, arguments)
            emit_execution_event(
                event_intent(
                    "tool.started",
                    status="running",
                    payload={"tool_key": tool_name, "call_id": call_id},
                )
            )
            try:
                result = await instrument_tool_invocation(
                    tool_name,
                    lambda: TOOL_HANDLERS[tool_name](args),
                )
            except BaseException as exc:
                duration_ms = max(
                    0, int((time.perf_counter() - started) * 1000)
                )
                cancelled = isinstance(exc, asyncio.CancelledError)
                emit_execution_event(
                    event_intent(
                        "tool.failed",
                        status="cancelled" if cancelled else "failed",
                        payload={
                            "tool_key": tool_name,
                            "call_id": call_id,
                            "duration_ms": duration_ms,
                            "code": (
                                "tool_cancelled"
                                if cancelled
                                else "tool_execution_failed"
                            ),
                        },
                    )
                )
                raise
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            emit_execution_event(
                event_intent(
                    "tool.completed",
                    status="succeeded",
                    payload={
                        "tool_key": tool_name,
                        "call_id": call_id,
                        "duration_ms": duration_ms,
                    },
                )
            )
            return result

        spec = _PUBLIC_AGENT_BY_TOOL.get(tool_name)
        if spec is None:
            return await call_handler()
        return await invoke_public_agent(
            db_path=options.get("db_path") or resolve_tasks_db_path(),
            owner=current_request_user() or "mcp-local",
            execution_id=options.get("execution_id") or new_execution_id(),
            agent_slug=spec.slug,
            arguments=_runtime_tool_arguments(options, arguments),
            transport=(
                options.get("transport")
                or ("authenticated_http" if current_request_user() else "mcp")
            ),
            call=call_handler,
            status_mapper=(
                (lambda _value: ExecutionStatus.RUNNING)
                if spec.lifecycle == "asynchronous"
                else None
            ),
            public_result_mapper=lambda value: {
                "result": {
                    "formatted": asdict(
                        format_tool_result(
                            tool_name,
                            value,
                            arguments=arguments,
                        )
                    )
                }
            },
            fingerprint_version=options.get("fingerprint_version", 1),
            fingerprint=options.get("fingerprint"),
        )
    finally:
        reset_private_agent_state(state_token)
        reset_private_agent_thread_id(thread_token)
        reset_private_conversation_messages(messages_token)


async def invoke_tool_formatted(
    name: Any, arguments: dict[str, Any]
) -> FormattedToolResult:
    """Invoke and format one tool through the shared envelope seam."""
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
    **options: Unpack[_ToolInvocationOptions],
) -> ToolResultEnvelope:
    """Return raw and formatted results for one validated invocation."""
    raw = await invoke_tool_raw(name, arguments, **options)
    await _maybe_enrich_cited(_tool_name(name), raw)
    return build_tool_result_envelope(
        _tool_name(name), raw, arguments=arguments
    )


async def _maybe_enrich_cited(tool_name: str, raw: Any) -> None:
    """Enrich cited document rows when the payload exposes them."""
    if not is_cited_tool(tool_name):
        return
    docs = _raw_doc_list(raw)
    if docs:
        await enrich_cited_doc_list(docs)


def _raw_doc_list(raw: Any) -> list[dict[str, Any]]:
    """Return live document dicts from a cited response payload."""
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
    """Validate a tool and eagerly prepare its raw AG-UI iterator."""
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
    """Return an eagerly prepared, lifecycle-projected AG-UI stream."""
    raw_events = prepare_tool_stream(
        name,
        arguments,
        run_id=run_id,
        dialogue_id=dialogue_id,
    )
    tool_name = _tool_name(name)
    spec = _PUBLIC_AGENT_BY_TOOL.get(tool_name)

    async def runtime_events() -> AsyncIterator[AguiEvent]:
        if spec is None:
            async for event in raw_events:
                yield event
            return

        async def carry(_runtime_run_id: str) -> _StreamCarrier:
            return _tracked_stream_carrier(raw_events)

        carrier = await invoke_public_agent_stream_response(
            db_path=resolve_tasks_db_path(),
            owner=current_request_user() or "mcp-local",
            execution_id=f"turn-a2a-{run_id}",
            agent_slug=spec.slug,
            arguments=arguments,
            transport="mcp_stream",
            call=carry,
            run_id=run_id,
        )
        async for event in carrier.body_iterator:
            yield event

    return project_stream_failures(
        runtime_events(),
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
    """Build the cached graph and seed state for one graph tool."""
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
    """Project a final graph state into terminal AG-UI frames."""
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
    """Drive a graph and emit progress, phase, and terminal events."""
    yield run_started(run_meta["run_id"], run_meta["dialogue_id"])
    declared_todos = todo_snapshot_for_phase(agent_name, None)
    if declared_todos:
        set_todos(declared_todos)
    seen_phases: set[str] = set()
    final_state: Mapping[str, Any] | None = None
    graph_events = stream_graph(
        app,
        initial_state,
        stream_mode=["custom", "updates", "values"],
        subgraphs=True,
        config=build_runnable_config(run_meta["run_id"]),
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
                        emit_execution_event(
                            event_intent(
                                "phase.started",
                                status="running",
                                payload={
                                    "phase": phase,
                                    "label_key": f"phase.{phase}",
                                },
                            )
                        )
                        set_todos(todo_snapshot_for_phase(agent_name, phase))
                        yield step_started(phase)
            elif mode == "values" and ns == ():
                final_state = chunk
    finally:
        await _close_async_iterator(graph_events)
    async for event in _terminal_graph_events(tool_name, final_state):
        yield event
    if declared_todos:
        set_todos(todo_snapshot_for_phase(agent_name, None, completed=True))
    yield run_finished(run_meta["run_id"])


def _stdio_progress_context() -> tuple[str | int | None, Any, str]:
    """Read the progress token, session, and run id for stdio dispatch."""
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
    """Return the graph and seed state for a progress-capable tool."""
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
    """Yield graph progress ticks while capturing the final state."""
    async for ns, mode, chunk in stream_graph(
        app,
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
    """Format a captured graph state into the stdio response."""
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
    """Drive one graph tool and forward stdio progress notifications."""
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
    """Stream provider chat chunks with the standard handler wiring."""
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
    """Dispatch one MCP tool and serialize its progress-aware result."""
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
    """Run the stdio MCP server and own its outbound runtime lifecycle."""
    configure_logging()
    validate_citation_database()
    server = Server("Phytomni-Server")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Return every public MCP tool definition."""
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
        """Dispatch one MCP tool call."""
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
