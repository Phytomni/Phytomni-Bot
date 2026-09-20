# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Owner-scoped read transports for the execution V2 API."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path as FileSystemPath
from typing import Any, Never, Protocol, TypedDict

from fastapi import Depends, FastAPI, Header, Path, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.background import BackgroundTask

from ...runtime.execution_content_stream_v2 import (
    execution_content_stream_for_db,
)
from ...runtime.execution_event_limits import DEFAULT_EXECUTION_EVENT_LIMITS
from ...runtime.execution_journal_store_v2 import SQLiteExecutionJournal
from ...runtime.execution_journal_v2 import (
    ExecutionContextStageV2,
    PublicTargetKind,
    PublicWarningV2,
    TrackingHealth,
)
from ...runtime.execution_log_artifact_v2 import (
    SQLiteExecutionLogArtifactStore,
)
from ...runtime.execution_target_store_v2 import SQLiteExecutionTargetStore
from ...runtime.execution_trace_target_v2 import resolve_trace_target
from ...runtime.execution_work_store_v2 import (
    ExecutionWorkNotFoundError,
    SQLiteExecutionWorkRepository,
)
from ...runtime.sqlite import sqlite_transaction
from ..lifecycle_contract import SafeApiError

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


def _dependency_member() -> Never:
    """Mark a Protocol-only route dependency member as unreachable."""
    raise NotImplementedError


class ExecutionV2ReadRouteDependencies(Protocol):
    """Storage, authorization, and scheduling seams for V2 reads."""

    @property
    def require_service(self) -> Callable[..., Any]:
        """Return the service-auth FastAPI dependency."""
        _dependency_member()

    def tasks_db_path(self) -> str:
        """Return the execution database path."""
        _dependency_member()

    def owner_header(
        self,
        x_phyto_owner: str,
        x_phyto_execution_schema: str | None = None,
    ) -> str:
        """Validate and return the service-asserted owner."""
        _dependency_member()

    def reservation(self, owner: str, execution_id: str) -> Any:
        """Return one owner-scoped execution reservation."""
        _dependency_member()

    def journal(
        self,
        owner: str,
        execution_id: str,
    ) -> SQLiteExecutionJournal:
        """Return one owner-scoped execution journal."""
        _dependency_member()

    async def download_target(
        self,
        delivery_ref: str,
        server_dir: str,
    ) -> str:
        """Materialize one private target."""
        _dependency_member()

    async def sleep(self, seconds: float) -> None:
        """Wait before the next stream poll."""
        _dependency_member()

    def heartbeat_poll_ticks(self) -> int:
        """Return the number of idle polls between heartbeats."""
        _dependency_member()


class _StreamCursorQuery(TypedDict):
    """Flat reconnect cursors accepted by the event stream."""

    after_seq: int | None
    after_revision: int
    after_offset: int
    last_event_id: str | None


class _TargetLookupQuery(TypedDict):
    """Typed path and paging values for a target lookup."""

    execution_id: str
    kind: PublicTargetKind
    target_id: str
    after_seq: int
    limit: int


async def _build_stream_cursor_query(
    after_seq: int | None = Query(default=None, ge=0),
    after_revision: int = Query(default=0, ge=0),
    after_offset: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> _StreamCursorQuery:
    """Collect flat stream cursors without changing the HTTP schema."""
    return {
        "after_seq": after_seq,
        "after_revision": after_revision,
        "after_offset": after_offset,
        "last_event_id": last_event_id,
    }


async def _build_target_lookup_query(
    execution_id: str,
    kind: PublicTargetKind,
    target_id: str = Path(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    ),
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> _TargetLookupQuery:
    """Collect one target address and its trace paging values."""
    return {
        "execution_id": execution_id,
        "kind": kind,
        "target_id": target_id,
        "after_seq": after_seq,
        "limit": limit,
    }


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


def _register_snapshot_routes(
    app: FastAPI,
    dependencies: ExecutionV2ReadRouteDependencies,
) -> None:
    """Register execution snapshot and bounded event-page reads."""

    @app.get("/v2/executions/{execution_id}")
    async def get_execution_snapshot(
        execution_id: str,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(dependencies.owner_header),
    ) -> JSONResponse:
        del _service
        record = dependencies.reservation(owner, execution_id)
        projection = dependencies.journal(owner, execution_id).get_projection(
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
        owner: str = Depends(dependencies.owner_header),
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(
            default=DEFAULT_EXECUTION_EVENT_LIMITS.default_page_size,
            ge=1,
            le=DEFAULT_EXECUTION_EVENT_LIMITS.max_page_size,
        ),
    ) -> JSONResponse:
        del _service
        page = dependencies.journal(owner, execution_id).list_events(
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


def _register_stream_route(
    app: FastAPI,
    dependencies: ExecutionV2ReadRouteDependencies,
) -> None:
    """Register the resumable execution event stream."""

    @app.get("/v2/executions/{execution_id}/events/stream")
    async def stream_execution_events(
        execution_id: str,
        request: Request,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(dependencies.owner_header),
        cursors: _StreamCursorQuery = Depends(_build_stream_cursor_query),
    ) -> StreamingResponse:
        """Flush a snapshot, drain committed history, and follow commits."""
        del _service
        record = dependencies.reservation(owner, execution_id)
        store = dependencies.journal(owner, execution_id)
        work_repository = SQLiteExecutionWorkRepository(
            dependencies.tasks_db_path()
        )
        content_stream = execution_content_stream_for_db(
            dependencies.tasks_db_path()
        )
        cursor = _event_cursor(cursors["after_seq"], cursors["last_event_id"])
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

        async def follow() -> AsyncIterator[str]:
            current = cursor
            content_revision = cursors["after_revision"]
            content_offset = cursors["after_offset"]
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
                        yield _event_delivery_frame(
                            execution_id=execution_id,
                            after_seq=current,
                            item=item,
                            delivered=delivered,
                        )
                        if (
                            delivered
                            > DEFAULT_EXECUTION_EVENT_LIMITS.max_live_backlog
                        ):
                            return
                        current = item.seq
                    continue
                content_frames = content_stream.list_after(
                    owner=owner,
                    execution_id=execution_id,
                    output_revision=content_revision,
                    after_offset=content_offset,
                )
                if content_frames:
                    idle_polls = 0
                    for content_frame in content_frames:
                        content_revision = content_frame.output_revision
                        content_offset = content_frame.offset
                        yield _sse_frame(
                            event="execution_content",
                            data=content_frame.model_dump(mode="json"),
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
                if idle_polls >= dependencies.heartbeat_poll_ticks():
                    yield ": heartbeat\n\n"
                    idle_polls = 0
                await dependencies.sleep(0.25)

        stream_headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(
            content=follow(),
            headers=stream_headers,
            media_type="text/event-stream",
        )


def _register_detail_routes(
    app: FastAPI,
    dependencies: ExecutionV2ReadRouteDependencies,
) -> None:
    """Register single-event and grouped-operation detail reads."""

    @app.get("/v2/executions/{execution_id}/events/{event_id}")
    async def get_execution_event_detail(
        execution_id: str,
        event_id: str,
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(dependencies.owner_header),
    ) -> JSONResponse:
        del _service
        event = dependencies.journal(owner, execution_id).get_event(
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
        owner: str = Depends(dependencies.owner_header),
    ) -> JSONResponse:
        """Return one grouped operation after execution-owner authorization."""
        del _service
        projection = dependencies.journal(owner, execution_id).get_projection(
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


def _register_target_route(
    app: FastAPI,
    dependencies: ExecutionV2ReadRouteDependencies,
) -> None:
    """Register public target metadata and trace resolution."""

    @app.get("/v2/executions/{execution_id}/targets/{kind}/{target_id}")
    async def get_execution_target(
        lookup: _TargetLookupQuery = Depends(_build_target_lookup_query),
        _service: None = Depends(dependencies.require_service),
        owner: str = Depends(dependencies.owner_header),
    ) -> JSONResponse:
        del _service
        execution_id = lookup["execution_id"]
        kind = lookup["kind"]
        target_id = lookup["target_id"]
        projection = dependencies.journal(owner, execution_id).get_projection(
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
                dependencies.journal(owner, execution_id),
                owner=owner,
                execution_id=execution_id,
                target_id=target_id,
                after_seq=lookup["after_seq"],
                limit=lookup["limit"],
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


def _register_target_content_route(
    app: FastAPI,
    dependencies: ExecutionV2ReadRouteDependencies,
) -> None:
    """Register private target materialization after owner authorization."""

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
        owner: str = Depends(dependencies.owner_header),
    ) -> Response:
        """Materialize one private binding after owner reauthorization."""
        del _service
        projection = dependencies.journal(owner, execution_id).get_projection(
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
            local_path = await dependencies.download_target(
                binding.delivery_ref,
                temp_dir,
            )
            materialized = _validated_delivery_path(temp_dir, local_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise _delivery_unavailable() from exc
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


def register_execution_v2_read_routes(
    app: FastAPI,
    dependencies: ExecutionV2ReadRouteDependencies,
) -> None:
    """Register V2 read routes in their stable OpenAPI order."""
    _register_snapshot_routes(app, dependencies)
    _register_stream_route(app, dependencies)
    _register_detail_routes(app, dependencies)
    _register_target_route(app, dependencies)
    _register_target_content_route(app, dependencies)


def _not_found() -> SafeApiError:
    return SafeApiError(
        status_code=404,
        code="not_found",
        message="resource not found",
        stage="execution_read",
        retryable=False,
    )


def _delivery_unavailable() -> SafeApiError:
    return SafeApiError(
        status_code=503,
        code="target_delivery_unavailable",
        message="target delivery unavailable",
        stage="execution_target",
        retryable=True,
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


def _event_cursor(after_seq: int | None, last_event_id: str | None) -> int:
    if after_seq is not None:
        return after_seq
    if last_event_id is None:
        return 0
    try:
        return max(0, int(last_event_id))
    except ValueError:
        return 0


def _event_delivery_frame(
    *,
    execution_id: str,
    after_seq: int,
    item: Any,
    delivered: int,
) -> str:
    """Render one event or the bounded-backlog terminal control frame."""
    backlog_exceeded = (
        delivered > DEFAULT_EXECUTION_EVENT_LIMITS.max_live_backlog
    )
    if backlog_exceeded:
        payload = {
            "schema_version": 2,
            "execution_id": execution_id,
            "after_seq": after_seq,
            "next_available_seq": item.seq,
            "reason": "backlog_exceeded",
        }
        return _sse_frame(event="execution_gap", data=payload)
    return _sse_frame(
        event="execution_event",
        data=item.to_public_dict(),
        event_id=item.seq,
    )


def _sse_frame(
    *,
    event: str,
    data: dict[str, Any],
    event_id: int | None = None,
) -> str:
    payload = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fields = [f"event: {event}", f"data: {payload}"]
    if event_id is not None:
        fields.insert(0, f"id: {event_id}")
    fields.extend(("", ""))
    return "\n".join(fields)


__all__ = [
    "ExecutionV2ReadRouteDependencies",
    "register_execution_v2_read_routes",
]
