# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite records and errors for the Runtime V2 logical-work store."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .execution_journal_v2 import SpanStatus, WorkUnitStatus
from .execution_store_support_v2 import validate_optional_aware_iso8601

Identifier = str
JoinPolicy = Literal["all", "fail_fast", "best_effort", "quorum"]
CancellationState = Literal[
    "none", "requested", "confirmed", "best_effort", "unsupported"
]
ProviderTraceHealth = Literal["healthy", "degraded", "unavailable"]


class ProviderBindingFields(TypedDict):
    """Keyword contract for one optimistic provider identity binding."""

    owner: str
    provider_kind: str
    provider_task_id: str
    provider_revision: int
    expected_revision: int


class WorkLeaseClaimFields(TypedDict):
    """Keyword contract for one optimistic worker lease claim."""

    owner: str
    worker_id: str
    lease_seconds: int
    expected_revision: int


class WorkRetryFields(TypedDict):
    """Keyword contract for one durable retry schedule."""

    owner: str
    worker_id: str
    next_attempt_at: datetime
    error_code: str
    expected_revision: int


class ProviderTraceStateFields(TypedDict):
    """Keyword contract for one provider-trace checkpoint update."""

    owner: str
    cursor: str | None
    source_revision: int
    adapter_version: str
    overlap_identities: tuple[str, ...]
    contact_at: str | None
    health: ProviderTraceHealth
    expected_revision: int


class WorkUnitUpdateFields(TypedDict):
    """Private optimistic update passed to the SQL writer."""

    owner: str
    execution_id: str
    work_unit_id: str
    expected_revision: int
    updates: dict[str, object]
    updated_at: str


class MissingOrConflictFields(TypedDict):
    """Private identity used to distinguish a miss from a stale CAS."""

    table: str
    id_column: str
    owner: str
    execution_id: str
    identity: str


class ExecutionWorkNotFoundError(LookupError):
    """The execution, span, or work unit is unknown or foreign."""


class ExecutionWorkConflictError(RuntimeError):
    """An optimistic revision, lease, or stable identity conflicted."""


class ExecutionWorkInvariantError(ValueError):
    """A parent, attempt, or lifecycle invariant was violated."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SpanSpec(_FrozenModel):
    """Stable logical identity and public metadata for one span."""

    owner: Identifier = Field(min_length=1, max_length=256)
    execution_id: Identifier = Field(min_length=1, max_length=128)
    span_id: Identifier = Field(min_length=1, max_length=128)
    parent_span_id: Identifier | None = Field(default=None, max_length=128)
    work_unit_id: Identifier | None = Field(default=None, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    label_key: str = Field(min_length=1, max_length=128)
    attempt: int = Field(default=1, ge=1)
    join_policy: JoinPolicy | None = None


class SpanRecord(SpanSpec):
    """Current inspectable span state."""

    status: SpanStatus = SpanStatus.PENDING
    started_at: str | None = None
    last_activity_at: str | None = None
    ended_at: str | None = None
    revision: int = Field(default=0, ge=0)

    def to_spec(self) -> SpanSpec:
        """Return the immutable creation fields for idempotency checks."""
        return SpanSpec.model_validate(
            self.model_dump(
                include={
                    "owner",
                    "execution_id",
                    "span_id",
                    "parent_span_id",
                    "work_unit_id",
                    "kind",
                    "label_key",
                    "attempt",
                    "join_policy",
                }
            )
        )


class WorkUnitSpec(_FrozenModel):
    """Stable logical work across one or more attempts."""

    owner: Identifier = Field(min_length=1, max_length=256)
    execution_id: Identifier = Field(min_length=1, max_length=128)
    work_unit_id: Identifier = Field(min_length=1, max_length=128)
    parent_span_id: Identifier = Field(min_length=1, max_length=128)
    operation_key: str = Field(min_length=1, max_length=128)
    driver: str = Field(min_length=1, max_length=64)
    join_policy: JoinPolicy | None = None
    max_attempts: int = Field(default=1, ge=1)
    deadline_at: str | None = Field(default=None, max_length=64)

    @field_validator("deadline_at")
    @classmethod
    def validate_deadline(cls, value: str | None) -> str | None:
        """Require an optional deadline to be timezone-aware ISO-8601."""
        return validate_optional_aware_iso8601(
            value,
            field_name="deadline_at",
        )


class WorkUnitRecord(WorkUnitSpec):
    """Current operational state for one logical unit."""

    status: WorkUnitStatus = WorkUnitStatus.PENDING
    attempt: int = Field(default=1, ge=1)
    next_attempt_at: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    provider_kind: str | None = None
    provider_task_id: str | None = None
    provider_revision: int = Field(default=0, ge=0)
    provider_trace_cursor: str | None = Field(default=None, max_length=128)
    provider_trace_revision: int = Field(default=0, ge=0)
    provider_trace_adapter_version: str | None = Field(
        default=None, max_length=32
    )
    provider_trace_overlap_identities: tuple[str, ...] = Field(
        default_factory=tuple, max_length=64
    )
    provider_trace_contact_at: str | None = Field(default=None, max_length=64)
    provider_trace_health: ProviderTraceHealth = "healthy"
    cancellation_state: CancellationState = "none"
    last_error_code: str | None = None
    created_at: str
    updated_at: str
    revision: int = Field(default=0, ge=0)

    @field_validator("provider_trace_overlap_identities")
    @classmethod
    def validate_trace_overlap(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject empty or oversized provider overlap identities."""
        if any(not identity or len(identity) > 128 for identity in value):
            raise ValueError("invalid provider trace overlap identity")
        return value

    @field_validator("provider_trace_contact_at")
    @classmethod
    def validate_trace_contact(cls, value: str | None) -> str | None:
        """Require provider-contact evidence to carry a timezone."""
        return validate_optional_aware_iso8601(
            value,
            field_name="provider_trace_contact_at",
        )
