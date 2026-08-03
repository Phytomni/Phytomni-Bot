# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private transport types shared by HTTP conversation-context routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...runtime.conversation_context.adapters import (
    ConversationContextExecutor,
)
from ...runtime.conversation_context.models import ConversationEnvelopeV1
from ...runtime.conversation_context.service import (
    ContextStoreUnavailableError,
    PreparedTurn,
)
from ..attachments import ManagedAttachmentEvidence
from ..lifecycle_contract import conversation_context_unavailable_error
from .attachment_inputs import ResolvedAttachmentInput


@dataclass(frozen=True, slots=True)
class ContextAgentRequest:
    """Shared transport fields for context invocation."""

    dialogue_id: str | None
    request_json: str
    debug: bool
    obs_file_list: list[str] | None
    resolved_attachments: ResolvedAttachmentInput | None = None
    dataset_description: str | None = None
    attachment_evidence: ManagedAttachmentEvidence | None = None


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


__all__ = ["ContextAgentRequest", "execute_context_lifecycle"]
