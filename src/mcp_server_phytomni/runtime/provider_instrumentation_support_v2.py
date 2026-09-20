# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed state and callable contracts for provider instrumentation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from .execution_instrumentation_v2 import ExecutionBoundary
from .execution_journal_v2 import WorkUnitStatus
from .execution_work_store_v2 import WorkUnitRecord
from .instrumentation_contracts_v2 import (
    ObservationFactIdentity,
    keyword_signature,
)


@dataclass(slots=True)
class ProviderWorkState:
    """One logical provider work unit and its causal span."""

    work_unit: WorkUnitRecord
    span: Any


@dataclass(slots=True)
class ProviderAttemptState:
    """Durable analysis and submission records for one provider call."""

    boundary: ExecutionBoundary
    provider_kind: str
    operation_key: str
    analysis: ProviderWorkState
    submission: ProviderWorkState
    retry_count: int = 0

    @property
    def analysis_work_unit(self) -> WorkUnitRecord:
        """Return the current durable analysis work unit."""
        return self.analysis.work_unit

    @analysis_work_unit.setter
    def analysis_work_unit(self, record: WorkUnitRecord) -> None:
        self.analysis.work_unit = record

    @property
    def analysis_span(self) -> Any:
        """Return the causal analysis span."""
        return self.analysis.span

    @property
    def submission_work_unit(self) -> WorkUnitRecord:
        """Return the current durable submission work unit."""
        return self.submission.work_unit

    @submission_work_unit.setter
    def submission_work_unit(self, record: WorkUnitRecord) -> None:
        self.submission.work_unit = record

    @property
    def submission_span(self) -> Any:
        """Return the causal submission span."""
        return self.submission.span


@dataclass(slots=True)
class ProviderObservationState:
    """Identity and revision received from callback or recovery polling."""

    provider_kind: str
    provider_task_id: str
    source_revision: int | None
    observed_status: str


class ProviderSubmissionOptions(TypedDict):
    """Optional behavior for one provider submission boundary."""

    call_with_idempotency: NotRequired[Callable[[str], Awaitable[Any]] | None]
    max_attempts: NotRequired[int]
    require_identity: NotRequired[bool]


class ProviderObservationKwargs(TypedDict):
    """Provider identity and revision folded into one durable work unit."""

    provider_kind: str
    provider_task_id: str
    source_revision: int | None
    observed_status: str


class ProviderFactKwargs(ObservationFactIdentity):
    """Finite public provider fact ready for journal validation."""

    summary_key: str
    summary_text: str
    payload: dict[str, object]
    idempotency_key: str
    target: NotRequired[dict[str, str] | None]


PROVIDER_SUBMISSION_SIGNATURE = keyword_signature(
    (
        ("provider_kind", "str"),
        ("operation_key", "str"),
        ("call", "Callable[[], Awaitable[T]]"),
        ("identity_from_result", "Callable[[T], str]"),
    ),
    (
        (
            "call_with_idempotency",
            "Callable[[str], Awaitable[T]] | None",
            None,
        ),
        ("max_attempts", "int", 1),
        ("require_identity", "bool", True),
    ),
    return_annotation="T",
)

PROVIDER_OBSERVATION_SIGNATURE = keyword_signature(
    (
        ("boundary", "ExecutionBoundary"),
        ("record", "WorkUnitRecord"),
        ("provider_kind", "str"),
        ("provider_task_id", "str"),
        ("source_revision", "int | None"),
        ("observed_status", "str"),
    ),
    return_annotation="bool",
)


def provider_observation_summary(status: str) -> str:
    """Return the bounded public summary for one provider status."""
    return {
        "pending": "External analysis is queued",
        "running": "External analysis is running",
        "succeeded": "External analysis completed",
        "failed": "External analysis failed",
        "cancelled": "External analysis was cancelled",
        "timed_out": "External analysis timed out",
    }.get(status, "External analysis status updated")


def work_status_for_observation(status: str) -> WorkUnitStatus | None:
    """Map one normalized provider status to its durable work state."""
    return {
        "pending": WorkUnitStatus.ACKNOWLEDGED,
        "running": WorkUnitStatus.RUNNING,
        "succeeded": WorkUnitStatus.SUCCEEDED,
        "failed": WorkUnitStatus.FAILED,
        "cancelled": WorkUnitStatus.CANCELLED,
        "timed_out": WorkUnitStatus.TIMED_OUT,
    }.get(status)


def safe_provider_code(code: str) -> str:
    """Normalize an untrusted retry code to a bounded public token."""
    allowed = "".join(
        character
        for character in code.lower()
        if character.isalnum() or character in {"_", "-"}
    )
    return allowed[:128] or "provider_retry"
