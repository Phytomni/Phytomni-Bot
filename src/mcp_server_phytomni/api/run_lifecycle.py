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
import inspect
import logging
import os
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from ..mcp.formatting.models import ResultDelivery
from ..mcp.formatting.redaction import strip_agent_result
from ..runtime.async_utils import wait_for_thread_event
from ..runtime.background_submission import BACKGROUND_RUNTIME_ERRORS
from ..runtime.checkpoint_backend import build_default_checkpointer
from ..runtime.conversation_context.store import ConversationContextStore
from ..runtime.deep_genome_store import DeepGenomeStore
from ..runtime.deep_genome_store_projection import snapshot_to_canonical_result
from ..runtime.run_registry import (
    RunFilter,
    RunOutcome,
    RunRecord,
    RunRegistry,
    RunRequestInfo,
    local_run_spec,
)
from ..runtime.run_registry_delivery import result_delivery_from_result
from ..runtime.task_manager import resolve_tasks_db_path
from ..storage.path_policy import IdFactory
from .lifecycle_contract import project_research_lifecycle

__all__ = [
    "ResolvedRemoteRun",
    "RunLifecycleContext",
    "RunListQuery",
    "RunPersistenceError",
    "agent_run_response",
    "claim_run_gc",
    "create_running_stream_run",
    "fetch_owner_run",
    "retry_owner_delivery",
    "list_owner_runs",
    "project_deep_genome_run",
    "project_public_run_record",
    "purge_expired_runs_best_effort",
    "purge_expired_runs_best_effort_async",
    "reconcile_run_task_logs",
    "record_sync_run",
    "release_run_gc",
    "resolve_remote_run",
    "run_record_to_dict",
    "schedule_run_gc",
    "settle_stream_run",
    "stamp_remote_request_info",
]


_LOGGER = logging.getLogger(__name__)
_RUN_GC_CAUGHT = BACKGROUND_RUNTIME_ERRORS
_RUN_GC_LOCK = threading.Lock()
_RUN_GC_ACTIVE = threading.Event()

type PurgeRun = Callable[[], None]
type ProjectRun = Callable[..., dict[str, Any]]
type FetchOwnerRun = Callable[..., Awaitable[dict[str, Any]]]
type ReconcileTaskLog = Callable[[str], Awaitable[dict[str, Any] | None]]
type StripResult = Callable[[dict[str, Any]], dict[str, Any]]
type RegistryFactory = Callable[[str], Any]
type CheckpointerFactory = Callable[[], Any]
type CleanupCandidate = tuple[str, str]
_CHECKPOINTS_DATABASE_FILENAME = "checkpoints.db"


@dataclass(frozen=True, slots=True)
class RunLifecycleContext:
    """Injected store and compatibility seams for one lifecycle operation."""

    db_path: str | None = None
    purge: PurgeRun | None = None
    project: ProjectRun | None = None


@dataclass(frozen=True, slots=True)
class RunListQuery:
    """Owner-run list filters and paging values from the HTTP query."""

    run_filter: RunFilter = field(default_factory=RunFilter)
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


async def _close_checkpointer(
    checkpointer: Any, logger: logging.Logger
) -> None:
    """Close a lifecycle-owned checkpointer connection when supported."""
    closer = getattr(checkpointer, "aclose", None)
    if not callable(closer):
        closer = getattr(checkpointer, "close", None)
    if not callable(closer):
        connection = getattr(checkpointer, "conn", None)
        closer = getattr(connection, "close", None)
    if not callable(closer):
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except _RUN_GC_CAUGHT as exc:
        logger.warning(
            "Review candidate checkpointer close failed: %s",
            exc.__class__.__name__,
        )


async def _delete_review_candidate_checkpoints(
    candidates: Sequence[CleanupCandidate],
    *,
    checkpointer_factory: CheckpointerFactory,
    logger: logging.Logger,
    close_checkpointer: bool,
) -> tuple[CleanupCandidate, ...]:
    """Delete only durable candidate threads and return completed rows."""
    try:
        checkpointer = checkpointer_factory()
    except _RUN_GC_CAUGHT as exc:
        logger.warning(
            "Review candidate checkpoint cleanup unavailable: %s",
            exc.__class__.__name__,
        )
        return ()
    try:
        deleter = getattr(checkpointer, "adelete_thread", None)
        if not callable(deleter):
            deleter = getattr(checkpointer, "delete_thread", None)
        if not callable(deleter):
            logger.warning(
                "Review candidate checkpoint cleanup unavailable: no deleter"
            )
            return ()
        deleted: list[CleanupCandidate] = []
        for conversation_key, candidate_thread_id in candidates:
            try:
                result = deleter(candidate_thread_id)
                if inspect.isawaitable(result):
                    await result
            except _RUN_GC_CAUGHT as exc:
                logger.warning(
                    "Review candidate checkpoint cleanup failed: %s",
                    exc.__class__.__name__,
                )
                continue
            deleted.append((conversation_key, candidate_thread_id))
        return tuple(deleted)
    finally:
        if close_checkpointer:
            await _close_checkpointer(checkpointer, logger)


def _checkpoint_database_path(tasks_db_path: str) -> str:
    """Return the persistent checkpoint DB beside the tasks DB."""
    directory = os.path.dirname(tasks_db_path) or "."
    return os.path.join(directory, _CHECKPOINTS_DATABASE_FILENAME)


def _lifecycle_checkpointer_factory(
    tasks_db_path: str,
) -> CheckpointerFactory:
    """Open a fresh persistent saver for one synchronous GC pass."""
    checkpoint_path = _checkpoint_database_path(tasks_db_path)
    return lambda: build_default_checkpointer(checkpoint_path)


def _run_async_at_sync_boundary(
    coroutine_factory: Callable[[], Coroutine[Any, Any, Any]],
) -> Any:
    """Run async checkpoint deletion without nesting or leaking event loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine_factory())

    result: list[Any] = []
    failures: list[BaseException] = []

    def _worker() -> None:
        try:
            result.append(asyncio.run(coroutine_factory()))
        except asyncio.CancelledError as exc:
            failures.append(exc)
        except _RUN_GC_CAUGHT as exc:
            failures.append(exc)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join()
    if failures:
        raise failures[0]
    return result[0] if result else None


def _purge_expired_context_best_effort(
    *,
    path: str,
    now: datetime,
    checkpointer_factory: CheckpointerFactory | None,
    logger: logging.Logger,
) -> None:
    """Purge staged rows and candidate checkpoints under one durable lock."""
    owns_checkpointer = checkpointer_factory is None
    factory = checkpointer_factory or _lifecycle_checkpointer_factory(path)
    store = ConversationContextStore(path)
    mutation_lock = store.acquire_review_mutation_lock()
    try:
        store.purge_expired_staged(now, mutation_lock_held=True)
        candidates = store.list_checkpoint_cleanup_candidates(
            mutation_lock_held=True
        )
        if not candidates:
            return
        deleted = _run_async_at_sync_boundary(
            lambda: _delete_review_candidate_checkpoints(
                candidates,
                checkpointer_factory=factory,
                logger=logger,
                close_checkpointer=owns_checkpointer,
            )
        )
        if deleted:
            store.complete_checkpoint_cleanup_candidates(
                deleted, mutation_lock_held=True
            )
    finally:
        mutation_lock.release()


def purge_expired_runs_best_effort(
    *,
    db_path: str | None = None,
    registry_factory: RegistryFactory = RunRegistry,
    logger: logging.Logger = _LOGGER,
    checkpointer_factory: CheckpointerFactory | None = None,
) -> None:
    """Run registry and staged-context TTL purges.

    Storage failures are swallowed so cleanup remains best effort.
    """
    path = _database_path(db_path)
    try:
        registry_factory(path).purge_expired()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("run TTL purge failed: %s", exc.__class__.__name__)
    try:
        _purge_expired_context_best_effort(
            path=path,
            now=datetime.now(UTC),
            checkpointer_factory=checkpointer_factory,
            logger=logger,
        )
    except _RUN_GC_CAUGHT as exc:
        logger.warning(
            "conversation context TTL purge failed: %s",
            exc.__class__.__name__,
        )


async def purge_expired_runs_best_effort_async(
    *, purge: PurgeRun = purge_expired_runs_best_effort
) -> None:
    """Run the local purge off-loop and coalesce concurrent callers."""
    if not claim_run_gc():
        return
    finished = asyncio.Event()
    loop = asyncio.get_running_loop()
    failures: list[BaseException] = []

    def _run() -> None:
        try:
            purge()
        except asyncio.CancelledError as exc:
            failures.append(exc)
        except _RUN_GC_CAUGHT as exc:
            failures.append(exc)
        finally:
            release_run_gc()
            loop.call_soon_threadsafe(finished.set)

    try:
        threading.Thread(target=_run, daemon=True).start()
    except RuntimeError:
        release_run_gc()
        raise
    await wait_for_thread_event(finished)
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
    result = (
        strip_agent_result(record.result)
        if isinstance(record.result, dict)
        else record.result
    )
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
        "request_id": info.request_id,
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
    if record.spec.agent == "research":
        stage, failure = project_research_lifecycle(
            record.status, record.stage, record.failure
        )
        payload["stage"] = stage
        if failure is not None:
            payload["failure"] = failure
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
    """Project the owner-scoped DeepGenome snapshot into one run envelope."""
    payload = run_record_to_dict(record)
    source_result = payload.get("result")
    existing_result = (
        source_result if isinstance(source_result, Mapping) else None
    )
    if len(record.task_ids) != 1:
        return _project_deep_genome_fallback(
            payload, existing_result, debug=debug
        )
    try:
        snapshot = DeepGenomeStore(_database_path(db_path)).get_snapshot(
            record.task_ids[0]
        )
    except sqlite3.Error:
        _LOGGER.warning(
            "deep_genome run snapshot unavailable for owner-scoped run"
        )
        return _project_deep_genome_fallback(
            payload, existing_result, debug=debug
        )
    if snapshot is None:
        return _project_deep_genome_fallback(
            payload, existing_result, debug=debug
        )

    result = snapshot_to_canonical_result(
        snapshot,
        existing_result=existing_result,
    )
    if debug and existing_result is not None:
        for private_key in (
            "task_results",
            "live_status",
            "artifacts",
            "raw",
        ):
            value = existing_result.get(private_key)
            if value is not None:
                result[private_key] = value
    payload["status"] = snapshot.status
    payload["result"] = result
    payload["answer"] = extract_answer(result)
    if _result_tracking_is_degraded(result):
        payload["degraded_tracking"] = True
    else:
        payload.pop("degraded_tracking", None)
    return payload


def _project_deep_genome_fallback(
    payload: dict[str, Any],
    source_result: Mapping[str, Any] | None,
    *,
    debug: bool,
) -> dict[str, Any]:
    """Keep only canonical blocks when the local snapshot is unavailable."""
    result: dict[str, Any] = {}
    if source_result is not None:
        for key in ("formatted", "execution", "a2ui"):
            value = source_result.get(key)
            if value is not None:
                result[key] = value
        if debug:
            for private_key in (
                "task_results",
                "live_status",
                "artifacts",
                "raw",
            ):
                value = source_result.get(private_key)
                if value is not None:
                    result[private_key] = value
    payload["result"] = result
    payload["answer"] = extract_answer(result)
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
    registry_factory: RegistryFactory = RunRegistry,
) -> dict[str, Any]:
    """Reconcile and flatten one owner-scoped run, or raise HTTP 404."""
    registry = registry_factory(_database_path(db_path))
    record = registry.get_run(run_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    if record.spec.agent == "deep_genome":
        return project_public_run_record(record, debug=debug, db_path=db_path)
    record = await registry.reconcile(run_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return project_public_run_record(record, debug=debug, db_path=db_path)


def _public_result_delivery(delivery: ResultDelivery) -> dict[str, Any]:
    """Serialize only the bounded public delivery fields."""
    return asdict(delivery)


async def retry_owner_delivery(
    run_id: str,
    *,
    owner: str,
    db_path: str | None = None,
    registry_factory: RegistryFactory = RunRegistry,
) -> dict[str, Any]:
    """Begin an owner-authorized delivery retry without rerunning science."""
    registry = registry_factory(_database_path(db_path))
    record = registry.get_run(run_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")

    delivery = result_delivery_from_result(record.result)
    if delivery is None:
        raise HTTPException(status_code=409, detail="delivery retry conflict")
    if delivery.status == "pending":
        return _public_result_delivery(delivery)
    if delivery.status != "failed" or not delivery.retryable:
        raise HTTPException(status_code=409, detail="delivery retry conflict")

    if not registry.begin_delivery_retry(run_id, owner=owner):
        current = registry.get_run(run_id, owner=owner)
        current_delivery = (
            result_delivery_from_result(current.result)
            if current is not None
            else None
        )
        if (
            current_delivery is not None
            and current_delivery.status == "pending"
        ):
            return _public_result_delivery(current_delivery)
        raise HTTPException(status_code=409, detail="delivery retry conflict")

    current = registry.get_run(run_id, owner=owner)
    current_delivery = (
        result_delivery_from_result(current.result)
        if current is not None
        else None
    )
    if current_delivery is None or current_delivery.status != "pending":
        raise HTTPException(status_code=409, detail="delivery retry conflict")
    return _public_result_delivery(current_delivery)


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
    **kwargs: Any,
) -> bool:
    """Settle an owner-scoped stream row and report durable success."""
    expected_revision = kwargs.pop("expected_revision", None)
    if kwargs:
        raise TypeError("unexpected stream settlement keyword")
    configured = context or RunLifecycleContext()
    purge = configured.purge or purge_expired_runs_best_effort
    try:
        updated = RunRegistry(_database_path(configured.db_path)).settle_run(
            run_id,
            owner=owner,
            status=status,
            result=result,
            expected_revision=expected_revision,
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
