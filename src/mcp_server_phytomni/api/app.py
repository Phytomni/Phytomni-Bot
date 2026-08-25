# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""FastAPI application factory for the external HTTP API.

Public functions: create_app.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Unpack, cast

from fastapi import (
    FastAPI,
    HTTPException,
)
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS

from ..agents.expert import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingContractError,
    ExpertRoutingDeclinedError,
    ToolSelection,
    select_agent_tool,
)
from ..agents.expert.routing_observability import (
    ExpertRouteOutcome,
    ExpertRoutePath,
    record_expert_route_outcome,
)
from ..common import logging_config as _logging_config
from ..config.defaults import ApiConfig, ServerConfig
from ..interop import a2a_discovery as _a2a_discovery
from ..interop import capabilities as _interop_capabilities
from ..interop import registry as _interop_registry
from ..mcp import app as _mcp_app
from ..mcp.app import prepare_tool_stream
from ..mcp.result_formatting import (
    resolve_debug,
    strip_agent_result,
    strip_chat_completion,
)
from ..mcp.schemas import ReviewAgent as ReviewAgentArgs
from ..public_agent_catalog import (
    PUBLIC_AGENT_CATALOG,
)
from ..public_agent_catalog import (
    agent_slug_to_tool as _catalog_slug_to_tool,
)
from ..public_agent_catalog import (
    legacy_aliases as _catalog_legacy_aliases,
)
from ..public_agent_catalog import (
    remote_agent_slugs as _catalog_remote_agent_slugs,
)
from ..runtime import request_context as _request_context
from ..runtime import stage_trace as _stage_trace
from ..runtime import task_reconcile as _task_reconcile
from ..runtime.attachment_assets import ResolvedAttachmentBundle
from ..runtime.execution_entrypoint_v2 import (
    invoke_public_agent,
    invoke_public_agent_operation,
    invoke_public_agent_stream_response,
)
from ..runtime.execution_identity_v2 import new_execution_id
from ..runtime.execution_instrumentation_v2 import current_execution_boundary
from ..runtime.execution_journal_v2 import ExecutionStatus
from ..runtime.execution_reservation_v2 import (
    ExecutionReservationConflictError,
    ExecutionReservationNotFoundError,
    SQLiteExecutionReservationRepository,
)
from ..runtime.locale import current_effective_locale
from ..runtime.resume import ahas_checkpoint as _runtime_has_checkpoint
from ..runtime.run_registry import (
    RunRecord,
    RunRegistry,
    RunRequestInfo,
)
from ..runtime.task_manager import resolve_tasks_db_path
from ..storage.path_policy import IdFactory
from ..version import __version__ as _package_version
from . import a2ui_runtime, run_lifecycle
from . import admin_auth as _admin_auth
from . import agent_capabilities as _agent_capabilities
from . import agent_runs as _agent_runs
from . import app_support as _app_support
from . import attachments as _attachments
from . import compat as _compat
from . import factory as _factory
from . import ratelimit as _ratelimit
from . import relay as _relay
from . import resolvers as _resolvers
from .a2a import card as _a2a_card
from .a2a import runtime as a2a_runtime
from .a2a.executor import (
    A2ARegistration,
)
from .agent_run_support import running_agent_run_response, stream_run_id
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
from .expert_routing_errors import (
    expert_routing_contract_error,
    expert_routing_provider_error,
)
from .lifecycle_contract import (
    SafeApiError,
    SafeErrorCode,
    empty_agent_result,
    expert_safe_error,
)
from .openai_mapping import (
    to_chat_completion,
    tool_accepts_stream,
)
from .research_input import (
    ResearchAdmissionRequest,
    ResearchCoordinatorRequest,
)
from .routes.attachment_inputs import (
    ResolvedAttachmentInput,
    build_expert_research_admission,
    prepare_selected_expert_arguments,
    restrict_expert_payload_for_research,
)
from .schemas import (
    ChatCompletionRequest,
    ChatMessage,
    ChatStreamCall,
    ExpertQueryRequest,
    ResumeRequest,
)
from .stream_answer import resolve_stream_answer_max_bytes


def _compatibility_export(name: str) -> Any:
    """Preserve the original ``api.app`` module identity for moved seams."""
    value = getattr(_agent_runs, name)
    if hasattr(value, "__module__"):
        value.__module__ = __name__
    return value


_AgentRunPreparation = _compatibility_export("_AgentRunPreparation")
_AgentRunPreflight = _compatibility_export("_AgentRunPreflight")
_format_agent_run_result = _compatibility_export("_format_agent_run_result")
_invoke_agent_run = _compatibility_export("_invoke_agent_run")
_prepare_agent_run = _compatibility_export("_prepare_agent_run")
_preflight_agent_run = _compatibility_export("_preflight_agent_run")
_remote_agent_run_response = _compatibility_export(
    "_remote_agent_run_response"
)
_resolve_remote_run = _compatibility_export("_resolve_remote_run")
_sync_agent_run_response = _compatibility_export("_sync_agent_run_response")
apply_runs_resolver = _agent_runs.apply_runs_resolver
invoke_tool_enveloped = _agent_runs.invoke_tool_enveloped
resolve_brief_gene_user_query = _agent_runs.resolve_brief_gene_user_query
resolve_deep_genome_user_query = _agent_runs.resolve_deep_genome_user_query
resolve_design_user_query = _agent_runs.resolve_design_user_query
resolve_network_user_query = _agent_runs.resolve_network_user_query
validate_tool_arguments = _agent_runs.validate_tool_arguments
_project_warnings = getattr(_agent_runs, "_project_warnings")
validate_native_attachments = _attachments.validate_native_attachments
DataStage = _stage_trace.DataStage
trace_data_stage = _stage_trace.trace_data_stage

# These assignments keep long-standing app-level monkeypatch seams available
# after route wiring moved to ``api.factory``.
invoke_tool_streamed = _mcp_app.invoke_tool_streamed
current_accepted_task_ids = _request_context.current_accepted_task_ids
current_recorder_degraded = _request_context.current_recorder_degraded
current_request_id = _request_context.current_request_id
current_request_user = _request_context.current_request_user
current_run_id = _request_context.current_run_id
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
serialize_execution_runtime_capability = (
    _agent_capabilities.serialize_execution_runtime_capability
)
serialize_file_upload_capability = (
    _agent_capabilities.serialize_file_upload_capability
)
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
    item.model: item.slug for item in PUBLIC_AGENT_CATALOG if item.model
}

# Full ``slug -> MCP tool name`` map for the native ``/v1/agents``
# endpoints. Slugs mirror the ``agents/<domain>/`` directory naming
# so the run table speaks the same vocabulary as the in-process agent
# packages.
_AGENT_SLUG_TO_TOOL = _catalog_slug_to_tool()

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
_REMOTE_AGENT_SLUGS = _catalog_remote_agent_slugs()


_EXPERT_STREAM_MODELS = {
    "chat": "phyto-chat",
    "knowledge": "phyto-knowledge",
    "brief_gene": "phyto-brief-gene",
}

# Historical Web ``tool_name`` aliases preserved on ``/v1/agents`` rows
# as ``legacy_aliases`` metadata. The route itself never accepts these
# as routing slugs; chat-ai and Phytomni-Web Go consume the list to
# build their own alias→slug translation table without out-of-band
# negotiation. Bot-added agents (brief_gene / design / network) ship
# an empty list so the shape stays uniform and a future agent must
# make an explicit declaration rather than silently inherit ``[]``.
_LEGACY_ALIASES: dict[str, list[str]] = _catalog_legacy_aliases()


_LOGGER = logging.getLogger(__name__)


def _routing_contract_error() -> SafeApiError:
    """Return one sanitized Expert routing contract failure."""
    return expert_routing_contract_error()


def _record_v0_route_outcome(
    outcome: ExpertRouteOutcome,
    *,
    payload: ExpertQueryRequest,
    http_status: int,
    error: BaseException | None = None,
) -> None:
    """Log one V0 selection-stage outcome without the query body."""
    record_expert_route_outcome(
        outcome,
        path=ExpertRoutePath.V0,
        forced=payload.forced_tool is not None,
        error_class=None if error is None else type(error).__name__,
        http_status=http_status,
    )


async def _start_routed_expert_stream(
    *,
    slug: str,
    tool_name: str,
    arguments: dict[str, Any],
    payload: ExpertQueryRequest,
    debug: bool,
) -> tuple[dict[str, Any], int]:
    """Start one selected stream-family run and expose its durable id."""
    response = await _stream_chat_completion(
        tool_name=tool_name,
        arguments=arguments,
        payload=ChatCompletionRequest(
            model=_EXPERT_STREAM_MODELS[slug],
            messages=[ChatMessage(role="user", content=payload.user_query)],
            stream=True,
            dialogue_id=payload.dialogue_id,
            debug=debug,
            locale=current_effective_locale(),
        ),
        user_query=payload.user_query,
    )
    run_id = await stream_run_id(response)
    if not run_id:
        raise HTTPException(status_code=500, detail="stream run is missing")
    return running_agent_run_response(
        run_id=run_id,
        agent=slug,
    )


async def _select_expert_routing(
    payload: ExpertQueryRequest,
) -> tuple[ToolSelection, str]:
    """Run the one canonical Expert selector and resolve its public slug."""
    try:
        selection = await select_agent_tool(
            payload.user_query,
            payload.history,
            allowed_tools=payload.allowed_tools,
            forced_tool=payload.forced_tool,
        )
    except ExpertRoutingDeclinedError as exc:
        if payload.forced_tool is not None or "ChatAgent" not in (
            payload.allowed_tools
        ):
            _record_v0_route_outcome(
                ExpertRouteOutcome.DECLINED_NO_FALLBACK,
                payload=payload,
                http_status=502,
                error=exc,
            )
            raise _routing_contract_error() from exc
        else:
            _record_v0_route_outcome(
                ExpertRouteOutcome.DECLINED_CHAT_FALLBACK,
                payload=payload,
                http_status=202,
                error=exc,
            )
            selection = ToolSelection(
                tool_name="ChatAgent",
                arguments={"user_query": payload.user_query},
            )
    except ExpertRoutingContractError as exc:
        _record_v0_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            payload=payload,
            http_status=502,
            error=exc,
        )
        raise _routing_contract_error() from exc
    except ExpertProviderError as exc:
        _record_v0_route_outcome(
            (
                ExpertRouteOutcome.PROVIDER_TIMEOUT
                if isinstance(exc, ExpertProviderTimeoutError)
                else ExpertRouteOutcome.PROVIDER_ERROR
            ),
            payload=payload,
            http_status=(
                504 if isinstance(exc, ExpertProviderTimeoutError) else 502
            ),
            error=exc,
        )
        raise expert_routing_provider_error(exc) from exc
    if selection is None:
        _record_v0_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            payload=payload,
            http_status=502,
        )
        raise _routing_contract_error()
    slug = _TOOL_TO_AGENT_SLUG.get(selection.tool_name)
    if slug is None:
        _record_v0_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            payload=payload,
            http_status=502,
        )
        raise _routing_contract_error()
    return selection, slug


async def _route_expert_query(
    payload: ExpertQueryRequest,
    *,
    debug: bool,
    attachment_input: ResolvedAttachmentInput | None = None,
    idempotency_key: str | None = None,
    execution_id: str | None = None,
    research_runtime_options: _agent_runs.ResearchHttpRuntimeOptions = (
        _agent_runs.ResearchHttpRuntimeOptions()
    ),
) -> tuple[dict[str, Any], int]:
    """Route one constrained Expert request to a native agent run."""
    payload = restrict_expert_payload_for_research(
        payload, ServerConfig().BUCKET_NAME
    )
    selection, slug = await _select_expert_routing(payload)
    request_json = json.dumps(
        {
            "agent": slug,
            "tool_name": selection.tool_name,
            "dialogue_id": payload.dialogue_id,
            "locale": current_effective_locale(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    resolved = attachment_input or ResolvedAttachmentInput(
        attachment_owner=current_request_user() or "anonymous",
        bundle=ResolvedAttachmentBundle(assets=()),
    )
    if selection.tool_name == "InSilicoResearchAgent":
        admission = build_expert_research_admission(
            payload,
            resolved,
            idempotency_key=idempotency_key,
            route_source="expert",
        )
        return await _agent_runs.invoke_research_http_run_via_runtime(
            admission,
            resolved.bundle,
            arguments=selection.arguments,
            config=ApiConfig(),
            db_path=resolve_tasks_db_path(),
            execution_id=execution_id,
            transport="expert_router",
            runtime_options=research_runtime_options,
        )
    arguments, attachment_context = prepare_selected_expert_arguments(
        agent=slug,
        selected_arguments=selection.arguments,
        payload=payload,
        resolved_input=resolved,
        db_path=resolve_tasks_db_path(),
    )
    try:
        if slug in _EXPERT_STREAM_MODELS:
            # Stream schemas historically omit empty file lists. Fill that
            # optional field only for the Expert dispatch gate so a missing
            # user_query still maps to selected_agent_invalid_argument.
            schema_arguments = dict(arguments)
            schema_arguments.setdefault("obs_file_list", [])
            validate_tool_arguments(selection.tool_name, schema_arguments)
            return await _start_routed_expert_stream(
                slug=slug,
                tool_name=selection.tool_name,
                arguments=arguments,
                payload=payload,
                debug=debug,
            )
        return await _invoke_agent_run(
            agent=slug,
            arguments=arguments,
            dialogue_id=payload.dialogue_id,
            request_json=request_json,
            debug=debug,
            attachment_evidence=attachment_context.evidence,
            execution_id=execution_id,
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


async def _format_review_result(
    final_state: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility seam for terminal Review result formatting."""
    return await a2ui_runtime.format_review_result(
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
    attachment_evidence: _attachments.ManagedAttachmentEvidence | None = None,
) -> _ReviewExecution:
    """Execute Review through one Runtime, including direct OpenAI calls."""
    execution_id = request_info.execution_id or new_execution_id()
    effective_request = replace(request_info, execution_id=execution_id)

    async def run_domain() -> _ReviewExecution:
        return await a2ui_runtime.run_review_with_interrupt(
            arguments=arguments,
            request_info=effective_request,
            dependencies=_a2ui_runtime_dependencies(),
            attachment_evidence=attachment_evidence,
        )

    boundary = current_execution_boundary()
    if boundary is not None:
        return await run_domain()

    def review_status(value: _ReviewExecution) -> ExecutionStatus:
        if value.status == "input_required":
            return ExecutionStatus.WAITING_INPUT
        if value.status == "failed":
            return ExecutionStatus.FAILED
        return ExecutionStatus.SUCCEEDED

    return await invoke_public_agent(
        db_path=resolve_tasks_db_path(),
        owner=current_request_user() or "anonymous",
        execution_id=execution_id,
        agent_slug="review",
        arguments=arguments,
        transport="openai_blocking",
        call=run_domain,
        status_mapper=review_status,
        public_result_mapper=lambda value: value.result or {},
    )


async def _execute_review_with_run_id(
    *,
    run_id: str,
    arguments: dict[str, Any],
    attachment_evidence: _attachments.ManagedAttachmentEvidence | None = None,
) -> _ReviewExecution:
    """Execute Review against a run reserved by the background runtime."""
    return await a2ui_runtime.execute_review_with_run_id(
        run_id=run_id,
        arguments=arguments,
        dependencies=_a2ui_runtime_dependencies(),
        attachment_evidence=attachment_evidence,
    )


async def _resume_review_run(
    *,
    thread_id: str,
    payload: ResumeRequest,
    debug: bool = False,
) -> tuple[dict[str, Any], int]:
    """Compatibility seam for Review pause/resume execution."""
    path = resolve_tasks_db_path()
    owner = _request_context.current_request_user() or "anonymous"

    async def resume_domain() -> tuple[dict[str, Any], int]:
        return await a2ui_runtime.resume_review_run(
            thread_id=thread_id,
            payload=payload,
            debug=debug,
            dependencies=_a2ui_runtime_dependencies(),
        )

    record = RunRegistry(path).get_run(thread_id, owner=owner)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"run not found: {thread_id}",
        )
    execution_id = record.request_info.execution_id
    if execution_id is None:
        raise HTTPException(
            status_code=409,
            detail="legacy A2UI execution is read-only",
        )
    reservations = SQLiteExecutionReservationRepository(path)
    try:
        reservation = reservations.get(owner=owner, execution_id=execution_id)
    except ExecutionReservationNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail="execution runtime reservation is unavailable",
        ) from exc
    if reservation.agent_slug != "review":
        raise HTTPException(
            status_code=409,
            detail="execution runtime agent mismatch",
        )
    encoded = payload.model_dump_json().encode("utf-8")
    action_hash = hashlib.sha256(encoded).hexdigest()[:16]
    try:
        return await invoke_public_agent_operation(
            db_path=path,
            owner=owner,
            execution_id=execution_id,
            agent_slug="review",
            operation="resume",
            action_id=f"review:{thread_id}:{action_hash}",
            expected_revision=reservation.supervisor_revision,
            arguments=payload.model_dump(mode="json"),
            transport="resume",
            call=resume_domain,
        )
    except ExecutionReservationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="This input request has already been handled.",
        ) from exc


async def _review_chat_completion_response(
    *,
    payload: ChatCompletionRequest,
    arguments: Mapping[str, object],
    user_query: str,
    attachment_evidence: _attachments.ManagedAttachmentEvidence | None = None,
    execution_id: str | None = None,
) -> JSONResponse:
    """Return the ReviewAgent non-stream chat response or interrupt body."""
    execution = await _run_review_with_interrupt(
        arguments=dict(arguments),
        request_info=a2ui_runtime.build_review_request_info(
            payload, user_query, execution_id=execution_id
        ),
        attachment_evidence=attachment_evidence,
    )
    if execution.interrupt is not None:
        # A ReviewAgent pause is not an OpenAI chat completion; return
        # the native interrupt body so clients can resume with the Bot
        # run id without guessing inside choices[].
        body = _review_run_body(execution, debug=True)
        if attachment_evidence is not None:
            body = _attachments.redact_managed_attachment_values(
                body, attachment_evidence
            )
        return JSONResponse(body)
    result = execution.result or {
        **empty_agent_result(),
        "raw": None,
    }
    if attachment_evidence is not None:
        result = _attachments.redact_managed_attachment_values(
            result, attachment_evidence
        )
    completion = to_chat_completion(
        result.get("formatted", {}),
        result.get("raw"),
        payload.model,
        result.get("execution"),
    )
    if attachment_evidence is not None:
        completion = _attachments.redact_managed_attachment_values(
            completion, attachment_evidence
        )
    completion["run_id"] = execution.run_id
    if not resolve_debug(payload.debug):
        completion = strip_chat_completion(completion)
    return JSONResponse(completion)


async def _stream_chat_response(
    **request: Unpack[ChatStreamCall],
) -> StreamingResponse:
    """Validate and build a streaming chat response."""
    tool_name = request["tool_name"]
    payload = request["payload"]
    agent_slug = _TOOL_TO_AGENT_SLUG.get(tool_name)
    if agent_slug is None:
        raise HTTPException(
            status_code=404, detail=f"unknown tool: {tool_name}"
        )
    if tool_name != "ReviewAgent" and not tool_accepts_stream(tool_name):
        raise HTTPException(
            status_code=400,
            detail=f"streaming is not supported for model {payload.model}",
        )
    path = resolve_tasks_db_path()
    owner = _request_context.current_request_user() or "anonymous"
    public_execution_id = request.get("execution_id") or new_execution_id()
    runtime_run_id = IdFactory().new_id("run", agent_slug)
    prepared_events = None
    if tool_name != "ReviewAgent" and payload.conversation is None:
        private_kwargs: dict[str, Any] = {}
        conversation_messages = request.get("conversation_messages")
        if conversation_messages:
            private_kwargs["conversation_messages"] = conversation_messages
        private_agent_state = request.get("private_agent_state")
        if private_agent_state is not None:
            private_kwargs["private_agent_state"] = private_agent_state
        try:
            prepared_events = prepare_tool_stream(
                tool_name,
                dict(request["arguments"]),
                run_id=runtime_run_id,
                dialogue_id=payload.dialogue_id,
                **private_kwargs,
            )
        except Exception as exc:
            raise _compat.streaming.stream_setup_error(
                exc, priming=False
            ) from exc

    async def build_response(reserved_run_id: str) -> StreamingResponse:
        RunRegistry(path).update_request_info(
            reserved_run_id,
            owner=owner,
            request_info=RunRequestInfo(
                dialogue_id=payload.dialogue_id,
                request_id=_request_context.current_request_id(),
                query=request["user_query"],
                tool_name=tool_name,
                model=payload.model,
                request_json=payload.model_dump_json(),
                locale=current_effective_locale(),
                execution_id=public_execution_id,
            ),
        )
        if tool_name == "ReviewAgent":
            return await _stream_review_a2ui_pause(
                arguments=dict(request["arguments"]),
                payload=payload,
                user_query=request["user_query"],
                runtime_run_id=reserved_run_id,
            )
        stream_request = cast(ChatStreamCall, dict(request))
        stream_request["runtime_run_id"] = reserved_run_id
        if prepared_events is not None:
            stream_request["runtime_prepared_events"] = prepared_events
        return await _stream_chat_completion(**stream_request)

    return await invoke_public_agent_stream_response(
        db_path=path,
        owner=owner,
        execution_id=public_execution_id,
        agent_slug=agent_slug,
        arguments=dict(request["arguments"]),
        transport="openai_stream",
        call=build_response,
        run_id=runtime_run_id,
        allow_terminal_replay=payload.conversation is not None,
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
    run_id: str,
    *,
    debug: bool = False,
    registry_factory: Any = RunRegistry,
) -> dict[str, Any]:
    """Compatibility seam for owner-checked run lookup and reconciliation."""
    return await run_lifecycle.fetch_owner_run(
        run_id,
        owner=current_request_user() or "anonymous",
        debug=debug,
        db_path=resolve_tasks_db_path(),
        registry_factory=registry_factory,
    )


async def _retry_owner_delivery(
    run_id: str, *, registry_factory: Any = RunRegistry
) -> dict[str, Any]:
    """Compatibility seam for owner-scoped archive delivery retry."""
    return await run_lifecycle.retry_owner_delivery(
        run_id,
        owner=current_request_user() or "anonymous",
        db_path=resolve_tasks_db_path(),
        registry_factory=registry_factory,
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
    """Resume one A2A task through the canonical execution Runtime."""
    path = resolve_tasks_db_path()
    owner = current_request_user() or "anonymous"
    record = RunRegistry(path).get_run_by_a2a_task(task_id, owner=owner)
    if record is None:
        return None
    execution_id = record.request_info.execution_id
    if execution_id is None:
        raise HTTPException(
            status_code=409,
            detail="legacy A2A execution is read-only",
        )
    try:
        reservation = SQLiteExecutionReservationRepository(path).get(
            owner=owner,
            execution_id=execution_id,
        )
    except ExecutionReservationNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail="execution runtime reservation is unavailable",
        ) from exc

    async def resume_domain() -> tuple[dict[str, Any], int] | None:
        return await a2a_runtime.resume_task(
            task_id,
            context_id,
            arguments,
            dependencies=_a2a_runtime_dependencies().resume,
        )

    encoded = json.dumps(
        dict(arguments),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    action_hash = hashlib.sha256(encoded).hexdigest()[:16]
    return await invoke_public_agent_operation(
        db_path=path,
        owner=owner,
        execution_id=execution_id,
        agent_slug=record.spec.agent,
        operation="resume",
        action_id=f"a2a:{task_id}:{action_hash}",
        expected_revision=reservation.supervisor_revision,
        arguments=dict(arguments),
        transport="a2a_resume",
        call=resume_domain,
    )


def _stream_answer_max_bytes() -> int:
    """Return the resolved soft cap for streamed chat answer storage."""
    return resolve_stream_answer_max_bytes(ApiConfig().STREAM_ANSWER_MAX_BYTES)


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


def create_app(
    *,
    context_executor: Any | None = None,
    run_registry_factory: Any | None = None,
    research_input_root_request_factory: (
        Callable[[ResearchAdmissionRequest], ResearchCoordinatorRequest] | None
    ) = None,
    research_input_runtime_required: bool = False,
) -> FastAPI:
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
    return _factory.build_app(
        context_executor=context_executor,
        run_registry_factory=run_registry_factory,
        research_input_root_request_factory=(
            research_input_root_request_factory
        ),
        research_input_runtime_required=research_input_runtime_required,
    )
