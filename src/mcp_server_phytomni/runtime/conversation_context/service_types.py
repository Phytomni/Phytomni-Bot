# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Storage-neutral value types shared by conversation execution seams."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ...contracts.conversation_context import ContextStageMetadata
from .models import BusinessContext, ContextDelta, ContextProjection
from .store import StoredTurn

_SYNC_CONTEXT_AGENTS = (
    "ChatAgent",
    "KnowledgeAgent",
    "DataAgent",
    "ReviewAgent",
    "BriefGeneAgent",
)


class PrepareStatus(StrEnum):
    """Durable lifecycle states returned by conversation preparation."""

    READY = "ready"
    RETURN_STAGED = "return_staged"
    RETURN_COMMITTED = "return_committed"
    REBUILD_REQUIRED = "rebuild_required"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class AgentSelection:
    """Agent selected for one bounded conversation turn."""

    selected_agent_id: str
    reason_code: str


@dataclass(frozen=True)
class AgentOutcome:
    """Result and metadata returned by one agent invocation."""

    result: dict[str, Any]
    assistant_summary: str | None = None
    context_delta: ContextDelta | None = None
    context_delta_error: bool = False
    status: str = "succeeded"
    private_stage_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AsyncAgentAcceptance:
    """Transport proof for one durably accepted asynchronous run."""

    result: dict[str, Any]
    status_code: int


class AsyncAcceptanceError(RuntimeError):
    """An async delegate returned no durable accepted-run identity."""


class ContextStoreUnavailableError(RuntimeError):
    """Context persistence failed before an agent outcome existed."""


@dataclass(frozen=True)
class PreparedTurn:
    """Result of preparing, invoking, or staging one conversation turn."""

    status: PrepareStatus
    context: BusinessContext | None = None
    projection: ContextProjection | None = None
    stored_turn: StoredTurn | None = None
    result: dict[str, Any] | None = None
    stage: ContextStageMetadata | None = None
    context_persistence_degraded: bool = False


__all__ = [
    "AsyncAcceptanceError",
    "AsyncAgentAcceptance",
    "AgentOutcome",
    "AgentSelection",
    "ContextStageMetadata",
    "ContextStoreUnavailableError",
    "PrepareStatus",
    "PreparedTurn",
]
