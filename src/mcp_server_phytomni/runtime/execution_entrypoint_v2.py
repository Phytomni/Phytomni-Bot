# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transport boundary adapter into the canonical execution Runtime."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ..public_agent_catalog import public_agent_spec
from .execution_drivers_v2 import CANONICAL_DRIVER_TYPES
from .execution_event_flags import execution_log_artifact_enabled
from .execution_instrumentation_v2 import (
    bind_execution_boundary,
    instrument_nested_agent_invocation,
)
from .execution_journal_store_v2 import SQLiteExecutionJournal
from .execution_journal_v2 import ExecutionStatus, TrackingHealth
from .execution_log_artifact_v2 import SQLiteExecutionLogArtifactStore
from .execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    ExecutionReservationConflictError,
    ExecutionReservationRecord,
    SQLiteExecutionReservationRepository,
    execution_command_hash,
)
from .execution_runtime_contracts import (
    DriverFailure,
    DriverOperation,
    DriverOutcome,
    ExecutionArtifactRef,
    ExecutionCommand,
    ExecutionRuntimeError,
    TransportNeutralResult,
    TransportNeutralTabular,
)
from .execution_runtime_v2 import ExecutionRuntime
from .execution_target_store_v2 import SQLiteExecutionTargetStore
from .execution_work_store_v2 import SQLiteExecutionWorkRepository
from .public_execution_safety import (
    PublicExecutionDataError,
    validate_public_execution_value,
)

BusinessCall = Callable[[], Awaitable[Any]]
StreamResponseCall = Callable[[str], Awaitable[Any]]
StatusMapper = Callable[[Any], ExecutionStatus]
PublicResultMapper = Callable[[Any], Any]
_ACTIVE_EXECUTION: ContextVar[bool] = ContextVar(
    "phytomni_runtime_v2_active",
    default=False,
)
_CANONICAL_RESERVATION_IDENTITY: ContextVar[
    CanonicalReservationIdentity | None
] = ContextVar("phytomni_canonical_reservation_identity", default=None)
_PRIVATE_VALUE_UNSET = object()
_PUBLIC_CITATION_FIELDS = (
    "title",
    "au",
    "ti",
    "so",
    "vl",
    "bp",
    "ep",
    "ar",
    "py",
    "di",
    "pm",
)
_MAX_PUBLIC_CITATION_REFERENCES = 64
_MAX_PUBLIC_CITATION_BYTES = 6144
_MAX_PUBLIC_TABULAR_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _PrivateInvocation:
    """Ephemeral value/error carrier; never written to journal or storage."""

    value: Any = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class CanonicalReservationIdentity:
    """Trusted detached-command identity kept outside business arguments."""

    owner: str
    execution_id: str
    fingerprint_version: int
    fingerprint: str
    command: ExecutionCommand

    def __post_init__(self) -> None:
        if not self.owner or not self.execution_id or not self.fingerprint:
            raise ValueError("canonical reservation identity is incomplete")
        if self.fingerprint_version < 1:
            raise ValueError("canonical fingerprint version is invalid")


@contextmanager
def bind_canonical_reservation_identity(
    identity: CanonicalReservationIdentity,
) -> Iterator[None]:
    """Bind one queue-validated identity across Expert preparation awaits."""
    token = _CANONICAL_RESERVATION_IDENTITY.set(identity)
    try:
        yield
    finally:
        _CANONICAL_RESERVATION_IDENTITY.reset(token)


@contextmanager
def bind_routed_reservation_identity(
    *,
    db_path: str,
    owner: str,
    execution_id: str,
    command: ExecutionCommand,
) -> Iterator[ExecutionReservationRecord | None]:
    """Fence one router-to-selected identity transition for Runtime start.

    Direct admission remains a no-op.  A routed transition is authorized only
    by the queue-validated router identity already bound by the dispatcher and
    by the selected command persisted through ``bind_routed_agent``.
    """
    outer = _CANONICAL_RESERVATION_IDENTITY.get()
    if outer is None or outer.command.agent_slug != EXPERT_ROUTER_AGENT_SLUG:
        yield None
        return
    if (
        outer.owner != owner
        or outer.execution_id != execution_id
        or command.agent_slug == EXPERT_ROUTER_AGENT_SLUG
    ):
        raise ExecutionRuntimeError("execution_identity_conflict")
    repository = SQLiteExecutionReservationRepository(db_path)
    current = repository.get(owner=owner, execution_id=execution_id)
    if (
        current.fingerprint_version != outer.fingerprint_version
        or current.fingerprint != outer.fingerprint
    ):
        raise ExecutionRuntimeError("execution_identity_conflict")
    try:
        bound = repository.bind_routed_agent(
            owner=owner,
            execution_id=execution_id,
            command=command,
        )
    except ExecutionReservationConflictError as exc:
        raise ExecutionRuntimeError("execution_identity_conflict") from exc
    if (
        bound.owner != owner
        or bound.execution_id != execution_id
        or bound.fingerprint_version != outer.fingerprint_version
        or bound.fingerprint != outer.fingerprint
        or bound.agent_slug != command.agent_slug
        or bound.command_hash != execution_command_hash(command)
    ):
        raise ExecutionRuntimeError("execution_identity_conflict")
    selected = CanonicalReservationIdentity(
        owner=owner,
        execution_id=execution_id,
        fingerprint_version=bound.fingerprint_version,
        fingerprint=bound.fingerprint,
        command=command,
    )
    with bind_canonical_reservation_identity(selected):
        yield bound


async def invoke_public_agent(
    *,
    db_path: str,
    owner: str,
    execution_id: str,
    agent_slug: str,
    arguments: dict[str, Any],
    transport: str,
    call: BusinessCall,
    status_mapper: StatusMapper | None = None,
    public_result_mapper: PublicResultMapper | None = None,
    admit_new_user_turn: bool = False,
    fingerprint_version: int = 1,
    fingerprint: str | None = None,
    run_id: str | None = None,
) -> Any:
    """Run one existing business call through Runtime without reshaping it."""
    spec = public_agent_spec(agent_slug)
    if spec is None:
        raise ExecutionRuntimeError("unknown_public_agent")
    if _ACTIVE_EXECUTION.get() and not admit_new_user_turn:
        return await instrument_nested_agent_invocation(agent_slug, call)

    async def start_handler(context, command, services):
        del context, command, services
        token = _ACTIVE_EXECUTION.set(True)
        try:
            try:
                value = await call()
            finally:
                _ACTIVE_EXECUTION.reset(token)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            return DriverOutcome(
                status=ExecutionStatus.FAILED,
                failure=DriverFailure(
                    code="business_execution_failed",
                    retryable=False,
                ),
                result=TransportNeutralResult(
                    private_value=_PrivateInvocation(error=exc)
                ),
            )
        status = (
            status_mapper(value)
            if status_mapper is not None
            else _default_status(value)
        )
        projected_value = (
            public_result_mapper(value)
            if public_result_mapper is not None
            else value
        )
        result = _public_result_with_private(
            projected_value,
            private_value=value,
            agent_slug=agent_slug,
        )
        return DriverOutcome(
            status=status,
            result=result,
            tracking_health=_default_tracking_health(value),
        )

    driver_type = CANONICAL_DRIVER_TYPES[spec.driver]
    driver = driver_type({DriverOperation.START: start_handler})
    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(
            db_path,
            run_id_factory=(lambda: run_id) if run_id is not None else None,
        ),
        journal=SQLiteExecutionJournal(db_path),
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={spec.driver: driver},
        target_store=SQLiteExecutionTargetStore(db_path),
        execution_log_store=(
            SQLiteExecutionLogArtifactStore(db_path)
            if execution_log_artifact_enabled()
            else None
        ),
    )
    command = ExecutionCommand(agent_slug=agent_slug, arguments=arguments)
    identity = _CANONICAL_RESERVATION_IDENTITY.get()
    reservation_command = command
    if identity is not None:
        if (
            identity.owner != owner
            or identity.execution_id != execution_id
            or identity.fingerprint_version != fingerprint_version
            or identity.fingerprint != (fingerprint or _fingerprint(command))
            or identity.command.agent_slug != agent_slug
        ):
            raise ExecutionRuntimeError("execution_identity_conflict")
        reservation_command = identity.command
    outcome = await runtime.start(
        owner=owner,
        execution_id=execution_id,
        fingerprint_version=fingerprint_version,
        fingerprint=fingerprint or _fingerprint(command),
        command=command,
        reservation_command=reservation_command,
        transport=transport,
    )
    private = (
        outcome.result.private_value if outcome.result is not None else None
    )
    if isinstance(private, _PrivateInvocation):
        if private.error is not None:
            raise private.error
        return private.value
    raise ExecutionRuntimeError("execution_result_requires_projection")


async def invoke_public_agent_stream_response(
    *,
    db_path: str,
    owner: str,
    execution_id: str,
    agent_slug: str,
    arguments: dict[str, Any],
    transport: str,
    call: StreamResponseCall,
    run_id: str | None = None,
    allow_terminal_replay: bool = False,
) -> Any:
    """Bind a streaming response to one Runtime lifecycle.

    Response construction runs after reservation.  Full iterator exhaustion
    settles success, a real iterator error settles failure, and consumer
    closure deliberately leaves the execution running for durable recovery.
    """
    if _ACTIVE_EXECUTION.get():
        raise ExecutionRuntimeError("nested_stream_response_unsupported")
    spec = public_agent_spec(agent_slug)
    if spec is None:
        raise ExecutionRuntimeError("unknown_public_agent")
    reservations = SQLiteExecutionReservationRepository(
        db_path,
        run_id_factory=(lambda: run_id) if run_id is not None else None,
    )
    stream_boundary: list[tuple[Any, Any]] = []
    stream_responses: list[Any] = []

    async def start_handler(context, command, services):
        del command
        if context.run_id is None:
            raise ExecutionRuntimeError("execution_run_id_required")
        stream_boundary.append((context, services))
        token = _ACTIVE_EXECUTION.set(True)
        try:
            response = await call(context.run_id)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            return _failed_private_outcome(exc)
        finally:
            _ACTIVE_EXECUTION.reset(token)
        stream_responses.append(response)
        return DriverOutcome.running(
            result=TransportNeutralResult(
                private_value=_PrivateInvocation(value=response)
            )
        )

    async def reconcile_handler(context, command, services):
        del context, services
        status = str(command.arguments.get("terminal_status", "failed"))
        projected_result = TransportNeutralResult()
        if len(stream_responses) == 1:
            result_reader = getattr(
                stream_responses[0], "runtime_terminal_result", None
            )
            if callable(result_reader):
                projected_result = _public_result_with_private(
                    result_reader(),
                    private_value=None,
                    agent_slug=agent_slug,
                )
        if status == "succeeded":
            return DriverOutcome.succeeded(projected_result)
        if status in {"input_required", "waiting_input", "paused"}:
            return DriverOutcome(
                status=ExecutionStatus.WAITING_INPUT,
                result=projected_result,
            )
        return DriverOutcome(
            status=ExecutionStatus.FAILED,
            result=projected_result,
            failure=DriverFailure(
                code="stream_execution_failed", retryable=False
            ),
        )

    driver_type = CANONICAL_DRIVER_TYPES[spec.driver]
    driver = driver_type(
        {
            DriverOperation.START: start_handler,
            DriverOperation.RECONCILE: reconcile_handler,
        }
    )
    runtime = ExecutionRuntime(
        reservations=reservations,
        journal=SQLiteExecutionJournal(db_path),
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={spec.driver: driver},
        target_store=SQLiteExecutionTargetStore(db_path),
        execution_log_store=(
            SQLiteExecutionLogArtifactStore(db_path)
            if execution_log_artifact_enabled()
            else None
        ),
    )
    command = ExecutionCommand(agent_slug=agent_slug, arguments=arguments)
    outcome = await runtime.start(
        owner=owner,
        execution_id=execution_id,
        fingerprint_version=1,
        fingerprint=_fingerprint(command),
        command=command,
        transport=transport,
    )
    try:
        response = _private_value(outcome)
    except ExecutionRuntimeError:
        if not (allow_terminal_replay and outcome.terminal):
            raise
        # Conversation-turn replay is a read adapter over an already settled
        # execution.  Rebuild only the stored wire response; the context store
        # prevents graph/tool re-invocation and Runtime remains untouched.
        existing = reservations.get(owner=owner, execution_id=execution_id)
        return await call(existing.run_id)
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None or not hasattr(body_iterator, "__aiter__"):
        raise ExecutionRuntimeError("stream_response_iterator_required")

    async def settle(status: str) -> None:
        record = reservations.get(owner=owner, execution_id=execution_id)
        await runtime.reconcile(
            owner=owner,
            execution_id=execution_id,
            command=ExecutionCommand(
                agent_slug=agent_slug,
                arguments={"terminal_status": status},
                action_id=f"stream-terminal:{status}",
                expected_revision=record.supervisor_revision,
            ),
            transport=transport,
        )

    async def wrapped() -> AsyncIterator[Any]:
        iterator = aiter(body_iterator)
        completed = False
        terminal_settled = False
        if len(stream_boundary) != 1:
            raise ExecutionRuntimeError("stream_execution_boundary_required")
        context, services = stream_boundary[0]

        async def settle_if_ready() -> None:
            nonlocal terminal_settled
            if terminal_settled:
                return
            ready_reader = getattr(response, "runtime_terminal_ready", None)
            if not (callable(ready_reader) and bool(ready_reader())):
                return
            status_reader = getattr(response, "runtime_terminal_status", None)
            terminal_status = (
                status_reader() if callable(status_reader) else "succeeded"
            )
            await settle(str(terminal_status))
            terminal_settled = True

        try:
            while True:
                with bind_execution_boundary(context, services):
                    token = _ACTIVE_EXECUTION.set(True)
                    try:
                        chunk = await anext(iterator)
                    except StopAsyncIteration:
                        completed = True
                        break
                    finally:
                        _ACTIVE_EXECUTION.reset(token)
                # A domain adapter records its typed outcome before yielding
                # the public terminal/surface frame.  Commit that observation
                # through Runtime first so the wire never outruns durability.
                await settle_if_ready()
                yield chunk
        except Exception:
            await settle("failed")
            terminal_settled = True
            raise
        finally:
            closer = getattr(iterator, "aclose", None)
            if callable(closer):
                close_result = closer()
                if inspect.isawaitable(close_result):
                    await close_result
            ready_reader = getattr(response, "runtime_terminal_ready", None)
            terminal_ready = completed or (
                callable(ready_reader) and bool(ready_reader())
            )
            if terminal_ready and not terminal_settled:
                status_reader = getattr(
                    response, "runtime_terminal_status", None
                )
                terminal_status = (
                    status_reader() if callable(status_reader) else "succeeded"
                )
                await settle(str(terminal_status))

    response.body_iterator = wrapped()
    return response


async def invoke_public_agent_operation(
    *,
    db_path: str,
    owner: str,
    execution_id: str,
    agent_slug: str,
    operation: str,
    action_id: str,
    expected_revision: int,
    arguments: dict[str, Any],
    transport: str,
    call: BusinessCall,
    status_mapper: StatusMapper | None = None,
    expected_provider_join_lease_token: str | None = None,
) -> Any:
    """Route a resume/cancel/recovery operation through the same Runtime."""
    try:
        driver_operation = DriverOperation(operation)
    except ValueError as exc:
        raise ExecutionRuntimeError("unsupported_runtime_operation") from exc
    if driver_operation is DriverOperation.START:
        raise ExecutionRuntimeError("unsupported_runtime_operation")
    spec = public_agent_spec(agent_slug)
    if spec is None:
        raise ExecutionRuntimeError("unknown_public_agent")

    async def operation_handler(context, command, services):
        del context, command, services
        token = _ACTIVE_EXECUTION.set(True)
        try:
            try:
                value = await call()
            finally:
                _ACTIVE_EXECUTION.reset(token)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            return _failed_private_outcome(exc)
        status = (
            status_mapper(value)
            if status_mapper is not None
            else _default_status(value)
        )
        result = _public_result_with_private(value, agent_slug=agent_slug)
        if status is ExecutionStatus.SUCCEEDED:
            return DriverOutcome.succeeded(result)
        if status is ExecutionStatus.FAILED:
            return DriverOutcome(
                status=status,
                failure=DriverFailure(
                    code="business_execution_failed", retryable=False
                ),
                result=result,
            )
        return DriverOutcome(status=status, result=result)

    driver_type = CANONICAL_DRIVER_TYPES[spec.driver]
    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(
            db_path,
            expected_provider_join_lease_token=(
                expected_provider_join_lease_token
            ),
        ),
        journal=SQLiteExecutionJournal(
            db_path,
            expected_provider_join_lease_token=(
                expected_provider_join_lease_token
            ),
        ),
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={
            spec.driver: driver_type({driver_operation: operation_handler})
        },
        target_store=SQLiteExecutionTargetStore(
            db_path,
            expected_provider_join_lease_token=(
                expected_provider_join_lease_token
            ),
        ),
        execution_log_store=(
            SQLiteExecutionLogArtifactStore(db_path)
            if execution_log_artifact_enabled()
            else None
        ),
    )
    command = ExecutionCommand(
        agent_slug=agent_slug,
        arguments=arguments,
        action_id=action_id,
        expected_revision=expected_revision,
    )
    runtime_call = getattr(runtime, driver_operation.value)
    outcome = await runtime_call(
        owner=owner,
        execution_id=execution_id,
        command=command,
        transport=transport,
    )
    return _private_value(outcome)


def _failed_private_outcome(exc: BaseException) -> DriverOutcome:
    return DriverOutcome(
        status=ExecutionStatus.FAILED,
        failure=DriverFailure(
            code="business_execution_failed",
            retryable=False,
        ),
        result=TransportNeutralResult(
            private_value=_PrivateInvocation(error=exc)
        ),
    )


def _private_value(outcome: DriverOutcome) -> Any:
    private = (
        outcome.result.private_value if outcome.result is not None else None
    )
    if isinstance(private, _PrivateInvocation):
        if private.error is not None:
            raise private.error
        return private.value
    raise ExecutionRuntimeError("execution_result_requires_projection")


def _default_status(value: Any) -> ExecutionStatus:
    if isinstance(value, tuple) and len(value) == 2:
        body, status_code = value
        if isinstance(body, dict):
            status = body.get("status")
            mapping = {
                "running": ExecutionStatus.RUNNING,
                "input_required": ExecutionStatus.WAITING_INPUT,
                "waiting_input": ExecutionStatus.WAITING_INPUT,
                "paused": ExecutionStatus.WAITING_INPUT,
                "partial": ExecutionStatus.PARTIAL,
                "failed": ExecutionStatus.FAILED,
                "cancelled": ExecutionStatus.CANCELLED,
                "timed_out": ExecutionStatus.TIMED_OUT,
            }
            if status in mapping:
                return mapping[status]
        if isinstance(status_code, int) and status_code == 202:
            return ExecutionStatus.RUNNING
    return ExecutionStatus.SUCCEEDED


def _default_tracking_health(value: Any) -> TrackingHealth:
    """Project explicit compatibility tracking state into Runtime V2."""
    candidate = (
        value[0] if isinstance(value, tuple) and len(value) == 2 else value
    )
    if not isinstance(candidate, Mapping):
        return TrackingHealth.HEALTHY
    if candidate.get("degraded_tracking") is True:
        return TrackingHealth.DEGRADED
    result = candidate.get("result")
    if not isinstance(result, Mapping):
        return TrackingHealth.HEALTHY
    execution = result.get("execution")
    if not isinstance(execution, Mapping):
        return TrackingHealth.HEALTHY
    tracking = execution.get("tracking")
    if isinstance(tracking, Mapping) and tracking.get("degraded") is True:
        return TrackingHealth.DEGRADED
    return TrackingHealth.HEALTHY


def _public_result_with_private(
    value: Any,
    *,
    private_value: Any = _PRIVATE_VALUE_UNSET,
    agent_slug: str | None = None,
) -> TransportNeutralResult:
    """Project common transport values without copying Agent decisions."""
    candidate = value
    if isinstance(value, tuple) and len(value) == 2:
        candidate = value[0]
    answer = ""
    follow_up_questions: tuple[str, ...] = ()
    references: tuple[Mapping[str, str | bool], ...] = ()
    tabular: TransportNeutralTabular | None = None
    artifacts: list[ExecutionArtifactRef] = []
    public_metadata: dict[str, str | int | float | bool | None] = {}
    if isinstance(candidate, Mapping):
        result = candidate.get("result")
        result_mapping = result if isinstance(result, Mapping) else candidate
        formatted = result_mapping.get("formatted")
        formatted_mapping = (
            formatted if isinstance(formatted, Mapping) else result_mapping
        )
        raw_answer = formatted_mapping.get("answer")
        if isinstance(raw_answer, str):
            answer = _safe_public_answer(
                raw_answer,
                task_identifiers=_task_identifiers(candidate),
            )
        raw_follow_up = formatted_mapping.get("follow_up_questions")
        if isinstance(raw_follow_up, Sequence) and not isinstance(
            raw_follow_up, (str, bytes)
        ):
            follow_up_questions = tuple(
                projected
                for item in raw_follow_up
                if isinstance(item, str)
                and (projected := _safe_public_text(item, max_chars=2048))
            )
        references = _safe_public_references(
            formatted_mapping.get("references")
        )
        tabular = _safe_public_tabular(formatted_mapping.get("tabular"))
        for key in ("stream", "partial", "truncated"):
            metadata_value = result_mapping.get(key)
            if isinstance(metadata_value, (str, int, float, bool)) or (
                metadata_value is None and key in result_mapping
            ):
                public_metadata[key] = metadata_value
        execution = result_mapping.get("execution")
        execution_mapping = (
            execution if isinstance(execution, Mapping) else result_mapping
        )
        raw_artifacts = execution_mapping.get("artifacts")
        if isinstance(raw_artifacts, Sequence) and not isinstance(
            raw_artifacts, (str, bytes)
        ):
            for raw_artifact in raw_artifacts:
                artifact = _public_artifact(raw_artifact)
                if artifact is not None:
                    artifacts.append(artifact)
        archive = _public_result_archive(
            result_mapping,
            execution_mapping,
            agent_slug=agent_slug,
        )
        if archive is not None and all(
            artifact.target_id != archive.target_id for artifact in artifacts
        ):
            artifacts.append(archive)
    return TransportNeutralResult(
        answer=answer,
        follow_up_questions=follow_up_questions,
        references=references,
        tabular=tabular,
        artifacts=tuple(artifacts),
        public_metadata=public_metadata or None,
        private_value=_PrivateInvocation(
            value=(
                value
                if private_value is _PRIVATE_VALUE_UNSET
                else private_value
            )
        ),
    )


def _safe_public_tabular(value: object) -> TransportNeutralTabular | None:
    """Keep one complete, finite scalar table or omit the invalid shape."""
    if not isinstance(value, Mapping):
        return None
    raw_headers = value.get("headers")
    raw_rows = value.get("rows")
    if (
        not isinstance(raw_headers, Sequence)
        or isinstance(raw_headers, (str, bytes))
        or not isinstance(raw_rows, Sequence)
        or isinstance(raw_rows, (str, bytes))
        or not raw_headers
        or len(raw_headers) > 256
        or len(raw_rows) > 100_000
    ):
        return None
    headers: list[str] = []
    for raw_header in raw_headers:
        if not isinstance(raw_header, str):
            return None
        header = raw_header.strip()
        if not header or len(header) > 512:
            return None
        try:
            validate_public_execution_value(header, max_string_chars=512)
        except PublicExecutionDataError:
            return None
        headers.append(header)
    rows: list[tuple[str | int | float | bool | None, ...]] = []
    for raw_row in raw_rows:
        if (
            not isinstance(raw_row, Sequence)
            or isinstance(raw_row, (str, bytes))
            or len(raw_row) != len(headers)
        ):
            return None
        row: list[str | int | float | bool | None] = []
        for raw_cell in raw_row:
            cell: str | int | float | bool | None
            if isinstance(raw_cell, str):
                if len(raw_cell) > 8192:
                    return None
                try:
                    validate_public_execution_value(
                        raw_cell,
                        max_string_chars=8192,
                    )
                except PublicExecutionDataError:
                    return None
                cell = raw_cell
            elif isinstance(raw_cell, bool) or raw_cell is None:
                cell = raw_cell
            elif isinstance(raw_cell, int):
                cell = (
                    raw_cell
                    if abs(raw_cell) <= 9_007_199_254_740_991
                    else str(raw_cell)
                )
            elif isinstance(raw_cell, float) and math.isfinite(raw_cell):
                cell = raw_cell
            else:
                return None
            row.append(cell)
        rows.append(tuple(row))
    try:
        tabular = TransportNeutralTabular(
            headers=tuple(headers),
            rows=tuple(rows),
        )
        encoded = json.dumps(
            tabular.to_public_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError):
        return None
    return tabular if len(encoded) <= _MAX_PUBLIC_TABULAR_BYTES else None


def _safe_public_references(
    value: object,
) -> tuple[Mapping[str, str | bool], ...]:
    """Keep an ordered, finite bibliography without paths or direct URLs."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    projected: list[Mapping[str, str | bool]] = []
    for raw_reference in value[:_MAX_PUBLIC_CITATION_REFERENCES]:
        if not isinstance(raw_reference, Mapping):
            continue
        reference: dict[str, str | bool] = {}
        for key in _PUBLIC_CITATION_FIELDS:
            raw_field = raw_reference.get(key)
            if not isinstance(raw_field, (str, int, float)) or isinstance(
                raw_field, bool
            ):
                continue
            field = str(raw_field).strip()
            if not field:
                continue
            try:
                validate_public_execution_value(field, max_string_chars=512)
            except PublicExecutionDataError:
                continue
            reference[key] = field
        raw_missing = raw_reference.get("doi_missing")
        if isinstance(raw_missing, bool):
            reference["doi_missing"] = raw_missing
        candidate = [*projected, reference]
        encoded = json.dumps(
            candidate,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_PUBLIC_CITATION_BYTES:
            break
        projected.append(reference)
    return tuple(projected)


def _safe_public_text(value: str, *, max_chars: int) -> str:
    """Keep bounded event text or omit it without changing transport output."""
    projected = value[-max_chars:]
    try:
        validate_public_execution_value(
            projected,
            max_string_chars=max_chars,
        )
    except PublicExecutionDataError:
        return ""
    return projected


def _safe_public_answer(
    value: str,
    *,
    task_identifiers: tuple[str, ...] = (),
) -> str:
    """Keep scientific content while excluding operational acknowledgements."""
    if _is_submission_acknowledgement(value, task_identifiers):
        return ""
    value = _redact_task_identifiers(value, task_identifiers)
    for index in range(0, len(value), 8192):
        try:
            validate_public_execution_value(
                value[slice(index, index + 8192)],
                max_string_chars=8192,
            )
        except PublicExecutionDataError:
            return ""
    return value


_TASK_IDENTIFIER_KEYS = frozenset(
    {
        "poll_task_id",
        "provider_task_id",
        "source_task_id",
        "submitted_task_id",
        "task_id",
        "task_ids",
    }
)
_ACKNOWLEDGEMENT_PREFIXES = (
    "analysis submitted",
    "submission accepted",
    "task created successfully",
    "task submission failed",
    "tasks created successfully",
)
_TASK_STATUS_WORDS = frozenset(
    {
        "accepted",
        "cancelled",
        "completed",
        "failed",
        "pending",
        "queued",
        "running",
        "submitted",
        "succeeded",
        "timed_out",
        "unknown",
    }
)


def _task_identifiers(value: object) -> tuple[str, ...]:
    """Collect opaque provider identifiers only for answer redaction."""
    found: list[str] = []

    def visit(item: object, *, depth: int, task_context: bool = False) -> None:
        if depth > 6 or len(found) >= 64:
            return
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).casefold()
                identifier_field = key in _TASK_IDENTIFIER_KEYS or (
                    task_context and key == "id"
                )
                if identifier_field:
                    visit_identifier(child, depth=depth + 1)
                else:
                    visit(
                        child,
                        depth=depth + 1,
                        task_context=key == "tasks",
                    )
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for child in item[:64]:
                visit(child, depth=depth + 1, task_context=task_context)

    def visit_identifier(item: object, *, depth: int) -> None:
        if isinstance(item, str):
            identifier = item.strip()
            if identifier and identifier not in found:
                found.append(identifier)
            return
        if isinstance(item, Mapping):
            for child in item.values():
                visit_identifier(child, depth=depth + 1)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for child in item[:64]:
                visit_identifier(child, depth=depth + 1)

    visit(value, depth=0)
    return tuple(found)


def _is_submission_acknowledgement(
    value: str,
    task_identifiers: tuple[str, ...],
) -> bool:
    normalized = " ".join(value.casefold().split())
    if normalized.startswith(_ACKNOWLEDGEMENT_PREFIXES):
        return True
    without_ids = _redact_task_identifiers(
        normalized,
        task_identifiers,
        replacement="",
    )
    words = without_ids.replace(":", " ").replace(".", " ").split()
    return bool(
        words
        and words[0] in {"task", "tasks"}
        and any(word in _TASK_STATUS_WORDS for word in words[1:])
        and len(words) <= 8
    )


def _redact_task_identifiers(
    value: str,
    task_identifiers: tuple[str, ...],
    *,
    replacement: str = "the analysis task",
) -> str:
    redacted = value
    for identifier in sorted(task_identifiers, key=len, reverse=True):
        redacted = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])",
            replacement,
            redacted,
        )
    return redacted


def _public_artifact(value: Any) -> ExecutionArtifactRef | None:
    """Accept only opaque, bounded descriptors from the existing envelope."""
    if not isinstance(value, Mapping):
        return None
    delivery_ref = value.get("download_ref")
    target_id = value.get("id", value.get("artifact_id"))
    role = value.get("role")
    if target_id is None and isinstance(delivery_ref, str) and delivery_ref:
        target_id = (
            "artifact-"
            + hashlib.sha256(
                delivery_ref.encode("utf-8", errors="strict")
            ).hexdigest()[:32]
        )
    if not isinstance(target_id, str) or not isinstance(role, str):
        return None
    name = value.get("name")
    media_type = value.get("media_type", "application/octet-stream")
    size_bytes = value.get("size_bytes", 0)
    if name is not None and not isinstance(name, str):
        return None
    if not isinstance(media_type, str) or not isinstance(size_bytes, int):
        return None
    try:
        return ExecutionArtifactRef(
            role=role,
            target_kind="artifact",
            target_id=target_id,
            name=name,
            media_type=media_type,
            size_bytes=size_bytes,
            private_delivery_ref=(
                delivery_ref
                if isinstance(delivery_ref, str) and delivery_ref
                else None
            ),
        )
    except ValueError:
        return None


def _public_result_archive(
    result: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    agent_slug: str | None,
) -> ExecutionArtifactRef | None:
    """Bind one ready archive to its private object without leaking paths."""
    if not agent_slug or not agent_slug.replace("_", "").isalnum():
        return None
    delivery = execution.get("delivery")
    private = result.get("delivery_internal")
    if not isinstance(delivery, Mapping) or not isinstance(private, Mapping):
        return None
    archive = delivery.get("archive")
    digest = delivery.get("inventory_digest")
    inventory_ref = private.get("inventory_ref")
    if (
        delivery.get("status") != "ready"
        or not isinstance(archive, Mapping)
        or not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in digest[7:])
        or not isinstance(inventory_ref, str)
        or not inventory_ref
        or "\\" in inventory_ref
        or len(inventory_ref) > 4096
    ):
        return None
    name = archive.get("name")
    media_type = archive.get("media_type")
    size_bytes = archive.get("size_bytes")
    public_ref = archive.get("download_ref")
    if (
        archive.get("role") != "result_archive"
        or name != f"{agent_slug}-results.zip"
        or media_type != "application/zip"
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or archive.get("downloadable") is not True
        or archive.get("report_context_eligible") is not False
        or not isinstance(public_ref, str)
        or public_ref != f"result-archive:{digest}"
    ):
        return None
    marker = f"/delivery/{digest.removeprefix('sha256:')}/"
    run_root, matched, leaf = inventory_ref.rpartition(marker)
    if (
        not run_root
        or matched != marker
        or leaf != ".phytomni-result-inventory.json"
    ):
        return None
    target_id = (
        "download-" + hashlib.sha256(public_ref.encode()).hexdigest()[:32]
    )
    try:
        return ExecutionArtifactRef(
            role="result_archive",
            target_kind="download",
            target_id=target_id,
            name=name,
            media_type=media_type,
            size_bytes=size_bytes,
            private_delivery_ref=f"{run_root}{marker}{name}",
        )
    except ValueError:
        return None


def _fingerprint(command: ExecutionCommand) -> str:
    try:
        payload = json.dumps(
            {"agent_slug": command.agent_slug, "arguments": command.arguments},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ExecutionRuntimeError("command_not_canonical") from exc
    return hashlib.sha256(payload).hexdigest()
