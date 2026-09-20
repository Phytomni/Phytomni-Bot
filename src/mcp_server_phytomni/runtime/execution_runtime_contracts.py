# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transport-neutral contracts shared by the execution Runtime and Drivers."""

from __future__ import annotations

import math
import re
from abc import abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Literal, Protocol, Unpack, runtime_checkable

from ..public_agent_catalog import PublicAgentSpec
from .execution_journal_store_v2 import ExecutionJournal
from .execution_journal_v2 import (
    ExecutionStatus,
    PublicTargetKind,
    SpanStatus,
    TrackingHealth,
    WorkUnitStatus,
)
from .execution_work_store_v2 import (
    CancellationState,
    ProviderBindingFields,
    SpanRecord,
    SpanSpec,
    WorkUnitRecord,
    WorkUnitSpec,
)

CancellationOutcome = Literal[
    "requested", "confirmed", "best_effort", "unsupported"
]
TERMINAL_EXECUTION_STATUSES: Final = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.PARTIAL,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
)


class DriverOperation(StrEnum):
    """Finite operations supported at every Driver boundary."""

    START = "start"
    RESUME = "resume"
    CANCEL = "cancel"
    RECOVER = "recover"
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True)
class _ExecutionIdentity:
    """Stable owner and idempotency identity for one execution."""

    owner_ref: str
    execution_id: str
    fingerprint_version: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _ExecutionRoute(_ExecutionIdentity):
    """Canonical Agent and causal route for one execution."""

    agent: PublicAgentSpec
    root_span_id: str
    current_span_id: str
    transport: str


@dataclass(frozen=True, slots=True)
class ExecutionContext(_ExecutionRoute):
    """Stable execution identity plus the current causal span."""

    parent_span_id: str | None = None
    run_id: str | None = None
    deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "owner_ref",
            "execution_id",
            "fingerprint",
            "root_span_id",
            "current_span_id",
            "transport",
        ):
            value = getattr(self, name)
            if not value or len(value) > 256:
                raise ValueError(f"invalid {name}")
        if self.fingerprint_version < 1:
            raise ValueError("fingerprint_version must be positive")
        if (
            self.deadline_at is not None
            and self.deadline_at.utcoffset() is None
        ):
            raise ValueError("deadline_at must include a timezone")

    def nested(
        self,
        *,
        agent: PublicAgentSpec,
        span_id: str,
    ) -> ExecutionContext:
        """Create a child Agent/span without allocating a new execution."""
        return replace(
            self,
            agent=agent,
            parent_span_id=self.current_span_id,
            current_span_id=span_id,
        )


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    """Private validated command delegated to a canonical business handler."""

    agent_slug: str
    arguments: Mapping[str, Any]
    action_id: str | None = None
    expected_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.agent_slug:
            raise ValueError("agent_slug is required")
        if self.expected_revision is not None and self.expected_revision < 0:
            raise ValueError("expected_revision must be non-negative")


@dataclass(frozen=True, slots=True)
class ExecutionArtifactRef:
    """Transport-neutral result reference; never a path or direct URL."""

    role: str
    target_kind: PublicTargetKind | str
    target_id: str
    name: str | None = None
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    # Private delivery provenance is persisted only in the owner-scoped target
    # store. Public result/event serializers enumerate fields explicitly and
    # must never expose this value.
    private_delivery_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.role or len(self.role) > 128 or not self.target_id:
            raise ValueError("artifact role and target id are required")
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.target_id)
            is None
        ):
            raise ValueError("artifact requires an opaque target id")
        name = self.name or self.role
        if (
            not name
            or len(name) > 256
            or "/" in name
            or "\\" in name
            or "://" in name
        ):
            raise ValueError("artifact name must be bounded metadata")
        if not self.media_type or len(self.media_type) > 128:
            raise ValueError("artifact media type must be bounded")
        if isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise ValueError("artifact size must be non-negative")
        if self.private_delivery_ref is not None and (
            not self.private_delivery_ref
            or len(self.private_delivery_ref) > 4096
        ):
            raise ValueError("artifact private delivery reference is invalid")
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "target_kind",
            PublicTargetKind(self.target_kind),
        )


TabularCell = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class TransportNeutralTabular:
    """Finite scalar table serialized for the DataAgent display."""

    headers: tuple[str, ...]
    rows: tuple[tuple[TabularCell, ...], ...]

    def __post_init__(self) -> None:
        if not self.headers or len(self.headers) > 256:
            raise ValueError("tabular headers are required")
        if any(not header or len(header) > 512 for header in self.headers):
            raise ValueError("tabular header is invalid")
        if len(self.rows) > 100_000:
            raise ValueError("tabular row count exceeds limit")
        for row in self.rows:
            if len(row) != len(self.headers):
                raise ValueError("tabular row width mismatch")
            for cell in row:
                if not isinstance(cell, (str, int, float, bool, type(None))):
                    raise ValueError("tabular cell is not scalar")
                if isinstance(cell, str) and len(cell) > 8192:
                    raise ValueError("tabular cell exceeds limit")
                if isinstance(cell, float) and not math.isfinite(cell):
                    raise ValueError("tabular cell is not finite")

    def to_public_dict(self) -> dict[str, object]:
        """Return detached JSON-compatible headers and rows."""
        return {
            "headers": list(self.headers),
            "rows": [list(row) for row in self.rows],
        }


@dataclass(frozen=True, slots=True)
class TransportNeutralResult:
    """Canonical business result before HTTP/MCP/OpenAI presentation."""

    answer: str = ""
    follow_up_questions: tuple[str, ...] = ()
    references: tuple[Mapping[str, str | bool], ...] = ()
    tabular: TransportNeutralTabular | None = None
    artifacts: tuple[ExecutionArtifactRef, ...] = ()
    public_metadata: Mapping[str, str | int | float | bool | None] | None = (
        None
    )
    # Ephemeral hand-off to the calling transport. Runtime persistence and
    # public projection code must never serialize this value.
    private_value: object | None = None


@dataclass(frozen=True, slots=True)
class DriverFailure:
    """Finite normalized failure without a raw exception payload."""

    code: str
    retryable: bool

    def __post_init__(self) -> None:
        if not self.code or len(self.code) > 128:
            raise ValueError("failure code is required")


@dataclass(frozen=True, slots=True)
class DriverOutcome:
    """Normalized Driver observation or terminal settlement candidate."""

    status: ExecutionStatus
    result: TransportNeutralResult | None = None
    failure: DriverFailure | None = None
    provider_revision: int = 0
    retry_after_ms: int | None = None
    tracking_health: TrackingHealth = TrackingHealth.HEALTHY
    cancellation_outcome: CancellationOutcome | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ExecutionStatus(self.status))
        object.__setattr__(
            self,
            "tracking_health",
            TrackingHealth(self.tracking_health),
        )
        if self.provider_revision < 0:
            raise ValueError("provider_revision must be non-negative")
        if self.retry_after_ms is not None and (
            self.retry_after_ms < 0 or self.terminal
        ):
            raise ValueError(
                "retry delay is valid only for non-terminal outcomes"
            )
        if self.status is ExecutionStatus.FAILED and self.failure is None:
            raise ValueError("failed outcome requires a failure")
        if (
            self.status is not ExecutionStatus.FAILED
            and self.failure is not None
        ):
            raise ValueError("failure is valid only for failed outcomes")

    @property
    def terminal(self) -> bool:
        """Return whether the driver outcome closes the execution."""
        return self.status in TERMINAL_EXECUTION_STATUSES

    @classmethod
    def running(
        cls,
        *,
        result: TransportNeutralResult | None = None,
        retry_after_ms: int | None = None,
        tracking_health: TrackingHealth = TrackingHealth.HEALTHY,
        cancellation_outcome: CancellationOutcome | None = None,
    ) -> DriverOutcome:
        """Build a non-terminal outcome with optional progress metadata."""
        return cls(
            status=ExecutionStatus.RUNNING,
            result=result,
            retry_after_ms=retry_after_ms,
            tracking_health=tracking_health,
            cancellation_outcome=cancellation_outcome,
        )

    @classmethod
    def succeeded(cls, result: TransportNeutralResult) -> DriverOutcome:
        """Build a successful terminal outcome carrying the public result."""
        return cls(status=ExecutionStatus.SUCCEEDED, result=result)

    @classmethod
    def failed(cls, *, code: str, retryable: bool = False) -> DriverOutcome:
        """Build a failed terminal outcome with a stable error code."""
        return cls(
            status=ExecutionStatus.FAILED,
            failure=DriverFailure(code=code, retryable=retryable),
        )


@dataclass(frozen=True, slots=True)
class TerminalSettlementAuthority:
    """Runtime-owned optimistic authority for exactly-one terminal write."""

    owner_ref: str
    execution_id: str
    expected_revision: int
    actor: Literal["runtime", "supervisor"]
    issued_at: datetime

    def __post_init__(self) -> None:
        if (
            not self.owner_ref
            or not self.execution_id
            or self.expected_revision < 0
        ):
            raise ValueError("invalid terminal settlement authority")
        if self.issued_at.utcoffset() is None:
            raise ValueError("issued_at must include a timezone")


@runtime_checkable
class ExecutionReservationSnapshot(Protocol):
    """Minimum persisted reservation view needed by shared instrumentation."""

    @property
    def execution_id(self) -> str:
        """Return the stable execution identity."""
        raise NotImplementedError

    @property
    def supervisor_revision(self) -> int:
        """Return the optimistic revision used for terminal settlement."""
        raise NotImplementedError


class ExecutionReservationService(Protocol):
    """Private reservation/settlement authority used by Runtime."""

    def get(
        self,
        *,
        owner: str,
        execution_id: str,
    ) -> ExecutionReservationSnapshot:
        """Read one owner-scoped execution reservation."""
        raise NotImplementedError

    def terminal_authority(
        self, context: ExecutionContext
    ) -> TerminalSettlementAuthority:
        """Issue optimistic authority for one Runtime terminal write."""
        raise NotImplementedError

    def settle_terminal(
        self,
        authority: TerminalSettlementAuthority,
        outcome: DriverOutcome,
    ) -> bool:
        """Commit the terminal outcome when the authority remains current."""
        raise NotImplementedError


@runtime_checkable
class ExecutionWorkService(Protocol):
    """Minimal logical-work seam used by Drivers."""

    def create_span(self, spec: SpanSpec) -> SpanRecord:
        """Create or recover one stable causal span."""
        raise NotImplementedError

    def find_span_by_work_unit_id(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
    ) -> SpanRecord | None:
        """Find the latest span associated with a logical work unit."""
        raise NotImplementedError

    def update_span_status(
        self,
        execution_id: str,
        span_id: str,
        *,
        owner: str,
        status: SpanStatus | str,
        expected_revision: int,
    ) -> SpanRecord:
        """CAS-update one span lifecycle state."""
        raise NotImplementedError

    def create_work_unit(self, spec: WorkUnitSpec) -> WorkUnitRecord:
        """Create or recover one stable logical work unit."""
        raise NotImplementedError

    def get_work_unit(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
    ) -> WorkUnitRecord:
        """Read one owner-scoped logical work unit."""
        raise NotImplementedError

    def update_work_unit_status(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        status: WorkUnitStatus | str,
        expected_revision: int,
    ) -> WorkUnitRecord:
        """CAS-update one work-unit lifecycle state."""
        raise NotImplementedError

    def find_work_unit_by_provider_task_id(
        self,
        execution_id: str,
        provider_task_id: str,
        *,
        owner: str,
        provider_kind: str,
    ) -> WorkUnitRecord | None:
        """Resolve a logical unit from its durable provider identity."""
        raise NotImplementedError

    def bind_provider(
        self,
        execution_id: str,
        work_unit_id: str,
        **binding: Unpack[ProviderBindingFields],
    ) -> WorkUnitRecord:
        """Bind an immutable provider identity at the expected revision."""
        raise NotImplementedError

    def set_cancellation_state(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        state: CancellationState,
        expected_revision: int,
    ) -> WorkUnitRecord:
        """CAS-update the finite cancellation state."""
        raise NotImplementedError

    def observe_provider_contact(
        self,
        execution_id: str,
        work_unit_id: str,
        *,
        owner: str,
        observed_at: str,
    ) -> bool:
        """Record monotonic evidence of provider contact."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ExecutionServices:
    """Explicit runtime dependencies passed to every Driver."""

    journal: ExecutionJournal
    reservations: ExecutionReservationService
    work: ExecutionWorkService
    clock: Callable[[], datetime]


@runtime_checkable
@dataclass(init=False, repr=False, eq=False, match_args=False)
class ExecutionDriver(Protocol):
    """Common operation contract implemented by every Driver kind."""

    @abstractmethod
    def execute(
        self,
        operation: DriverOperation,
        context: ExecutionContext,
        command: ExecutionCommand,
        services: ExecutionServices,
    ) -> Awaitable[DriverOutcome]:
        """Execute one normalized operation through explicit services."""
        raise NotImplementedError


class ExecutionRuntimeError(RuntimeError):
    """Finite Runtime/Driver failure suitable for stable classification."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        if not code or len(code) > 128:
            raise ValueError("runtime error code is required")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
