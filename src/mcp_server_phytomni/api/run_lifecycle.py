# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Run-registry lifecycle and public projection helpers for the HTTP API.

This module owns the bookkeeping seams shared by synchronous, streaming, and
remote-agent HTTP requests.  It deliberately does not import ``api.app``:
the application keeps only thin compatibility wrappers so route tests and
existing monkeypatch seams remain stable while registry policy has one home.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from ..runtime.deep_genome_store import (
    DeepGenomeStore,
    snapshot_to_formatted_report_metadata,
    snapshot_to_public_dict,
)
from ..runtime.run_registry import (
    RunFilter,
    RunOutcome,
    RunRecord,
    RunRegistry,
    RunRequestInfo,
    local_run_spec,
)
from ..runtime.task_manager import resolve_tasks_db_path
from ..storage.path_policy import IdFactory

__all__ = [
    "agent_run_response",
    "claim_run_gc",
    "create_running_stream_run",
    "fetch_owner_run",
    "list_owner_runs",
    "RunLifecycleContext",
    "RunListQuery",
    "project_deep_genome_run",
    "project_public_run_record",
    "purge_expired_runs_best_effort",
    "purge_expired_runs_best_effort_async",
    "reconcile_run_task_logs",
    "record_sync_run",
    "release_run_gc",
    "ResolvedRemoteRun",
    "RunPersistenceError",
    "resolve_remote_run",
    "schedule_run_gc",
    "settle_stream_run",
    "stamp_remote_request_info",
    "run_record_to_dict",
]


_LOGGER = logging.getLogger(__name__)
_RUN_GC_CAUGHT: tuple[type[Exception], ...] = (Exception,)
_RUN_GC_LOCK = threading.Lock()
_RUN_GC_ACTIVE = threading.Event()
_RUN_GC_POLL_SECONDS = 0.01

type PurgeRun = Callable[[], None]
type ProjectRun = Callable[..., dict[str, Any]]
type FetchOwnerRun = Callable[..., Awaitable[dict[str, Any]]]
type ReconcileTaskLog = Callable[[str], Awaitable[dict[str, Any] | None]]
type StripResult = Callable[[dict[str, Any]], dict[str, Any]]
type RegistryFactory = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class RunLifecycleContext:
    """Injected store and compatibility seams for one lifecycle operation."""

    db_path: str | None = None
    purge: PurgeRun | None = None
    project: ProjectRun | None = None


@dataclass(frozen=True, slots=True)
class RunListQuery:
    """Owner-run list filters and paging values from the HTTP query."""

    run_filter: RunFilter = RunFilter()
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ResolvedRemoteRun:
    """Owner-scoped durable and request-local remote run identity."""

    run_id: str | None
    task_ids: tuple[str, ...]
    persisted: bool
    degraded_tracking: bool


class RunPersistenceError(RuntimeError):
    """Raised when a public run cannot be durably persisted."""


def _database_path(db_path: str | None) -> str:
    """Resolve the registry path once at the lifecycle boundary."""
    return db_path if db_path is not None else resolve_tasks_db_path()


def purge_expired_runs_best_effort(
    *,
    db_path: str | None = None,
    registry_factory: RegistryFactory = RunRegistry,
    logger: logging.Logger = _LOGGER,
) -> None:
    """Run one registry TTL purge, swallowing SQLite and OS failures."""
    try:
        registry_factory(_database_path(db_path)).purge_expired()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("run TTL purge failed: %s", exc.__class__.__name__)


async def purge_expired_runs_best_effort_async(
    *, purge: PurgeRun = purge_expired_runs_best_effort
) -> None:
    """Run the local purge off-loop and coalesce concurrent callers."""
    if not claim_run_gc():
        return
    finished = threading.Event()
    failures: list[Exception] = []

    def _run() -> None:
        try:
            purge()
        except _RUN_GC_CAUGHT as exc:
            failures.append(exc)
        finally:
            release_run_gc()
            finished.set()

    try:
        threading.Thread(target=_run, daemon=True).start()
    except RuntimeError:
        release_run_gc()
        raise
    while not finished.is_set():  # noqa: ASYNC110
        await asyncio.sleep(_RUN_GC_POLL_SECONDS)
    if failures:
        raise failures[0]


def claim_run_gc() -> bool:
    """Claim the process-local GC slot, or coalesce with its active pass."""
    with _RUN_GC_LOCK:
        if _RUN_GC_ACTIVE.is_set():
            return False
        _RUN_GC_ACTIVE.set()
        return True


def release_run_gc() -> None:
    """Release the process-local GC slot after its worker exits."""
    with _RUN_GC_LOCK:
        _RUN_GC_ACTIVE.clear()


async def schedule_run_gc(
    background: BackgroundTasks,
    *,
    task: Callable[[], Awaitable[None]] = purge_expired_runs_best_effort_async,
) -> None:
    """Schedule a best-effort registry purge after the response flushes."""
    background.add_task(task)


def extract_answer(result: Any) -> str | None:
    """Pull a display-ready answer from either supported result envelope."""
    if not isinstance(result, dict):
        return None
    formatted = result.get("formatted")
    if isinstance(formatted, dict):
        candidate = formatted.get("answer")
        if isinstance(candidate, str):
            return candidate
    candidate = result.get("answer")
    if isinstance(candidate, str):
        return candidate
    return None


def run_record_to_dict(record: Any) -> dict[str, Any]:
    """Flatten a ``RunRecord`` into the public HTTP run envelope."""
    info = record.request_info
    result = record.result
    payload: dict[str, Any] = {
        "run_id": record.spec.run_id,
        "agent": record.spec.agent,
        "origin": record.spec.origin,
        "user_id": record.spec.user_id,
        "status": record.status,
        "result": result,
        "error": record.error,
        "created_at": record.timestamps.created_at,
        "updated_at": record.timestamps.updated_at,
        "expires_at": record.timestamps.expires_at,
        "task_ids": list(record.task_ids),
        "dialogue_id": info.dialogue_id,
        "query": info.query,
        "tool_name": info.tool_name,
        "model": info.model,
        "a2a_task_id": record.a2a.task_id,
        "a2a_context_id": record.a2a.context_id,
        "a2a_message_id": record.a2a.message_id,
        "answer": extract_answer(result),
    }
    if _result_tracking_is_degraded(result):
        payload["degraded_tracking"] = True
    return payload


def _result_tracking_is_degraded(result: Any) -> bool:
    """Derive the compatibility flag without rebuilding the result."""
    if not isinstance(result, Mapping):
        return False
    execution = result.get("execution")
    tracking = (
        execution.get("tracking") if isinstance(execution, Mapping) else {}
    )
    return isinstance(tracking, Mapping) and tracking.get("degraded") is True


def agent_run_response(
    *,
    run_id: str | None,
    agent: str,
    status: str,
    result: dict[str, Any],
    include_run_id: bool = True,
) -> dict[str, Any]:
    """Build the shared public ``agent.run`` response envelope."""
    body: dict[str, Any] = {
        "id": run_id,
        "object": "agent.run",
        "agent": agent,
        "status": status,
        "task_ids": [],
        "result": result,
    }
    if include_run_id:
        body["run_id"] = run_id
    return body


def project_deep_genome_run(
    record: RunRecord, *, debug: bool = False, db_path: str | None = None
) -> dict[str, Any]:
    """Merge the owner-scoped DeepGenome snapshot into one run envelope."""
    payload = run_record_to_dict(record)
    result = payload.get("result")
    merged = dict(result) if isinstance(result, Mapping) else {}
    if not debug:
        for private_key in ("task_results", "live_status", "artifacts", "raw"):
            merged.pop(private_key, None)
        payload["result"] = merged
    if len(record.task_ids) != 1:
        return payload
    try:
        snapshot = DeepGenomeStore(_database_path(db_path)).get_snapshot(
            record.task_ids[0]
        )
    except sqlite3.Error:
        _LOGGER.warning(
            "deep_genome run snapshot unavailable for owner-scoped run"
        )
        return payload
    if snapshot is None:
        return payload

    merged.update(snapshot_to_public_dict(snapshot))
    formatted = merged.get("formatted")
    if isinstance(formatted, Mapping):
        formatted_copy = dict(formatted)
        existing_metadata = formatted_copy.get("metadata")
        metadata = (
            dict(existing_metadata)
            if isinstance(existing_metadata, Mapping)
            else {}
        )
        metadata["report"] = snapshot_to_formatted_report_metadata(snapshot)
        formatted_copy["metadata"] = metadata
        merged["formatted"] = formatted_copy
    payload["status"] = snapshot.status
    payload["result"] = merged
    best_report = merged.get("final_report") or merged.get(
        "intermediate_report"
    )
    if isinstance(best_report, str) and best_report.strip():
        payload["answer"] = best_report
    return payload


def project_public_run_record(
    record: RunRecord, *, debug: bool = False, db_path: str | None = None
) -> dict[str, Any]:
    """Project one owner-scoped run through its public read contract."""
    if record.spec.agent == "deep_genome":
        return project_deep_genome_run(record, debug=debug, db_path=db_path)
    return run_record_to_dict(record)


async def fetch_owner_run(
    run_id: str,
    *,
    owner: str,
    debug: bool = False,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Reconcile and flatten one owner-scoped run, or raise HTTP 404."""
    registry = RunRegistry(_database_path(db_path))
    record = registry.get_run(run_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    if record.spec.agent == "deep_genome":
        return project_public_run_record(record, debug=debug, db_path=db_path)
    record = await registry.reconcile(run_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return project_public_run_record(record, debug=debug, db_path=db_path)


def list_owner_runs(
    owner: str,
    query: RunListQuery,
    *,
    debug: bool = False,
    context: RunLifecycleContext | None = None,
) -> dict[str, Any]:
    """Return the owner-scoped ``GET /v1/runs`` envelope."""
    configured = context or RunLifecycleContext()
    purge = configured.purge or purge_expired_runs_best_effort
    project = configured.project or project_public_run_record
    purge()
    records = RunRegistry(_database_path(configured.db_path)).list_runs(
        owner=owner,
        run_filter=query.run_filter,
        limit=query.limit,
        offset=query.offset,
    )
    return {
        "object": "list",
        "data": [project(record, debug=debug) for record in records],
    }


def resolve_remote_run(
    owner: str,
    *,
    run_id: str | None,
    accepted_task_ids: Sequence[str],
    recorder_degraded: bool,
    db_path: str | None = None,
) -> ResolvedRemoteRun:
    """Resolve durable identity without losing accepted upstream work."""
    if run_id is None:
        return ResolvedRemoteRun(
            run_id=None,
            task_ids=tuple(accepted_task_ids),
            persisted=False,
            degraded_tracking=recorder_degraded,
        )
    record = RunRegistry(_database_path(db_path)).get_run(run_id, owner=owner)
    if record is None:
        return ResolvedRemoteRun(
            run_id=None,
            task_ids=tuple(accepted_task_ids),
            persisted=False,
            degraded_tracking=True,
        )
    return ResolvedRemoteRun(
        run_id=run_id,
        task_ids=tuple(record.task_ids),
        persisted=True,
        degraded_tracking=False,
    )


def record_sync_run(
    *,
    agent: str,
    owner: str,
    result: dict[str, Any],
    request_info: RunRequestInfo | None = None,
    db_path: str | None = None,
) -> str:
    """Persist a terminal local run or fail before public success."""
    run_id = IdFactory().new_id("run", agent)
    try:
        RunRegistry(_database_path(db_path)).create_run(
            local_run_spec(run_id, owner, agent),
            outcome=RunOutcome(status="succeeded", result=result),
            request_info=request_info,
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.error(
            "sync run persistence failed for agent %s (%s)",
            agent,
            exc.__class__.__name__,
        )
        raise RunPersistenceError("completed run persistence failed") from exc
    return run_id


def create_running_stream_run(
    run_id: str,
    agent: str,
    owner: str,
    request_info: RunRequestInfo,
    *,
    db_path: str | None = None,
) -> None:
    """Write the initial owner-scoped running row for an HTTP stream."""
    try:
        RunRegistry(_database_path(db_path)).create_run(
            local_run_spec(run_id, owner, agent),
            outcome=RunOutcome(status="running"),
            request_info=request_info,
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "stream run create failed for %s: %s",
            agent,
            exc.__class__.__name__,
        )


def settle_stream_run(
    run_id: str,
    owner: str,
    status: str,
    result: dict[str, Any],
    *,
    context: RunLifecycleContext | None = None,
) -> bool:
    """Settle an owner-scoped stream row and report durable success."""
    configured = context or RunLifecycleContext()
    purge = configured.purge or purge_expired_runs_best_effort
    try:
        updated = RunRegistry(_database_path(configured.db_path)).settle_run(
            run_id,
            owner=owner,
            status=status,
            result=result,
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.error(
            "stream run settlement failed for %s (%s)",
            run_id,
            exc.__class__.__name__,
        )
        updated = False
    purge()
    return updated


def stamp_remote_request_info(
    *,
    run_id: str | None,
    owner: str,
    request_info: RunRequestInfo,
    db_path: str | None = None,
) -> None:
    """Back-fill request metadata on a chokepoint-created run row."""
    if run_id is None:
        return
    try:
        RunRegistry(_database_path(db_path)).update_request_info(
            run_id, owner=owner, request_info=request_info
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "remote run request-info back-fill failed for run %s: %s",
            run_id,
            exc.__class__.__name__,
        )


async def reconcile_run_task_logs(
    run_id: str,
    debug: bool,
    *,
    fetch: FetchOwnerRun,
    reconcile: ReconcileTaskLog,
    strip: StripResult,
) -> dict[str, Any]:
    """Verify ownership and reconcile every task log attached to a run."""
    record = await fetch(run_id)
    task_ids = record.get("task_ids", [])
    task_logs: list[dict[str, Any]] = []
    for task_id in task_ids:
        log = await reconcile(task_id)
        if log is None:
            continue
        if not debug:
            log = strip(log)
        task_logs.append(log)
    return {
        "run_id": run_id,
        "task_ids": task_ids,
        "task_logs": task_logs,
    }
