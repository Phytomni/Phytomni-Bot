# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared runtime for detached, owned background submissions."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..storage.path_policy import IdFactory
from .execution_defaults import empty_execution_projection
from .live_tasks import (
    deregister_live_task,
    is_live_running,
    register_live_task,
)
from .request_context import current_run_id, request_context
from .result_run_layout import RESULT_DELIVERY_AGENTS
from .run_registry import RunRegistry, RunRequestInfo, RunSpec
from .run_registry_delivery import (
    carry_execution_tasks,
    failed_child_ids_from_result,
)

_LOGGER = logging.getLogger(__name__)
_RUN_ID_ATTEMPTS = 3

__all__ = [
    "BackgroundSubmissionExecutionError",
    "BackgroundSubmissionLaunchError",
    "BackgroundSubmissionOutcome",
    "BackgroundSubmissionReservation",
    "BACKGROUND_RUNTIME_ERRORS",
    "failed_child_ids",
    "launch_background_submission",
    "reserve_background_submission",
]


class BackgroundSubmissionLaunchError(RuntimeError):
    """Raised when a reserved worker cannot be launched."""


class BackgroundSubmissionExecutionError(RuntimeError):
    """Stable internal signal for a post-acceptance submission failure."""


BACKGROUND_RUNTIME_ERRORS: tuple[type[Exception], ...] = (Exception,)


@dataclass(frozen=True, slots=True)
class BackgroundSubmissionOutcome:
    """Bounded result returned by one detached submission operation."""

    accepted_task_ids: tuple[str, ...] = ()
    failed_task_ids: tuple[str, ...] = ()
    result: dict[str, Any] | None = None
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class BackgroundSubmissionReservation:
    """Immutable identity and context binding for one detached worker."""

    run_id: str
    owner: str
    agent: str
    request_info: RunRequestInfo
    revision: int = 0


def _safe_request_info(request_info: RunRequestInfo) -> RunRequestInfo:
    """Keep only correlation and routing metadata in a reserved run."""
    return RunRequestInfo(
        dialogue_id=request_info.dialogue_id,
        request_id=request_info.request_id,
        tool_name=request_info.tool_name,
        model=request_info.model,
        locale=request_info.locale,
        a2a=request_info.a2a,
    )


def reserve_background_submission(
    *,
    agent: str,
    owner: str,
    request_info: RunRequestInfo,
    db_path: str,
) -> BackgroundSubmissionReservation:
    """Persist one fresh running umbrella before background work starts."""
    safe_request_info = _safe_request_info(request_info)
    try:
        registry = RunRegistry(db_path)
    except (sqlite3.Error, OSError) as exc:
        raise BackgroundSubmissionLaunchError(
            "unable to reserve background run"
        ) from exc
    for _attempt in range(_RUN_ID_ATTEMPTS):
        run_id = IdFactory().new_id("run", agent)
        try:
            registry.reserve_run(
                RunSpec(
                    run_id=run_id,
                    user_id=owner,
                    agent=agent,
                    origin="remote",
                ),
                request_info=safe_request_info,
                result=empty_execution_projection(
                    result_archive_required=agent in RESULT_DELIVERY_AGENTS
                ),
            )
        except sqlite3.IntegrityError:
            continue
        except (sqlite3.Error, OSError) as exc:
            raise BackgroundSubmissionLaunchError(
                "unable to reserve background run"
            ) from exc
        return BackgroundSubmissionReservation(
            run_id=run_id,
            owner=owner,
            agent=agent,
            request_info=safe_request_info,
        )
    raise BackgroundSubmissionLaunchError("unable to reserve background run")


_TERMINAL_WORKER_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def failed_child_ids(*, owner: str, db_path: str) -> tuple[str, ...]:
    """Return doomed child ids already recorded on the reserved umbrella."""
    run_id = current_run_id()
    if not isinstance(run_id, str) or not run_id:
        return ()
    record = RunRegistry(db_path).get_run(run_id, owner=owner)
    if record is None:
        return ()
    return failed_child_ids_from_result(record.result)


def _is_terminal_worker(record: object) -> bool:
    """Return whether a stored run is already past the worker window."""
    return getattr(record, "status", None) in _TERMINAL_WORKER_STATUSES


def _settle_failed(
    db_path: str,
    reservation: BackgroundSubmissionReservation,
    *,
    error: str,
    result: dict[str, Any] | None = None,
) -> bool:
    """Best-effort safe settlement that cannot leak a worker exception."""
    try:
        registry = RunRegistry(db_path)
        return registry.fail_running_run(
            reservation.run_id,
            owner=reservation.owner,
            result=result or empty_execution_projection(degraded=True),
            error=error,
            expected_revision=reservation.revision,
        )
    except BACKGROUND_RUNTIME_ERRORS as exc:
        _LOGGER.error(
            "Background submission settlement failed",
            extra={
                "run_id": reservation.run_id,
                "agent": reservation.agent,
                "stage": "settle_failed",
                "error_type": type(exc).__name__,
            },
        )
        return False


def _settle_cancelled(
    db_path: str,
    reservation: BackgroundSubmissionReservation,
    *,
    result: dict[str, Any] | None = None,
) -> bool:
    """Settle an owned worker as cancelled, not failed."""
    try:
        registry = RunRegistry(db_path)
        return registry.settle_run(
            reservation.run_id,
            owner=reservation.owner,
            status="cancelled",
            result=result or empty_execution_projection(degraded=True),
            error="background_submission_cancelled",
            expected_revision=reservation.revision,
        )
    except BACKGROUND_RUNTIME_ERRORS as exc:
        _LOGGER.error(
            "Background submission settlement failed",
            extra={
                "run_id": reservation.run_id,
                "agent": reservation.agent,
                "stage": "settle_cancelled",
                "error_type": type(exc).__name__,
            },
        )
        return False


def _apply_background_outcome(
    reservation: BackgroundSubmissionReservation,
    outcome: BackgroundSubmissionOutcome,
    *,
    db_path: str,
) -> None:
    """Settle or persist one detached worker outcome onto the umbrella."""
    if outcome.degraded:
        degraded_result = empty_execution_projection(degraded=True)
        degraded_result["execution"]["tasks"] = [
            {
                "id": task_id,
                "accepted": True,
                "status": "submitted",
            }
            for task_id in outcome.accepted_task_ids
        ]
        _settle_failed(
            db_path,
            reservation,
            error="background_submission_tracking_failed",
            result=degraded_result,
        )
        return
    if not outcome.accepted_task_ids and not outcome.failed_task_ids:
        raise BackgroundSubmissionExecutionError("no accepted child tasks")
    registry = RunRegistry(db_path)
    current = registry.get_run(reservation.run_id, owner=reservation.owner)
    projection = outcome.result or empty_execution_projection()
    if current is not None:
        projection = carry_execution_tasks(current.result, projection)
    if not outcome.accepted_task_ids:
        _settle_failed(
            db_path,
            reservation,
            error="background_submission_children_failed",
            result=projection,
        )
        return
    execution = projection.get("execution")
    if not isinstance(execution, dict):
        execution = {}
        projection["execution"] = execution
    if execution.get("warnings"):
        execution["tracking"] = {"degraded": True}
    if _is_terminal_worker(current):
        return
    if current is None or not set(outcome.accepted_task_ids).intersection(
        current.task_ids
    ):
        raise BackgroundSubmissionExecutionError(
            "accepted child tasks are not queryable"
        )
    if registry.update_running_result(
        reservation.run_id,
        owner=reservation.owner,
        result=projection,
    ):
        return
    current = registry.get_run(reservation.run_id, owner=reservation.owner)
    if _is_terminal_worker(current):
        return
    raise BackgroundSubmissionExecutionError(
        "unable to update running projection"
    )


async def _run_background_submission(
    reservation: BackgroundSubmissionReservation,
    operation: Callable[[], Awaitable[BackgroundSubmissionOutcome]],
    *,
    db_path: str,
) -> None:
    try:
        RunRegistry(db_path)
        with request_context(
            reservation.owner,
            reservation.request_info.request_id,
            reservation.run_id,
            locale=reservation.request_info.locale,
        ):
            outcome = await operation()
            _apply_background_outcome(reservation, outcome, db_path=db_path)
    except asyncio.CancelledError:
        _settle_cancelled(db_path, reservation)
        raise
    except BACKGROUND_RUNTIME_ERRORS as exc:
        _LOGGER.error(
            "Background submission failed",
            extra={
                "run_id": reservation.run_id,
                "agent": reservation.agent,
                "stage": "worker",
                "error_type": type(exc).__name__,
            },
        )
        _settle_failed(
            db_path,
            reservation,
            error="background_submission_failed",
        )
    finally:
        deregister_live_task(reservation.run_id)


def _observe_background_task(
    task: asyncio.Task[None],
    *,
    reservation: BackgroundSubmissionReservation,
    db_path: str,
) -> None:
    """Retrieve one task result and contain an unexpected escaped failure."""
    if task.cancelled():
        _settle_cancelled(db_path, reservation)
        return
    escaped = task.exception()
    if escaped is None:
        return
    _LOGGER.error(
        "Background submission task escaped its worker boundary",
        extra={
            "run_id": reservation.run_id,
            "agent": reservation.agent,
            "stage": "done_callback",
            "error_type": type(escaped).__name__,
        },
    )
    _settle_failed(
        db_path,
        reservation,
        error="background_submission_failed",
    )


def launch_background_submission(
    reservation: BackgroundSubmissionReservation,
    operation: Callable[[], Awaitable[BackgroundSubmissionOutcome]],
    *,
    db_path: str,
) -> None:
    """Launch and register exactly one worker for a reserved umbrella."""
    if is_live_running(reservation.run_id):
        raise BackgroundSubmissionLaunchError(
            "background submission worker is already active"
        )
    coroutine = _run_background_submission(
        reservation,
        operation,
        db_path=db_path,
    )
    try:
        task = asyncio.create_task(
            coroutine,
            name=(
                "background-submission:"
                f"{reservation.agent}:{reservation.run_id}"
            ),
        )
    except Exception as exc:
        coroutine.close()
        _settle_failed(
            db_path,
            reservation,
            error="background_submission_launch_failed",
        )
        raise BackgroundSubmissionLaunchError(
            "unable to launch background submission"
        ) from exc

    def observe(completed: asyncio.Task[None]) -> None:
        _observe_background_task(
            completed,
            reservation=reservation,
            db_path=db_path,
        )

    task.add_done_callback(observe)
    try:
        register_live_task(reservation.run_id, task)
    except Exception as exc:
        task.cancel()
        deregister_live_task(reservation.run_id)
        _settle_failed(
            db_path,
            reservation,
            error="background_submission_launch_failed",
        )
        raise BackgroundSubmissionLaunchError(
            "unable to launch background submission"
        ) from exc
