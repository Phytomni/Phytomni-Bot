# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Value objects and pure helpers for execution reservations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from inspect import Parameter, Signature
from typing import Any, NamedTuple, NotRequired, TypedDict, Unpack, cast

from ..config.defaults import ApiConfig
from ..public_agent_catalog import Driver, public_agent_spec
from .execution_journal_store_v2 import SQLiteExecutionJournal
from .execution_journal_v2 import (
    EventStatus,
    ExecutionEventIntentV2,
    ExecutionEventType,
    ExecutionProjectionV2,
    ExecutionStatus,
    parse_execution_event_intent_v2,
)
from .execution_runtime_contracts import (
    TERMINAL_EXECUTION_STATUSES,
    CancellationOutcome,
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
)

# Private admission-only identity for autonomous Expert routing.  It is not a
# public Agent and must never be exported by the canonical Agent catalog.
EXPERT_ROUTER_AGENT_SLUG = "expert-router"


class ExecutionReservationRecord(NamedTuple):
    """Private Bot binding below the stable public execution identity."""

    owner: str
    execution_id: str
    run_id: str
    fingerprint_version: int
    fingerprint: str
    command_hash: str
    agent_slug: str
    driver: Driver
    root_span_id: str
    status: ExecutionStatus
    deadline_at: str
    supervisor_revision: int
    next_attempt_at: str | None
    tracking_health: str
    cancellation_state: CancellationOutcome
    context_stage_json: str | None


class ExecutionOperationClaim(NamedTuple):
    """Durable idempotency claim for resume/recovery/control operations."""

    operation_id: str
    operation: str
    expected_revision: int
    command_hash: str
    claimed: bool
    state: str
    outcome_json: str | None
    supervisor_revision: int


@dataclass(frozen=True, slots=True)
class CanonicalReservationFields:
    """Shared owner-scoped command identity used before admission."""

    owner: str
    execution_id: str
    fingerprint_version: int
    fingerprint: str
    command: ExecutionCommand


class CanonicalReservationKwargs(TypedDict):
    """Typed keyword identity shared by reservation entry points."""

    owner: str
    execution_id: str
    fingerprint_version: int
    fingerprint: str
    command: ExecutionCommand


class ReservationKwargs(CanonicalReservationKwargs):
    """Typed keyword contract for one reservation request."""

    durable_command: NotRequired[Mapping[str, Any] | None]


class OperationClaimKwargs(TypedDict):
    """Typed keyword contract for one durable operation claim."""

    owner: str
    execution_id: str
    operation_id: str
    operation: str
    expected_revision: int
    command: ExecutionCommand


class ObservationKwargs(TypedDict):
    """Typed keyword contract for one non-terminal observation."""

    owner: str
    execution_id: str
    status: ExecutionStatus
    tracking_health: str
    cancellation_state: str
    next_attempt_at: str | None


class RoutedBindingKwargs(TypedDict):
    """Typed keyword contract for a routed-binding comparison."""

    owner: str
    execution_id: str
    reservation_agent: str
    reservation_command_hash: str
    fingerprint_version: object
    fingerprint: object


@dataclass(frozen=True, slots=True)
class ReservationRequest(CanonicalReservationFields):
    """Validated call arguments for an execution reservation."""

    durable_command: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class ReservationProfile:
    """Catalog-derived storage shape for one admitted Agent."""

    slug: str
    driver: Driver
    tool: str
    model: str | None
    lifecycle: str
    deadline_seconds: int


@dataclass(frozen=True, slots=True)
class ReservationPlan:
    """Canonical values shared by replay and first-time admission."""

    request: ReservationRequest
    profile: ReservationProfile
    command_hash: str
    durable_command_json: str | None


@dataclass(frozen=True, slots=True)
class OperationClaimRequest:
    """Validated identity for one operation claim."""

    owner: str
    execution_id: str
    operation_id: str
    operation: str
    expected_revision: int
    command: ExecutionCommand


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    """Validated call arguments for a non-terminal observation."""

    owner: str
    execution_id: str
    status: ExecutionStatus
    tracking_health: str
    cancellation_state: str
    next_attempt_at: str | None


@dataclass(frozen=True, slots=True)
class RoutedBindingRequest:
    """Reservation identity compared with one durable router command."""

    owner: str
    execution_id: str
    reservation_agent: str
    reservation_command_hash: str
    fingerprint_version: object
    fingerprint: object


class TerminalReservationState(NamedTuple):
    """Reservation columns participating in terminal settlement."""

    status: str
    terminal_outcome: str | None
    cancellation_state: str
    root_span_id: str
    agent_slug: str
    supervisor_revision: int


@dataclass(frozen=True, slots=True)
class TerminalSnapshot:
    """Reservation, projection, and root-span state read in one snapshot."""

    reservation: TerminalReservationState
    projection: ExecutionProjectionV2
    root_span_status: str | None


@dataclass(frozen=True, slots=True)
class TerminalPlan:
    """Canonical terminal values written by the reservation CAS."""

    status: ExecutionStatus
    cancellation_state: str
    result_json: str | None
    expires_at: str
    now: datetime


_KEYWORD_ONLY = Parameter.KEYWORD_ONLY
_POSITIONAL = Parameter.POSITIONAL_OR_KEYWORD

RESERVE_SIGNATURE = Signature(
    parameters=(
        Parameter("self", _POSITIONAL),
        Parameter("owner", _KEYWORD_ONLY, annotation=str),
        Parameter("execution_id", _KEYWORD_ONLY, annotation=str),
        Parameter("fingerprint_version", _KEYWORD_ONLY, annotation=int),
        Parameter("fingerprint", _KEYWORD_ONLY, annotation=str),
        Parameter("command", _KEYWORD_ONLY, annotation=ExecutionCommand),
        Parameter(
            "durable_command",
            _KEYWORD_ONLY,
            default=None,
            annotation=Mapping[str, Any] | None,
        ),
    ),
    return_annotation=ExecutionReservationRecord,
)

OPERATION_CLAIM_SIGNATURE = Signature(
    parameters=(
        Parameter("self", _POSITIONAL),
        Parameter("owner", _KEYWORD_ONLY, annotation=str),
        Parameter("execution_id", _KEYWORD_ONLY, annotation=str),
        Parameter("operation_id", _KEYWORD_ONLY, annotation=str),
        Parameter("operation", _KEYWORD_ONLY, annotation=str),
        Parameter("expected_revision", _KEYWORD_ONLY, annotation=int),
        Parameter("command", _KEYWORD_ONLY, annotation=ExecutionCommand),
    ),
    return_annotation=ExecutionOperationClaim,
)

OBSERVATION_SIGNATURE = Signature(
    parameters=(
        Parameter("self", _POSITIONAL),
        Parameter("owner", _KEYWORD_ONLY, annotation=str),
        Parameter("execution_id", _KEYWORD_ONLY, annotation=str),
        Parameter("status", _KEYWORD_ONLY, annotation=ExecutionStatus),
        Parameter("tracking_health", _KEYWORD_ONLY, annotation=str),
        Parameter("cancellation_state", _KEYWORD_ONLY, annotation=str),
        Parameter(
            "next_attempt_at",
            _KEYWORD_ONLY,
            annotation=str | None,
        ),
    ),
    return_annotation=bool,
)

ROUTED_BINDING_SIGNATURE = Signature(
    parameters=(
        Parameter("durable_command", _POSITIONAL),
        Parameter("owner", _KEYWORD_ONLY, annotation=str),
        Parameter("execution_id", _KEYWORD_ONLY, annotation=str),
        Parameter("reservation_agent", _KEYWORD_ONLY, annotation=str),
        Parameter(
            "reservation_command_hash",
            _KEYWORD_ONLY,
            annotation=str,
        ),
        Parameter("fingerprint_version", _KEYWORD_ONLY, annotation=object),
        Parameter("fingerprint", _KEYWORD_ONLY, annotation=object),
    ),
    return_annotation=bool,
)


def reservation_request(
    repository: object,
    kwargs: Mapping[str, object],
) -> ReservationRequest:
    """Bind one public reservation call to its typed value object."""
    bound = RESERVE_SIGNATURE.bind(repository, **kwargs)
    bound.apply_defaults()
    values = bound.arguments
    request = ReservationRequest(
        owner=cast(str, values["owner"]),
        execution_id=cast(str, values["execution_id"]),
        fingerprint_version=cast(int, values["fingerprint_version"]),
        fingerprint=cast(str, values["fingerprint"]),
        command=cast(ExecutionCommand, values["command"]),
        durable_command=cast(
            Mapping[str, Any] | None,
            values["durable_command"],
        ),
    )
    if (
        not request.owner
        or not request.execution_id
        or not request.fingerprint
    ):
        raise ValueError("owner, execution_id, and fingerprint are required")
    if request.fingerprint_version < 1 or len(request.fingerprint) > 256:
        raise ValueError("invalid fingerprint")
    return request


def operation_claim_request(
    repository: object,
    kwargs: Mapping[str, object],
) -> OperationClaimRequest:
    """Bind one public operation claim to its typed value object."""
    values = OPERATION_CLAIM_SIGNATURE.bind(repository, **kwargs).arguments
    request = OperationClaimRequest(
        owner=cast(str, values["owner"]),
        execution_id=cast(str, values["execution_id"]),
        operation_id=cast(str, values["operation_id"]),
        operation=cast(str, values["operation"]),
        expected_revision=cast(int, values["expected_revision"]),
        command=cast(ExecutionCommand, values["command"]),
    )
    if not request.operation_id or request.expected_revision < 0:
        raise ValueError("operation identity and revision are required")
    return request


def observation_request(
    repository: object,
    kwargs: Mapping[str, object],
) -> ObservationRequest:
    """Bind one public observation call to its typed value object."""
    values = OBSERVATION_SIGNATURE.bind(repository, **kwargs).arguments
    return ObservationRequest(
        owner=cast(str, values["owner"]),
        execution_id=cast(str, values["execution_id"]),
        status=cast(ExecutionStatus, values["status"]),
        tracking_health=cast(str, values["tracking_health"]),
        cancellation_state=cast(str, values["cancellation_state"]),
        next_attempt_at=cast(str | None, values["next_attempt_at"]),
    )


def reservation_plan(request: ReservationRequest) -> ReservationPlan:
    """Derive canonical catalog and command values for admission."""
    spec = public_agent_spec(request.command.agent_slug)
    if spec is None and request.command.agent_slug != EXPERT_ROUTER_AGENT_SLUG:
        raise ValueError("unknown public Agent")
    profile = ReservationProfile(
        slug=spec.slug if spec is not None else EXPERT_ROUTER_AGENT_SLUG,
        driver=spec.driver if spec is not None else "local_graph",
        tool=spec.tool if spec is not None else "ExpertRouter",
        model=spec.model if spec is not None else None,
        lifecycle=spec.lifecycle if spec is not None else "synchronous",
        deadline_seconds=spec.deadline_seconds if spec is not None else 3600,
    )
    return ReservationPlan(
        request=request,
        profile=profile,
        command_hash=execution_command_hash(request.command),
        durable_command_json=durable_command_json(request.durable_command),
    )


def reservation_replay_matches(
    record: ExecutionReservationRecord,
    plan: ReservationPlan,
) -> bool:
    """Return whether an existing reservation is the same admission."""
    return all(
        (
            record.fingerprint_version == plan.request.fingerprint_version,
            record.fingerprint == plan.request.fingerprint,
            record.command_hash == plan.command_hash,
            record.agent_slug == plan.profile.slug,
            record.driver == plan.profile.driver,
        )
    )


def execution_command_hash(command: ExecutionCommand) -> str:
    """Return the canonical durable hash used by reservation fences."""
    try:
        encoded = json.dumps(
            {
                "agent_slug": command.agent_slug,
                "arguments": command.arguments,
                "action_id": command.action_id,
                "expected_revision": command.expected_revision,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ValueError("Agent command is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def routed_binding_matches_command(
    durable_command: Mapping[str, object],
    **kwargs: Unpack[RoutedBindingKwargs],
) -> bool:
    """Validate the persisted selected binding below one router command."""
    binding = _routed_binding_request(durable_command, kwargs)
    arguments = durable_command.get("arguments")
    if not isinstance(arguments, dict):
        return False
    if not _routed_envelope_matches(durable_command, arguments, binding):
        return False
    spec = public_agent_spec(binding.reservation_agent)
    if spec is None or spec.lifecycle != "asynchronous":
        return False
    if not _routed_tool_matches(arguments, spec.tool):
        return False
    return _routed_hash_differs(arguments, binding.reservation_command_hash)


def _routed_binding_request(
    durable_command: Mapping[str, object],
    kwargs: Mapping[str, object],
) -> RoutedBindingRequest:
    values = ROUTED_BINDING_SIGNATURE.bind(durable_command, **kwargs).arguments
    return RoutedBindingRequest(
        owner=cast(str, values["owner"]),
        execution_id=cast(str, values["execution_id"]),
        reservation_agent=cast(str, values["reservation_agent"]),
        reservation_command_hash=cast(
            str,
            values["reservation_command_hash"],
        ),
        fingerprint_version=values["fingerprint_version"],
        fingerprint=values["fingerprint"],
    )


def _routed_envelope_matches(
    durable_command: Mapping[str, object],
    arguments: Mapping[str, object],
    binding: RoutedBindingRequest,
) -> bool:
    command_hash = binding.reservation_command_hash
    conversation = arguments.get("__conversation")
    return all(
        (
            durable_command.get("owner_ref") == binding.owner,
            durable_command.get("execution_id") == binding.execution_id,
            durable_command.get("agent") == EXPERT_ROUTER_AGENT_SLUG,
            durable_command.get("fingerprint_version")
            == binding.fingerprint_version,
            durable_command.get("fingerprint") == binding.fingerprint,
            binding.reservation_agent != EXPERT_ROUTER_AGENT_SLUG,
            len(command_hash) == 64,
            all(character in "0123456789abcdef" for character in command_hash),
            isinstance(conversation, dict),
            isinstance(conversation, dict)
            and conversation.get("mode") == "expert",
        )
    )


def _routed_tool_matches(
    arguments: Mapping[str, object],
    tool: str,
) -> bool:
    allowed_tools = arguments.get("__allowed_tools")
    forced_tool = arguments.get("__forced_tool")
    return bool(
        isinstance(allowed_tools, list)
        and tool in allowed_tools
        and (not isinstance(forced_tool, str) or forced_tool == tool)
    )


def _routed_hash_differs(
    arguments: Mapping[str, object],
    reservation_command_hash: str,
) -> bool:
    try:
        router_hash = execution_command_hash(
            ExecutionCommand(
                agent_slug=EXPERT_ROUTER_AGENT_SLUG,
                arguments=cast(dict[str, Any], arguments),
            )
        )
    except (TypeError, ValueError):
        return False
    return reservation_command_hash != router_hash


def terminal_event_intents(
    *,
    execution_id: str,
    root_span_id: str,
    agent_slug: str,
    actor: str,
    outcome: DriverOutcome,
) -> tuple[ExecutionEventIntentV2, ExecutionEventIntentV2]:
    """Build the bounded public facts owned by terminal settlement."""
    execution_type, span_type, payload = _terminal_event_shape(outcome)
    event_status = EventStatus(outcome.status.value)
    readable_status = outcome.status.value.replace("_", " ")
    span_payload = (
        {"phase": agent_slug}
        if span_type
        in {
            ExecutionEventType.SPAN_SUCCEEDED,
            ExecutionEventType.SPAN_CANCELLED,
        }
        else payload
    )
    span_intent = parse_execution_event_intent_v2(
        {
            "type": span_type.value,
            "status": event_status.value,
            "source": actor,
            "span_id": root_span_id,
            "attempt": 1,
            "summary": {
                "key": f"agent.{agent_slug}.{outcome.status.value}",
                "text": f"Agent {readable_status}",
            },
            "public_payload": span_payload,
            "idempotency_key": f"terminal:{execution_id}:root-span",
        }
    )
    execution_intent = parse_execution_event_intent_v2(
        {
            "type": execution_type.value,
            "status": event_status.value,
            "source": actor,
            "span_id": root_span_id,
            "attempt": 1,
            "summary": {
                "key": f"execution.{outcome.status.value}",
                "text": f"Execution {readable_status}",
            },
            "public_payload": payload,
            "idempotency_key": f"terminal:{execution_id}:execution",
        }
    )
    return span_intent, execution_intent


def _terminal_event_shape(
    outcome: DriverOutcome,
) -> tuple[ExecutionEventType, ExecutionEventType, dict[str, object]]:
    if outcome.status is ExecutionStatus.SUCCEEDED:
        return (
            ExecutionEventType.EXECUTION_SUCCEEDED,
            ExecutionEventType.SPAN_SUCCEEDED,
            {},
        )
    if outcome.status is ExecutionStatus.PARTIAL:
        return (
            ExecutionEventType.EXECUTION_PARTIAL,
            ExecutionEventType.SPAN_PARTIAL,
            {"code": "partial_result", "retryable": False},
        )
    if outcome.status is ExecutionStatus.FAILED:
        assert outcome.failure is not None
        return (
            ExecutionEventType.EXECUTION_FAILED,
            ExecutionEventType.SPAN_FAILED,
            {
                "code": outcome.failure.code,
                "retryable": outcome.failure.retryable,
            },
        )
    if outcome.status is ExecutionStatus.CANCELLED:
        return (
            ExecutionEventType.EXECUTION_CANCELLED,
            ExecutionEventType.SPAN_CANCELLED,
            {"outcome": "best_effort"},
        )
    if outcome.status is ExecutionStatus.TIMED_OUT:
        return (
            ExecutionEventType.EXECUTION_TIMED_OUT,
            ExecutionEventType.SPAN_TIMED_OUT,
            {"code": "execution_timed_out", "retryable": False},
        )
    raise ValueError("terminal outcome required")


def public_result_json(outcome: DriverOutcome) -> str | None:
    """Serialize the public result, never private transport data."""
    if outcome.result is None:
        return None
    result = outcome.result
    public_result: dict[str, Any] = {
        "answer": result.answer,
        "follow_up_questions": list(result.follow_up_questions),
        "references": [dict(reference) for reference in result.references],
        "artifacts": [
            {
                "role": artifact.role,
                "target_kind": str(artifact.target_kind),
                "target_id": artifact.target_id,
                "name": artifact.name,
                "media_type": artifact.media_type,
                "size_bytes": artifact.size_bytes,
            }
            for artifact in result.artifacts
        ],
        "metadata": dict(result.public_metadata or {}),
    }
    if result.tabular is not None:
        public_result["tabular"] = result.tabular.to_public_dict()
    return json.dumps(
        public_result,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def terminal_expiry(status: ExecutionStatus, now: datetime) -> str:
    """Apply the existing run-retention policy to V2 terminal settlement."""
    config = ApiConfig()
    delta = (
        timedelta(hours=config.API_RUN_TTL_OK_HOURS)
        if status is ExecutionStatus.SUCCEEDED
        else timedelta(days=config.API_RUN_TTL_FAIL_DAYS)
    )
    return (now + delta).isoformat()


def durable_command_json(
    command: Mapping[str, Any] | None,
) -> str | None:
    """Encode one bounded, canonical detached command."""
    if command is None:
        return None
    try:
        encoded = json.dumps(
            command,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, UnicodeEncodeError) as exc:
        raise ValueError("durable command is not canonical JSON") from exc
    if len(encoded.encode("utf-8")) > 262_144:
        raise ValueError("durable command exceeds size limit")
    return encoded


def reservation_record(row: Sequence[object]) -> ExecutionReservationRecord:
    """Decode the stable reservation SELECT layout."""
    return ExecutionReservationRecord(
        owner=cast(str, row[0]),
        execution_id=cast(str, row[1]),
        run_id=cast(str, row[2]),
        fingerprint_version=cast(int, row[3]),
        fingerprint=cast(str, row[4]),
        command_hash=cast(str, row[5]),
        agent_slug=cast(str, row[6]),
        driver=cast(Driver, row[7]),
        root_span_id=cast(str, row[8]),
        status=ExecutionStatus(cast(str, row[9])),
        deadline_at=cast(str, row[10]),
        supervisor_revision=cast(int, row[11]),
        next_attempt_at=cast(str | None, row[12]),
        tracking_health=cast(str, row[13]),
        cancellation_state=cast(CancellationOutcome, row[14]),
        context_stage_json=cast(str | None, row[15]),
    )


def terminal_snapshot(
    journal: SQLiteExecutionJournal,
    connection: sqlite3.Connection,
    authority: TerminalSettlementAuthority,
    row: Sequence[object],
) -> TerminalSnapshot:
    """Read projection and root-span evidence inside the settlement TX."""
    reservation = TerminalReservationState(
        status=str(row[0]),
        terminal_outcome=None if row[1] is None else str(row[1]),
        cancellation_state=str(row[2]),
        root_span_id=str(row[3]),
        agent_slug=str(row[4]),
        supervisor_revision=int(cast(int, row[5])),
    )
    projection = _load_projection_locked(
        journal,
        connection,
        authority.owner_ref,
        authority.execution_id,
    )
    root_span = connection.execute(
        "SELECT status FROM execution_spans WHERE owner_ref = ? "
        "AND execution_id = ? AND span_id = ?",
        (
            authority.owner_ref,
            authority.execution_id,
            reservation.root_span_id,
        ),
    ).fetchone()
    return TerminalSnapshot(
        reservation=reservation,
        projection=projection,
        root_span_status=str(root_span[0]) if root_span else None,
    )


def terminal_replay_result(
    snapshot: TerminalSnapshot,
    authority: TerminalSettlementAuthority,
    outcome: DriverOutcome,
) -> bool | None:
    """Return a settled result, or ``None`` when the CAS may proceed."""
    existing = snapshot.reservation.terminal_outcome
    if existing is not None:
        return bool(
            existing == outcome.status.value
            and snapshot.projection.terminal is not None
            and snapshot.projection.terminal.status == outcome.status.value
        )
    if snapshot.reservation.supervisor_revision != authority.expected_revision:
        return False
    projection_terminal = snapshot.projection.terminal
    if (
        projection_terminal is not None
        and projection_terminal.status != outcome.status.value
    ):
        return False
    if (
        snapshot.root_span_status
        in {status.value for status in TERMINAL_EXECUTION_STATUSES}
        and snapshot.root_span_status != outcome.status.value
    ):
        return False
    return None


def terminal_plan(
    snapshot: TerminalSnapshot,
    outcome: DriverOutcome,
    clock: Callable[[], datetime],
) -> TerminalPlan:
    """Derive the canonical reservation values for terminal settlement."""
    cancellation_state: str | None = outcome.cancellation_outcome
    if cancellation_state is None:
        cancellation_state = (
            "confirmed"
            if outcome.status is ExecutionStatus.CANCELLED
            else snapshot.reservation.cancellation_state
        )
    result_json = public_result_json(outcome)
    now = clock()
    if now.utcoffset() is None:
        raise ValueError("clock must return timezone-aware datetime")
    return TerminalPlan(
        status=outcome.status,
        cancellation_state=cancellation_state,
        result_json=result_json,
        expires_at=terminal_expiry(outcome.status, now),
        now=now,
    )


def append_event_locked(
    journal: SQLiteExecutionJournal,
    connection: sqlite3.Connection,
    execution_id: str,
    owner: str,
    intent: ExecutionEventIntentV2,
) -> None:
    """Append through the journal's transaction-scoped storage hook."""
    append = cast(
        Callable[..., object],
        getattr(journal, "_append_locked"),
    )
    append(
        connection,
        execution_id=execution_id,
        owner=owner,
        intent=intent,
    )


def _load_projection_locked(
    journal: SQLiteExecutionJournal,
    connection: sqlite3.Connection,
    owner: str,
    execution_id: str,
) -> ExecutionProjectionV2:
    loader = cast(
        Callable[..., ExecutionProjectionV2],
        getattr(journal, "_load_or_rebuild_projection"),
    )
    return loader(connection, owner, execution_id)


setattr(
    routed_binding_matches_command,
    "__signature__",
    ROUTED_BINDING_SIGNATURE,
)


__all__ = [
    "CanonicalReservationFields",
    "CanonicalReservationKwargs",
    "EXPERT_ROUTER_AGENT_SLUG",
    "ExecutionOperationClaim",
    "ExecutionReservationRecord",
    "ObservationKwargs",
    "OperationClaimKwargs",
    "ReservationKwargs",
    "TERMINAL_EXECUTION_STATUSES",
    "append_event_locked",
    "execution_command_hash",
    "observation_request",
    "operation_claim_request",
    "reservation_plan",
    "reservation_record",
    "reservation_replay_matches",
    "routed_binding_matches_command",
    "terminal_event_intents",
    "terminal_plan",
    "terminal_replay_result",
    "terminal_snapshot",
]
