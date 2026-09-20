# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Native agent-run dispatch helpers.

The public HTTP application keeps these helpers available through
``api.app`` for compatibility.  Dependencies are resolved from that module
at call time so the long-standing monkeypatch seams used by the HTTP tests
remain intact while the route facade stays small.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from importlib import import_module
from inspect import Parameter, Signature
from typing import Any, NotRequired, TypedDict, Unpack, cast

from fastapi.responses import JSONResponse

from ..agents.brief_gene.resolve_query import resolve_brief_gene_user_query
from ..agents.deep_genome.resolve_query import resolve_deep_genome_user_query
from ..agents.design.resolve_query import resolve_design_user_query
from ..agents.network.resolve_query import resolve_network_user_query
from ..agents.research.dispatch_runtime import (
    build_research_object_metadata_port,
)
from ..agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
    ResearchInventoryRequest,
    validate_research_inventory,
)
from ..config.defaults import ApiConfig, ServerConfig
from ..mcp.app import invoke_tool_enveloped, validate_tool_arguments
from ..mcp.result_formatting import strip_agent_result
from ..runtime.execution_entrypoint_v2 import (
    bind_routed_reservation_identity,
    invoke_public_agent,
)
from ..runtime.execution_identity_v2 import new_execution_id
from ..runtime.execution_instrumentation_v2 import current_execution_boundary
from ..runtime.execution_reservation_v2 import (
    ExecutionReservationConflictError,
    ExecutionReservationNotFoundError,
    SQLiteExecutionReservationRepository,
)
from ..runtime.execution_runtime_contracts import (
    ExecutionCommand,
    ExecutionRuntimeError,
)
from ..runtime.locale import current_effective_locale
from ..runtime.research_input_store import ResearchInputStore
from ..runtime.run_registry import RunRegistry
from ..runtime.stage_trace import DataStage
from ..runtime.submission_outcome import (
    project_submission_warnings as _project_warnings,
)
from ..storage.path_policy import IdFactory
from . import research_capabilities, run_lifecycle
from .agent_run_pipeline import (
    AgentRunPreflight as _AgentRunPreflight,
)
from .agent_run_pipeline import (
    AgentRunPreparation as _AgentRunPreparation,
)
from .agent_run_pipeline import (
    existing_execution_identity as _existing_execution_identity,
)
from .agent_run_pipeline import (
    failed_remote_runtime_response as _failed_remote_runtime_response,
)
from .agent_run_pipeline import (
    format_agent_run_result as _format_agent_run_result,
)
from .agent_run_pipeline import preflight_agent_run as _preflight_agent_run
from .agent_run_pipeline import prepare_agent_run as _prepare_agent_run
from .agent_run_pipeline import (
    remote_agent_run_response as _remote_agent_run_response,
)
from .agent_run_pipeline import resolve_remote_run as _resolve_remote_run
from .agent_run_pipeline import (
    sync_agent_run_response as _sync_agent_run_response,
)
from .agent_run_support import running_agent_run_response
from .attachments import (
    AttachmentContractError,
    redact_managed_attachment_values,
    validate_research_attachment_bundle,
)
from .lifecycle_contract import (
    SafeApiError,
    SafeErrorCode,
    build_agent_run_response,
    empty_agent_result,
)
from .research_capabilities import research_input_runtime_capability
from .research_input import (
    ResearchHttpAdmissionInput,
    ResearchInventoryValidator,
    ResearchRoutePreflight,
    launch_research_input_worker,
    parse_idempotency_identity,
    research_input_root_worker_ready,
)
from .resolvers import apply_runs_resolver
from .resumable_uploads import UploadContractError
from .routes.context_helpers import safe_native_request_json

_default_research_input_worker = launch_research_input_worker


@dataclass(frozen=True, slots=True)
class ResearchHttpRuntimeOptions:
    """Runtime policy for the private Research HTTP adapter seam."""

    allow_uninstalled: bool = True


@dataclass(frozen=True, slots=True)
class _ResearchRuntimeLocation:
    """Configuration and storage location for Research admission."""

    config: ApiConfig
    db_path: str


@dataclass(frozen=True, slots=True)
class _ResearchRuntimeIdentity:
    """Public execution identity and transport for Research admission."""

    execution_id: str | None
    transport: str


@dataclass(frozen=True, slots=True)
class _ResearchRuntimeInvocation:
    """Compact request passed into the public Research Runtime root."""

    request: ResearchHttpAdmissionInput
    attachment_bundle: Any
    arguments: Mapping[str, Any]
    location: _ResearchRuntimeLocation
    identity: _ResearchRuntimeIdentity
    runtime_options: ResearchHttpRuntimeOptions


class _ResearchRuntimeCallOptions(TypedDict):
    """Compatible keyword controls for public Research Runtime admission."""

    arguments: Mapping[str, Any]
    config: ApiConfig
    db_path: str
    execution_id: str | None
    transport: str
    runtime_options: NotRequired[ResearchHttpRuntimeOptions]


__all__ = [
    "apply_runs_resolver",
    "invoke_tool_enveloped",
    "resolve_brief_gene_user_query",
    "resolve_deep_genome_user_query",
    "resolve_design_user_query",
    "resolve_network_user_query",
    "validate_tool_arguments",
    "_project_warnings",
    "_AgentRunPreparation",
    "_AgentRunPreflight",
    "_format_agent_run_result",
    "_invoke_agent_run",
    "_prepare_agent_run",
    "_preflight_agent_run",
    "_remote_agent_run_response",
    "_resolve_remote_run",
    "_sync_agent_run_response",
    "execute_native_research_http",
    "invoke_research_http_run",
    "invoke_research_http_run_via_runtime",
    "ResearchHttpRuntimeOptions",
]


def _app_module() -> Any:
    """Load ``api.app`` after module import to retain compatibility seams."""
    return import_module(".app", package=__package__)


def _app_attr(name: str) -> Any:
    """Resolve one private compatibility seam without static access
    warnings."""
    return getattr(_app_module(), name)


async def execute_native_research_http(**options: Any) -> JSONResponse:
    """Build one opaque Research request and dispatch its durable adapter."""
    agent = options["agent"]
    payload = options["payload"]
    request = options["request"]
    arguments = options["arguments"]
    attachment_owner = options["attachment_owner"]
    dependencies = options["dependencies"]
    if any(
        bool(arguments.get(name)) for name in ("data_list", "obs_file_list")
    ):
        raise SafeApiError(
            status_code=422,
            code="research_data_block_invalid",
            message="Research data blocks must use managed attachments.",
            stage="request_validation",
            retryable=False,
        )
    managed_asset_ids = _opaque_attachment_ids(payload.attachments)

    def resolve_bundle(_asset_ids: tuple[str, ...]) -> Any:
        """Resolve managed assets only after the durable replay lookup."""
        resolver = dependencies.upload.asset_resolver
        if callable(resolver):
            resolver = resolver()
        resolved = cast(Any, resolver).resolve_bundle(
            [{"asset_id": asset_id} for asset_id in _asset_ids],
            attachment_owner,
        )
        resolved_ids = tuple(
            getattr(asset, "asset_id", None)
            for asset in getattr(resolved, "all_assets", ())
        )
        if resolved_ids != _asset_ids:
            raise SafeApiError(
                status_code=422,
                code="invalid_upload_metadata",
                message="The attachment reference is invalid.",
                stage="request_validation",
                retryable=False,
            )
        return resolved

    admission = ResearchHttpAdmissionInput(
        owner=attachment_owner,
        idempotency_key=request.headers.get("Idempotency-Key"),
        conversation=payload.conversation,
        original_query=next(
            (
                value
                for key in ("user_query", "query", "research_topic")
                if isinstance(value := arguments.get(key), str)
                and value.strip()
            ),
            "",
        ),
        managed_asset_ids=managed_asset_ids,
        locale=current_effective_locale(),
        interop_mode=arguments.get("interop_mode", "off"),
        interop_targets=tuple(arguments.get("interop_targets", ())),
        route_source=(
            "dedicated_web" if payload.conversation is not None else "native"
        ),
    )
    prepared = {
        key: value
        for key, value in arguments.items()
        if key not in {"data_list", "obs_file_list"}
    }
    body, status_code = await dependencies.native.invoke_agent_run(
        agent=agent,
        arguments=prepared,
        dialogue_id=payload.dialogue_id,
        debug=dependencies.chat.projection.resolve_debug(payload.debug),
        request_json=safe_native_request_json(
            dialogue_id=payload.dialogue_id,
            locale=current_effective_locale(),
            route=agent,
        ),
        attachment_evidence=None,
        research_http_input=admission,
        research_attachment_bundle=resolve_bundle,
        execution_id=options.get("execution_id"),
    )
    return JSONResponse(body, status_code=status_code)


def _opaque_attachment_ids(attachments: Any) -> tuple[str, ...]:
    """Read only opaque managed IDs without touching upload or OBS state."""
    if attachments in (None, ()):
        return ()
    if isinstance(attachments, (str, bytes, bytearray)) or not isinstance(
        attachments, (list, tuple)
    ):
        raise SafeApiError(
            status_code=422,
            code="invalid_upload_metadata",
            message="The attachment reference is invalid.",
            stage="request_validation",
            retryable=False,
        )
    asset_ids: list[str] = []
    for item in attachments:
        if isinstance(item, Mapping):
            asset_id = item.get("asset_id")
        else:
            asset_id = getattr(item, "asset_id", None)
        if not isinstance(asset_id, str) or not asset_id:
            raise SafeApiError(
                status_code=422,
                code="invalid_upload_metadata",
                message="The attachment reference is invalid.",
                stage="request_validation",
                retryable=False,
            )
        asset_ids.append(asset_id)
    return tuple(asset_ids)


def _resolve_attachment_bundle(
    source: Any,
    asset_ids: tuple[str, ...],
) -> Any:
    """Call either a fresh resolver seam or a test-injected bundle source."""
    if not callable(source):
        return source
    try:
        parameters = Signature.from_callable(source).parameters.values()
        accepts_argument = any(
            parameter.kind
            in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_argument = True
    if accepts_argument:
        return source(asset_ids)
    return source()


async def invoke_research_http_run(
    request: ResearchHttpAdmissionInput,
    attachment_bundle: Any,
    *,
    config: ApiConfig,
    db_path: str,
    runtime_options: ResearchHttpRuntimeOptions = ResearchHttpRuntimeOptions(),
) -> tuple[dict[str, Any], int]:
    """Admit one Research HTTP request without generic reservation.

    Direct adapter callers retain an explicit permissive option. The serving
    factory supplies the strict production option.
    """
    RunRegistry(db_path)
    allow_uninstalled = runtime_options.allow_uninstalled

    def resolve_snapshots(
        asset_ids: tuple[str, ...],
    ) -> tuple[ManagedResearchAssetSnapshot, ...]:
        """Resolve and validate assets only for a fresh admission."""
        try:
            bundle = _resolve_attachment_bundle(attachment_bundle, asset_ids)
            resolved_ids = tuple(
                getattr(asset, "asset_id", None)
                for asset in getattr(bundle, "all_assets", ())
            )
            if resolved_ids != asset_ids:
                raise SafeApiError(
                    status_code=422,
                    code="invalid_upload_metadata",
                    message="The attachment reference is invalid.",
                    stage="request_validation",
                    retryable=False,
                )
            return validate_research_attachment_bundle(bundle, config)
        except UploadContractError as exc:
            raise SafeApiError(
                status_code=exc.status_code,
                code=exc.code,
                message="The attachment reference is invalid.",
                stage="request_validation",
                retryable=exc.retryable,
            ) from exc
        except AttachmentContractError as exc:
            code = (
                "research_input_limit_exceeded"
                if exc.code == "attachment_limit_exceeded"
                else exc.code
            )
            status = 413 if code == "research_input_limit_exceeded" else 422
            raise SafeApiError(
                status_code=status,
                code=code,
                message=str(exc),
                stage="request_validation",
                retryable=False,
            ) from exc

    preflight = ResearchRoutePreflight(
        store=ResearchInputStore(db_path),
        config=config,
        managed_snapshot_resolver=resolve_snapshots,
        inventory_validator=_research_inventory_validator(config),
        worker_launcher=(
            None
            if (
                allow_uninstalled
                and launch_research_input_worker
                is _default_research_input_worker
            )
            else launch_research_input_worker
        ),
        runtime_ready=lambda: (
            (allow_uninstalled or research_input_root_worker_ready())
            and (
                research_input_runtime_capability(
                    config,
                    research_capabilities.current_research_relay_snapshot(
                        config
                    ),
                ).ready
            )
        ),
    )
    try:
        outcome = await preflight.admit(request)
    except ValueError as exc:
        raise _research_admission_error(exc) from exc
    if outcome.status_code == 200:
        return _research_replay_response(
            outcome.run_id, request.owner, db_path
        )
    return running_agent_run_response(
        run_id=outcome.run_id,
        agent="research",
    )


async def invoke_research_http_run_via_runtime(
    request: ResearchHttpAdmissionInput,
    attachment_bundle: Any,
    **options: Unpack[_ResearchRuntimeCallOptions],
) -> tuple[dict[str, Any], int]:
    """Adapt the Research domain admission to the one public Runtime root."""
    return await _invoke_research_runtime(
        _ResearchRuntimeInvocation(
            request=request,
            attachment_bundle=attachment_bundle,
            arguments=options["arguments"],
            location=_ResearchRuntimeLocation(
                config=options["config"],
                db_path=options["db_path"],
            ),
            identity=_ResearchRuntimeIdentity(
                execution_id=options["execution_id"],
                transport=options["transport"],
            ),
            runtime_options=options.get(
                "runtime_options", ResearchHttpRuntimeOptions()
            ),
        )
    )


async def _invoke_research_runtime(
    invocation: _ResearchRuntimeInvocation,
) -> tuple[dict[str, Any], int]:
    """Bind one prepared Research admission to its execution identity."""
    request = invocation.request
    db_path = invocation.location.db_path
    public_execution_id = (
        invocation.identity.execution_id or _research_execution_id(request)
    )
    repository = SQLiteExecutionReservationRepository(db_path)
    selected_command = ExecutionCommand(
        agent_slug="research",
        arguments=dict(invocation.arguments),
    )
    try:
        with bind_routed_reservation_identity(
            db_path=db_path,
            owner=request.owner,
            execution_id=public_execution_id,
            command=selected_command,
        ):
            try:
                reservation = repository.get(
                    owner=request.owner,
                    execution_id=public_execution_id,
                )
            except ExecutionReservationNotFoundError:
                reservation = None
            if reservation is not None and reservation.agent_slug != (
                "research"
            ):
                raise SafeApiError(
                    status_code=409,
                    code="execution_identity_conflict",
                    message="Execution identity conflicts with this request.",
                    stage="execution_admission",
                    retryable=False,
                )
            run_id = (
                reservation.run_id
                if reservation is not None
                else IdFactory().new_id("run", "research")
            )
            runtime_request = ResearchHttpAdmissionInput(
                **request.as_dict(), runtime_run_id=run_id
            )

            async def admit() -> tuple[dict[str, Any], int]:
                return await invoke_research_http_run(
                    runtime_request,
                    invocation.attachment_bundle,
                    config=invocation.location.config,
                    db_path=db_path,
                    runtime_options=invocation.runtime_options,
                )

            return await invoke_public_agent(
                db_path=db_path,
                owner=request.owner,
                execution_id=public_execution_id,
                agent_slug="research",
                arguments=dict(invocation.arguments),
                transport=invocation.identity.transport,
                call=admit,
                fingerprint_version=(
                    reservation.fingerprint_version
                    if reservation is not None
                    else 1
                ),
                fingerprint=(
                    reservation.fingerprint
                    if reservation is not None
                    else None
                ),
                run_id=run_id,
            )
    except (ExecutionReservationConflictError, ExecutionRuntimeError) as exc:
        if isinstance(exc, ExecutionRuntimeError) and exc.code != (
            "execution_identity_conflict"
        ):
            raise
        raise SafeApiError(
            status_code=409,
            code="execution_identity_conflict",
            message="Execution identity conflicts with this request.",
            stage="execution_admission",
            retryable=False,
        ) from exc


def _research_execution_id(request: ResearchHttpAdmissionInput) -> str:
    """Derive a stable fallback only when a caller omitted its execution ID."""
    identity = parse_idempotency_identity(
        request.idempotency_key,
        request.conversation,
    )
    return f"turn-research-{identity.canonical_digest[:32]}"


def _research_inventory_validator(
    config: ApiConfig,
) -> ResearchInventoryValidator:
    """Build the active direct or relay validator used after pure parsing."""

    async def validate(
        parsed: Any,
        snapshots: tuple[ManagedResearchAssetSnapshot, ...],
    ) -> None:
        if not parsed.candidates:
            return
        source = ServerConfig()
        port = build_research_object_metadata_port()
        await validate_research_inventory(
            ResearchInventoryRequest(
                parsed_input=parsed,
                managed_assets=snapshots,
                configured_bucket=source.BUCKET_NAME,
                max_managed_references=config.API_MAX_ATTACHMENTS_PER_REQUEST,
                max_pasted_references=config.API_MAX_RESEARCH_DATASET_PATHS,
                max_combined_references=(
                    config.API_MAX_RESEARCH_INPUT_REFERENCES
                ),
            ),
            port,
        )

    return validate


def _research_admission_error(exc: ValueError) -> SafeApiError:
    """Project a domain admission error without exposing private details."""
    message = str(getattr(exc, "safe_message", "") or "").strip()
    message = message or "Research input resolution failed."
    return SafeApiError(
        status_code=int(getattr(exc, "http_status_hint", 400)),
        code=str(getattr(exc, "code", "research_input_resolution_failed")),
        message=message,
        stage=str(getattr(exc, "stage", "input_resolution")),
        retryable=bool(getattr(exc, "retryable", False)),
    )


def _research_replay_response(
    run_id: str, owner: str, db_path: str
) -> tuple[dict[str, Any], int]:
    """Project an exact terminal replay as a canonical agent.run body."""
    record = RunRegistry(db_path).get_run(run_id, owner=owner)
    if record is None:
        raise SafeApiError(
            status_code=500,
            code="run_persistence_failed",
            message="The completed run could not be persisted.",
            stage="run_persist",
            retryable=False,
        )
    public = run_lifecycle.run_record_to_dict(record)
    result = public.get("result")
    if not isinstance(result, Mapping):
        result = empty_agent_result()
    body = build_agent_run_response(
        run_id=run_id,
        agent="research",
        status=str(public.get("status", "running")),
        task_ids=public.get("task_ids", ()),
        result=result,
        persisted=True,
    )
    for field in ("stage", "failure"):
        if field in public:
            body[field] = public[field]
    return body, 200


def _has_in_request_context_execution(request: Mapping[str, Any]) -> bool:
    """Return whether this invoke carries V1 conversation execution fields.

    Native and Expert POSTs omit these fields and keep the detached 202
    worker. Context Data/Review need a completed AgentOutcome so the
    sync staging path can persist a bounded turn instead of 409
    ``conversation_context_turn_in_progress``.
    """
    return (
        request.get("agent_thread_id") is not None
        or request.get("private_agent_state") is not None
        or bool(request.get("conversation_messages"))
    )


async def _invoke_agent_run_request(
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Dispatch a normalized native run request through lifecycle stages."""
    normalized = dict(request)
    execution_id = normalized.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        execution_id = new_execution_id()
        normalized["execution_id"] = execution_id
    request = normalized
    agent = request["agent"]
    arguments = request["arguments"]
    dialogue_id = request.get("dialogue_id")
    request_json = request.get("request_json")
    attachment_evidence = request.get("attachment_evidence")
    preflight = _app_attr("_preflight_agent_run")(
        agent=agent,
        arguments=arguments,
        dialogue_id=dialogue_id,
        request_json=request_json,
        attachment_evidence=attachment_evidence,
        execution_id=request.get("execution_id"),
    )

    async def dispatch() -> tuple[dict[str, Any], int]:
        prepared = await _app_attr("_prepare_agent_run")(
            agent=agent,
            arguments=arguments,
            preflight=preflight,
        )
        return await _dispatch_agent_run_request(request, prepared)

    execution_identity = _existing_execution_identity(
        db_path=_app_attr("resolve_tasks_db_path")(),
        owner=preflight.owner,
        execution_id=execution_id,
    )
    try:
        return await invoke_public_agent(
            db_path=_app_attr("resolve_tasks_db_path")(),
            owner=preflight.owner,
            execution_id=execution_id,
            agent_slug=agent,
            arguments=dict(arguments),
            transport="authenticated_http",
            call=dispatch,
            fingerprint_version=(
                execution_identity[0] if execution_identity is not None else 1
            ),
            fingerprint=(
                execution_identity[1]
                if execution_identity is not None
                else None
            ),
        )
    except ExecutionRuntimeError as exc:
        if exc.code not in {
            "terminal_settlement_failed",
            "terminal_settlement_conflict",
        }:
            if agent in _app_attr("_REMOTE_AGENT_SLUGS"):
                return _failed_remote_runtime_response(
                    owner=preflight.owner,
                    execution_id=execution_id,
                    agent=agent,
                )
            raise
        raise SafeApiError(
            status_code=500,
            code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
            message="The completed run could not be persisted.",
            stage="run_persist",
            retryable=False,
        ) from exc
    except Exception:
        if agent in _app_attr("_REMOTE_AGENT_SLUGS"):
            return _failed_remote_runtime_response(
                owner=preflight.owner,
                execution_id=execution_id,
                agent=agent,
            )
        raise


def _has_review_adapter(request: Mapping[str, Any]) -> bool:
    """Return whether a Review request carries the synchronous adapter."""
    private_state = request.get("private_agent_state")
    return isinstance(private_state, Mapping) and (
        private_state.get("review_adapter") is not None
    )


def _requires_sync_reservation(request: Mapping[str, Any]) -> bool:
    """Return whether this local request must attach its reserved run."""
    execution_id = request.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        return False
    agent = request["agent"]
    if agent in _app_attr("_REMOTE_AGENT_SLUGS"):
        return False
    if agent != "review":
        return True
    return _has_review_adapter(request)


def _attach_prepared_execution(
    request: Mapping[str, Any],
    prepared: _AgentRunPreparation,
) -> str | None:
    """Attach request metadata to the Runtime-reserved local run."""
    if not _requires_sync_reservation(request):
        return None
    execution_id = cast(str, request["execution_id"])
    boundary = current_execution_boundary()
    if (
        boundary is None
        or boundary.context.execution_id != execution_id
        or boundary.context.owner_ref != prepared.owner
    ):
        raise ExecutionRuntimeError("execution_boundary_required")
    run_id = boundary.context.run_id
    assert run_id is not None
    try:
        run_lifecycle.attach_execution_request_info(
            run_id=run_id,
            owner=prepared.owner,
            request_info=prepared.request_info,
            db_path=_app_attr("resolve_tasks_db_path")(),
        )
    except run_lifecycle.RunPersistenceError as exc:
        raise SafeApiError(
            status_code=500,
            code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
            message="The admitted run could not be persisted.",
            stage="run_persist",
            retryable=False,
        ) from exc
    return run_id


async def _invoke_prepared_envelope(
    request: Mapping[str, Any],
    prepared: _AgentRunPreparation,
) -> Any:
    """Invoke the selected tool with context only when supplied."""
    app = _app_module()
    if _has_in_request_context_execution(request):
        return await app.invoke_tool_enveloped(
            prepared.tool_name,
            request["arguments"],
            conversation_messages=request.get("conversation_messages", ()),
            agent_thread_id=request.get("agent_thread_id"),
            private_agent_state=request.get("private_agent_state"),
        )
    return await app.invoke_tool_enveloped(
        prepared.tool_name,
        request["arguments"],
    )


async def _format_prepared_result(
    request: Mapping[str, Any],
    prepared: _AgentRunPreparation,
    envelope: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Format and redact one tool envelope for its public response."""
    agent = request["agent"]
    format_context = (
        _app_attr("trace_data_stage")(
            DataStage.RESULT_FORMAT,
            dependency="formatter",
        )
        if agent == "data"
        else nullcontext()
    )
    async with format_context:
        result, response_result = _app_attr("_format_agent_run_result")(
            envelope,
            resolve_meta=prepared.resolve_meta,
            debug=request.get("debug", False),
        )
    attachment_evidence = request.get("attachment_evidence")
    if attachment_evidence is not None:
        result = redact_managed_attachment_values(result, attachment_evidence)
        result = strip_agent_result(result)
        response_result = strip_agent_result(result)
    return result, response_result


async def _invoke_review_interrupt(
    request: Mapping[str, Any],
    prepared: _AgentRunPreparation,
) -> tuple[dict[str, Any], int]:
    """Run the legacy Review interrupt path for an unadapted request."""
    execution = await _app_attr("_run_review_with_interrupt")(
        arguments=request["arguments"],
        request_info=prepared.request_info,
    )
    return (
        _app_attr("_review_run_body")(
            execution,
            debug=request.get("debug", False),
        ),
        200,
    )


async def _dispatch_agent_run_request(
    request: Mapping[str, Any],
    prepared: _AgentRunPreparation,
) -> tuple[dict[str, Any], int]:
    """Delegate to established business preparation and response shaping."""
    return await _invoke_prepared_agent_run(request, prepared)


async def _invoke_prepared_agent_run(
    request: Mapping[str, Any],
    prepared: _AgentRunPreparation,
) -> tuple[dict[str, Any], int]:
    """Format and settle a request after structural and semantic setup."""
    agent = request["agent"]
    reserved_run_id = _attach_prepared_execution(request, prepared)
    if agent == "review" and not _has_review_adapter(request):
        return await _invoke_review_interrupt(request, prepared)
    try:
        envelope = await _invoke_prepared_envelope(request, prepared)
        result, response_result = await _format_prepared_result(
            request, prepared, envelope
        )
        if agent in _app_attr("_REMOTE_AGENT_SLUGS"):
            return _app_attr("_remote_agent_run_response")(
                agent=agent,
                owner=prepared.owner,
                request_info=prepared.request_info,
                response_result=response_result,
            )
        if reserved_run_id is None:
            raise ExecutionRuntimeError("execution_boundary_required")
        return await _app_attr("_sync_agent_run_response")(
            agent=agent,
            owner=prepared.owner,
            request_info=prepared.request_info,
            result=result,
            response_result=response_result,
            reserved_run_id=reserved_run_id,
        )
    except SafeApiError:
        raise
    except Exception as exc:
        safe_error = (
            getattr(_app_module(), "_factory").project_data_stage_error(exc)
            if agent == "data"
            else None
        )
        if safe_error is None:
            raise
        raise safe_error from exc


def _invoke_agent_run(**kwargs: Any) -> Any:
    """Validate and dispatch one native run through the lifecycle contract.

    The historical implementation was an ``async def`` with eight explicit
    keyword-only parameters.  Python performed that call-time binding before
    creating its coroutine, so the compatibility facade must do the same
    even though the implementation is now kept behind a low-complexity
    request mapping.  ``Signature.bind`` also keeps unknown and missing
    arguments from being silently accepted by the ``**kwargs`` facade.
    """
    bound = _INVOKE_AGENT_RUN_SIGNATURE.bind(**kwargs)
    bound.apply_defaults()
    return _invoke_agent_run_request(bound.arguments)


_INVOKE_AGENT_RUN_ANNOTATIONS = {
    "agent": "str",
    "arguments": "dict[str, Any]",
    "conversation_messages": "tuple[dict[str, str], ...]",
    "agent_thread_id": "str | None",
    "private_agent_state": "Mapping[str, Any] | None",
    "dialogue_id": "str | None",
    "request_json": "str | None",
    "attachment_evidence": "ManagedAttachmentEvidence | None",
    "execution_id": "str | None",
    "debug": "bool",
    "return": "tuple[dict[str, Any], int]",
}
_INVOKE_AGENT_RUN_SIGNATURE = Signature(
    [
        Parameter("agent", Parameter.KEYWORD_ONLY, annotation="str"),
        Parameter(
            "arguments",
            Parameter.KEYWORD_ONLY,
            annotation="dict[str, Any]",
        ),
        Parameter(
            "conversation_messages",
            Parameter.KEYWORD_ONLY,
            annotation="tuple[dict[str, str], ...]",
            default=(),
        ),
        Parameter(
            "agent_thread_id",
            Parameter.KEYWORD_ONLY,
            annotation="str | None",
            default=None,
        ),
        Parameter(
            "private_agent_state",
            Parameter.KEYWORD_ONLY,
            annotation="Mapping[str, Any] | None",
            default=None,
        ),
        Parameter(
            "dialogue_id",
            Parameter.KEYWORD_ONLY,
            annotation="str | None",
            default=None,
        ),
        Parameter(
            "request_json",
            Parameter.KEYWORD_ONLY,
            annotation="str | None",
            default=None,
        ),
        Parameter(
            "attachment_evidence",
            Parameter.KEYWORD_ONLY,
            annotation="ManagedAttachmentEvidence | None",
            default=None,
        ),
        Parameter(
            "execution_id",
            Parameter.KEYWORD_ONLY,
            annotation="str | None",
            default=None,
        ),
        Parameter(
            "debug", Parameter.KEYWORD_ONLY, annotation="bool", default=False
        ),
    ],
    return_annotation="tuple[dict[str, Any], int]",
)
setattr(_invoke_agent_run, "__annotations__", _INVOKE_AGENT_RUN_ANNOTATIONS)
setattr(_invoke_agent_run, "__signature__", _INVOKE_AGENT_RUN_SIGNATURE)
