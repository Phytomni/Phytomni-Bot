# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Service admission and execution-keyed V2 transport routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...public_agent_catalog import public_agent_spec
from ...runtime.execution_drivers_v2 import CANONICAL_DRIVER_TYPES
from ...runtime.execution_event_flags import execution_log_artifact_enabled
from ...runtime.execution_journal_store_v2 import (
    ExecutionJournalNotFoundError,
    SQLiteExecutionJournal,
)
from ...runtime.execution_log_artifact_v2 import (
    SQLiteExecutionLogArtifactStore,
)
from ...runtime.execution_reservation_support_v2 import (
    TERMINAL_EXECUTION_STATUSES,
)
from ...runtime.execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    ExecutionReservationConflictError,
    ExecutionReservationNotFoundError,
    SQLiteExecutionReservationRepository,
)
from ...runtime.execution_runtime_contracts import (
    CancellationOutcome,
    DriverOperation,
    DriverOutcome,
    ExecutionCommand,
    ExecutionContext,
    ExecutionServices,
)
from ...runtime.execution_runtime_v2 import ExecutionRuntime
from ...runtime.execution_target_store_v2 import SQLiteExecutionTargetStore
from ...runtime.execution_work_store_v2 import SQLiteExecutionWorkRepository
from ...storage.downloads import download_obs_file
from ..lifecycle_contract import SafeApiError
from .execution_v2_read_routes import register_execution_v2_read_routes

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_MAX_COMMAND_BYTES = 262_144
EXECUTION_V2_HEARTBEAT_POLL_TICKS = 60


class ExecutionAdmissionV2(BaseModel):
    """Finite service-to-service admission command."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=16)
    owner_ref: str = Field(min_length=1, max_length=256)
    execution_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    fingerprint_version: int = Field(ge=1, le=16)
    fingerprint: str = Field(min_length=32, max_length=256)
    agent_slug: str = Field(
        min_length=1,
        max_length=64,
        pattern=_IDENTIFIER_PATTERN,
    )
    arguments: dict[str, Any]

    @model_validator(mode="after")
    def validate_private_command_size(self) -> ExecutionAdmissionV2:
        """Reject non-canonical or oversized private command arguments."""
        try:
            serialized = json.dumps(
                self.arguments,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            encoded = serialized.encode("utf-8")
        except (TypeError, UnicodeEncodeError) as exc:
            raise ValueError("arguments must be canonical JSON") from exc
        if len(encoded) > _MAX_COMMAND_BYTES:
            raise ValueError("arguments exceed size limit")
        return self


class ExecutionActionV2(BaseModel):
    """Revision-checked input action for one resumable execution."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    surface_id: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    widget: Literal["confirm", "form", "choice"]
    payload: dict[str, Any]


class ExecutionCancellationV2(BaseModel):
    """Revision-checked explicit cancellation request."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    reason: Literal["user_requested", "operator_requested", "deadline"]


@dataclass(frozen=True, slots=True)
class ExecutionV2RouteDependencies:
    """Injected auth and storage seams for internal execution routes."""

    require_service: Callable[..., Any]
    tasks_db_path: Callable[[], str]
    resume_execution: Callable[..., Awaitable[Any]] | None = None


@dataclass(frozen=True, slots=True)
class _ExecutionV2RouteContext:
    """Bound route helpers shared by read and control registrations."""

    dependencies: ExecutionV2RouteDependencies

    @property
    def require_service(self) -> Callable[..., Any]:
        """Return the configured service-auth dependency."""
        return self.dependencies.require_service

    def tasks_db_path(self) -> str:
        """Return the configured execution database path."""
        return self.dependencies.tasks_db_path()

    def owner_header(
        self,
        x_phyto_owner: str = Header(alias="X-Phyto-Owner"),
        x_phyto_execution_schema: str | None = Header(
            default=None,
            alias="X-Phyto-Execution-Schema",
        ),
    ) -> str:
        """Validate the service-asserted owner and execution schema."""
        if x_phyto_execution_schema not in {None, "2"}:
            raise _unsupported_contract()
        owner = x_phyto_owner.strip()
        if not owner or len(owner) > 256:
            raise _safe_error(
                422,
                "invalid_owner",
                "request validation failed",
                "request_validation",
            )
        return owner

    def reservation(self, owner: str, execution_id: str) -> Any:
        """Return one owner-scoped reservation or a bounded 404."""
        try:
            return SQLiteExecutionReservationRepository(
                self.dependencies.tasks_db_path()
            ).get(owner=owner, execution_id=execution_id)
        except ExecutionReservationNotFoundError as exc:
            raise _not_found() from exc

    def journal(self, owner: str, execution_id: str) -> SQLiteExecutionJournal:
        """Return a journal only when its execution projection exists."""
        store = SQLiteExecutionJournal(self.dependencies.tasks_db_path())
        try:
            store.get_projection(execution_id, owner=owner)
        except ExecutionJournalNotFoundError as exc:
            raise _not_found() from exc
        return store

    async def sleep(self, seconds: float) -> None:
        """Sleep through the route module's patchable scheduler seam."""
        await asyncio.sleep(seconds)

    async def download_target(
        self,
        delivery_ref: str,
        server_dir: str,
    ) -> str:
        """Materialize a target through the patchable OBS seam."""
        return await download_obs_file(delivery_ref, server_dir)

    @staticmethod
    def heartbeat_poll_ticks() -> int:
        """Read the patchable stream heartbeat interval."""
        return EXECUTION_V2_HEARTBEAT_POLL_TICKS


def _register_admission_route(
    app: FastAPI,
    dependencies: ExecutionV2RouteDependencies,
) -> None:
    """Register service-only admission without running Agent work inline."""

    @app.post("/v2/executions", status_code=202)
    async def admit_execution(
        body: ExecutionAdmissionV2,
        _service: None = Depends(dependencies.require_service),
    ) -> JSONResponse:
        del _service
        if body.schema_version != 2:
            raise _unsupported_contract()
        spec = public_agent_spec(body.agent_slug)
        if spec is None and body.agent_slug != EXPERT_ROUTER_AGENT_SLUG:
            raise SafeApiError(
                status_code=409,
                code="execution_contract_unsupported",
                message="execution contract unsupported",
                stage="execution_admission",
                retryable=False,
            )
        repository = SQLiteExecutionReservationRepository(
            dependencies.tasks_db_path()
        )
        try:
            repository.get(
                owner=body.owner_ref,
                execution_id=body.execution_id,
            )
            replay = True
        except ExecutionReservationNotFoundError:
            replay = False
        command = ExecutionCommand(
            agent_slug=body.agent_slug,
            arguments=body.arguments,
        )
        try:
            record = repository.reserve(
                owner=body.owner_ref,
                execution_id=body.execution_id,
                fingerprint_version=body.fingerprint_version,
                fingerprint=body.fingerprint,
                command=command,
                durable_command={
                    "agent": body.agent_slug,
                    "arguments": body.arguments,
                    "execution_id": body.execution_id,
                    "owner_ref": body.owner_ref,
                    "fingerprint_version": body.fingerprint_version,
                    "fingerprint": body.fingerprint,
                },
            )
        except ExecutionReservationConflictError as exc:
            raise SafeApiError(
                status_code=409,
                code="execution_identity_conflict",
                message="execution identity conflict",
                stage="execution_admission",
                retryable=False,
            ) from exc
        return JSONResponse(
            status_code=202,
            content={
                "schema_version": 2,
                "execution_id": record.execution_id,
                "run_id": record.run_id,
                "agent_slug": record.agent_slug,
                "status": record.status.value,
                "event_cursor": 0,
                "supervisor_revision": record.supervisor_revision,
                "idempotent_replay": replay,
            },
        )


def _register_action_route(
    app: FastAPI,
    dependencies: ExecutionV2RouteDependencies,
    context: _ExecutionV2RouteContext,
) -> None:
    """Register revision-checked execution input actions."""

    @app.post("/v2/executions/{execution_id}/actions")
    async def post_execution_action(
        execution_id: str,
        body: ExecutionActionV2,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(context.owner_header),
    ) -> JSONResponse:
        del _service
        record = context.reservation(owner, execution_id)
        spec = public_agent_spec(record.agent_slug)
        if (
            spec is None
            or spec.resume == "none"
            or dependencies.resume_execution is None
        ):
            raise _safe_error(
                409,
                "action_unsupported",
                "execution action unsupported",
                "execution_action",
            )
        if record.supervisor_revision != body.expected_revision:
            raise _revision_conflict()
        if record.status in TERMINAL_EXECUTION_STATUSES:
            raise _terminal_conflict("execution_action")
        projection = context.journal(owner, execution_id).get_projection(
            execution_id,
            owner=owner,
        )
        required_input = projection.input_required
        if (
            required_input is None
            or required_input.surface_id != body.surface_id
            or required_input.widget != body.widget
            or required_input.action_revision != body.expected_revision
        ):
            raise _safe_error(
                409,
                "run_state_conflict",
                "execution is not waiting for this input",
                "execution_action",
            )
        await dependencies.resume_execution(
            owner=owner,
            record=record,
            action=body,
        )
        updated = context.reservation(owner, execution_id)
        projection = context.journal(owner, execution_id).get_projection(
            execution_id, owner=owner
        )
        return JSONResponse(
            status_code=202,
            content={
                "schema_version": 2,
                "execution_id": execution_id,
                "operation_id": body.action_id,
                "status": projection.status.value,
                "supervisor_revision": updated.supervisor_revision,
            },
        )


def _register_cancel_route(
    app: FastAPI,
    dependencies: ExecutionV2RouteDependencies,
    context: _ExecutionV2RouteContext,
) -> None:
    """Register explicit execution cancellation."""

    @app.post("/v2/executions/{execution_id}/cancel")
    async def cancel_execution(
        execution_id: str,
        body: ExecutionCancellationV2,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(context.owner_header),
    ) -> JSONResponse:
        del _service
        record = context.reservation(owner, execution_id)
        if record.supervisor_revision != body.expected_revision:
            raise _revision_conflict()
        projection = context.journal(owner, execution_id).get_projection(
            execution_id,
            owner=owner,
        )
        if projection.terminal is not None:
            raise _terminal_conflict("execution_cancel")
        spec = public_agent_spec(record.agent_slug)
        if spec is None:
            raise _not_found()

        async def cancellation_handler(
            driver_context: ExecutionContext,
            command: ExecutionCommand,
            services: ExecutionServices,
        ) -> DriverOutcome:
            del driver_context, command, services
            outcome: CancellationOutcome = (
                "best_effort"
                if spec.cancellation in {"cooperative", "best_effort"}
                else "unsupported"
            )
            return DriverOutcome.running(cancellation_outcome=outcome)

        driver_type = CANONICAL_DRIVER_TYPES[spec.driver]
        runtime = ExecutionRuntime(
            reservations=SQLiteExecutionReservationRepository(
                dependencies.tasks_db_path()
            ),
            journal=SQLiteExecutionJournal(dependencies.tasks_db_path()),
            work=SQLiteExecutionWorkRepository(dependencies.tasks_db_path()),
            drivers={
                spec.driver: driver_type(
                    {DriverOperation.CANCEL: cancellation_handler}
                )
            },
            target_store=SQLiteExecutionTargetStore(
                dependencies.tasks_db_path()
            ),
            execution_log_store=(
                SQLiteExecutionLogArtifactStore(dependencies.tasks_db_path())
                if execution_log_artifact_enabled()
                else None
            ),
        )
        try:
            outcome = await runtime.cancel(
                owner=owner,
                execution_id=execution_id,
                command=ExecutionCommand(
                    agent_slug=record.agent_slug,
                    arguments={"reason": body.reason},
                    action_id=body.request_id,
                    expected_revision=body.expected_revision,
                ),
                transport="service_api",
            )
        except ExecutionReservationConflictError as exc:
            raise _revision_conflict() from exc
        updated = context.reservation(owner, execution_id)
        return JSONResponse(
            status_code=202,
            content={
                "schema_version": 2,
                "execution_id": execution_id,
                "request_id": body.request_id,
                "status": outcome.status.value,
                "cancellation_outcome": outcome.cancellation_outcome,
                "supervisor_revision": updated.supervisor_revision,
            },
        )


def register_execution_v2_routes(
    app: FastAPI,
    dependencies: ExecutionV2RouteDependencies,
) -> None:
    """Register execution V2 routes in their stable OpenAPI order."""
    context = _ExecutionV2RouteContext(dependencies)
    _register_admission_route(app, dependencies)
    register_execution_v2_read_routes(
        app,
        context,
    )
    _register_action_route(app, dependencies, context)
    _register_cancel_route(app, dependencies, context)


def _safe_error(
    status_code: int,
    code: str,
    message: str,
    stage: str,
    *,
    retryable: bool = False,
) -> SafeApiError:
    return SafeApiError(
        status_code=status_code,
        code=code,
        message=message,
        stage=stage,
        retryable=retryable,
    )


def _not_found() -> SafeApiError:
    return _safe_error(
        404, "not_found", "resource not found", "execution_read"
    )


def _revision_conflict() -> SafeApiError:
    return _safe_error(
        409,
        "execution_revision_conflict",
        "execution revision conflict",
        "execution_control",
    )


def _unsupported_contract() -> SafeApiError:
    return _safe_error(
        409,
        "execution_contract_unsupported",
        "execution contract unsupported",
        "execution_contract",
    )


def _terminal_conflict(stage: str) -> SafeApiError:
    return _safe_error(
        409,
        "execution_terminal_conflict",
        "execution is already terminal",
        stage,
    )


__all__ = [
    "ExecutionAdmissionV2",
    "ExecutionActionV2",
    "ExecutionCancellationV2",
    "ExecutionV2RouteDependencies",
    "register_execution_v2_routes",
]
