# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transport boundary adapter into the canonical execution Runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
)
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from inspect import Parameter, Signature
from typing import Any, NotRequired, TypedDict, Unpack

from ..public_agent_catalog import Driver, public_agent_spec
from .async_iterator_v2 import close_async_iterator
from .execution_drivers_v2 import CANONICAL_DRIVER_TYPES
from .execution_event_flags import execution_log_artifact_enabled
from .execution_instrumentation_v2 import (
    bind_execution_boundary,
    instrument_nested_agent_invocation,
)
from .execution_journal_store_v2 import SQLiteExecutionJournal
from .execution_journal_v2 import ExecutionStatus
from .execution_log_artifact_v2 import SQLiteExecutionLogArtifactStore
from .execution_public_projection_v2 import (
    PrivateInvocation as _PrivateInvocation,
)
from .execution_public_projection_v2 import (
    default_status as _default_status,
)
from .execution_public_projection_v2 import (
    default_tracking_health as _default_tracking_health,
)
from .execution_public_projection_v2 import (
    public_result_with_private as _public_result_with_private,
)
from .execution_reservation_support_v2 import CanonicalReservationFields
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
    ExecutionCommand,
    ExecutionDriver,
    ExecutionRuntimeError,
    TransportNeutralResult,
)
from .execution_runtime_v2 import ExecutionRuntime
from .execution_target_store_v2 import SQLiteExecutionTargetStore
from .execution_work_store_v2 import SQLiteExecutionWorkRepository
from .instrumentation_contracts_v2 import keyword_signature

BusinessCall = Callable[[], Awaitable[Any]]
StreamResponseCall = Callable[[str], Awaitable[Any]]
StatusMapper = Callable[[Any], ExecutionStatus]
PublicResultMapper = Callable[[Any], Any]
_BUSINESS_FAILURES: tuple[type[BaseException], ...] = (BaseException,)
_STREAM_FAILURES: tuple[type[Exception], ...] = (Exception,)
_ACTIVE_EXECUTION: ContextVar[bool] = ContextVar(
    "phytomni_runtime_v2_active",
    default=False,
)
_CANONICAL_RESERVATION_IDENTITY: ContextVar[
    CanonicalReservationIdentity | None
] = ContextVar("phytomni_canonical_reservation_identity", default=None)


class InvocationTargetKwargs(TypedDict):
    """Common transport identity accepted by all public invocations."""

    db_path: str
    owner: str
    execution_id: str
    agent_slug: str
    arguments: dict[str, Any]
    transport: str


class InvokePublicAgentKwargs(InvocationTargetKwargs):
    """Stable keyword contract for one public Agent invocation."""

    call: BusinessCall
    status_mapper: NotRequired[StatusMapper | None]
    public_result_mapper: NotRequired[PublicResultMapper | None]
    admit_new_user_turn: NotRequired[bool]
    fingerprint_version: NotRequired[int]
    fingerprint: NotRequired[str | None]
    run_id: NotRequired[str | None]


class StreamResponseKwargs(InvocationTargetKwargs):
    """Stable keyword contract for one streaming public invocation."""

    call: StreamResponseCall
    run_id: NotRequired[str | None]
    allow_terminal_replay: NotRequired[bool]


class InvokePublicAgentOperationKwargs(InvocationTargetKwargs):
    """Stable keyword contract for a public Runtime operation."""

    operation: str
    action_id: str
    expected_revision: int
    call: BusinessCall
    status_mapper: NotRequired[StatusMapper | None]
    expected_provider_join_lease_token: NotRequired[str | None]


def _public_signature(
    parameters: tuple[tuple[str, object, object], ...],
) -> Signature:
    return keyword_signature(
        tuple(
            (name, annotation)
            for name, annotation, default in parameters
            if default is Parameter.empty
        ),
        tuple(
            (name, annotation, default)
            for name, annotation, default in parameters
            if default is not Parameter.empty
        ),
        return_annotation=Any,
    )


_REQUIRED = Parameter.empty
_PUBLIC_AGENT_SIGNATURE = _public_signature(
    (
        ("db_path", str, _REQUIRED),
        ("owner", str, _REQUIRED),
        ("execution_id", str, _REQUIRED),
        ("agent_slug", str, _REQUIRED),
        ("arguments", dict[str, Any], _REQUIRED),
        ("transport", str, _REQUIRED),
        ("call", BusinessCall, _REQUIRED),
        ("status_mapper", StatusMapper | None, None),
        ("public_result_mapper", PublicResultMapper | None, None),
        ("admit_new_user_turn", bool, False),
        ("fingerprint_version", int, 1),
        ("fingerprint", str | None, None),
        ("run_id", str | None, None),
    )
)
_STREAM_RESPONSE_SIGNATURE = _public_signature(
    (
        ("db_path", str, _REQUIRED),
        ("owner", str, _REQUIRED),
        ("execution_id", str, _REQUIRED),
        ("agent_slug", str, _REQUIRED),
        ("arguments", dict[str, Any], _REQUIRED),
        ("transport", str, _REQUIRED),
        ("call", StreamResponseCall, _REQUIRED),
        ("run_id", str | None, None),
        ("allow_terminal_replay", bool, False),
    )
)
_PUBLIC_OPERATION_SIGNATURE = _public_signature(
    (
        ("db_path", str, _REQUIRED),
        ("owner", str, _REQUIRED),
        ("execution_id", str, _REQUIRED),
        ("agent_slug", str, _REQUIRED),
        ("operation", str, _REQUIRED),
        ("action_id", str, _REQUIRED),
        ("expected_revision", int, _REQUIRED),
        ("arguments", dict[str, Any], _REQUIRED),
        ("transport", str, _REQUIRED),
        ("call", BusinessCall, _REQUIRED),
        ("status_mapper", StatusMapper | None, None),
        ("expected_provider_join_lease_token", str | None, None),
    )
)


@dataclass(frozen=True, slots=True)
class _InvocationTarget:
    """Shared owner-scoped Runtime target and public command."""

    db_path: str
    owner: str
    execution_id: str
    agent_slug: str
    arguments: dict[str, Any]
    transport: str


@dataclass(frozen=True, slots=True)
class _InvocationFingerprint:
    """Admission fingerprint and optional fixed run identity."""

    version: int = 1
    value: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class _AgentInvocation:
    """Normalized inputs for a start invocation."""

    target: _InvocationTarget
    call: BusinessCall
    status_mapper: StatusMapper | None = None
    public_result_mapper: PublicResultMapper | None = None
    admit_new_user_turn: bool = False
    fingerprint: _InvocationFingerprint = _InvocationFingerprint()


@dataclass(frozen=True, slots=True)
class _StreamInvocation:
    """Normalized inputs for a streamed start invocation."""

    target: _InvocationTarget
    call: StreamResponseCall
    run_id: str | None = None
    allow_terminal_replay: bool = False


@dataclass(frozen=True, slots=True)
class _OperationInvocation:
    """Normalized inputs for a non-start Runtime operation."""

    target: _InvocationTarget
    operation: str
    action_id: str
    expected_revision: int
    call: BusinessCall
    status_mapper: StatusMapper | None = None
    expected_provider_join_lease_token: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalReservationIdentity(CanonicalReservationFields):
    """Trusted detached-command identity kept outside business arguments."""

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
        bound.owner,
        bound.execution_id,
        bound.fingerprint_version,
        bound.fingerprint,
        bound.agent_slug,
        bound.command_hash,
    ) != (
        owner,
        execution_id,
        outer.fingerprint_version,
        outer.fingerprint,
        command.agent_slug,
        execution_command_hash(command),
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
    **kwargs: Unpack[InvokePublicAgentKwargs],
) -> Any:
    """Run one existing business call through Runtime without reshaping it."""
    _validate_keyword_call(_PUBLIC_AGENT_SIGNATURE, kwargs)
    return await _invoke_public_agent(_agent_invocation(kwargs))


def _validate_keyword_call(
    signature: Signature,
    kwargs: Mapping[str, Any],
) -> None:
    signature.bind(**dict(kwargs))


def _invocation_target(kwargs: InvocationTargetKwargs) -> _InvocationTarget:
    return _InvocationTarget(
        db_path=kwargs["db_path"],
        owner=kwargs["owner"],
        execution_id=kwargs["execution_id"],
        agent_slug=kwargs["agent_slug"],
        arguments=kwargs["arguments"],
        transport=kwargs["transport"],
    )


def _agent_invocation(kwargs: InvokePublicAgentKwargs) -> _AgentInvocation:
    return _AgentInvocation(
        target=_invocation_target(kwargs),
        call=kwargs["call"],
        status_mapper=kwargs.get("status_mapper"),
        public_result_mapper=kwargs.get("public_result_mapper"),
        admit_new_user_turn=kwargs.get("admit_new_user_turn", False),
        fingerprint=_InvocationFingerprint(
            version=kwargs.get("fingerprint_version", 1),
            value=kwargs.get("fingerprint"),
            run_id=kwargs.get("run_id"),
        ),
    )


def _run_id_factory(run_id: str | None) -> Callable[[], str] | None:
    if run_id is None:
        return None
    return lambda: run_id


async def _invoke_public_agent(request: _AgentInvocation) -> Any:
    spec = public_agent_spec(request.target.agent_slug)
    if spec is None:
        raise ExecutionRuntimeError("unknown_public_agent")
    if _ACTIVE_EXECUTION.get() and not request.admit_new_user_turn:
        return await instrument_nested_agent_invocation(
            request.target.agent_slug, request.call
        )
    handler = _make_start_handler(request)
    driver_type = CANONICAL_DRIVER_TYPES[spec.driver]
    driver = driver_type({DriverOperation.START: handler})
    reservations = SQLiteExecutionReservationRepository(
        request.target.db_path,
        run_id_factory=_run_id_factory(request.fingerprint.run_id),
    )
    runtime = _build_runtime(
        request.target.db_path,
        spec.driver,
        driver,
        reservations,
    )
    command = ExecutionCommand(
        agent_slug=request.target.agent_slug,
        arguments=request.target.arguments,
    )
    fingerprint = request.fingerprint.value or _fingerprint(command)
    outcome = await runtime.start(
        owner=request.target.owner,
        execution_id=request.target.execution_id,
        fingerprint_version=request.fingerprint.version,
        fingerprint=fingerprint,
        command=command,
        reservation_command=_reservation_command(
            request,
            command,
            fingerprint=fingerprint,
        ),
        transport=request.target.transport,
    )
    return _private_value(outcome)


def _make_start_handler(request: _AgentInvocation) -> Any:
    async def start_handler(
        context: Any, command: Any, services: Any
    ) -> DriverOutcome:
        del context, command, services
        captured = await _capture_active_call(request.call)
        if captured.error is not None:
            return _failed_private_outcome(captured.error)
        value = captured.value
        status = (
            request.status_mapper(value)
            if request.status_mapper is not None
            else _default_status(value)
        )
        projected_value = (
            request.public_result_mapper(value)
            if request.public_result_mapper is not None
            else value
        )
        return DriverOutcome(
            status=status,
            result=_public_result_with_private(
                projected_value,
                private_value=value,
                agent_slug=request.target.agent_slug,
            ),
            tracking_health=_default_tracking_health(value),
        )

    return start_handler


async def _capture_active_call(call: BusinessCall) -> _PrivateInvocation:
    token = _ACTIVE_EXECUTION.set(True)
    try:
        try:
            value = await call()
        finally:
            _ACTIVE_EXECUTION.reset(token)
    except asyncio.CancelledError:
        raise
    except _BUSINESS_FAILURES as exc:
        return _PrivateInvocation(error=exc)
    return _PrivateInvocation(value=value)


def _reservation_command(
    request: _AgentInvocation,
    command: ExecutionCommand,
    *,
    fingerprint: str,
) -> ExecutionCommand:
    identity = _CANONICAL_RESERVATION_IDENTITY.get()
    if identity is None:
        return command
    actual = (
        identity.owner,
        identity.execution_id,
        identity.fingerprint_version,
        identity.fingerprint,
        identity.command.agent_slug,
    )
    expected = (
        request.target.owner,
        request.target.execution_id,
        request.fingerprint.version,
        fingerprint,
        request.target.agent_slug,
    )
    if actual != expected:
        raise ExecutionRuntimeError("execution_identity_conflict")
    return identity.command


def _build_runtime(
    db_path: str,
    driver_name: Driver,
    driver: ExecutionDriver,
    reservations: SQLiteExecutionReservationRepository,
    expected_provider_join_lease_token: str | None = None,
) -> ExecutionRuntime:
    return ExecutionRuntime(
        reservations=reservations,
        journal=SQLiteExecutionJournal(
            db_path,
            expected_provider_join_lease_token=(
                expected_provider_join_lease_token
            ),
        ),
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={driver_name: driver},
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


async def invoke_public_agent_stream_response(
    **kwargs: Unpack[StreamResponseKwargs],
) -> Any:
    """Bind a streaming response to one Runtime lifecycle.

    Response construction runs after reservation. Full iterator exhaustion
    settles success, a real iterator error settles failure, and consumer
    closure deliberately leaves the execution running for durable recovery.
    """
    _validate_keyword_call(_STREAM_RESPONSE_SIGNATURE, kwargs)
    request = _StreamInvocation(
        target=_invocation_target(kwargs),
        call=kwargs["call"],
        run_id=kwargs.get("run_id"),
        allow_terminal_replay=kwargs.get("allow_terminal_replay", False),
    )
    return await _invoke_public_agent_stream_response(request)


async def _invoke_public_agent_stream_response(
    request: _StreamInvocation,
) -> Any:
    if _ACTIVE_EXECUTION.get():
        raise ExecutionRuntimeError("nested_stream_response_unsupported")
    spec = public_agent_spec(request.target.agent_slug)
    if spec is None:
        raise ExecutionRuntimeError("unknown_public_agent")
    reservations = SQLiteExecutionReservationRepository(
        request.target.db_path,
        run_id_factory=_run_id_factory(request.run_id),
    )
    lifecycle = _StreamLifecycle(request, reservations)
    driver_type = CANONICAL_DRIVER_TYPES[spec.driver]
    driver = driver_type(lifecycle.handlers())
    runtime = _build_runtime(
        request.target.db_path,
        spec.driver,
        driver,
        reservations,
    )
    lifecycle.attach_runtime(runtime)
    command = ExecutionCommand(
        agent_slug=request.target.agent_slug,
        arguments=request.target.arguments,
    )
    outcome = await runtime.start(
        owner=request.target.owner,
        execution_id=request.target.execution_id,
        fingerprint_version=1,
        fingerprint=_fingerprint(command),
        command=command,
        transport=request.target.transport,
    )
    return await lifecycle.bind_response(outcome)


@dataclass(slots=True)
class _StreamLifecycle:
    """Own the state and settlement rules for one streaming response."""

    request: _StreamInvocation
    reservations: SQLiteExecutionReservationRepository
    boundaries: list[tuple[Any, Any]] = field(default_factory=list)
    responses: list[Any] = field(default_factory=list)
    runtime: ExecutionRuntime | None = field(init=False, default=None)
    terminal_settled: bool = field(init=False, default=False)

    def handlers(self) -> dict[DriverOperation, Any]:
        """Return the two canonical driver handlers for this stream."""
        return {
            DriverOperation.START: self._start_handler,
            DriverOperation.RECONCILE: self._reconcile_handler,
        }

    def attach_runtime(self, runtime: ExecutionRuntime) -> None:
        """Attach the Runtime after its driver has captured this lifecycle."""
        self.runtime = runtime

    async def _start_handler(
        self,
        context: Any,
        command: Any,
        services: Any,
    ) -> DriverOutcome:
        del command
        if context.run_id is None:
            raise ExecutionRuntimeError("execution_run_id_required")
        self.boundaries.append((context, services))
        captured = await _capture_active_call(
            lambda: self.request.call(context.run_id)
        )
        if captured.error is not None:
            return _failed_private_outcome(captured.error)
        self.responses.append(captured.value)
        return DriverOutcome.running(
            result=TransportNeutralResult(
                private_value=_PrivateInvocation(value=captured.value)
            )
        )

    async def _reconcile_handler(
        self,
        context: Any,
        command: ExecutionCommand,
        services: Any,
    ) -> DriverOutcome:
        del context, services
        status = str(command.arguments.get("terminal_status", "failed"))
        projected_result = self._projected_result()
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
                code="stream_execution_failed",
                retryable=False,
            ),
        )

    def _projected_result(self) -> TransportNeutralResult:
        if len(self.responses) != 1:
            return TransportNeutralResult()
        result_reader = getattr(
            self.responses[0],
            "runtime_terminal_result",
            None,
        )
        if not callable(result_reader):
            return TransportNeutralResult()
        return _public_result_with_private(
            result_reader(),
            private_value=None,
            agent_slug=self.request.target.agent_slug,
        )

    async def bind_response(self, outcome: DriverOutcome) -> Any:
        """Restore replay or attach the durable iterator wrapper."""
        try:
            response = _private_value(outcome)
        except ExecutionRuntimeError:
            if not (self.request.allow_terminal_replay and outcome.terminal):
                raise
            existing = self.reservations.get(
                owner=self.request.target.owner,
                execution_id=self.request.target.execution_id,
            )
            return await self.request.call(existing.run_id)
        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None or not hasattr(body_iterator, "__aiter__"):
            raise ExecutionRuntimeError("stream_response_iterator_required")
        response.body_iterator = self._wrapped(response, body_iterator)
        return response

    async def _settle(self, status: str) -> None:
        record = self.reservations.get(
            owner=self.request.target.owner,
            execution_id=self.request.target.execution_id,
        )
        await self._runtime_required().reconcile(
            owner=self.request.target.owner,
            execution_id=self.request.target.execution_id,
            command=ExecutionCommand(
                agent_slug=self.request.target.agent_slug,
                arguments={"terminal_status": status},
                action_id=f"stream-terminal:{status}",
                expected_revision=record.supervisor_revision,
            ),
            transport=self.request.target.transport,
        )

    async def _settle_if_ready(self, response: Any) -> None:
        if self.terminal_settled:
            return
        ready, status = _response_terminal_status(
            response,
            completed=False,
        )
        if not ready:
            return
        await self._settle(status)
        self.terminal_settled = True

    async def _wrapped(
        self,
        response: Any,
        body_iterator: Any,
    ) -> AsyncIterator[Any]:
        iterator = aiter(body_iterator)
        completed = False
        context, services = self._stream_boundary()
        try:
            while True:
                try:
                    chunk = await self._next_chunk(
                        iterator,
                        context,
                        services,
                    )
                except StopAsyncIteration:
                    completed = True
                    break
                await self._settle_if_ready(response)
                yield chunk
        except _STREAM_FAILURES:
            await self._settle("failed")
            self.terminal_settled = True
            raise
        finally:
            await close_async_iterator(iterator)
            ready, status = _response_terminal_status(
                response,
                completed=completed,
            )
            if ready and not self.terminal_settled:
                await self._settle(status)

    @staticmethod
    async def _next_chunk(
        iterator: Any,
        context: Any,
        services: Any,
    ) -> Any:
        with bind_execution_boundary(context, services):
            token = _ACTIVE_EXECUTION.set(True)
            try:
                return await anext(iterator)
            finally:
                _ACTIVE_EXECUTION.reset(token)

    def _stream_boundary(self) -> tuple[Any, Any]:
        if len(self.boundaries) != 1:
            raise ExecutionRuntimeError("stream_execution_boundary_required")
        return self.boundaries[0]

    def _runtime_required(self) -> ExecutionRuntime:
        if self.runtime is None:
            raise ExecutionRuntimeError("stream_execution_runtime_required")
        return self.runtime


def _response_terminal_status(
    response: Any,
    *,
    completed: bool,
) -> tuple[bool, str]:
    ready_reader = getattr(response, "runtime_terminal_ready", None)
    ready = completed or (callable(ready_reader) and bool(ready_reader()))
    if not ready:
        return False, ""
    status_reader = getattr(response, "runtime_terminal_status", None)
    terminal_status = (
        status_reader() if callable(status_reader) else "succeeded"
    )
    return True, str(terminal_status)


async def invoke_public_agent_operation(
    **kwargs: Unpack[InvokePublicAgentOperationKwargs],
) -> Any:
    """Route a resume/cancel/recovery operation through the same Runtime."""
    _validate_keyword_call(_PUBLIC_OPERATION_SIGNATURE, kwargs)
    request = _OperationInvocation(
        target=_invocation_target(kwargs),
        operation=kwargs["operation"],
        action_id=kwargs["action_id"],
        expected_revision=kwargs["expected_revision"],
        call=kwargs["call"],
        status_mapper=kwargs.get("status_mapper"),
        expected_provider_join_lease_token=kwargs.get(
            "expected_provider_join_lease_token"
        ),
    )
    return await _invoke_public_agent_operation(request)


async def _invoke_public_agent_operation(
    request: _OperationInvocation,
) -> Any:
    driver_operation = _driver_operation(request.operation)
    spec = public_agent_spec(request.target.agent_slug)
    if spec is None:
        raise ExecutionRuntimeError("unknown_public_agent")
    handler = _make_operation_handler(request)
    driver_type = CANONICAL_DRIVER_TYPES[spec.driver]
    driver = driver_type({driver_operation: handler})
    reservations = SQLiteExecutionReservationRepository(
        request.target.db_path,
        expected_provider_join_lease_token=(
            request.expected_provider_join_lease_token
        ),
    )
    runtime = _build_runtime(
        request.target.db_path,
        spec.driver,
        driver,
        reservations,
        request.expected_provider_join_lease_token,
    )
    command = ExecutionCommand(
        agent_slug=request.target.agent_slug,
        arguments=request.target.arguments,
        action_id=request.action_id,
        expected_revision=request.expected_revision,
    )
    runtime_call = getattr(runtime, driver_operation.value)
    outcome = await runtime_call(
        owner=request.target.owner,
        execution_id=request.target.execution_id,
        command=command,
        transport=request.target.transport,
    )
    return _private_value(outcome)


def _driver_operation(operation: str) -> DriverOperation:
    try:
        driver_operation = DriverOperation(operation)
    except ValueError as exc:
        raise ExecutionRuntimeError("unsupported_runtime_operation") from exc
    if driver_operation is DriverOperation.START:
        raise ExecutionRuntimeError("unsupported_runtime_operation")
    return driver_operation


def _make_operation_handler(request: _OperationInvocation) -> Any:
    async def operation_handler(
        context: Any,
        command: Any,
        services: Any,
    ) -> DriverOutcome:
        del context, command, services
        captured = await _capture_active_call(request.call)
        if captured.error is not None:
            return _failed_private_outcome(captured.error)
        value = captured.value
        status = (
            request.status_mapper(value)
            if request.status_mapper is not None
            else _default_status(value)
        )
        result = _public_result_with_private(
            value,
            agent_slug=request.target.agent_slug,
        )
        if status is ExecutionStatus.SUCCEEDED:
            return DriverOutcome.succeeded(result)
        if status is ExecutionStatus.FAILED:
            return DriverOutcome(
                status=status,
                failure=DriverFailure(
                    code="business_execution_failed",
                    retryable=False,
                ),
                result=result,
            )
        return DriverOutcome(status=status, result=result)

    return operation_handler


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


def _fingerprint(command: ExecutionCommand) -> str:
    try:
        payload = _canonical_start_command(command).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ExecutionRuntimeError("command_not_canonical") from exc
    return hashlib.sha256(payload).hexdigest()


def _canonical_start_command(command: ExecutionCommand) -> str:
    return json.dumps(
        {"agent_slug": command.agent_slug, "arguments": command.arguments},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _install_public_signatures() -> None:
    for function, signature in (
        (invoke_public_agent, _PUBLIC_AGENT_SIGNATURE),
        (invoke_public_agent_stream_response, _STREAM_RESPONSE_SIGNATURE),
        (invoke_public_agent_operation, _PUBLIC_OPERATION_SIGNATURE),
    ):
        setattr(function, "__signature__", signature)


_install_public_signatures()
