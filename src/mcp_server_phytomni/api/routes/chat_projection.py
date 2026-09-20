# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Blocking Chat completion projection behind the canonical Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ...runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from ...runtime.locale import SupportedLocale, current_effective_locale
from ...runtime.run_registry import RunRegistry, RunRequestInfo
from ..app_support import build_safe_chat_request_info
from ..attachments import (
    ManagedAttachmentEvidence,
    redact_managed_attachment_values,
)
from ..schemas import ChatCompletionRequest
from .agent_dependencies import AgentRouteDependencies


def ordinary_chat_private_context(
    tool_name: str,
    prepared: Mapping[str, Any],
) -> tuple[Any, dict[str, Any] | None]:
    """Return private history and retrieval state for an ordinary Chat run."""
    conversation_messages = (
        prepared["conversation_messages"]
        if tool_name in {"ChatAgent", "KnowledgeAgent"}
        else ()
    )
    private_agent_state = (
        {"retrieval_query": prepared["user_query"]}
        if tool_name == "KnowledgeAgent"
        else None
    )
    return conversation_messages, private_agent_state


def formatted_with_metadata(
    envelope: Any,
    resolve_meta: dict[str, Any],
) -> dict[str, Any]:
    """Project one tool envelope and merge resolver metadata."""
    formatted_dict = asdict(envelope.formatted)
    if not resolve_meta:
        return formatted_dict
    existing_meta = formatted_dict.get("metadata")
    if isinstance(existing_meta, dict):
        existing_meta.update(resolve_meta)
    else:
        formatted_dict["metadata"] = dict(resolve_meta)
    return formatted_dict


def chat_run_request_info(
    payload: ChatCompletionRequest,
    user_query: str,
    tool_name: str,
    locale: SupportedLocale,
) -> RunRequestInfo:
    """Build the run-registry request record without app-layer imports."""
    return build_safe_chat_request_info(
        payload,
        user_query,
        tool_name=tool_name,
        locale=locale,
    )


def request_info_for_execution(
    request_info: RunRequestInfo,
    execution_id: str,
) -> RunRequestInfo:
    """Attach the canonical execution id without losing correlations."""
    return RunRequestInfo(
        dialogue_id=request_info.dialogue_id,
        request_id=request_info.request_id,
        query=request_info.query,
        tool_name=request_info.tool_name,
        model=request_info.model,
        request_json=request_info.request_json,
        locale=request_info.locale,
        a2a=request_info.a2a,
        execution_id=execution_id,
    )


def _project_tool_envelope(
    envelope: Any,
    resolve_meta: dict[str, Any],
    evidence: ManagedAttachmentEvidence | None,
) -> dict[str, Any]:
    """Project and redact one private tool envelope."""
    envelope_dict: dict[str, Any] = {
        "formatted": formatted_with_metadata(envelope, resolve_meta),
        "execution": asdict(envelope.execution),
        "raw": envelope.raw,
    }
    if evidence is None:
        return envelope_dict
    return redact_managed_attachment_values(envelope_dict, evidence)


def _record_chat_request(
    dependencies: AgentRouteDependencies,
    owner: str,
    execution_id: str,
    request_info: RunRequestInfo,
) -> str:
    """Attach safe request metadata and return the canonical run id."""
    run_id = (
        SQLiteExecutionReservationRepository(dependencies.tasks_db_path())
        .get(
            owner=owner,
            execution_id=execution_id,
        )
        .run_id
    )
    RunRegistry(dependencies.tasks_db_path()).update_request_info(
        run_id,
        owner=owner,
        request_info=request_info,
    )
    return run_id


def _completion_from_envelope(
    payload: ChatCompletionRequest,
    dependencies: AgentRouteDependencies,
    envelope: Mapping[str, Any],
    evidence: ManagedAttachmentEvidence | None,
    run_id: str,
) -> dict[str, Any]:
    """Build one redacted OpenAI completion from a projected envelope."""
    completion = dependencies.chat.projection.to_chat_completion(
        envelope["formatted"],
        envelope.get("raw"),
        payload.model,
        envelope["execution"],
    )
    if evidence is not None:
        completion = redact_managed_attachment_values(completion, evidence)
    completion["run_id"] = run_id
    if envelope["execution"]["tracking"].get("degraded") is True:
        completion["degraded_tracking"] = True
    if not dependencies.chat.projection.resolve_debug(payload.debug):
        completion = dependencies.chat.projection.strip_chat_completion(
            completion
        )
    return completion


async def finalize_ordinary_chat_response(
    payload: ChatCompletionRequest,
    dependencies: AgentRouteDependencies,
    *,
    tool_name: str,
    prepared: Mapping[str, Any],
    execution_id: str,
) -> JSONResponse:
    """Invoke one ordinary chat agent and project its safe completion."""
    conversation_messages, private_agent_state = ordinary_chat_private_context(
        tool_name, prepared
    )
    agent_slug = dependencies.catalog.model_to_agent_slug.get(payload.model)
    owner = dependencies.chat.projection.current_user() or "anonymous"
    request_info = request_info_for_execution(
        chat_run_request_info(
            payload,
            prepared["user_query"],
            tool_name,
            current_effective_locale(),
        ),
        execution_id,
    )
    tool_envelope = await dependencies.chat.execution.invoke_tool_enveloped(
        tool_name,
        prepared["arguments"],
        conversation_messages=conversation_messages,
        private_agent_state=private_agent_state,
        execution_id=execution_id,
        transport="openai_blocking",
        db_path=dependencies.tasks_db_path(),
    )
    envelope = _project_tool_envelope(
        tool_envelope,
        prepared["resolve_meta"],
        prepared["evidence"],
    )
    if agent_slug is None:
        raise HTTPException(
            status_code=404, detail="agent model is not public"
        )
    run_id = _record_chat_request(
        dependencies,
        owner,
        execution_id,
        request_info,
    )
    return JSONResponse(
        _completion_from_envelope(
            payload,
            dependencies,
            envelope,
            prepared["evidence"],
            run_id,
        )
    )
