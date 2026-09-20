# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Native Agent preparation and public response projection helpers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Any, NotRequired, TypedDict, Unpack

from fastapi import HTTPException

from ..mcp.result_formatting import strip_agent_result
from ..runtime.execution_instrumentation_v2 import current_execution_boundary
from ..runtime.execution_reservation_v2 import (
    ExecutionReservationNotFoundError,
    SQLiteExecutionReservationRepository,
)
from ..runtime.execution_runtime_contracts import ExecutionRuntimeError
from ..runtime.run_registry import RunRequestInfo
from . import run_lifecycle
from .agent_run_support import request_info_query
from .attachments import ManagedAttachmentEvidence
from .lifecycle_contract import (
    build_agent_run_response,
    canonicalize_agent_run_body,
    empty_agent_result,
)
from .resolvers import ResolverDispatch


def _app_module() -> Any:
    """Load ``api.app`` lazily to retain its monkeypatch compatibility."""
    return import_module(".app", package=__package__)


def _app_attr(name: str) -> Any:
    """Resolve one app compatibility seam without static private access."""
    return getattr(_app_module(), name)


@dataclass(frozen=True, slots=True)
class AgentRunPreparation:
    """Resolved context shared by one native Agent-run response."""

    tool_name: str
    owner: str
    request_info: RunRequestInfo
    resolve_meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentRunPreflight:
    """Synchronous validation and immutable request context for one run."""

    tool_name: str
    owner: str
    request_info: RunRequestInfo


class _PreflightOptions(TypedDict):
    """Compatible keyword inputs to the structural preflight seam."""

    agent: str
    arguments: dict[str, Any]
    dialogue_id: str | None
    request_json: str | None
    attachment_evidence: NotRequired[ManagedAttachmentEvidence | None]
    execution_id: NotRequired[str | None]


class _SyncResponseOptions(TypedDict):
    """Compatible keyword inputs to the synchronous response seam."""

    agent: str
    owner: str
    request_info: RunRequestInfo
    result: dict[str, Any]
    response_result: dict[str, Any]
    reserved_run_id: str


def _project_submission_warnings(raw: Any) -> list[dict[str, Any]]:
    """Project safe remote-submission warnings into HTTP execution state."""
    if not isinstance(raw, Mapping):
        return []
    return _app_attr("_project_warnings")(raw.get("submission_warnings"))


def preflight_agent_run(
    **options: Unpack[_PreflightOptions],
) -> AgentRunPreflight:
    """Validate structural inputs and capture context before dispatch."""
    agent = options["agent"]
    arguments = options["arguments"]
    app = _app_module()
    tool_name = _app_attr("_AGENT_SLUG_TO_TOOL").get(agent)
    if tool_name is None:
        raise HTTPException(
            status_code=404, detail=f"agent not found: {agent}"
        )
    if agent in _app_attr("_REMOTE_AGENT_SLUGS"):
        validation_arguments = deepcopy(arguments)
        if agent in {"deep_genome", "design"} and validation_arguments.get(
            "resolve_gene_id"
        ):
            validation_arguments.setdefault("species_code", "ath")
            validation_arguments.setdefault("gene_id", "AT1G01010")
        elif agent == "network" and validation_arguments.get("resolve_to_id"):
            validation_arguments.setdefault("species_code", "osa")
            validation_arguments.setdefault("to_id", "TO:0000001")
        _app_attr("validate_tool_arguments")(tool_name, validation_arguments)
    owner = _app_attr("current_request_user")() or "anonymous"
    evidence = options.get("attachment_evidence")
    validation_owner = (
        evidence.attachment_owner if evidence is not None else owner
    )
    _app_attr("validate_native_attachments")(
        agent,
        arguments,
        owner=validation_owner,
        db_path=_app_attr("resolve_tasks_db_path")(),
        managed_evidence=evidence,
    )
    return AgentRunPreflight(
        tool_name=tool_name,
        owner=owner,
        request_info=RunRequestInfo(
            dialogue_id=options["dialogue_id"],
            request_id=_app_attr("current_request_id")(),
            query=request_info_query(arguments, options["request_json"]),
            tool_name=tool_name,
            model=None,
            request_json=options["request_json"],
            locale=app.current_effective_locale(),
            execution_id=options.get("execution_id"),
        ),
    )


async def prepare_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    preflight: AgentRunPreflight,
) -> AgentRunPreparation:
    """Resolve semantic arguments after structural preflight."""
    app = _app_module()
    resolve_meta = await app.apply_runs_resolver(
        agent,
        arguments,
        dispatch=ResolverDispatch(
            brief_gene_resolver=app.resolve_brief_gene_user_query,
            deep_genome_resolver=app.resolve_deep_genome_user_query,
            design_resolver=app.resolve_design_user_query,
            network_resolver=app.resolve_network_user_query,
        ),
    )
    _app_attr("validate_tool_arguments")(preflight.tool_name, arguments)
    return AgentRunPreparation(
        tool_name=preflight.tool_name,
        owner=preflight.owner,
        request_info=preflight.request_info,
        resolve_meta=resolve_meta,
    )


def format_agent_run_result(
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


def remote_agent_run_response(
    *,
    agent: str,
    owner: str,
    request_info: RunRequestInfo,
    response_result: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Shape a compatibility 202 over the Runtime-owned execution root."""
    boundary = current_execution_boundary()
    if boundary is None or boundary.context.owner_ref != owner:
        raise ExecutionRuntimeError("execution_boundary_required")
    run_id = boundary.context.run_id
    if run_id is None:
        raise ExecutionRuntimeError("execution_run_id_required")
    task_ids = tuple(_app_attr("current_accepted_task_ids")())
    degraded_tracking = bool(_app_attr("current_recorder_degraded")())
    if not task_ids:
        raise ExecutionRuntimeError("remote_submission_identity_required")
    _app_attr("_stamp_remote_request_info")(
        run_id=run_id, owner=owner, request_info=request_info
    )
    result = (
        empty_agent_result(degraded=True)
        if degraded_tracking
        else response_result
    )
    body = build_agent_run_response(
        run_id=run_id,
        agent=agent,
        status="running",
        task_ids=task_ids,
        result=result,
        persisted=True,
        degraded_tracking=degraded_tracking,
    )
    return body, 202


def failed_remote_runtime_response(
    *, owner: str, execution_id: str, agent: str
) -> tuple[dict[str, Any], int]:
    """Project a safe compatibility response from a failed reservation."""
    record = SQLiteExecutionReservationRepository(
        _app_attr("resolve_tasks_db_path")()
    ).get(owner=owner, execution_id=execution_id)
    degraded = bool(_app_attr("current_recorder_degraded")())
    body = build_agent_run_response(
        run_id=record.run_id,
        agent=agent,
        status="failed",
        task_ids=tuple(_app_attr("current_accepted_task_ids")()),
        result=empty_agent_result(degraded=degraded),
        persisted=True,
        degraded_tracking=degraded,
    )
    return body, 202


def existing_execution_identity(
    *, db_path: str, owner: str, execution_id: str
) -> tuple[int, str] | None:
    """Reuse admission identity after Expert routing binds an Agent."""
    try:
        record = SQLiteExecutionReservationRepository(db_path).get(
            owner=owner,
            execution_id=execution_id,
        )
    except ExecutionReservationNotFoundError:
        return None
    return record.fingerprint_version, record.fingerprint


async def sync_agent_run_response(
    **options: Unpack[_SyncResponseOptions],
) -> tuple[dict[str, Any], int]:
    """Shape a terminal response over the Runtime-owned run identity."""
    run_id = options["reserved_run_id"]
    canonical = canonicalize_agent_run_body(
        {
            "id": run_id,
            "object": "agent.run",
            "agent": options["agent"],
            "status": "succeeded",
            "task_ids": [],
            "result": options["response_result"],
        }
    )
    body = build_agent_run_response(
        run_id=run_id,
        agent=options["agent"],
        status="succeeded",
        task_ids=(),
        result=canonical["result"],
        persisted=True,
        degraded_tracking=canonical.get("degraded_tracking") is True,
    )
    return body, 200


def resolve_remote_run(owner: str) -> run_lifecycle.ResolvedRemoteRun:
    """Recover remote-run context through the app compatibility seams."""
    return run_lifecycle.resolve_remote_run(
        owner,
        run_id=_app_attr("current_run_id")(),
        accepted_task_ids=_app_attr("current_accepted_task_ids")(),
        recorder_degraded=_app_attr("current_recorder_degraded")(),
        db_path=_app_attr("resolve_tasks_db_path")(),
    )


__all__ = [
    "AgentRunPreparation",
    "AgentRunPreflight",
    "existing_execution_identity",
    "failed_remote_runtime_response",
    "format_agent_run_result",
    "preflight_agent_run",
    "prepare_agent_run",
    "remote_agent_run_response",
    "resolve_remote_run",
    "sync_agent_run_response",
]
