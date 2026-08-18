# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private transport types shared by HTTP conversation-context routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ...agents.expert import (
    ExpertProviderError,
    ExpertProviderTimeoutError,
    ExpertRoutingDeclinedError,
    ToolSelectionError,
)
from ...agents.expert.routing_observability import (
    ExpertRouteOutcome,
    ExpertRoutePath,
    record_expert_route_outcome,
)
from ...runtime.conversation_context.adapters import (
    ConversationContextExecutor,
)
from ...runtime.conversation_context.models import ConversationEnvelopeV1
from ...runtime.conversation_context.review_lock import (
    ReviewMutationLockTimeoutError,
)
from ...runtime.conversation_context.service import (
    ContextStoreUnavailableError,
    PreparedTurn,
)
from ..attachments import ManagedAttachmentEvidence
from ..expert_routing_errors import (
    expert_routing_contract_error,
    expert_routing_provider_error,
)
from ..lifecycle_contract import conversation_context_unavailable_error


@dataclass(frozen=True, slots=True)
class ContextAgentRequest:
    """Shared transport fields for context invocation."""

    dialogue_id: str | None
    request_json: str
    debug: bool
    obs_file_list: list[str] | None
    attachment_arguments: Mapping[str, Any] | None = None
    attachment_evidence: ManagedAttachmentEvidence | None = None


@dataclass(frozen=True, slots=True)
class ContextLifecycleHttpRequest:
    """HTTP wrapper inputs for one conversation-context lifecycle turn."""

    executor: ConversationContextExecutor
    envelope: ConversationEnvelopeV1
    invoke: Callable[..., Awaitable[Any]]
    delegate_async: Callable[..., Awaitable[Any]]
    selection_failure_detail: str
    selected_arguments: Mapping[str, Any] | None = None


async def execute_context_lifecycle(
    *,
    executor: ConversationContextExecutor,
    envelope: ConversationEnvelopeV1,
    invoke: Callable[..., Awaitable[Any]],
    delegate_async: Callable[..., Awaitable[Any]],
    selected_arguments: Mapping[str, Any] | None = None,
) -> PreparedTurn:
    """Run one context turn and translate pre-outcome store failures."""
    try:
        return await executor.execute(
            envelope=envelope,
            invoke=invoke,
            delegate_async=delegate_async,
            selected_arguments=selected_arguments,
        )
    except ContextStoreUnavailableError as exc:
        raise conversation_context_unavailable_error() from exc


async def inspect_context_replay(
    *,
    executor: ConversationContextExecutor,
    envelope: ConversationEnvelopeV1,
    selection_failure_detail: str | None = None,
) -> PreparedTurn | None:
    """Inspect a durable replay while preserving store-failure mapping."""
    try:
        return await executor.inspect_replay(envelope=envelope)
    except ContextStoreUnavailableError as exc:
        raise conversation_context_unavailable_error() from exc
    except (ToolSelectionError, ValueError) as exc:
        if selection_failure_detail is None:
            raise
        raise HTTPException(
            status_code=502,
            detail=selection_failure_detail,
        ) from exc


async def execute_context_lifecycle_http(
    request: ContextLifecycleHttpRequest,
) -> PreparedTurn:
    """Run one context turn and map routing faults to public HTTP errors."""
    try:
        return await execute_context_lifecycle(
            executor=request.executor,
            envelope=request.envelope,
            invoke=request.invoke,
            delegate_async=request.delegate_async,
            selected_arguments=request.selected_arguments,
        )
    except ReviewMutationLockTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="Review mutation is busy",
        ) from exc
    except ExpertProviderError as exc:
        timed_out = isinstance(exc, ExpertProviderTimeoutError)
        record_expert_route_outcome(
            (
                ExpertRouteOutcome.PROVIDER_TIMEOUT
                if timed_out
                else ExpertRouteOutcome.PROVIDER_ERROR
            ),
            path=ExpertRoutePath.CONTEXT,
            forced=False,
            error_class=type(exc).__name__,
            http_status=504 if timed_out else 502,
        )
        raise expert_routing_provider_error(exc) from exc
    except ExpertRoutingDeclinedError as exc:
        record_expert_route_outcome(
            ExpertRouteOutcome.DECLINED_NO_FALLBACK,
            path=ExpertRoutePath.CONTEXT,
            forced=False,
            error_class=type(exc).__name__,
            http_status=502,
        )
        raise expert_routing_contract_error() from exc
    except (ToolSelectionError, ValueError) as exc:
        record_expert_route_outcome(
            ExpertRouteOutcome.SELECTION_CONTRACT,
            path=ExpertRoutePath.CONTEXT,
            forced=False,
            error_class=type(exc).__name__,
            http_status=502,
        )
        raise HTTPException(
            status_code=502,
            detail=request.selection_failure_detail,
        ) from exc


__all__ = [
    "ContextAgentRequest",
    "ContextLifecycleHttpRequest",
    "execute_context_lifecycle",
    "execute_context_lifecycle_http",
    "inspect_context_replay",
]
