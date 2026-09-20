# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Service admission and execution-keyed V2 transport routes."""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path as FileSystemPath
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, Path, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.background import BackgroundTask

from ...public_agent_catalog import public_agent_spec
from ...runtime.execution_content_stream_v2 import (
    execution_content_stream_for_db,
)
from ...runtime.execution_drivers_v2 import CANONICAL_DRIVER_TYPES
from ...runtime.execution_event_flags import execution_log_artifact_enabled
from ...runtime.execution_event_limits import DEFAULT_EXECUTION_EVENT_LIMITS
from ...runtime.execution_journal_store_v2 import (
    ExecutionJournalNotFoundError,
    SQLiteExecutionJournal,
)
from ...runtime.execution_journal_v2 import (
    ExecutionContextStageV2,
    PublicTargetKind,
    PublicWarningV2,
    TrackingHealth,
)
from ...runtime.execution_log_artifact_v2 import (
    SQLiteExecutionLogArtifactStore,
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
from ...runtime.execution_trace_target_v2 import resolve_trace_target
from ...runtime.execution_work_store_v2 import (
    ExecutionWorkNotFoundError,
    SQLiteExecutionWorkRepository,
)
from ...runtime.sqlite import sqlite_transaction
from ...storage.downloads import download_obs_file
from ..lifecycle_contract import SafeApiError

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
        try:
            encoded = json.dumps(
                self.arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
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


def _project_reservation_diagnostics(
    projection: Any,
    *,
    record: Any,
    db_path: str,
) -> Any:
    """Overlay bounded pre-journal reconcile health on GET/SSE snapshots."""
    try:
        with sqlite_transaction(db_path) as connection:
            row = connection.execute(
                "SELECT state FROM execution_commands_v2 "
                "WHERE owner_ref = ? AND execution_id = ?",
                (record.owner, record.execution_id),
            ).fetchone()
    except sqlite3.Error:
        row = None
    reconcile_pending = row is not None and row[0] == "reconcile"
    if (
        record.tracking_health != TrackingHealth.DEGRADED.value
        and not reconcile_pending
    ):
        return projection
    warnings = projection.warnings
    if reconcile_pending:
        warning = PublicWarningV2(code="execution_reconciliation_pending")
        if warning not in warnings:
            warnings = (*warnings, warning)
    return projection.model_copy(
        update={
            "tracking_health": TrackingHealth.DEGRADED,
            "warnings": warnings,
        }
    )


def _project_provider_liveness(
    projection: Any,
    *,
    owner: str,
    execution_id: str,
    db_path: str,
    work_repository: SQLiteExecutionWorkRepository | None = None,
) -> Any:
    """Overlay sequence-free provider contact state on a public snapshot."""
    try:
        repository = work_repository or SQLiteExecutionWorkRepository(db_path)
        contact_at = repository.latest_provider_contact_at(
            execution_id,
            owner=owner,
        )
    except (OSError, sqlite3.Error, ExecutionWorkNotFoundError):
        return projection
    if contact_at is None:
        return projection
    clocks = projection.execution_stage.clocks
    current = clocks.last_provider_contact_at
    if current is not None and _parse_timestamp(current) >= _parse_timestamp(
        contact_at
    ):
        return projection
    stage = projection.execution_stage.model_copy(
        update={
            "clocks": clocks.observe_provider_contact(
                contact_at,
                semantic_changed=False,
            )
        }
    )
    return projection.model_copy(update={"execution_stage": stage})


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def register_execution_v2_routes(
    app: FastAPI,
    dependencies: ExecutionV2RouteDependencies,
) -> None:
    """Register service-only admission without running Agent work inline."""

    def owner_header(
        x_phyto_owner: str = Header(alias="X-Phyto-Owner"),
        x_phyto_execution_schema: str | None = Header(
            default=None,
            alias="X-Phyto-Execution-Schema",
        ),
    ) -> str:
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

    def reservation(owner: str, execution_id: str):
        try:
            return SQLiteExecutionReservationRepository(
                dependencies.tasks_db_path()
            ).get(owner=owner, execution_id=execution_id)
        except ExecutionReservationNotFoundError as exc:
            raise _not_found() from exc

    def journal(owner: str, execution_id: str) -> SQLiteExecutionJournal:
        store = SQLiteExecutionJournal(dependencies.tasks_db_path())
        try:
            store.get_projection(execution_id, owner=owner)
        except ExecutionJournalNotFoundError as exc:
            raise _not_found() from exc
        return store

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

    @app.get("/v2/executions/{execution_id}")
    async def get_execution_snapshot(
        execution_id: str,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
    ) -> JSONResponse:
        del _service
        record = reservation(owner, execution_id)
        projection = journal(owner, execution_id).get_projection(
            execution_id, owner=owner
        )
        projection = _project_reservation_diagnostics(
            _project_provider_liveness(
                projection.model_copy(
                    update={
                        "run_id": record.run_id,
                        "operation_revision": record.supervisor_revision,
                        "agent_slug": record.agent_slug,
                        "context_stage": (
                            ExecutionContextStageV2.model_validate(
                                json.loads(record.context_stage_json)
                            )
                            if record.context_stage_json is not None
                            else None
                        ),
                    }
                ),
                owner=owner,
                execution_id=execution_id,
                db_path=dependencies.tasks_db_path(),
            ),
            record=record,
            db_path=dependencies.tasks_db_path(),
        )
        return JSONResponse(projection.model_dump(mode="json"))

    @app.get("/v2/executions/{execution_id}/events")
    async def get_execution_events(
        execution_id: str,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(
            default=DEFAULT_EXECUTION_EVENT_LIMITS.default_page_size,
            ge=1,
            le=DEFAULT_EXECUTION_EVENT_LIMITS.max_page_size,
        ),
    ) -> JSONResponse:
        del _service
        page = journal(owner, execution_id).list_events(
            execution_id,
            owner=owner,
            after_seq=after_seq,
            limit=limit,
        )
        assert page is not None
        return JSONResponse(
            {
                "schema_version": 2,
                "execution_id": execution_id,
                "items": [item.to_public_dict() for item in page.items],
                "next_after_seq": page.next_after_seq,
                "has_more": page.has_more,
                "gaps": [
                    {
                        "first_missing_seq": gap.first_missing_seq,
                        "last_missing_seq": gap.last_missing_seq,
                    }
                    for gap in page.gaps
                ],
            }
        )

    @app.get("/v2/executions/{execution_id}/events/stream")
    async def stream_execution_events(
        execution_id: str,
        request: Request,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
        after_seq: int | None = Query(default=None, ge=0),
        after_revision: int = Query(default=0, ge=0),
        after_offset: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(
            default=None,
            alias="Last-Event-ID",
        ),
    ) -> StreamingResponse:
        """Flush a snapshot, drain committed history, and follow commits."""
        del _service
        record = reservation(owner, execution_id)
        store = journal(owner, execution_id)
        work_repository = SQLiteExecutionWorkRepository(
            dependencies.tasks_db_path()
        )
        content_stream = execution_content_stream_for_db(
            dependencies.tasks_db_path()
        )
        cursor = _event_cursor(after_seq, last_event_id)
        initial_projection = _project_reservation_diagnostics(
            _project_provider_liveness(
                store.get_projection(execution_id, owner=owner).model_copy(
                    update={
                        "operation_revision": record.supervisor_revision,
                        "agent_slug": record.agent_slug,
                        "context_stage": (
                            ExecutionContextStageV2.model_validate(
                                json.loads(record.context_stage_json)
                            )
                            if record.context_stage_json is not None
                            else None
                        ),
                    }
                ),
                owner=owner,
                execution_id=execution_id,
                db_path=dependencies.tasks_db_path(),
                work_repository=work_repository,
            ),
            record=record,
            db_path=dependencies.tasks_db_path(),
        )

        async def follow():
            current = cursor
            content_revision = after_revision
            content_offset = after_offset
            delivered = 0
            idle_polls = 0
            stage_clocks = initial_projection.execution_stage.clocks
            delivered_contact_at = stage_clocks.last_provider_contact_at
            yield _sse_frame(
                event="execution_snapshot",
                data=initial_projection.model_dump(mode="json"),
            )
            while True:
                try:
                    page = store.list_events(
                        execution_id,
                        owner=owner,
                        after_seq=current,
                        limit=DEFAULT_EXECUTION_EVENT_LIMITS.max_page_size,
                    )
                except (OSError, sqlite3.Error):
                    yield _sse_frame(
                        event="execution_tracking",
                        data={
                            "schema_version": 2,
                            "execution_id": execution_id,
                            "tracking_health": "degraded",
                            "reason": "journal_unavailable",
                        },
                    )
                    return
                if page is None:
                    yield _sse_frame(
                        event="execution_tracking",
                        data={
                            "schema_version": 2,
                            "execution_id": execution_id,
                            "tracking_health": "degraded",
                            "reason": "journal_record_missing",
                        },
                    )
                    return
                for gap in page.gaps:
                    yield _sse_frame(
                        event="execution_gap",
                        data={
                            "schema_version": 2,
                            "execution_id": execution_id,
                            "first_missing_seq": gap.first_missing_seq,
                            "last_missing_seq": gap.last_missing_seq,
                            "reason": "history_pruned_or_gap",
                        },
                    )
                if page.items:
                    idle_polls = 0
                    for item in page.items:
                        delivered += 1
                        if (
                            delivered
                            > DEFAULT_EXECUTION_EVENT_LIMITS.max_live_backlog
                        ):
                            yield _sse_frame(
                                event="execution_gap",
                                data={
                                    "schema_version": 2,
                                    "execution_id": execution_id,
                                    "after_seq": current,
                                    "next_available_seq": item.seq,
                                    "reason": "backlog_exceeded",
                                },
                            )
                            return
                        current = item.seq
                        yield _sse_frame(
                            event="execution_event",
                            data=item.to_public_dict(),
                            event_id=item.seq,
                        )
                    continue
                content_frames = content_stream.list_after(
                    owner=owner,
                    execution_id=execution_id,
                    output_revision=content_revision,
                    after_offset=content_offset,
                )
                if content_frames:
                    idle_polls = 0
                    for frame in content_frames:
                        content_revision = frame.output_revision
                        content_offset = frame.offset
                        yield _sse_frame(
                            event="execution_content",
                            data=frame.model_dump(mode="json"),
                        )
                    continue
                projection = _project_provider_liveness(
                    store.get_projection(execution_id, owner=owner),
                    owner=owner,
                    execution_id=execution_id,
                    db_path=dependencies.tasks_db_path(),
                    work_repository=work_repository,
                )
                provider_contact_at = (
                    projection.execution_stage.clocks.last_provider_contact_at
                )
                if provider_contact_at != delivered_contact_at:
                    delivered_contact_at = provider_contact_at
                    yield _sse_frame(
                        event="execution_snapshot",
                        data=projection.model_dump(mode="json"),
                    )
                if (
                    projection.terminal is not None
                    and current >= projection.latest_seq
                ) or await request.is_disconnected():
                    return
                idle_polls += 1
                if idle_polls >= EXECUTION_V2_HEARTBEAT_POLL_TICKS:
                    yield ": heartbeat\n\n"
                    idle_polls = 0
                await asyncio.sleep(0.25)

        return StreamingResponse(
            follow(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v2/executions/{execution_id}/events/{event_id}")
    async def get_execution_event_detail(
        execution_id: str,
        event_id: str,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
    ) -> JSONResponse:
        del _service
        event = journal(owner, execution_id).get_event(
            execution_id, event_id, owner=owner
        )
        if event is None:
            raise _not_found()
        return JSONResponse(event.to_public_dict())

    @app.get("/v2/executions/{execution_id}/operations/{operation_id}")
    async def get_execution_operation_detail(
        execution_id: str,
        operation_id: str = Path(
            min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
        ),
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
    ) -> JSONResponse:
        """Return one grouped operation after execution-owner authorization."""
        del _service
        projection = journal(owner, execution_id).get_projection(
            execution_id, owner=owner
        )
        operation = next(
            (
                item
                for item in projection.operations
                if item.operation_id == operation_id
            ),
            None,
        )
        if operation is None:
            raise _not_found()
        return JSONResponse(operation.model_dump(mode="json"))

    @app.get("/v2/executions/{execution_id}/targets/{kind}/{target_id}")
    async def get_execution_target(
        execution_id: str,
        kind: PublicTargetKind,
        target_id: str = Path(
            min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
        ),
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> JSONResponse:
        del _service
        projection = journal(owner, execution_id).get_projection(
            execution_id, owner=owner
        )
        target = next(
            (
                item
                for item in projection.targets
                if item.kind is kind and item.id == target_id
            ),
            None,
        )
        if target is None:
            raise _not_found()
        if kind is PublicTargetKind.TRACE:
            resolution = resolve_trace_target(
                journal(owner, execution_id),
                owner=owner,
                execution_id=execution_id,
                target_id=target_id,
                after_seq=after_seq,
                limit=limit,
            )
            if resolution is None:
                raise _not_found()
            return JSONResponse(
                resolution.model_dump(mode="json", exclude_none=True)
            )
        binding = SQLiteExecutionTargetStore(dependencies.tasks_db_path()).get(
            owner=owner,
            execution_id=execution_id,
            kind=kind.value,
            target_id=target_id,
        )
        response_body: dict[str, object] = {
            "schema_version": 2,
            "execution_id": execution_id,
            "target": target.model_dump(mode="json"),
            "resolution": "authorized",
            "delivery_available": binding is not None,
        }
        if binding is not None:
            # These immutable, bounded display fields are already public result
            # facts. Keep the private delivery_ref and semantic role
            # server-side.
            response_body.update(
                {
                    "name": binding.name,
                    "media_type": binding.media_type,
                    "size_bytes": binding.size_bytes,
                }
            )
        return JSONResponse(response_body)

    @app.get(
        "/v2/executions/{execution_id}/targets/{kind}/{target_id}/content"
    )
    async def get_execution_target_content(
        execution_id: str,
        kind: PublicTargetKind,
        target_id: str = Path(
            min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
        ),
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
    ) -> Response:
        """Materialize one private binding after owner reauthorization."""
        del _service
        projection = journal(owner, execution_id).get_projection(
            execution_id, owner=owner
        )
        if not any(
            item.kind is kind and item.id == target_id
            for item in projection.targets
        ):
            raise _not_found()
        binding = SQLiteExecutionTargetStore(dependencies.tasks_db_path()).get(
            owner=owner,
            execution_id=execution_id,
            kind=kind.value,
            target_id=target_id,
        )
        if binding is None:
            raise _not_found()
        if binding.role == "execution_log":
            if binding.delivery_ref != f"execution-log:{target_id}":
                raise _not_found()
            content = SQLiteExecutionLogArtifactStore(
                dependencies.tasks_db_path()
            ).get(
                owner=owner,
                execution_id=execution_id,
                target_id=target_id,
            )
            if content is None:
                raise _not_found()
            return Response(
                content=content,
                media_type="application/json",
                headers={
                    "Content-Disposition": (
                        'attachment; filename="execution-log.json"'
                    )
                },
            )
        temp_dir = tempfile.mkdtemp(prefix="phytomni-target-")
        try:
            local_path = await download_obs_file(
                binding.delivery_ref,
                temp_dir,
            )
            materialized = _validated_delivery_path(temp_dir, local_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise _safe_error(
                503,
                "target_delivery_unavailable",
                "target delivery unavailable",
                "execution_target",
                retryable=True,
            ) from exc
        return FileResponse(
            materialized,
            media_type=binding.media_type,
            filename=binding.name,
            background=BackgroundTask(
                shutil.rmtree,
                temp_dir,
                ignore_errors=True,
            ),
        )

    @app.post("/v2/executions/{execution_id}/actions")
    async def post_execution_action(
        execution_id: str,
        body: ExecutionActionV2,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
    ) -> JSONResponse:
        del _service
        record = reservation(owner, execution_id)
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
        if record.status.value in {
            "succeeded",
            "partial",
            "failed",
            "cancelled",
            "timed_out",
        }:
            raise _safe_error(
                409,
                "execution_terminal_conflict",
                "execution is already terminal",
                "execution_action",
            )
        projection = journal(owner, execution_id).get_projection(
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
        updated = reservation(owner, execution_id)
        projection = journal(owner, execution_id).get_projection(
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

    @app.post("/v2/executions/{execution_id}/cancel")
    async def cancel_execution(
        execution_id: str,
        body: ExecutionCancellationV2,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(owner_header),
    ) -> JSONResponse:
        del _service
        record = reservation(owner, execution_id)
        if record.supervisor_revision != body.expected_revision:
            raise _revision_conflict()
        projection = journal(owner, execution_id).get_projection(
            execution_id,
            owner=owner,
        )
        if projection.terminal is not None:
            raise _terminal_conflict("execution_cancel")
        spec = public_agent_spec(record.agent_slug)
        if spec is None:
            raise _not_found()

        async def cancellation_handler(
            context: ExecutionContext,
            command: ExecutionCommand,
            services: ExecutionServices,
        ) -> DriverOutcome:
            del context, command, services
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
        updated = reservation(owner, execution_id)
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


def _validated_delivery_path(
    delivery_root: str,
    materialized_path: str,
) -> FileSystemPath:
    """Resolve and confine a downloaded target to its one-shot directory."""
    resolved_root = FileSystemPath(delivery_root).resolve()
    resolved_path = FileSystemPath(materialized_path).resolve(strict=True)
    if (
        not resolved_path.is_file()
        or resolved_root not in resolved_path.parents
    ):
        raise ValueError("target materialized outside delivery root")
    return resolved_path


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


def _event_cursor(after_seq: int | None, last_event_id: str | None) -> int:
    if after_seq is not None:
        return after_seq
    if last_event_id is None:
        return 0
    try:
        return max(0, int(last_event_id))
    except ValueError:
        return 0


def _sse_frame(
    *,
    event: str,
    data: dict[str, Any],
    event_id: int | None = None,
) -> str:
    lines = [] if event_id is None else [f"id: {event_id}"]
    lines.extend(
        (
            f"event: {event}",
            "data: "
            + json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            "",
            "",
        )
    )
    return "\n".join(lines)


__all__ = [
    "ExecutionAdmissionV2",
    "ExecutionActionV2",
    "ExecutionCancellationV2",
    "ExecutionV2RouteDependencies",
    "register_execution_v2_routes",
]
