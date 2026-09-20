# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Public-safe lifecycle error types and constructors."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..runtime.locale import SupportedLocale, message_for
from ..runtime.run_registry_models import ResearchFailureCode


class SafeErrorCode(StrEnum):
    """Stable public-safe lifecycle and transport error codes."""

    RUN_PERSISTENCE_FAILED = "run_persistence_failed"
    RUNNING_WITHOUT_WORK = "running_without_work"
    SUCCEEDED_WITHOUT_PERSISTENCE = "succeeded_without_persistence"
    INPUT_REQUIRED_WITHOUT_SURFACE = "input_required_without_surface"
    PROJECTION_FAILED = "projection_failed"
    A2UI_ACTION_CONFLICT = "a2ui_action_conflict"
    CHECKPOINT_NOT_AVAILABLE = "checkpoint_not_available"
    ROUTING_CONTRACT_VIOLATION = "routing_contract_violation"
    ROUTING_UPSTREAM_FAILED = "routing_upstream_failed"
    SELECTED_AGENT_INVALID_ARGUMENT = "selected_agent_invalid_argument"
    UPSTREAM_FAILED = "upstream_failed"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    CONVERSATION_CONTEXT_UNAVAILABLE = "conversation_context_unavailable"


class ResearchFailureDetail(BaseModel):
    """Bounded, public-safe detail for one durable Research failure."""

    model_config = ConfigDict(extra="forbid")

    code: ResearchFailureCode
    message: str = Field(min_length=1, max_length=512)
    stage: Literal[
        "input_resolution", "planning", "execution", "report_assembly"
    ]
    retryable: bool
    http_status_hint: int = Field(ge=400, le=599)


class LifecycleInvariantError(RuntimeError):
    """Raised before an invalid run response reaches a public boundary."""

    def __init__(self, code: SafeErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(slots=True)
class SafeApiError(RuntimeError):
    """One public-safe API failure routed through the factory handlers."""

    status_code: int
    code: str
    message: str
    stage: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.code)


def expert_safe_error(
    code: SafeErrorCode,
    *,
    status_code: int,
    locale: SupportedLocale,
    stage: str,
    retryable: bool,
) -> SafeApiError:
    """Build one localized, request-data-free Expert error."""
    return SafeApiError(
        status_code=status_code,
        code=code.value,
        message=message_for(code.value, locale),
        stage=stage,
        retryable=retryable,
    )


def run_persistence_error() -> SafeApiError:
    """Return the stable public error for durable run persistence failures."""
    return SafeApiError(
        status_code=500,
        code=SafeErrorCode.RUN_PERSISTENCE_FAILED.value,
        message="run persistence failed",
        stage="persistence",
    )


def conversation_context_unavailable_error() -> SafeApiError:
    """Return the retryable public error for pre-outcome context failure."""
    return SafeApiError(
        status_code=503,
        code=SafeErrorCode.CONVERSATION_CONTEXT_UNAVAILABLE.value,
        message="conversation context unavailable",
        stage="context",
        retryable=True,
    )


__all__ = [
    "LifecycleInvariantError",
    "ResearchFailureDetail",
    "SafeApiError",
    "SafeErrorCode",
    "conversation_context_unavailable_error",
    "expert_safe_error",
    "run_persistence_error",
]
