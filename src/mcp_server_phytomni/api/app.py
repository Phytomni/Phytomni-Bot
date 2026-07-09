# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI application factory for the external HTTP API.

Public functions: create_app.
"""

# pylint: disable=too-many-lines
# C0302: FastAPI app factory + every route registration lives in one
# file. Splitting per-route modules makes dependency wiring opaque
# without reducing total complexity. See docs/development/lint-exemptions.md.

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
)
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..agents.brief_gene.resolve_query import (
    BriefGeneResolveError,
    resolve_brief_gene_user_query,
)
from ..agents.deep_genome.resolve_query import (
    DeepGenomeResolveError,
    DeepGenomeResolveResult,
    resolve_deep_genome_user_query,
)
from ..agents.design.resolve_query import (
    DigitalDesignResolveError,
    DigitalDesignResolveResult,
    resolve_design_user_query,
)
from ..agents.expert import select_agent_tool
from ..agents.network.resolve_query import (
    GeneNetworkResolveError,
    GeneNetworkResolveResult,
    resolve_network_user_query,
)
from ..agents.review.agent import review_stream_target
from ..agents.shared.gauss import aclose_gauss_pool
from ..agents.shared.intermediate_state import merge_intermediate_state
from ..common.httpx_client import aclose_shared_client, init_shared_client
from ..common.logging_config import configure_logging
from ..config.defaults import (
    ApiConfig,
    BriefGeneConfig,
    DeepGenomeConfig,
    DigitalDesignConfig,
    GeneNetworkConfig,
)
from ..config.settings import SensitiveConfig
from ..mcp.app import invoke_tool_enveloped, invoke_tool_streamed
from ..mcp.result_formatting import (
    build_tool_result_envelope,
    resolve_debug,
    strip_agent_result,
    strip_chat_completion,
)
from ..mcp.schemas import ReviewAgent as ReviewAgentArgs
from ..runtime.langgraph_runner import build_runnable_config
from ..runtime.request_context import (
    bind_request_id,
    bind_request_user,
    bind_run_id,
    current_recorder_degraded,
    current_request_id,
    current_request_user,
    current_run_id,
    reset_request_var,
)
from ..runtime.resume import NoCheckpointError, aresume_graph, detect_interrupt
from ..runtime.run_registry import (
    RunFilter,
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)
from ..runtime.task_manager import resolve_tasks_db_path
from ..runtime.task_reconcile import reconcile_task_log
from ..storage.path_policy import IdFactory
from .admin_auth import is_service_token_valid, require_service_principal
from .auth import (
    ApiPrincipal,
    get_key_store,
    require_principal,
    scopes_satisfy,
)
from .file_upload import handle_file_upload
from .openai_mapping import (
    MODEL_TO_TOOL,
    flatten_messages,
    to_chat_completion,
    to_chat_completion_chunks,
    tool_accepts_obs,
    tool_accepts_resolve_gene_id,
    tool_accepts_stream,
    tool_for_model,
)
from .ratelimit import make_rate_limiter
from .relay import RelayAuditQuery, create_relay_router, get_audit_store
from .schemas import (
    AgentRunRequest,
    ApiErrorDetail,
    ApiErrorResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyDeleteResponse,
    ApiKeyListResponse,
    ApiKeyRecordResponse,
    ChatCompletionRequest,
    ExpertQueryRequest,
    FileUploadResponse,
    ResumeRequest,
    UploadPurpose,
)
from .stream_answer import (
    StreamAnswerAccumulator,
    resolve_stream_answer_max_bytes,
)

__all__ = ["create_app"]

_ERROR_TYPES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "unprocessable_entity",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}

# Public agent slug recorded on the ``runs`` row for sync chat calls.
# Kept here (not in ``openai_mapping``) so this run-registry concern
# stays inside the API layer and does not perturb the pure mapping
# module that other API touch points already share.
_MODEL_TO_AGENT_SLUG = {
    "phyto-chat": "chat",
    "phyto-knowledge": "knowledge",
    "phyto-review": "review",
    "phyto-brief-gene": "brief_gene",
}

# Full ``slug -> MCP tool name`` map for the native ``/v1/agents``
# endpoints. Slugs mirror the ``agents/<domain>/`` directory naming
# so the run table speaks the same vocabulary as the in-process agent
# packages.
_AGENT_SLUG_TO_TOOL = {
    "chat": "ChatAgent",
    "knowledge": "KnowledgeAgent",
    "data": "DataAgent",
    "review": "ReviewAgent",
    "brief_gene": "BriefGeneAgent",
    "analyst": "AnalystAgent",
    "deep_genome": "DeepGenomeAgent",
    "research": "InSilicoResearchAgent",
    "design": "DigitalDesignAgent",
    "network": "GeneNetworkAgent",
}

# Inverse of ``_AGENT_SLUG_TO_TOOL``: the Expert router returns the MCP
# tool name the LLM selected, which this maps back to the public agent
# slug ``_invoke_agent_run`` dispatches on. Derived from the forward map
# so the two cannot drift.
_TOOL_TO_AGENT_SLUG = {
    tool: slug for slug, tool in _AGENT_SLUG_TO_TOOL.items()
}

# Slugs whose handlers submit a remote analysis task and rely on the
# ``records_submission`` chokepoint in ``runtime.submit_recorder`` to
# write the runs row with ``origin="remote"`` and bind the freshly-minted
# run id to ``current_run_id()`` so the API layer can recover it directly.
_REMOTE_AGENT_SLUGS = frozenset(
    {"analyst", "deep_genome", "research", "design", "network"}
)

# Historical Web ``tool_name`` aliases preserved on ``/v1/agents`` rows
# as ``legacy_aliases`` metadata. The route itself never accepts these
# as routing slugs; chat-ai and Phytomni-Web Go consume the list to
# build their own alias→slug translation table without out-of-band
# negotiation. Bot-added agents (brief_gene / design / network) ship
# an empty list so the shape stays uniform and a future agent must
# make an explicit declaration rather than silently inherit ``[]``.
_LEGACY_ALIASES: dict[str, list[str]] = {
    "ChatAgent": ["ChatAgent"],
    "KnowledgeAgent": ["KnowledgeAgent", "KnowledgeAgents"],
    "DataAgent": ["DataAgent", "DatabaseAgents"],
    "ReviewAgent": ["ReviewAgent", "ReviewAgents"],
    "BriefGeneAgent": [],
    "AnalystAgent": ["AnalystAgent", "AnalysisAgents"],
    "DeepGenomeAgent": ["DeepGenomeAgent"],
    "InSilicoResearchAgent": ["InSilicoResearchAgent"],
    "DigitalDesignAgent": [],
    "GeneNetworkAgent": [],
}


_LOGGER = logging.getLogger(__name__)


def _purge_expired_runs_best_effort() -> None:
    """Run a single ``RunRegistry.purge_expired`` pass, swallowing errors.

    Every API write path (sync chat completions, native agent runs,
    and the registry listing) drives the lazy GC through this helper
    so an expired row never outlives its TTL. A SQLite / OS
    failure must never propagate — the user-facing write already
    succeeded and the next request can re-trigger the purge — but
    the failure does emit a sanitized ``warning`` log so ops can
    notice a stuck GC. Only the exception class name is logged;
    the message is dropped to avoid leaking on-disk paths or SQL
    fragments that might appear in pysqlite error strings.
    """
    try:
        RunRegistry(resolve_tasks_db_path()).purge_expired()
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning("run TTL purge failed: %s", exc.__class__.__name__)


def _schedule_run_gc(background: BackgroundTasks) -> None:
    """Schedule the run-registry GC to run after the response flushes.

    FastAPI resolves BackgroundTasks by dependency injection, so a
    write route declares this dependency instead of blocking its
    response on the SQLite DELETE scan. The purge stays best-effort and
    idempotent, so running it once per request (deduping the former
    per-helper inline calls) carries no data risk.
    """
    background.add_task(_purge_expired_runs_best_effort)


def _extract_answer(result: Any) -> str | None:
    """Pull a display-ready answer string from a stored run result.

    Tolerates the two envelope shapes the API writes today: the chat /
    agent-run path stores ``{"formatted": {"answer": ...}, "raw": ...}``
    while older sync writers may carry a top-level ``answer``. Returns
    ``None`` when neither shape carries a string answer so chat-ai can
    render an "ongoing" placeholder without crashing.
    """
    if not isinstance(result, dict):
        return None
    formatted = result.get("formatted")
    if isinstance(formatted, dict):
        candidate = formatted.get("answer")
        if isinstance(candidate, str):
            return candidate
    candidate = result.get("answer")
    if isinstance(candidate, str):
        return candidate
    return None


def _run_record_to_dict(record: Any) -> dict[str, Any]:
    """Flatten a ``RunRecord`` into the JSON envelope the API returns.

    Unpacks ``spec`` (identity bundle), ``timestamps`` (lifecycle
    bundle), and ``request_info`` (per-request metadata bundle) so the
    on-wire shape stays a flat object rather than the nested dataclass
    tree, and serialises ``task_ids`` as a list so clients consume it
    as a JSON array. The ``answer`` shortcut surfaces the formatted
    response text directly so chat-ai's history page does not have to
    descend into ``result.formatted.answer`` per row.

    Args:
        record: The ``RunRegistry`` record to flatten.

    Returns:
        A JSON-serialisable dict.
    """
    info = record.request_info
    return {
        "run_id": record.spec.run_id,
        "agent": record.spec.agent,
        "origin": record.spec.origin,
        "user_id": record.spec.user_id,
        "status": record.status,
        "result": record.result,
        "error": record.error,
        "created_at": record.timestamps.created_at,
        "updated_at": record.timestamps.updated_at,
        "expires_at": record.timestamps.expires_at,
        "task_ids": list(record.task_ids),
        "dialogue_id": info.dialogue_id,
        "query": info.query,
        "tool_name": info.tool_name,
        "model": info.model,
        "answer": _extract_answer(record.result),
    }


def _stream_chat_completion(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Wrap ``invoke_tool_streamed`` + SSE shaper + two-stage run write.

    The wrapper is an async generator: each emitted SSE line forwards
    immediately to the client (no buffering). The run row is written
    twice: a ``running`` row before the first frame so ``RunStarted``
    carries a real, persisted registry id, then a terminal
    ``succeeded``/``failed`` settle from the ``finally`` block keyed
    on whether the stream actually reached ``RunFinished``. A client
    that disconnects right after ``RunFinished`` still settles
    succeeded — the answer was produced regardless of whether the
    socket stayed open to see it. ChatAgent settle persists the
    accumulated answer under a soft byte cap; other streamed agents keep
    stream-mode placeholder markers.

    Auth, rate-limit, request-id, and OBS argument prep all happen
    before this helper is called, mirroring the non-stream branch.
    """
    agent_slug = _MODEL_TO_AGENT_SLUG.get(payload.model)
    owner = current_request_user() or "anonymous"
    run_id = IdFactory().new_id("run", agent_slug or "chat")
    request_info = RunRequestInfo(
        dialogue_id=payload.dialogue_id,
        query=user_query,
        tool_name=tool_name,
        model=payload.model,
        request_json=payload.model_dump_json(),
    )
    # Stage 1: pre-mint + write running so RunStarted carries the real
    # registry id instead of an unpersisted placeholder.
    if agent_slug is not None:
        _create_running_stream_run(run_id, agent_slug, owner, request_info)
    events = invoke_tool_streamed(
        tool_name,
        arguments,
        run_id=run_id,
        dialogue_id=payload.dialogue_id,
    )
    accumulator: StreamAnswerAccumulator | None = None
    if tool_name == "ChatAgent":
        accumulator = StreamAnswerAccumulator(
            events,
            max_bytes=_stream_answer_max_bytes(),
        )
        events = accumulator
    sse_lines = to_chat_completion_chunks(events, payload.model)

    async def _wrapped() -> AsyncIterator[str]:
        """Forward each SSE line, then settle the run from ``finally``."""
        reached_finish = False
        try:
            async for line in sse_lines:
                if "event: RunFinished\n" in line:
                    reached_finish = True
                yield line
        finally:
            # Stage 2: settle terminal keyed on reaching RunFinished,
            # not on connection close.
            if agent_slug is not None:
                status = "succeeded" if reached_finish else "failed"
                if accumulator is not None:
                    snap = accumulator.snapshot
                    result: dict[str, Any] = {
                        "formatted": {"answer": snap.answer},
                        "raw": None,
                        "stream": True,
                        "truncated": snap.truncated,
                        "partial": status == "failed",
                    }
                else:
                    result = {
                        "formatted": {"answer": "[streamed]"},
                        "raw": None,
                        "stream": True,
                    }
                _settle_stream_run(run_id, owner, status, result)

    return StreamingResponse(_wrapped(), media_type="text/event-stream")


async def _maybe_resolve_brief_gene_query(
    *,
    raw_query: str,
    resolve_flag: bool,
    tool_name: str | None,
    agent_slug: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve free-form text into a gene id when ``resolve_gene_id`` is on.

    Both the OpenAI-compatible chat-completions route and the native
    ``/v1/agents/brief_gene/runs`` route funnel through here so the
    flag has identical semantics on both surfaces: BriefGene-only,
    explicit 400 on misuse, explicit 400 on resolver failure, and a
    deterministic metadata patch describing the rewrite.

    Args:
        raw_query: The original ``user_query`` text from the request.
        resolve_flag: Whether the caller opted into LLM preprocessing.
        tool_name: MCP tool name when the chat-completions path resolved
            it; ``None`` for the native runs path which gates on slug.
        agent_slug: Native agent slug when the runs path supplied one;
            ``None`` for the chat-completions path which gates on tool.

    Returns:
        Tuple of ``(user_query_for_tool, metadata_patch)``. The metadata
        patch is empty when the flag is off and otherwise carries the
        ``original_query`` / ``resolved_gene_id`` / ``resolved_species_code``
        / ``resolve_gene_id`` keys for caller observability.

    Raises:
        HTTPException: 400 when the flag is set on a non-BriefGene
            tool/slug or the resolver raises ``BriefGeneResolveError``.
    """
    if not resolve_flag:
        return raw_query, {}
    brief_tool = (
        tool_name == "BriefGeneAgent"
        and tool_accepts_resolve_gene_id(tool_name)
    )
    brief_slug = agent_slug == "brief_gene"
    if not (brief_tool or brief_slug):
        raise HTTPException(
            status_code=400,
            detail="resolve_gene_id is only valid for BriefGene calls",
        )
    try:
        result = await resolve_brief_gene_user_query(
            raw_query,
            brief_config=BriefGeneConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except BriefGeneResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.gene_id, {
        "original_query": raw_query,
        "resolved_gene_id": result.gene_id,
        "resolved_species_code": result.species_code,
        "resolve_gene_id": True,
    }


async def _maybe_resolve_deep_genome_query(
    *,
    raw_query: str,
    resolve_flag: bool,
    agent_slug: str | None = None,
) -> tuple[DeepGenomeResolveResult | None, dict[str, Any]]:
    """Mirror of ``_maybe_resolve_brief_gene_query`` for deep_genome.

    Sibling helper kept per-domain so each agent's 400-on-misuse
    string names the agent explicitly and the resolver call site
    closes over the deep_genome-typed result + error class. Returns
    the typed result (or ``None`` when ``resolve_flag`` is off) so
    the caller can inject both ``gene_id`` and ``species_code`` into
    the downstream agent arguments.
    """
    if not resolve_flag:
        return None, {}
    if agent_slug != "deep_genome":
        raise HTTPException(
            status_code=400,
            detail="resolve_gene_id is only valid for DeepGenome calls",
        )
    try:
        result = await resolve_deep_genome_user_query(
            raw_query,
            deep_genome_config=DeepGenomeConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except DeepGenomeResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result, {
        "original_query": raw_query,
        "resolved_gene_id": result.gene_id,
        "resolved_species_code": result.species_code,
        "resolve_gene_id": True,
    }


async def _maybe_resolve_design_query(
    *,
    raw_query: str,
    resolve_flag: bool,
    agent_slug: str | None = None,
) -> tuple[DigitalDesignResolveResult | None, dict[str, Any]]:
    """Mirror of ``_maybe_resolve_brief_gene_query`` for design.

    Returns the typed result (or ``None`` when ``resolve_flag`` is
    off) so the caller can inject both ``gene_id`` and
    ``species_code`` into the DigitalDesignAgent arguments.
    """
    if not resolve_flag:
        return None, {}
    if agent_slug != "design":
        raise HTTPException(
            status_code=400,
            detail=("resolve_gene_id is only valid for DigitalDesign calls"),
        )
    try:
        result = await resolve_design_user_query(
            raw_query,
            design_config=DigitalDesignConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except DigitalDesignResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result, {
        "original_query": raw_query,
        "resolved_gene_id": result.gene_id,
        "resolved_species_code": result.species_code,
        "resolve_gene_id": True,
    }


async def _apply_runs_resolver(
    agent: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Run the right resolver for ``/v1/agents/{agent}/runs`` calls.

    Pops the ``resolve_gene_id`` / ``resolve_to_id`` flags and the
    transient ``user_query`` field from ``arguments`` before forwarding
    so the per-agent Pydantic schema never sees them. When a flag is
    set, dispatches to the matching ``_maybe_resolve_<domain>_query``
    helper, injects the resolved id into the agent-specific target
    field (``user_query`` for BriefGene, ``gene_id`` for deep_genome /
    design, ``to_id`` for network), and returns the metadata patch the
    caller layers onto ``formatted.metadata``.
    """
    flag_gene_id = bool(arguments.pop("resolve_gene_id", False))
    flag_to_id = bool(arguments.pop("resolve_to_id", False))
    if not (flag_gene_id or flag_to_id):
        # ``user_query`` is otherwise part of the brief_gene schema;
        # only pop it when both flags are off AND the agent does not
        # consume it directly, so we leave brief_gene's structured
        # ``user_query`` untouched on the no-resolve path.
        return {}
    raw_query = arguments.pop("user_query", None)
    if not isinstance(raw_query, str) or not raw_query.strip():
        flag_label = "resolve_to_id" if flag_to_id else "resolve_gene_id"
        raise HTTPException(
            status_code=400,
            detail=f"user_query is required when {flag_label} is true",
        )
    if flag_to_id and agent != "network":
        raise HTTPException(
            status_code=400,
            detail="resolve_to_id is only valid for GeneNetwork calls",
        )
    if flag_to_id:
        network_result, meta = await _maybe_resolve_network_query(
            raw_query=raw_query,
            resolve_flag=True,
            agent_slug=agent,
        )
        assert network_result is not None
        arguments["to_id"] = network_result.to_id
        arguments["species_code"] = network_result.species_code
        return meta
    # flag_gene_id branch: dispatch by agent slug to the matching
    # gene-id resolver and inject into the agent-shaped target field.
    if agent == "brief_gene":
        resolved, meta = await _maybe_resolve_brief_gene_query(
            raw_query=raw_query,
            resolve_flag=True,
            tool_name=None,
            agent_slug=agent,
        )
        arguments["user_query"] = resolved
        return meta
    if agent == "deep_genome":
        deep_genome_result, meta = await _maybe_resolve_deep_genome_query(
            raw_query=raw_query,
            resolve_flag=True,
            agent_slug=agent,
        )
        assert deep_genome_result is not None
        arguments["gene_id"] = deep_genome_result.gene_id
        arguments["species_code"] = deep_genome_result.species_code
        return meta
    if agent == "design":
        design_result, meta = await _maybe_resolve_design_query(
            raw_query=raw_query,
            resolve_flag=True,
            agent_slug=agent,
        )
        assert design_result is not None
        arguments["gene_id"] = design_result.gene_id
        arguments["species_code"] = design_result.species_code
        return meta
    raise HTTPException(
        status_code=400,
        detail=(
            "resolve_gene_id is only valid for BriefGene / DeepGenome / "
            f"DigitalDesign calls (received agent {agent!r})"
        ),
    )


async def _maybe_resolve_network_query(
    *,
    raw_query: str,
    resolve_flag: bool,
    agent_slug: str | None = None,
) -> tuple[GeneNetworkResolveResult | None, dict[str, Any]]:
    """Mirror of the gene-id resolvers but for GeneNetwork's TO id.

    The flag, target field, and metadata key all use ``to_id`` rather
    than ``gene_id`` because the GeneNetwork tool dispatches on a
    Trait Ontology identifier; the resolver itself injects the
    committed TO catalog into the LLM prompt and validates the
    returned id against that catalog. Returns the typed result (or
    ``None`` when ``resolve_flag`` is off) so the caller can inject
    both ``to_id`` and ``species_code`` into the GeneNetworkAgent
    arguments.
    """
    if not resolve_flag:
        return None, {}
    if agent_slug != "network":
        raise HTTPException(
            status_code=400,
            detail="resolve_to_id is only valid for GeneNetwork calls",
        )
    try:
        result = await resolve_network_user_query(
            raw_query,
            network_config=GeneNetworkConfig(),
            sensitive_config=SensitiveConfig.load(),
        )
    except GeneNetworkResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result, {
        "original_query": raw_query,
        "resolved_to_id": result.to_id,
        "resolved_species_code": result.species_code,
        "resolve_to_id": True,
    }


# pylint: disable=too-many-locals
# Request validation -> DB lookup -> reconciliation -> response
# assembly inline; helpers would require 5+ context args each.
# See docs/development/lint-exemptions.md.
async def _invoke_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    dialogue_id: str | None = None,
    request_json: str | None = None,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Dispatch one ``/v1/agents/{agent}/runs`` call and shape the body.

    Owns the slug -> tool lookup, the shared ``invoke_tool_formatted``
    call, and the origin-aware run id resolution. Returns the
    ``agent.run`` envelope: sync agents get ``status="succeeded"`` at
    HTTP 200, remote agents get ``status="running"`` plus the child
    ``task_ids`` at HTTP 202 (the submission ack convention) so a
    client can immediately poll ``/v1/runs/{id}`` for the live status.
    The formatted result is surfaced in both cases — sync clients
    consume it directly; remote clients can read the raw payload for
    additional context but should track the run by ``id`` and
    ``task_ids`` since those are uniformly populated for every
    remote agent. Analysis dedup hits also return the caller's own
    run id and fresh task id at 202 (the reuse mints a caller-owned
    row recorded through the normal chokepoint path); the only case
    where ``id=null`` / ``task_ids=[]`` is returned is when the
    local registry write fails and ``degraded_tracking: True`` is
    added to the body.

    Args:
        agent: Public agent alias (e.g. ``"chat"``).
        arguments: Tool-specific kwargs forwarded to the agent.
        debug: When True, include the raw handler payload in the
            result block. Default strips it to reduce response volume.

    Returns:
        ``(body, status_code)`` — ``body`` is the ``agent.run``
        envelope (``id`` / ``object`` / ``agent`` / ``status`` /
        ``task_ids`` / ``result``), ``status_code`` is 202 for remote
        submissions and 200 for synchronous completions.

    Raises:
        HTTPException: 404 when the slug is unknown.
    """
    tool_name = _AGENT_SLUG_TO_TOOL.get(agent)
    if tool_name is None:
        raise HTTPException(
            status_code=404, detail=f"agent not found: {agent}"
        )
    resolve_meta = await _apply_runs_resolver(agent, arguments)
    owner = current_request_user() or "anonymous"
    request_info = RunRequestInfo(
        dialogue_id=dialogue_id,
        query=(
            arguments.get("user_query")
            if isinstance(arguments.get("user_query"), str)
            else None
        ),
        tool_name=tool_name,
        model=None,
        request_json=request_json,
    )
    if agent == "review":
        execution = await _run_review_with_interrupt(
            arguments=arguments,
            request_info=request_info,
        )
        return _review_run_body(execution, debug=debug), 200
    envelope = await invoke_tool_enveloped(tool_name, arguments)
    formatted_dict = asdict(envelope.formatted)
    if resolve_meta:
        existing_meta = formatted_dict.get("metadata") or {}
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        formatted_dict["metadata"] = {**existing_meta, **resolve_meta}
    result = {"formatted": formatted_dict, "raw": envelope.raw}
    response_result = result if debug else strip_agent_result(result)
    if agent in _REMOTE_AGENT_SLUGS:
        run_id, task_ids = _resolve_remote_run(owner)
        _stamp_remote_request_info(
            run_id=run_id, owner=owner, request_info=request_info
        )
        body: dict[str, Any] = {
            "id": run_id,
            "object": "agent.run",
            "agent": agent,
            "status": "running",
            "task_ids": task_ids,
            "result": response_result,
        }
        # Surface the silent-failure case: the submit chokepoint hit
        # an ``sqlite3.Error`` / ``OSError`` during the local registry
        # write, so the remote tasks are live (the network call
        # already succeeded) but the local ``runs`` / ``tasks`` rows
        # were not persisted and ``GET /v1/runs/{run_id}`` will 404
        # until a manual reconcile is run. This degraded-tracking flag
        # is what makes that shape legible: a recorder failure is now
        # the ONLY path that emits ``id=None`` / ``task_ids=[]``, since
        # an analyst dedup hit flows through the normal recorder and
        # returns the caller's own run id and fresh task id.
        if current_recorder_degraded():
            body["degraded_tracking"] = True
        return body, 202
    run_id = _record_sync_run(
        agent=agent,
        owner=owner,
        result=result,
        request_info=request_info,
    )
    body = {
        "id": run_id,
        "object": "agent.run",
        "agent": agent,
        "status": "succeeded",
        "task_ids": [],
        "result": response_result,
    }
    return body, 200


def _resolve_remote_run(owner: str) -> tuple[str | None, list[str]]:
    """Read the chokepoint's run id from contextvar, then list its tasks.

    The submit chokepoint in ``mcp/handlers`` calls ``bind_run_id``
    after it writes the runs row plus its N child task rows, so the
    HTTP layer can recover the run identity directly from the
    per-request contextvar — no formatter-specific metadata key
    (analyst's ``task_id`` vs deep_genome's ``server_id`` vs
    research's missing entry) is consulted. The child task ids are
    then sourced from ``RunRegistry.get_run`` so a multi-task
    submission returns every task id the chokepoint persisted, not
    just the primary one.

    Args:
        owner: Authenticated user id used for the registry read.

    Returns:
        ``(run_id, task_ids)`` where ``run_id`` is ``None`` and
        ``task_ids`` is empty when the chokepoint did not bind a
        run id. This arises only when the registry write failed
        midway (see ``runtime.submit_recorder.record_submitted_task``
        and ``current_recorder_degraded()``); ``_invoke_agent_run``
        then adds ``degraded_tracking: True`` to the HTTP body.
        Analysis dedup hits no longer produce this shape: a reuse
        mints a caller-owned row through the normal chokepoint path
        and binds a fresh ``run_id`` before returning.
    """
    run_id = current_run_id()
    if run_id is None:
        return None, []
    record = RunRegistry(resolve_tasks_db_path()).get_run(run_id, owner=owner)
    if record is None:
        return run_id, []
    return run_id, list(record.task_ids)


async def _route_expert_query(
    payload: ExpertQueryRequest, *, debug: bool
) -> tuple[dict[str, Any], int]:
    """Autonomously route an Expert query and shape its agent.run body.

    Runs the in-process LLM tool selector, maps the chosen tool name back
    to its agent slug, injects ``obs_file_list`` only for obs-capable
    tools, then delegates to ``_invoke_agent_run`` so the resolved slug,
    formatted envelope, and sync(200)/remote(202) branching all come from
    the same path as ``POST /v1/agents/{slug}/runs``. When the router
    selects no tool the query falls back to the chat agent.

    Args:
        payload: The validated Expert routing request.
        debug: Whether to keep the raw handler payload in the result.

    Returns:
        ``(body, status_code)`` — the ``agent.run`` envelope plus its HTTP
        status, identical in shape to ``_invoke_agent_run``.

    Raises:
        HTTPException: 400 when ``forced_tool`` is set (unsupported in v1)
            or the router produced arguments that fail the agent schema;
            502 when the router selects a tool outside the agent set.
    """
    if payload.forced_tool is not None:
        raise HTTPException(
            status_code=400,
            detail="forced_tool is not supported in v1",
        )
    selection = await select_agent_tool(payload.user_query, payload.history)
    request_json = payload.model_dump_json()
    if selection is None:
        return await _invoke_agent_run(
            agent="chat",
            arguments={
                "user_query": payload.user_query,
                "obs_file_list": list(payload.obs_file_list),
            },
            dialogue_id=payload.dialogue_id,
            request_json=request_json,
            debug=debug,
        )
    slug = _TOOL_TO_AGENT_SLUG.get(selection.tool_name)
    if slug is None:
        _LOGGER.warning(
            "Expert router selected an unknown tool: %s",
            selection.tool_name,
        )
        raise HTTPException(
            status_code=502,
            detail="router selected an unavailable tool",
        )
    arguments = dict(selection.arguments)
    if tool_accepts_obs(selection.tool_name):
        arguments["obs_file_list"] = list(payload.obs_file_list)
    try:
        return await _invoke_agent_run(
            agent=slug,
            arguments=arguments,
            dialogue_id=payload.dialogue_id,
            request_json=request_json,
            debug=debug,
        )
    except McpError as exc:
        if exc.error.code == INVALID_PARAMS:
            raise HTTPException(
                status_code=400,
                detail=f"router produced invalid arguments for {slug}",
            ) from exc
        raise


def _strip_run_result(record: dict[str, Any]) -> dict[str, Any]:
    """Strip raw from one run record's result for default-mode listing."""
    result = record.get("result")
    if isinstance(result, dict):
        return {
            **record,
            "result": strip_agent_result(result),
        }
    return record


# pylint: enable=too-many-locals


# pylint: disable=too-many-arguments
# RunFilter is built from each query arg; folding into a Pydantic
# query model triples the route boilerplate. See
# docs/development/lint-exemptions.md.
def _list_owner_runs(
    *,
    owner: str,
    status: str | None,
    agent: str | None,
    origin: str | None,
    dialogue_id: str | None,
    created_after: str | None,
    created_before: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Return one ``GET /v1/runs`` body scoped to ``owner``.

    Drives the lazy GC via ``_purge_expired_runs_best_effort`` (the
    same helper the sync chat and native agent run write paths use)
    so the registry stays bounded under listing-heavy and
    submission-heavy workloads alike.

    Args:
        owner: User id whose runs to return. Set by the route from
            ``current_request_user()`` for owner-only calls, or from
            the ``user_id`` query parameter for delegated calls that
            already passed the service-token check.
        status: Optional exact-match status filter.
        agent: Optional exact-match agent slug filter.
        origin: Optional exact-match origin filter.
        dialogue_id: Optional exact-match dialogue id filter. Runs
            before ``limit`` so a chat-ai history query for one
            dialogue always retrieves every matching row.
        created_after: Optional ISO-8601 lower bound (inclusive).
        created_before: Optional ISO-8601 upper bound (inclusive).
        limit: Max rows to return.
        offset: Rows to skip (paging).

    Returns:
        ``{"object", "data"}`` envelope with the flat run records.
    """
    _purge_expired_runs_best_effort()
    records = RunRegistry(resolve_tasks_db_path()).list_runs(
        owner=owner,
        run_filter=RunFilter(
            status=status,
            agent=agent,
            origin=origin,
            dialogue_id=dialogue_id,
            created_after=created_after,
            created_before=created_before,
        ),
        limit=limit,
        offset=offset,
    )
    return {
        "object": "list",
        "data": [_run_record_to_dict(record) for record in records],
    }


# pylint: enable=too-many-arguments


@dataclass(frozen=True)
class _ReviewExecution:
    """Internal result of one interrupt-aware ReviewAgent graph run."""

    run_id: str
    status: str
    result: dict[str, Any] | None = None
    interrupt: dict[str, Any] | None = None


def _review_stream_app() -> Any:
    """Return the cached ReviewAgent compiled graph app."""
    app, _ = review_stream_target("", [])
    return app


def _review_initial_state(args: ReviewAgentArgs) -> Mapping[str, Any]:
    """Build the ReviewAgent initial graph state for one request."""
    _, initial_state = review_stream_target(
        args.user_query, args.obs_file_list
    )
    return initial_state


def _validate_review_arguments(arguments: dict[str, Any]) -> ReviewAgentArgs:
    """Validate a ReviewAgent argument dict with the MCP schema."""
    try:
        return ReviewAgentArgs(**arguments)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="invalid ReviewAgent arguments",
        ) from exc


def _review_interrupt_result(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Return the registry payload stored for a paused review run."""
    return {
        "interrupt": dict(interrupt),
        "status": "input_required",
    }


def _review_interrupt_body(
    *,
    thread_id: str,
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the HTTP body for a paused review run."""
    return {
        "id": thread_id,
        "run_id": thread_id,
        "object": "agent.run",
        "agent": "review",
        "status": "input_required",
        "task_ids": [],
        "interrupt": dict(interrupt),
    }


def _format_review_result(
    final_state: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a terminal ReviewAgent graph state like MCP dispatch."""
    raw_payload = merge_intermediate_state(final_state)
    envelope = build_tool_result_envelope(
        "ReviewAgent",
        raw_payload,
        arguments=arguments,
    )
    return {
        "formatted": asdict(envelope.formatted),
        "raw": envelope.raw,
    }


def _review_run_body(
    execution: _ReviewExecution,
    *,
    debug: bool,
) -> dict[str, Any]:
    """Shape a ReviewAgent execution as an ``agent.run`` response."""
    if execution.interrupt is not None:
        return _review_interrupt_body(
            thread_id=execution.run_id,
            interrupt=execution.interrupt,
        )
    result = execution.result or {"formatted": {"answer": ""}, "raw": None}
    response_result = result if debug else strip_agent_result(result)
    return {
        "id": execution.run_id,
        "run_id": execution.run_id,
        "object": "agent.run",
        "agent": "review",
        "status": execution.status,
        "task_ids": [],
        "result": response_result,
    }


async def _run_review_with_interrupt(
    *,
    arguments: dict[str, Any],
    request_info: RunRequestInfo,
) -> _ReviewExecution:
    """Run ReviewAgent once, surfacing a LangGraph interrupt if present."""
    args = _validate_review_arguments(arguments)
    owner = current_request_user() or "anonymous"
    run_id = IdFactory().new_id("run", "review")
    app = _review_stream_app()
    initial_state = _review_initial_state(args)
    final_state = await app.ainvoke(
        initial_state,
        config=build_runnable_config(run_id),
    )
    interrupt = detect_interrupt(final_state, run_id)
    registry = RunRegistry(resolve_tasks_db_path())
    if interrupt is not None:
        interrupt_dict = dict(interrupt)
        registry.create_run(
            RunSpec(
                run_id=run_id,
                user_id=owner,
                agent="review",
                origin="local",
            ),
            outcome=RunOutcome(
                status="input_required",
                result=_review_interrupt_result(interrupt_dict),
            ),
            request_info=request_info,
        )
        return _ReviewExecution(
            run_id=run_id,
            status="input_required",
            interrupt=interrupt_dict,
        )
    result = _format_review_result(final_state, arguments=arguments)
    registry.create_run(
        RunSpec(
            run_id=run_id,
            user_id=owner,
            agent="review",
            origin="local",
        ),
        outcome=RunOutcome(status="succeeded", result=result),
        request_info=request_info,
    )
    return _ReviewExecution(run_id=run_id, status="succeeded", result=result)


async def _resume_review_run(
    *,
    thread_id: str,
    payload: ResumeRequest,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Resume a paused ReviewAgent graph thread and settle its run row."""
    owner = current_request_user() or "anonymous"
    registry = RunRegistry(resolve_tasks_db_path())
    record = registry.get_run(thread_id, owner=owner)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"run not found: {thread_id}",
        )
    if record.status != "input_required":
        raise HTTPException(
            status_code=409,
            detail="run is not awaiting input",
        )
    try:
        final_state = await aresume_graph(
            _review_stream_app(),
            thread_id,
            {"approved": payload.approved, "edits": payload.edits},
        )
    except NoCheckpointError as exc:
        _LOGGER.exception("resume checkpoint missing for run %s", thread_id)
        raise HTTPException(
            status_code=409,
            detail="no pause point for run",
        ) from exc
    interrupt = detect_interrupt(final_state, thread_id)
    if interrupt is not None:
        interrupt_dict = dict(interrupt)
        registry.settle_run(
            thread_id,
            owner=owner,
            status="input_required",
            result=_review_interrupt_result(interrupt_dict),
        )
        return (
            _review_interrupt_body(
                thread_id=thread_id,
                interrupt=interrupt_dict,
            ),
            200,
        )
    result = _format_review_result(final_state)
    registry.settle_run(
        thread_id,
        owner=owner,
        status="succeeded",
        result=result,
    )
    execution = _ReviewExecution(
        run_id=thread_id,
        status="succeeded",
        result=result,
    )
    return _review_run_body(execution, debug=debug), 200


async def _review_chat_completion_response(
    *,
    payload: ChatCompletionRequest,
    arguments: Mapping[str, object],
    user_query: str,
) -> JSONResponse:
    """Return the ReviewAgent non-stream chat response or interrupt body."""
    execution = await _run_review_with_interrupt(
        arguments=dict(arguments),
        request_info=RunRequestInfo(
            dialogue_id=payload.dialogue_id,
            query=user_query,
            tool_name="ReviewAgent",
            model=payload.model,
            request_json=payload.model_dump_json(),
        ),
    )
    if execution.interrupt is not None:
        # A ReviewAgent pause is not an OpenAI chat completion; return
        # the native interrupt body so clients can resume with the Bot
        # run id without guessing inside choices[].
        return JSONResponse(_review_run_body(execution, debug=True))
    result = execution.result or {"formatted": {"answer": ""}, "raw": None}
    completion = to_chat_completion(
        result.get("formatted", {}),
        result.get("raw"),
        payload.model,
    )
    completion["run_id"] = execution.run_id
    if not resolve_debug(payload.debug):
        completion = strip_chat_completion(completion)
    return JSONResponse(completion)


def _stream_chat_response(
    *,
    tool_name: str,
    arguments: dict[str, object],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Validate and build a streaming chat response."""
    if tool_name == "ReviewAgent":
        raise HTTPException(
            status_code=400,
            detail=(
                "streaming is not supported for human-in-the-loop review; "
                "use stream=false and POST /v1/runs/{id}/resume"
            ),
        )
    if not tool_accepts_stream(tool_name):
        raise HTTPException(
            status_code=400,
            detail=f"streaming is not supported for model {payload.model}",
        )
    return _stream_chat_completion(
        tool_name=tool_name,
        arguments=arguments,
        payload=payload,
        user_query=user_query,
    )


async def _fetch_owner_run(run_id: str) -> dict[str, Any]:
    """Reconcile + flatten one ``GET /v1/runs/{run_id}`` request body.

    Args:
        run_id: Run id to fetch.

    Returns:
        Flat JSON-serialisable run envelope.

    Raises:
        HTTPException: 404 when the run is unknown or foreign-owned.
    """
    owner = current_request_user() or "anonymous"
    registry = RunRegistry(resolve_tasks_db_path())
    record = await registry.reconcile(run_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return _run_record_to_dict(record)


def _record_sync_run(
    *,
    agent: str,
    owner: str,
    result: dict[str, Any],
    request_info: RunRequestInfo | None = None,
) -> str | None:
    """Persist a terminal ``origin="local"`` run for a sync agent call.

    Mints a fresh ``run_id`` via ``IdFactory().new_id("run", agent)``
    and writes one ``runs`` row at terminal status ``"succeeded"`` so
    the upcoming ``/v1/runs/{id}`` and ``/v1/runs`` endpoints replay
    the formatted answer without re-invoking the agent. SQLite / OS
    failures are swallowed — a successful HTTP completion must never
    fail because the bookkeeping write hit the disk wrong.

    The MCP stdio path never reaches this helper (it does not enter
    the FastAPI request lifecycle), so the existing stdio MCP
    contract stays byte-equivalent.

    Args:
        agent: Public agent alias (e.g. ``"chat"``).
        owner: Authenticated user id (``"anonymous"`` for stdio).
        result: The formatted result dict (stored as JSON in
            ``result_json``).
        request_info: Per-request metadata captured at the HTTP
            boundary; ``None`` keeps every per-request column NULL.

    Returns:
        The minted ``run_id`` on a successful write, otherwise
        ``None``.
    """
    run_id = IdFactory().new_id("run", agent)
    try:
        RunRegistry(resolve_tasks_db_path()).create_run(
            RunSpec(
                run_id=run_id,
                user_id=owner,
                agent=agent,
                origin="local",
            ),
            outcome=RunOutcome(status="succeeded", result=result),
            request_info=request_info,
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "sync run bookkeeping write failed for agent %s: %s",
            agent,
            exc.__class__.__name__,
        )
        return None
    return run_id


def _create_running_stream_run(
    run_id: str, agent: str, owner: str, request_info: RunRequestInfo
) -> None:
    """Write the initial running row for a streaming run (stage 1).

    Best-effort: a SQLite / OS failure is swallowed so a bookkeeping
    miss never blocks the stream. The RunStarted frame still carries
    the minted id; the row simply may not exist for later polling.

    Args:
        run_id: Registry run id pre-minted by the caller.
        agent: Public agent alias (e.g. ``"chat"``).
        owner: Authenticated user id (``"anonymous"`` for stdio).
        request_info: Per-request metadata captured at the HTTP
            boundary.
    """
    try:
        RunRegistry(resolve_tasks_db_path()).create_run(
            RunSpec(
                run_id=run_id,
                user_id=owner,
                agent=agent,
                origin="local",
            ),
            outcome=RunOutcome(status="running"),
            request_info=request_info,
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "stream run create failed for %s: %s",
            agent,
            exc.__class__.__name__,
        )


def _stream_answer_max_bytes() -> int:
    """Return the resolved soft cap for streamed chat answer storage."""
    return resolve_stream_answer_max_bytes(ApiConfig().STREAM_ANSWER_MAX_BYTES)


def _settle_stream_run(
    run_id: str,
    owner: str,
    status: str,
    result: dict[str, Any],
) -> None:
    """Settle a streaming run to a terminal status (stage 2).

    Targeted owner-scoped UPDATE via ``RunRegistry.settle_run`` so the
    original ``created_at`` and request-info columns survive; a missing
    or foreign row is a silent no-op. Best-effort, mirroring the
    stage-1 swallow — bookkeeping must never break the stream.

    Args:
        run_id: Registry run id pre-minted for this stream.
        owner: Authenticated user id used for the owner-scoped lookup.
        status: Terminal status to write (``"succeeded"``/``"failed"``).
        result: Terminal result payload written to ``result_json``.
    """
    try:
        RunRegistry(resolve_tasks_db_path()).settle_run(
            run_id,
            owner=owner,
            status=status,
            result=result,
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "stream run settle failed for %s: %s",
            run_id,
            exc.__class__.__name__,
        )
    _purge_expired_runs_best_effort()


def _stamp_remote_request_info(
    *,
    run_id: str | None,
    owner: str,
    request_info: RunRequestInfo,
) -> None:
    """Back-fill request-info columns on a chokepoint-minted run row.

    Remote agents (analyst / deep_genome / research / design / network)
    have their run row created inside the submit chokepoint before the
    API layer can attach request metadata. Once the response returns
    and ``_resolve_remote_run`` recovers the run id, this helper
    updates the five per-request columns owner-scoped so the history
    page sees the same shape as sync runs. SQLite / OS failures are
    swallowed — the user already got their 202 response.

    Args:
        run_id: Run id minted by the chokepoint; ``None`` skips the
            write (analyst dedup-hit passthrough or chokepoint failure).
        owner: Authenticated user id used for the owner check.
        request_info: Field values to write.
    """
    if run_id is None:
        return
    try:
        RunRegistry(resolve_tasks_db_path()).update_request_info(
            run_id, owner=owner, request_info=request_info
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "remote run request-info back-fill failed for run %s: %s",
            run_id,
            exc.__class__.__name__,
        )
        return


def _error_response(
    status_code: int,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build a unified error-envelope JSON response.

    Args:
        status_code: HTTP status code mirrored into the body.
        message: Human-readable explanation.
        headers: Optional response headers to propagate (e.g.
            Retry-After, WWW-Authenticate) from the raised exception.

    Returns:
        JSON response carrying the unified error envelope.
    """
    payload = ApiErrorResponse(
        error=ApiErrorDetail(
            type=_ERROR_TYPES.get(status_code, "error"),
            code=status_code,
            message=message,
            request_id=current_request_id(),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers=dict(headers) if headers else None,
    )


def request_context_middleware(app: ASGIApp) -> ASGIApp:
    """Wrap an ASGI app to bind a per-request correlation id.

    A generated request id is bound to the contextvar for the request's
    lifetime and echoed as the ``X-Request-Id`` response header so the
    error envelope and clients can correlate a call. The user contextvar
    is also bracketed here (bound to None, reset on exit) so the value
    require_principal sets is always restored without relying on the
    server copying the contextvars context per request. A closure-based
    pure ASGI middleware is used (not BaseHTTPMiddleware) so the
    contextvars are set in the same task that runs the endpoint and
    exception handlers.

    Args:
        app: The downstream ASGI application to wrap.

    Returns:
        An ASGI application that binds request context then delegates.
    """

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the request id, inject the header, then delegate."""
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        request_id = IdFactory().new_id("request")
        id_token = bind_request_id(request_id)
        user_token = bind_request_user(None)
        run_token = bind_run_id(None)

        async def send_with_header(message: Message) -> None:
            """Attach X-Request-Id on the response start event."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await app(scope, receive, send_with_header)
        finally:
            reset_request_var(run_token)
            reset_request_var(user_token)
            reset_request_var(id_token)

    return asgi


def _nearest_existing(path: Path) -> Path:
    """Return the closest existing ancestor of a path.

    Args:
        path: Filesystem path to walk upward from.

    Returns:
        The path itself or the nearest existing parent directory.
    """
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path(path.anchor or ".")


def _store_path_writable(raw_path: str) -> bool:
    """Verify a SQLite store path's directory is writable.

    The check never creates files or directories so readiness probes
    stay side-effect free.

    Args:
        raw_path: Configured SQLite store path.

    Returns:
        True when the nearest existing ancestor directory is writable.
    """
    parent = Path(raw_path).expanduser().resolve().parent
    return os.access(_nearest_existing(parent), os.W_OK)


async def _reconcile_run_task_logs(run_id: str, debug: bool) -> dict[str, Any]:
    """Reconcile task logs for every task in a run.

    Fetches the run to verify ownership, iterates its task ids, and
    returns a JSON-ready envelope with one reconciled log per task.
    When ``debug`` is False, the raw handler payload is stripped from
    each log via ``strip_agent_result`` so default-mode responses stay
    compact.

    Args:
        run_id: Run whose task logs are being reconciled.
        debug: When True, keep the raw handler payload in each log.

    Returns:
        ``{"run_id", "task_ids", "task_logs"}`` envelope ready for
        ``JSONResponse``.

    Raises:
        HTTPException: Propagated from ``_fetch_owner_run`` when the
            run is unknown or foreign-owned.
    """
    record = await _fetch_owner_run(run_id)
    task_ids = record.get("task_ids", [])
    task_logs: list[dict[str, Any]] = []
    for task_id in task_ids:
        log = await reconcile_task_log(task_id)
        if log is None:
            continue
        if not debug:
            log = strip_agent_result(log)
        task_logs.append(log)
    return {
        "run_id": run_id,
        "task_ids": task_ids,
        "task_logs": task_logs,
    }


@asynccontextmanager
async def _http_lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Own the process-wide shared ``AsyncClient`` for the API lifetime.

    The shared client carries a keep-alive connection pool used by
    every agent call site that opens ``get_async_client(timeout=...)``;
    initialising it once here avoids a per-request TLS handshake on
    the high-frequency LLM / retrieval paths. Teardown is wrapped in
    ``try / finally`` so a startup error never prevents the rest of
    the FastAPI shutdown chain from running.
    """
    init_shared_client()
    try:
        yield
    finally:
        await aclose_shared_client()
        await aclose_gauss_pool()


# pylint: disable=too-many-arguments,too-many-locals,too-many-statements
# create_app is a FastAPI factory that wires every route + dependency
# in one closure; the nested route handlers each accumulate request
# validation -> DB lookup -> response assembly inline. Splitting per
# module triples dependency-injection boilerplate. The disable runs
# to EOF since create_app is the last function in the file.
# See docs/development/lint-exemptions.md.
def create_app() -> FastAPI:
    """Build the FastAPI application.

    Returns:
        Configured FastAPI app exposing liveness/readiness probes and the
        unified error envelope. Authenticated routes are added by later
        API layers.
    """
    configure_logging()
    app = FastAPI(
        title="Phytomni HTTP API",
        version="0.1.0",
        lifespan=_http_lifespan,
    )
    app.add_middleware(request_context_middleware)
    rate_limit = make_rate_limiter()

    async def authorized(
        principal: ApiPrincipal = Depends(require_principal),
    ) -> ApiPrincipal:
        """Authenticate, then enforce the per-key request budget."""
        limit = ApiConfig().API_RATE_LIMIT_PER_MIN
        retry_after = rate_limit(principal.key_prefix, limit)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        return principal

    def require_scope(
        *needed: str,
    ) -> Callable[..., Awaitable[ApiPrincipal]]:
        """Build a dependency requiring the caller to hold scopes.

        Runs after ``authorized`` (authentication + rate limit), then
        checks the granted scopes, raising 403 (distinct from the
        auth-layer 401) when a required scope is missing. An all-access
        key (an empty scope set) satisfies every requirement.
        """

        async def _scoped(
            caller: ApiPrincipal = Depends(authorized),
        ) -> ApiPrincipal:
            if not scopes_satisfy(caller.scopes, needed):
                raise HTTPException(
                    status_code=403, detail="insufficient scope"
                )
            return caller

        return _scoped

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Return a dependency-free liveness signal."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Return readiness after checking local store paths."""
        config = ApiConfig()
        checks = {
            "api_keys_db": _store_path_writable(config.API_KEYS_DB_PATH),
            "tasks_db": _store_path_writable(config.API_TASKS_DB_PATH),
        }
        if not all(checks.values()):
            return _error_response(
                503, "one or more local stores are not writable"
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "checks": checks},
        )

    @app.get("/v1/models")
    async def list_models(
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """List the chat-like model ids (OpenAI convention)."""
        del principal  # Auth side-effect only.
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "object": "model",
                        "owned_by": "phytomni",
                    }
                    for model_id in MODEL_TO_TOOL
                ],
            }
        )

    @app.post(
        "/v1/api-keys",
        status_code=201,
        response_model=ApiKeyCreateResponse,
    )
    async def issue_api_key(
        payload: ApiKeyCreateRequest,
        _admin: None = Depends(require_service_principal),
    ) -> ApiKeyCreateResponse:
        """Mint a per-user API key for the upstream service.

        The plaintext key is shown exactly once in the response. The
        service-token dependency is the only gate so a leaked user key
        cannot escalate to issuance.
        """
        del _admin  # Auth side-effect only.
        expires_at: datetime | None = None
        if payload.expires_days is not None:
            expires_at = datetime.now(UTC) + timedelta(
                days=payload.expires_days
            )
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        created = store.create(
            user_id=payload.user_id,
            name=payload.name,
            expires_at=expires_at,
        )
        return ApiKeyCreateResponse(
            api_key=created.api_key,
            prefix=created.prefix,
            user_id=created.user_id,
            expires_at=(expires_at.isoformat() if expires_at else None),
        )

    @app.get("/v1/api-keys", response_model=ApiKeyListResponse)
    async def list_api_keys(
        user_id: str | None = None,
        _admin: None = Depends(require_service_principal),
    ) -> ApiKeyListResponse:
        """List per-user API keys; ``user_id`` filters to one user."""
        del _admin  # Auth side-effect only.
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        records = store.list(user_id=user_id)
        return ApiKeyListResponse(
            data=[
                ApiKeyRecordResponse(
                    user_id=record.user_id,
                    name=record.name,
                    prefix=record.prefix,
                    created_at=record.created_at,
                    revoked_at=record.revoked_at,
                    last_used_at=record.last_used_at,
                    expires_at=record.expires_at,
                    active=record.active,
                    scopes=sorted(record.scopes),
                )
                for record in records
            ],
        )

    @app.delete(
        "/v1/api-keys/{prefix}",
        response_model=ApiKeyDeleteResponse,
    )
    async def revoke_api_key(
        prefix: str,
        _admin: None = Depends(require_service_principal),
    ) -> ApiKeyDeleteResponse:
        """Revoke an active key by its public prefix."""
        del _admin  # Auth side-effect only.
        store = get_key_store(ApiConfig().API_KEYS_DB_PATH)
        deleted = store.revoke(prefix)
        return ApiKeyDeleteResponse(prefix=prefix, deleted=deleted)

    @app.get("/v1/relay/audit")
    async def list_relay_audit(
        _admin: None = Depends(require_service_principal),
        *,
        user_id: str | None = None,
        key_prefix: str | None = None,
        service: str | None = None,
        status_code: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        """List relay audit records, gated by the service token.

        Only the service token may read the audit trail; a per-user
        ``ptm_...`` key alone cannot. The returned records carry no key
        hash, salt, or plaintext key, only the public ``key_prefix``.
        """
        del _admin  # Auth side-effect only.
        store = get_audit_store(ApiConfig().RELAY_AUDIT_DB_PATH)
        records = store.query(
            RelayAuditQuery(
                user_id=user_id,
                key_prefix=key_prefix,
                service=service,
                status_code=status_code,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                offset=offset,
            )
        )
        return JSONResponse(
            {
                "object": "list",
                "data": [record.model_dump() for record in records],
            }
        )

    @app.get("/v1/relay/audit/{request_id}")
    async def get_relay_audit(
        request_id: str,
        _admin: None = Depends(require_service_principal),
    ) -> JSONResponse:
        """Fetch relay audit records by request id, service-token gated."""
        del _admin  # Auth side-effect only.
        store = get_audit_store(ApiConfig().RELAY_AUDIT_DB_PATH)
        records = store.get_by_request_id(request_id)
        return JSONResponse(
            {
                "object": "list",
                "request_id": request_id,
                "data": [record.model_dump() for record in records],
            }
        )

    @app.post(
        "/v1/chat/completions",
        dependencies=[Depends(_schedule_run_gc)],
    )
    async def chat_completions(
        payload: ChatCompletionRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> Response:
        """Run a chat-like agent in an OpenAI-compatible shape.

        With ``stream=true`` and a streaming-capable model, returns a
        ``text/event-stream`` carrying ``data: {...}\\n\\n`` chunks
        plus a terminating ``data: [DONE]\\n\\n``; the non-stream
        path returns a JSON ``chat.completion`` envelope unchanged.
        """
        del principal  # Auth side-effect; identity flows via contextvar.
        tool_name = tool_for_model(payload.model)
        if tool_name is None:
            raise HTTPException(
                status_code=404,
                detail=f"model not found: {payload.model}",
            )
        obs_files = payload.obs_file_list or []
        accepts_obs = tool_accepts_obs(tool_name)
        if obs_files and not accepts_obs:
            raise HTTPException(
                status_code=400,
                detail=f"model {payload.model} does not accept "
                "obs_file_list",
            )
        try:
            user_query = flatten_messages(payload.messages)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        user_query, resolve_meta = await _maybe_resolve_brief_gene_query(
            raw_query=user_query,
            resolve_flag=bool(payload.resolve_gene_id),
            tool_name=tool_name,
        )
        arguments: dict[str, object] = {"user_query": user_query}
        if accepts_obs:
            arguments["obs_file_list"] = obs_files
        if payload.stream:
            return _stream_chat_response(
                tool_name=tool_name,
                arguments=arguments,
                payload=payload,
                user_query=user_query,
            )
        if tool_name == "ReviewAgent":
            return await _review_chat_completion_response(
                payload=payload,
                arguments=arguments,
                user_query=user_query,
            )
        envelope = await invoke_tool_enveloped(tool_name, arguments)
        formatted_dict = asdict(envelope.formatted)
        if resolve_meta:
            existing_meta = formatted_dict.get("metadata") or {}
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            formatted_dict["metadata"] = {**existing_meta, **resolve_meta}
        envelope_dict = {
            "formatted": formatted_dict,
            "raw": envelope.raw,
        }
        agent_slug = _MODEL_TO_AGENT_SLUG.get(payload.model)
        chat_run_id: str | None = None
        if agent_slug is not None:
            chat_run_id = _record_sync_run(
                agent=agent_slug,
                owner=current_request_user() or "anonymous",
                result=envelope_dict,
                request_info=RunRequestInfo(
                    dialogue_id=payload.dialogue_id,
                    query=user_query,
                    tool_name=tool_name,
                    model=payload.model,
                    request_json=payload.model_dump_json(),
                ),
            )
        completion = to_chat_completion(
            formatted_dict,
            envelope.raw,
            payload.model,
        )
        # Expose the Bot-side run id so Web can join chat completions
        # against ``GET /v1/runs?dialogue_id=...`` without relying on
        # the OpenAI ``chatcmpl-*`` provider id. ``None`` means the
        # registry write failed; surface that explicitly rather than
        # silently degrading (mirrors the remote-agent
        # ``degraded_tracking`` signal).
        completion["run_id"] = chat_run_id
        if chat_run_id is None and agent_slug is not None:
            completion["degraded_tracking"] = True
        if not resolve_debug(payload.debug):
            completion = strip_chat_completion(completion)
        return JSONResponse(completion)

    @app.get("/v1/agents")
    async def list_agents(
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """List the agents reachable via ``/v1/agents/{slug}/runs``."""
        del principal
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "slug": slug,
                        "tool": tool,
                        "origin": (
                            "remote"
                            if slug in _REMOTE_AGENT_SLUGS
                            else "local"
                        ),
                        "legacy_aliases": _LEGACY_ALIASES.get(tool, []),
                    }
                    for slug, tool in _AGENT_SLUG_TO_TOOL.items()
                ],
            }
        )

    @app.post(
        "/v1/agents/{agent}/runs",
        dependencies=[Depends(_schedule_run_gc)],
    )
    async def create_agent_run(
        agent: str,
        payload: AgentRunRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """Invoke one agent by slug and return its agent.run envelope."""
        del principal
        body, status_code = await _invoke_agent_run(
            agent=agent,
            arguments=payload.arguments,
            dialogue_id=payload.dialogue_id,
            debug=resolve_debug(payload.debug),
            request_json=payload.model_dump_json(),
        )
        return JSONResponse(body, status_code=status_code)

    @app.post(
        "/v1/query/route",
        dependencies=[Depends(_schedule_run_gc)],
    )
    async def route_query(
        payload: ExpertQueryRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> JSONResponse:
        """Autonomously route a query to an agent and return its run."""
        del principal
        body, status_code = await _route_expert_query(
            payload, debug=resolve_debug(None)
        )
        return JSONResponse(body, status_code=status_code)

    @app.post(
        "/v1/files",
        status_code=201,
        response_model=FileUploadResponse,
    )
    async def upload_file(
        request: Request,
        file: UploadFile = File(...),
        purpose: UploadPurpose = Form("agent_context"),
        principal: ApiPrincipal = Depends(require_scope("agents")),
    ) -> FileUploadResponse | JSONResponse:
        """Accept one multipart file upload and store it in OBS.

        Pre-checks ``Content-Length`` so oversize requests are rejected
        before the body is buffered; falls back to a post-read size
        guard inside ``upload_user_file`` so missing or falsified
        Content-Length (e.g. chunked transfer) is still caught. The
        sanitized filename, byte length, and public OBS path are
        returned in a ``FileUploadResponse`` shape with ``path`` aliased
        to ``obs_path`` so existing chat-ai code that already reads
        ``path`` from the legacy upload bridge can plug in unchanged.
        """
        return await handle_file_upload(
            request=request,
            file=file,
            purpose=purpose,
            user_id=principal.user_id,
            error_response=_error_response,
        )

    @app.get("/v1/runs/{run_id}/logs")
    async def get_run_logs(
        run_id: str,
        principal: ApiPrincipal = Depends(require_scope("agents")),
        debug: bool = False,
    ) -> JSONResponse:
        """Return reconciled task logs for a run.

        Fetches the run to verify ownership, then reconciles logs for
        each task in the run. Default mode strips the raw handler
        payload from each task log; pass ``debug=true`` to include it.
        """
        del principal
        return JSONResponse(
            await _reconcile_run_task_logs(run_id, resolve_debug(debug))
        )

    @app.get("/v1/runs/{run_id}")
    async def get_run(
        run_id: str,
        principal: ApiPrincipal = Depends(require_scope("agents")),
        debug: bool = False,
    ) -> JSONResponse:
        """Return one owner-scoped run record by id.

        Default mode strips the raw handler payload from result;
        pass ``debug=true`` to include it.
        """
        del principal
        record = await _fetch_owner_run(run_id)
        if not resolve_debug(debug) and isinstance(record.get("result"), dict):
            record = {
                **record,
                "result": strip_agent_result(record["result"]),
            }
        return JSONResponse(record)

    @app.post("/v1/runs/{thread_id}/resume")
    async def resume_run(
        thread_id: str,
        body: ResumeRequest,
        principal: ApiPrincipal = Depends(require_scope("agents")),
        debug: bool = False,
    ) -> JSONResponse:
        """Resume a paused ReviewAgent run by LangGraph thread id."""
        del principal
        response_body, status_code = await _resume_review_run(
            thread_id=thread_id,
            payload=body,
            debug=resolve_debug(debug),
        )
        return JSONResponse(response_body, status_code=status_code)

    @app.get("/v1/runs")
    async def list_runs(
        principal: ApiPrincipal = Depends(require_scope("agents")),
        *,
        status: str | None = None,
        agent: str | None = None,
        origin: str | None = None,
        user_id: str | None = None,
        dialogue_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 10,
        offset: int = 0,
        debug: bool = False,
        authorization: str | None = Header(default=None),
        x_service_token: str | None = Header(
            default=None, alias="X-Service-Token"
        ),
    ) -> JSONResponse:
        """List runs with owner-only or service-token-delegated scoping.

        Without ``user_id`` the route returns the authenticated user's
        runs only. With ``user_id`` it requires a valid service token
        in addition to the user key, then scopes the listing to that
        user instead of the caller — the path Phytomni-Web Go uses to
        render history pages for any tenant. Acts as the lazy GC
        trigger via ``_purge_expired_runs_best_effort``.

        ``dialogue_id`` runs as a server-side ``WHERE`` predicate
        before ``limit`` / ``offset`` so a chat-ai history query for
        one dialogue always retrieves every matching row regardless
        of the caller's total run count.

        Default mode strips the raw handler payload from each result;
        pass ``debug=true`` to include it.
        """
        del principal
        is_service = is_service_token_valid(authorization, x_service_token)
        if user_id is not None and not is_service:
            raise HTTPException(
                status_code=403,
                detail=("user_id query parameter requires the service token"),
            )
        owner = (
            user_id
            if user_id is not None
            else (current_request_user() or "anonymous")
        )
        body = _list_owner_runs(
            owner=owner,
            status=status,
            agent=agent,
            origin=origin,
            dialogue_id=dialogue_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
        )
        if not resolve_debug(debug):
            data = body.get("data")
            if isinstance(data, list):
                body = {
                    **body,
                    "data": [_strip_run_result(r) for r in data],
                }
        return JSONResponse(body)

    app.include_router(create_relay_router())

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Render HTTP exceptions through the unified envelope."""
        message = exc.detail if isinstance(exc.detail, str) else "error"
        return _error_response(
            exc.status_code, message, getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        """Render request validation errors as 422 envelopes."""
        return _error_response(422, "request validation failed")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        _request: Request, _exc: Exception
    ) -> JSONResponse:
        """Render unexpected errors as 500 envelopes."""
        return _error_response(500, "internal server error")

    return app
