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

from ..storage.path_policy import IdFactory
from .execution_defaults import empty_execution_projection
from .live_tasks import (
    deregister_live_task,
    is_live_running,
    register_live_task,
)
from .request_context import request_context
from .run_registry import RunRegistry, RunRequestInfo, RunSpec

_LOGGER = logging.getLogger(__name__)
_RUN_ID_ATTEMPTS = 3

__all__ = [
    "BackgroundSubmissionLaunchError",
    "BackgroundSubmissionReservation",
    "launch_background_submission",
    "reserve_background_submission",
]


class BackgroundSubmissionLaunchError(RuntimeError):
    """Raised when a reserved worker cannot be launched."""


@dataclass(frozen=True, slots=True)
class BackgroundSubmissionReservation:
    """Immutable identity and context binding for one detached worker."""

    run_id: str
    owner: str
    agent: str
    request_info: RunRequestInfo


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
                result=empty_execution_projection(),
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


def _settle_failed(
    db_path: str,
    reservation: BackgroundSubmissionReservation,
    *,
    error: str,
) -> None:
    """Best-effort safe settlement that cannot leak a worker exception."""
    try:
        registry = RunRegistry(db_path)
        registry.fail_running_run(
            reservation.run_id,
            owner=reservation.owner,
            result=empty_execution_projection(degraded=True),
            error=error,
        )
    except Exception as exc:
        _LOGGER.error(
            "Background submission settlement failed",
            extra={
                "run_id": reservation.run_id,
                "agent": reservation.agent,
                "error_type": type(exc).__name__,
            },
        )


async def _run_background_submission(
    reservation: BackgroundSubmissionReservation,
    operation: Callable[[], Awaitable[None]],
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
            await operation()
    except asyncio.CancelledError:
        _settle_failed(
            db_path,
            reservation,
            error="background_submission_cancelled",
        )
        raise
    except Exception as exc:
        _LOGGER.error(
            "Background submission failed",
            extra={
                "run_id": reservation.run_id,
                "agent": reservation.agent,
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


def launch_background_submission(
    reservation: BackgroundSubmissionReservation,
    operation: Callable[[], Awaitable[None]],
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
    register_live_task(reservation.run_id, task)
