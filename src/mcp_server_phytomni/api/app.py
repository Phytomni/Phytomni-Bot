# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI application factory for the external HTTP API.

Public functions: create_app.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
)
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from ..agents.brief_gene.resolve_query import resolve_brief_gene_user_query
from ..agents.deep_genome.resolve_query import resolve_deep_genome_user_query
from ..agents.design.resolve_query import resolve_design_user_query
from ..agents.expert import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingContractError,
    select_agent_tool,
)
from ..agents.network.resolve_query import resolve_network_user_query
from ..common import logging_config as _logging_config
from ..config.defaults import ApiConfig
from ..interop import a2a_discovery as _a2a_discovery
from ..interop import capabilities as _interop_capabilities
from ..interop import registry as _interop_registry
from ..mcp import app as _mcp_app
from ..mcp.app import (
    invoke_tool_enveloped,
    prepare_tool_stream,
    validate_tool_arguments,
)
from ..mcp.result_formatting import (
    resolve_debug,
    strip_agent_result,
    strip_chat_completion,
)
from ..mcp.schemas import ReviewAgent as ReviewAgentArgs
from ..runtime import task_reconcile as _task_reconcile
from ..runtime.background_submission import (
    BackgroundSubmissionLaunchError,
    BackgroundSubmissionOutcome,
    launch_background_submission,
    reserve_background_submission,
)
from ..runtime.locale import current_effective_locale
from ..runtime.request_context import (
    current_accepted_task_ids,
    current_recorder_degraded,
    current_request_user,
    current_run_id,
)
from ..runtime.request_context import (
    current_request_id as _current_request_id,
)
from ..runtime.resume import ahas_checkpoint as _runtime_has_checkpoint
from ..runtime.run_registry import (
    RunRecord,
    RunRegistry,
    RunRequestInfo,
)
from ..runtime.stage_trace import DataStage, trace_data_stage
from ..runtime.submission_outcome import (
    project_submission_warnings as _project_warnings,
)
from ..runtime.task_manager import resolve_tasks_db_path
from ..version import __version__ as _package_version
from . import a2ui_runtime, run_lifecycle
from . import admin_auth as _admin_auth
from . import agent_capabilities as _agent_capabilities
from . import app_support as _app_support
from . import compat as _compat
from . import factory as _factory
from . import file_upload as _file_upload
from . import ratelimit as _ratelimit
from . import relay as _relay
from . import resolvers as _resolvers
from .a2a import card as _a2a_card
from .a2a import runtime as a2a_runtime
from .a2a.executor import (
    A2ARegistration,
)
from .attachments import (
    prepare_expert_arguments,
    validate_native_attachments,
)
from .compat import (
    _a2ui_interrupt_body,
    _a2ui_runtime_dependencies,
    _chat_a2ui_interrupt_result,
    _chat_a2ui_stream_app,
    _format_chat_a2ui_result,
    _purge_expired_runs_best_effort,
    _resume_paused_run,
    _stream_chat_completion,
    _stream_review_a2ui_pause,
)
from .lifecycle_contract import (
    SafeApiError,
    SafeErrorCode,
    build_agent_run_response,
    canonicalize_agent_run_body,
    empty_agent_result,
    expert_safe_error,
)
from .openai_mapping import (
    to_chat_completion,
    tool_accepts_stream,
)
from .resolvers import (
    ResolverDispatch,
    apply_runs_resolver,
)
from .schemas import ChatCompletionRequest, ExpertQueryRequest, ResumeRequest
from .stream_answer import resolve_stream_answer_max_bytes

# These assignments keep long-standing app-level monkeypatch seams available
# after route wiring moved to ``api.factory``.
invoke_tool_streamed = _mcp_app.invoke_tool_streamed
current_request_id = _current_request_id
reconcile_task_log = _task_reconcile.reconcile_task_log
discover_external_a2a_capabilities = (
    _a2a_discovery.discover_external_a2a_capabilities
)
discover_external_mcp_capabilities = (
    _interop_capabilities.discover_external_mcp_capabilities
)
_discover_interop_targets = getattr(_app_support, "discover_interop_targets")
_error_response = getattr(_app_support, "error_response")
_http_lifespan = getattr(_app_support, "_http_lifespan")
_interop_result_body = getattr(_app_support, "interop_result_body")
_memory_audit_response = getattr(_app_support, "memory_audit_response")
_memory_response = getattr(_app_support, "memory_response")
resolve_http_locale = getattr(_app_support, "resolve_http_locale")
_memory_revision = getattr(_app_support, "memory_revision")
_memory_write = getattr(_app_support, "memory_write")
_reconcile_run_task_logs = getattr(_app_support, "reconcile_run_task_logs")
request_context_middleware = getattr(
    _app_support, "request_context_middleware"
)
_store_path_writable = getattr(_app_support, "store_path_writable")
__version__ = _package_version
build_agent_card = _a2a_card.build_agent_card
configure_logging = _logging_config.configure_logging
InteropRegistryError = _interop_registry.InteropRegistryError
load_interop_registry = _interop_registry.load_interop_registry
_relay_audit_record_to_dict = getattr(_compat, "_relay_audit_record_to_dict")
_resume_a2ui_run = getattr(_compat, "_resume_a2ui_run")
_schedule_run_gc = getattr(_compat, "_schedule_run_gc")
is_service_token_valid = _admin_auth.is_service_token_valid
require_service_principal = _admin_auth.require_service_principal
serialize_agent_capability = _agent_capabilities.serialize_agent_capability
handle_file_upload = _file_upload.handle_file_upload
make_rate_limiter = _ratelimit.make_rate_limiter
create_relay_router = _relay.create_relay_router
resolve_chat_query = _resolvers.resolve_chat_query
_has_graph_checkpoint = _runtime_has_checkpoint

__all__ = ["create_app", "prepare_tool_stream"]

_COMPATIBILITY_REEXPORTS = frozenset(
    {
        "_chat_a2ui_initial_state",
        "_claim_run_gc",
        "_extract_answer",
        "_failed_stream_result",
        "_new_stream_run_id",
        "_open_a2ui_surface_for_action",
        "_project_primed_stream",
        "_purge_expired_runs_best_effort_async",
        "_replay_primed_stream",
        "_run_record_to_dict",
        "_settle_a2ui_stream_failure",
        "_stream_a2ui_enabled",
        "_stream_agent_slug",
        "_stream_chat_a2ui_confirm",
        "_stream_setup_error",
        "_streaming_dependencies",
        "_submitted_a2ui_value",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily expose compatibility seams moved out of this module."""
    if name in _COMPATIBILITY_REEXPORTS:
        return getattr(_compat, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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

_BACKGROUND_SUBMISSION_AGENT_SLUGS = frozenset(
    {"analyst", "research", "network", "design"}
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


@dataclass(frozen=True, slots=True)
class _AgentRunPreparation:
    """Resolved context shared by one native agent-run response."""

    tool_name: str
    owner: str
    request_info: RunRequestInfo
    resolve_meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _AgentRunPreflight:
    """Synchronous validation and immutable request context for one run."""

    tool_name: str
    owner: str
    request_info: RunRequestInfo


def _project_submission_warnings(raw: Any) -> list[dict[str, Any]]:
    """Project safe remote-submission warnings into HTTP execution state."""
    if not isinstance(raw, Mapping):
        return []
    return _project_warnings(raw.get("submission_warnings"))


def _preflight_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    dialogue_id: str | None,
    request_json: str | None,
) -> _AgentRunPreflight:
    """Validate structural inputs and capture request context
    before dispatch."""
    tool_name = _AGENT_SLUG_TO_TOOL.get(agent)
    if tool_name is None:
        raise HTTPException(
            status_code=404, detail=f"agent not found: {agent}"
        )
    if agent in _BACKGROUND_SUBMISSION_AGENT_SLUGS:
        validation_arguments = deepcopy(arguments)
        if agent == "design" and validation_arguments.get("resolve_gene_id"):
            validation_arguments.setdefault("species_code", "ath")
            validation_arguments.setdefault("gene_id", "AT1G01010")
        elif agent == "network" and validation_arguments.get("resolve_to_id"):
            validation_arguments.setdefault("species_code", "osa")
            validation_arguments.setdefault("to_id", "TO:0000001")
        validate_tool_arguments(tool_name, validation_arguments)
    owner = current_request_user() or "anonymous"
    validate_native_attachments(
        agent,
        arguments,
        owner=owner,
        db_path=resolve_tasks_db_path(),
    )
    return _AgentRunPreflight(
        tool_name=tool_name,
        owner=owner,
        request_info=RunRequestInfo(
            dialogue_id=dialogue_id,
            request_id=current_request_id(),
            query=_request_info_query(arguments, request_json),
            tool_name=tool_name,
            model=None,
            request_json=request_json,
            locale=current_effective_locale(),
        ),
    )


async def _prepare_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    preflight: _AgentRunPreflight,
) -> _AgentRunPreparation:
    """Resolve semantic arguments after structural preflight."""
    resolve_meta = await apply_runs_resolver(
        agent,
        arguments,
        dispatch=ResolverDispatch(
            brief_gene_resolver=resolve_brief_gene_user_query,
            deep_genome_resolver=resolve_deep_genome_user_query,
            design_resolver=resolve_design_user_query,
            network_resolver=resolve_network_user_query,
        ),
    )
    return _AgentRunPreparation(
        tool_name=preflight.tool_name,
        owner=preflight.owner,
        request_info=preflight.request_info,
        resolve_meta=resolve_meta,
    )


def _routing_contract_error() -> SafeApiError:
    """Return one sanitized Expert routing contract failure."""
    return expert_safe_error(
        SafeErrorCode.ROUTING_CONTRACT_VIOLATION,
        status_code=502,
        locale=current_effective_locale(),
        stage="routing",
        retryable=False,
    )


def _request_info_query(
    arguments: Mapping[str, Any], request_json: str | None
) -> str | None:
    """Resolve the original query without trusting selected arguments."""
    value = arguments.get("user_query")
    if isinstance(value, str):
        return value
    try:
        value = json.loads(request_json or "{}").get("user_query")
    except (AttributeError, TypeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _format_agent_run_result(
    envelope: Any,
    *,
    resolve_meta: dict[str, Any],
    debug: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build raw and default-projected result blocks from one envelope."""
    formatted_dict = asdict(envelope.formatted)
    if resolve_meta:
        existing_meta = formatted_dict.get("metadata") or {}
        if not isinstance(existing_meta, dict):
            existing_meta = {}
        formatted_dict["metadata"] = {**existing_meta, **resolve_meta}
    execution = getattr(envelope, "execution", None)
    if execution is None:
        # Keep compatibility for narrow adapters that still provide the
        # historical two-field envelope during the migration.
        execution_dict: dict[str, Any] = {
            "warnings": _project_submission_warnings(envelope.raw),
        }
    else:
        execution_dict = asdict(execution)
    result = {
        "formatted": formatted_dict,
        "execution": execution_dict,
        "raw": envelope.raw,
    }
    response_result = result if debug else strip_agent_result(result)
    return result, response_result


async def _execute_background_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    preflight: _AgentRunPreflight,
    debug: bool,
) -> BackgroundSubmissionOutcome:
    """Resolve, invoke, and project one already-reserved background run."""
    prepared = await _prepare_agent_run(
        agent=agent,
        arguments=arguments,
        preflight=preflight,
    )
    envelope = await invoke_tool_enveloped(prepared.tool_name, arguments)
    _result, response_result = _format_agent_run_result(
        envelope,
        resolve_meta=prepared.resolve_meta,
        debug=debug,
    )
    return BackgroundSubmissionOutcome(
        accepted_task_ids=current_accepted_task_ids(),
        result=response_result,
        degraded=current_recorder_degraded(),
    )


def _background_agent_run_response(
    *,
    agent: str,
    arguments: dict[str, Any],
    preflight: _AgentRunPreflight,
    debug: bool,
) -> tuple[dict[str, Any], int]:
    """Reserve and launch one background run before returning 202."""
    db_path = resolve_tasks_db_path()
    worker_arguments = deepcopy(arguments)
    reservation = reserve_background_submission(
        agent=agent,
        owner=preflight.owner,
        request_info=preflight.request_info,
        db_path=db_path,
    )
    launch_background_submission(
        reservation,
        lambda: _execute_background_agent_run(
            agent=agent,
            arguments=worker_arguments,
            preflight=preflight,
            debug=debug,
        ),
        db_path=db_path,
    )
    body = build_agent_run_response(
        run_id=reservation.run_id,
        agent=agent,
        status="running",
        task_ids=[],
        result=empty_agent_result(),
        persisted=True,
        degraded_tracking=False,
    )
    return body, 202


def _remote_agent_run_response(
    *,
    agent: str,
    owner: str,
    request_info: RunRequestInfo,
    response_result: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Shape the 202 submission response and expose tracking degradation."""
    resolved = _resolve_remote_run(owner)
    _stamp_remote_request_info(
        run_id=resolved.run_id, owner=owner, request_info=request_info
    )
    result = (
        empty_agent_result(degraded=True)
        if resolved.degraded_tracking
        else response_result
    )
    body = build_agent_run_response(
        run_id=resolved.run_id,
        agent=agent,
        status="running",
        task_ids=resolved.task_ids,
        result=result,
        persisted=resolved.persisted,
        degraded_tracking=resolved.degraded_tracking,
    )
    return body, 202


async def _sync_agent_run_response(
    *,
    agent: str,
    owner: str,
    request_info: RunRequestInfo,
    result: dict[str, Any],
    response_result: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Persist and shape a terminal synchronous agent response."""
    try:
        persistence_context = (
            trace_data_stage(
                DataStage.RUN_PERSIST,
                dependency="run_registry",
            )
            if agent == "data"
            else nullcontext()
        )
        async with persistence_context:
            run_id = _record_sync_run(
                agent=agent,
                owner=owner,
                result=result,
                request_info=request_info,
            )
    except run_lifecycle.RunPersistenceError as exc:
        raise SafeApiError(
            status_code=500,
            code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
            message="The completed run could not be persisted.",
            stage="run_persist",
            retryable=False,
        ) from exc
    canonical = canonicalize_agent_run_body(
        {
            "id": run_id,
            "object": "agent.run",
            "agent": agent,
            "status": "succeeded",
            "task_ids": [],
            "result": response_result,
        }
    )
    body = build_agent_run_response(
        run_id=run_id,
        agent=agent,
        status="succeeded",
        task_ids=(),
        result=canonical["result"],
        persisted=True,
        degraded_tracking=canonical.get("degraded_tracking") is True,
    )
    return body, 200


async def _invoke_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    dialogue_id: str | None = None,
    request_json: str | None = None,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Dispatch one native run through the shared lifecycle contract."""
    preflight = _preflight_agent_run(
        agent=agent,
        arguments=arguments,
        dialogue_id=dialogue_id,
        request_json=request_json,
    )
    if agent in _BACKGROUND_SUBMISSION_AGENT_SLUGS:
        try:
            return _background_agent_run_response(
                agent=agent,
                arguments=arguments,
                preflight=preflight,
                debug=debug,
            )
        except BackgroundSubmissionLaunchError as exc:
            raise SafeApiError(
                status_code=500,
                code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
                message="The background run could not be started.",
                stage="submission_start",
                retryable=False,
            ) from exc
    prepared = await _prepare_agent_run(
        agent=agent,
        arguments=arguments,
        preflight=preflight,
    )
    if agent == "review":
        execution = await _run_review_with_interrupt(
            arguments=arguments,
            request_info=prepared.request_info,
        )
        return _review_run_body(execution, debug=debug), 200
    try:
        envelope = await invoke_tool_enveloped(prepared.tool_name, arguments)
        format_context = (
            trace_data_stage(
                DataStage.RESULT_FORMAT,
                dependency="formatter",
            )
            if agent == "data"
            else nullcontext()
        )
        async with format_context:
            result, response_result = _format_agent_run_result(
                envelope,
                resolve_meta=prepared.resolve_meta,
                debug=debug,
            )
        if agent in _REMOTE_AGENT_SLUGS:
            return _remote_agent_run_response(
                agent=agent,
                owner=prepared.owner,
                request_info=prepared.request_info,
                response_result=response_result,
            )
        return await _sync_agent_run_response(
            agent=agent,
            owner=prepared.owner,
            request_info=prepared.request_info,
            result=result,
            response_result=response_result,
        )
    except SafeApiError:
        raise
    except Exception as exc:
        safe_error = (
            _factory.project_data_stage_error(exc) if agent == "data" else None
        )
        if safe_error is None:
            raise
        raise safe_error from exc


def _resolve_remote_run(owner: str) -> run_lifecycle.ResolvedRemoteRun:
    """Compatibility seam for remote-run context recovery."""
    return run_lifecycle.resolve_remote_run(
        owner,
        run_id=current_run_id(),
        accepted_task_ids=current_accepted_task_ids(),
        recorder_degraded=current_recorder_degraded(),
        db_path=resolve_tasks_db_path(),
    )


async def _route_expert_query(
    payload: ExpertQueryRequest, *, debug: bool
) -> tuple[dict[str, Any], int]:
    """Route one constrained Expert request to a native agent run."""
    try:
        selection = await select_agent_tool(
            payload.user_query,
            payload.history,
            allowed_tools=payload.allowed_tools,
            forced_tool=payload.forced_tool,
        )
    except ExpertRoutingContractError as exc:
        _LOGGER.warning(
            "Expert routing selection contract failed (%s)",
            exc.__class__.__name__,
        )
        raise _routing_contract_error() from exc
    except ExpertProviderTimeoutError as exc:
        raise expert_safe_error(
            SafeErrorCode.UPSTREAM_TIMEOUT,
            status_code=504,
            locale=current_effective_locale(),
            stage="routing",
            retryable=True,
        ) from exc
    except ExpertProviderError as exc:
        raise expert_safe_error(
            SafeErrorCode.ROUTING_UPSTREAM_FAILED,
            status_code=502,
            locale=current_effective_locale(),
            stage="routing",
            retryable=True,
        ) from exc
    if selection is None:
        raise _routing_contract_error()
    slug = _TOOL_TO_AGENT_SLUG.get(selection.tool_name)
    if slug is None:
        _LOGGER.warning("Expert routing selected an unavailable tool")
        raise _routing_contract_error()
    request_json = json.dumps(
        {
            "agent": slug,
            "tool_name": selection.tool_name,
            "user_query": payload.user_query,
            "dialogue_id": payload.dialogue_id,
            "locale": current_effective_locale(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    owner = current_request_user() or "anonymous"
    arguments = prepare_expert_arguments(
        slug,
        selection.arguments,
        obs_file_list=payload.obs_file_list,
        owner=owner,
        db_path=resolve_tasks_db_path(),
    )
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
            raise expert_safe_error(
                SafeErrorCode.SELECTED_AGENT_INVALID_ARGUMENT,
                status_code=400,
                locale=current_effective_locale(),
                stage="dispatch_validation",
                retryable=False,
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


def _list_owner_runs(
    *,
    owner: str,
    query: run_lifecycle.RunListQuery,
    debug: bool = False,
) -> dict[str, Any]:
    """Compatibility seam for owner-scoped run listing."""
    return run_lifecycle.list_owner_runs(
        owner,
        query,
        debug=debug,
        context=run_lifecycle.RunLifecycleContext(
            db_path=resolve_tasks_db_path(),
            purge=_purge_expired_runs_best_effort,
            project=_project_public_run_record,
        ),
    )


_ReviewExecution = a2ui_runtime.ReviewExecution


def _review_stream_app() -> Any:
    """Compatibility seam for the ReviewAgent compiled graph."""
    return a2ui_runtime.build_review_stream_app()


def _review_initial_state(args: ReviewAgentArgs) -> Mapping[str, Any]:
    """Compatibility seam for ReviewAgent initial-state construction."""
    return a2ui_runtime.build_review_initial_state(args)


def _validate_review_arguments(arguments: dict[str, Any]) -> ReviewAgentArgs:
    """Compatibility seam for ReviewAgent argument validation."""
    return a2ui_runtime.validate_review_arguments(arguments)


def _review_interrupt_result(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility seam for paused Review result projection."""
    return a2ui_runtime.review_interrupt_result(interrupt)


def _maybe_project_review_interrupt(
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for Review interrupt projection."""
    return a2ui_runtime.project_review_interrupt(interrupt)


def _review_interrupt_body(
    *,
    thread_id: str,
    interrupt: Mapping[str, Any],
) -> dict[str, Any]:
    """Compatibility seam for the paused Review HTTP body."""
    return a2ui_runtime.review_interrupt_body(
        thread_id=thread_id,
        interrupt=interrupt,
    )


def _format_review_result(
    final_state: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility seam for terminal Review result formatting."""
    return a2ui_runtime.format_review_result(
        final_state,
        arguments=arguments,
    )


def _review_run_body(
    execution: _ReviewExecution,
    *,
    debug: bool,
) -> dict[str, Any]:
    """Compatibility seam for Review run response shaping."""
    return a2ui_runtime.review_run_body(execution, debug=debug)


async def _run_review_with_interrupt(
    *,
    arguments: dict[str, Any],
    request_info: RunRequestInfo,
) -> _ReviewExecution:
    """Compatibility seam for interrupt-aware Review execution."""
    return await a2ui_runtime.run_review_with_interrupt(
        arguments=arguments,
        request_info=request_info,
        dependencies=_a2ui_runtime_dependencies(),
    )


async def _resume_review_run(
    *,
    thread_id: str,
    payload: ResumeRequest,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Compatibility seam for Review pause/resume execution."""
    return await a2ui_runtime.resume_review_run(
        thread_id=thread_id,
        payload=payload,
        debug=debug,
        dependencies=_a2ui_runtime_dependencies(),
    )


async def _review_chat_completion_response(
    *,
    payload: ChatCompletionRequest,
    arguments: Mapping[str, object],
    user_query: str,
) -> JSONResponse:
    """Return the ReviewAgent non-stream chat response or interrupt body."""
    execution = await _run_review_with_interrupt(
        arguments=dict(arguments),
        request_info=a2ui_runtime.build_review_request_info(
            payload, user_query
        ),
    )
    if execution.interrupt is not None:
        # A ReviewAgent pause is not an OpenAI chat completion; return
        # the native interrupt body so clients can resume with the Bot
        # run id without guessing inside choices[].
        return JSONResponse(_review_run_body(execution, debug=True))
    result = execution.result or {
        **empty_agent_result(),
        "raw": None,
    }
    completion = to_chat_completion(
        result.get("formatted", {}),
        result.get("raw"),
        payload.model,
        result.get("execution"),
    )
    completion["run_id"] = execution.run_id
    if not resolve_debug(payload.debug):
        completion = strip_chat_completion(completion)
    return JSONResponse(completion)


async def _stream_chat_response(
    *,
    tool_name: str,
    arguments: dict[str, object],
    payload: ChatCompletionRequest,
    user_query: str,
) -> StreamingResponse:
    """Validate and build a streaming chat response."""
    if tool_name == "ReviewAgent":
        if not ApiConfig().A2UI_ENABLED:
            raise HTTPException(
                status_code=400,
                detail=(
                    "streaming is not supported for human-in-the-loop "
                    "review; use stream=false and POST /v1/runs/{id}/resume"
                ),
            )
        return await _stream_review_a2ui_pause(
            arguments=dict(arguments),
            payload=payload,
            user_query=user_query,
        )
    if not tool_accepts_stream(tool_name):
        raise HTTPException(
            status_code=400,
            detail=f"streaming is not supported for model {payload.model}",
        )
    return await _stream_chat_completion(
        tool_name=tool_name,
        arguments=arguments,
        payload=payload,
        user_query=user_query,
    )


def _project_deep_genome_run(
    record: RunRecord, *, debug: bool = False
) -> dict[str, Any]:
    """Compatibility seam for DeepGenome public projection."""
    return run_lifecycle.project_deep_genome_run(
        record,
        debug=debug,
        db_path=resolve_tasks_db_path(),
    )


def _project_public_run_record(
    record: RunRecord, *, debug: bool = False
) -> dict[str, Any]:
    """Compatibility seam for the public run projection."""
    return run_lifecycle.project_public_run_record(
        record,
        debug=debug,
        db_path=resolve_tasks_db_path(),
    )


async def _fetch_owner_run(
    run_id: str, *, debug: bool = False
) -> dict[str, Any]:
    """Compatibility seam for owner-checked run lookup and reconciliation."""
    return await run_lifecycle.fetch_owner_run(
        run_id,
        owner=current_request_user() or "anonymous",
        debug=debug,
        db_path=resolve_tasks_db_path(),
    )


def _record_sync_run(
    *,
    agent: str,
    owner: str,
    result: dict[str, Any],
    request_info: RunRequestInfo | None = None,
) -> str:
    """Compatibility seam for terminal synchronous run creation."""
    return run_lifecycle.record_sync_run(
        agent=agent,
        owner=owner,
        result=result,
        request_info=request_info,
        db_path=resolve_tasks_db_path(),
    )


def _a2a_runtime_dependencies() -> a2a_runtime.A2ARuntimeDependencies:
    """Bind app compatibility seams into the A2A runtime record."""
    registry = a2a_runtime.A2ARegistryDependencies(
        registry_factory=RunRegistry,
        current_user=current_request_user,
        tasks_db_path=resolve_tasks_db_path,
    )
    return a2a_runtime.A2ARuntimeDependencies(
        registry=registry,
        resume=a2a_runtime.A2AResumeDependencies(
            registry=registry,
            graphs=a2a_runtime.A2AGraphDependencies(
                chat_graph=_chat_a2ui_stream_app,
                review_graph=_review_stream_app,
                resume_graph=_resume_paused_run,
            ),
            projection=a2a_runtime.A2AProjectionDependencies(
                resume_payload=_a2a_resume_payload,
                interrupts=a2a_runtime.A2AInterruptDependencies(
                    chat_interrupt_result=_chat_a2ui_interrupt_result,
                    review_interrupt_result=_review_interrupt_result,
                    project_review_interrupt=_maybe_project_review_interrupt,
                    chat_interrupt_body=_a2ui_interrupt_body,
                    review_interrupt_body=_review_interrupt_body,
                ),
                format_chat_result=_format_chat_a2ui_result,
                format_review_result=_format_review_result,
            ),
        ),
    )


def _record_a2a_registration(registration: A2ARegistration) -> None:
    """Compatibility seam for A2A registration persistence."""
    a2a_runtime.record_registration(
        registration,
        dependencies=_a2a_runtime_dependencies().registry,
    )


def _get_a2a_task(task_id: str, history_length: int) -> Any:
    """Return an owner-scoped A2A task projection, or ``None``."""
    return a2a_runtime.get_task(
        task_id,
        history_length,
        dependencies=_a2a_runtime_dependencies().registry,
    )


def _a2a_resume_payload(
    agent: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate A2A input data into the shared resume payload."""
    return a2a_runtime.resume_payload(agent, arguments)


async def _resume_a2a_task(
    task_id: str,
    context_id: str,
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], int] | None:
    """Compatibility seam for owner-scoped A2A pause resumption."""
    return await a2a_runtime.resume_task(
        task_id,
        context_id,
        arguments,
        dependencies=_a2a_runtime_dependencies().resume,
    )


def _create_running_stream_run(
    run_id: str, agent: str, owner: str, request_info: RunRequestInfo
) -> None:
    """Compatibility seam for initial streaming run creation."""
    run_lifecycle.create_running_stream_run(
        run_id,
        agent,
        owner,
        request_info,
        db_path=resolve_tasks_db_path(),
    )


def _stream_answer_max_bytes() -> int:
    """Return the resolved soft cap for streamed chat answer storage."""
    return resolve_stream_answer_max_bytes(ApiConfig().STREAM_ANSWER_MAX_BYTES)


def _settle_stream_run(
    run_id: str,
    owner: str,
    status: str,
    result: dict[str, Any],
) -> bool:
    """Compatibility seam for streaming run settlement."""
    return run_lifecycle.settle_stream_run(
        run_id,
        owner,
        status,
        result,
        context=run_lifecycle.RunLifecycleContext(
            db_path=resolve_tasks_db_path(),
            purge=_purge_expired_runs_best_effort,
        ),
    )


def _stamp_remote_request_info(
    *,
    run_id: str | None,
    owner: str,
    request_info: RunRequestInfo,
) -> None:
    """Compatibility seam for remote request metadata stamping."""
    run_lifecycle.stamp_remote_request_info(
        run_id=run_id,
        owner=owner,
        request_info=request_info,
        db_path=resolve_tasks_db_path(),
    )


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Returns:
        Configured FastAPI app exposing liveness/readiness probes and the
        unified error envelope. Authenticated routes are added by later
        API layers.

    The implementation delegates to the typed factory while retaining the
    established public seam. The factory continues to wire
    ``invoke_tool_enveloped``, ``invoke_tool_streamed``, ``_invoke_agent_run``,
    ``_route_expert_query``, and ``resolve_chat_query`` through this module so
    existing integrations and tests can patch those names.
    """
    return _factory.build_app()
