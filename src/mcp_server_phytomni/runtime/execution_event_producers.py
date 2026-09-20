# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared lifecycle and remote-reconciliation execution event producers."""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from .execution_event_flags import execution_event_production_enabled
from .execution_event_observability import observe_execution_event
from .execution_event_sink import event_intent
from .execution_event_store import SQLiteExecutionEventStore


def _append(
    db_path: str,
    run_id: str,
    owner: str,
    *,
    kind: str,
    status: str,
    idempotency_key: str,
    payload: Mapping[str, Any] | None = None,
    target: Mapping[str, str] | None = None,
    task_id: str | None = None,
) -> None:
    """Append a validated fact without letting observability break work."""
    if not execution_event_production_enabled():
        observe_execution_event("production_disabled")
        return
    try:
        SQLiteExecutionEventStore(db_path).append(
            run_id,
            owner=owner,
            intent=event_intent(
                kind,
                status=status,
                payload=payload,
                target=target,
                task_id=task_id,
                idempotency_key=idempotency_key,
            ),
        )
        observe_execution_event("append_committed")
    except Exception:
        observe_execution_event("append_failed")
        return


def emit_run_settlement(
    db_path: str,
    *,
    run_id: str,
    owner: str,
    status: str,
    revision: int,
) -> None:
    """Emit one idempotent terminal fact after the run CAS succeeds."""
    kind_by_status = {
        "succeeded": "run.succeeded",
        "failed": "run.failed",
        "cancelled": "run.cancelled",
    }
    kind = kind_by_status.get(status)
    if kind is None:
        return
    payload: dict[str, Any] = {}
    if status == "failed":
        payload = {"code": "execution_failed", "retryable": False}
    _append(
        db_path,
        run_id,
        owner,
        kind=kind,
        status=status,
        payload=payload,
        idempotency_key=f"run:settlement:{revision}:{status}",
    )


def emit_run_started(
    db_path: str,
    *,
    run_id: str,
    owner: str,
) -> None:
    """Emit the idempotent first lifecycle fact for a durable run."""
    _append(
        db_path,
        run_id,
        owner,
        kind="run.started",
        status="running",
        idempotency_key="run:started",
    )


def emit_input_required(
    db_path: str,
    *,
    run_id: str,
    owner: str,
    revision: int,
    surface_id: str,
    widget: str,
) -> None:
    """Emit the paired waiting and typed input-required facts."""
    _append(
        db_path,
        run_id,
        owner,
        kind="run.waiting_input",
        status="waiting",
        idempotency_key=f"run:waiting-input:{revision}:{surface_id}",
    )
    _append(
        db_path,
        run_id,
        owner,
        kind="input.required",
        status="waiting",
        payload={"surface_id": surface_id, "widget": widget},
        idempotency_key=f"input:required:{revision}:{surface_id}",
    )


def emit_input_resolved(
    db_path: str,
    *,
    run_id: str,
    owner: str,
    revision: int,
    surface_id: str,
    outcome: str,
) -> None:
    """Emit resume facts before re-entering an owner-authorized checkpoint."""
    _append(
        db_path,
        run_id,
        owner,
        kind="input.resolved",
        status="running",
        payload={"surface_id": surface_id, "outcome": outcome},
        idempotency_key=f"input:resolved:{revision}:{surface_id}",
    )
    _append(
        db_path,
        run_id,
        owner,
        kind="run.resumed",
        status="running",
        idempotency_key=f"run:resumed:{revision}:{surface_id}",
    )


def emit_remote_progress(
    db_path: str,
    *,
    run_id: str,
    owner: str,
    revision: int,
    task_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Translate remote reconciliation into a safe progress fact."""
    statuses = tuple(str(row.get("status", "")).lower() for row in task_rows)
    completed = sum(
        status
        in {"succeeded", "success", "completed", "done", "failed", "cancelled"}
        for status in statuses
    )
    digest = hashlib.sha256("\x00".join(statuses).encode()).hexdigest()[:16]
    _append(
        db_path,
        run_id,
        owner,
        kind="phase.progress",
        status="running",
        payload={
            "phase": "remote_execution",
            "completed": completed,
            "total": len(task_rows),
        },
        idempotency_key=f"remote:progress:{revision}:{digest}",
    )


def emit_remote_artifacts(
    db_path: str,
    *,
    run_id: str,
    owner: str,
    revision: int,
    artifacts: Sequence[Mapping[str, Any]],
) -> None:
    """Translate remote artifact paths to names and opaque targets."""
    for group in artifacts:
        task_id = str(group.get("task_id") or "") or None
        paths = group.get("paths")
        if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)):
            continue
        for raw_path in paths:
            if not isinstance(raw_path, str) or not raw_path:
                continue
            normalized = raw_path.replace("\\", "/")
            name = PurePosixPath(normalized).name
            if not name or len(name) > 255:
                continue
            digest = hashlib.sha256(
                f"{run_id}\x00{raw_path}".encode()
            ).hexdigest()
            media_type = (
                mimetypes.guess_type(name)[0] or "application/octet-stream"
            )
            _append(
                db_path,
                run_id,
                owner,
                kind="artifact.published",
                status="succeeded",
                payload={
                    "name": name,
                    "media_type": media_type,
                    "size_bytes": 0,
                },
                target={"kind": "artifact", "id": f"remote-{digest}"},
                task_id=task_id,
                idempotency_key=f"remote:artifact:{revision}:{digest}",
            )
