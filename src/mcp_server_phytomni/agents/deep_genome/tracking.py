# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable transition coordination for DeepGenome remote work items.

The sink is the only agent-layer adapter that turns remote observations into
owner-scoped SQLite transitions.  Polling remains in :mod:`coordinator`; this
module only binds the existing store, applies sanitized transitions, and
settles tracking failures.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...runtime.deep_genome_store import (
    DeepGenomeStore,
    DeepGenomeTrackingError,
    DeepGenomeTransitionError,
    RemoteSubmission,
)
from ...runtime.task_manager import resolve_tasks_db_path
from .coordinator import WorkItemOutcome

logger = logging.getLogger(__name__)

_TRACKING_ERRORS: tuple[type[Exception], ...] = (
    DeepGenomeTrackingError,
    DeepGenomeTransitionError,
    sqlite3.Error,
    OSError,
)
_BEST_EFFORT_ERRORS: tuple[type[Exception], ...] = (Exception,)

CancelSubmission = Callable[[RemoteSubmission], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DeepGenomeTransitionSink:
    """Persist transitions for one owner-scoped DeepGenome task.

    A sink with no store or umbrella ID is deliberately a no-op.  Anonymous
    or legacy graph invocations still receive the coordinator's in-memory
    outcomes, while tracked HTTP runs share one sink across every branch.
    """

    store: DeepGenomeStore | None
    umbrella_task_id: str | None
    cancel_submission: CancelSubmission | None = None

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, Any] | None,
        *,
        store_path: str | None = None,
        cancel_submission: CancelSubmission | None = None,
    ) -> DeepGenomeTransitionSink:
        """Build a sink from graph state without opening a store for guests."""
        task_id = state.get("task_id") if state is not None else None
        if not isinstance(task_id, str) or not task_id.strip():
            return cls(None, None, cancel_submission)
        return cls(
            DeepGenomeStore(store_path or resolve_tasks_db_path()),
            task_id,
            cancel_submission,
        )

    async def fail_tracking(
        self,
        submission: RemoteSubmission | None,
        cause: Exception,
    ) -> None:
        """Best-effort cancel accepted work and fail the owner task."""
        if submission is not None and self.cancel_submission is not None:
            try:
                await self.cancel_submission(submission)
            except _BEST_EFFORT_ERRORS as exc:
                logger.warning(
                    "DeepGenome cancellation unavailable; error_type=%s",
                    type(exc).__name__,
                )
        if self.store is not None and self.umbrella_task_id is not None:
            try:
                self.store.fail_umbrella(
                    self.umbrella_task_id,
                    reason="remote analysis tracking failed",
                )
            except _TRACKING_ERRORS as exc:
                logger.warning(
                    "DeepGenome tracking failure settlement unavailable; "
                    "error_type=%s",
                    type(exc).__name__,
                )
        raise DeepGenomeTrackingError(
            "remote analysis tracking failed"
        ) from cause

    async def accept_remote_submission(
        self,
        work_item_key: str,
        submission: RemoteSubmission,
    ) -> None:
        """Persist caller/effective identities before polling starts."""
        if self.store is None or self.umbrella_task_id is None:
            return
        try:
            self.store.accept_remote_submission(
                self.umbrella_task_id,
                work_item_key=work_item_key,
                submission=submission,
            )
        except _TRACKING_ERRORS as exc:
            await self.fail_tracking(submission, exc)

    async def persist_work_item_transition(
        self,
        work_item_key: str,
        submission: RemoteSubmission,
        status: str,
        summary: str | None,
        failure_reason: str | None,
    ) -> WorkItemOutcome:
        """Persist one poll observation and return its local outcome."""
        if self.store is None or self.umbrella_task_id is None:
            return WorkItemOutcome(status, summary, failure_reason)
        try:
            self.store.apply_work_item_transition(
                self.umbrella_task_id,
                work_item_key=work_item_key,
                status=status,
                summary_markdown=summary,
                failure_reason=failure_reason,
            )
        except _TRACKING_ERRORS as exc:
            await self.fail_tracking(submission, exc)
        return WorkItemOutcome(status, summary, failure_reason)

    async def record_work_item_failure(self, work_item_key: str) -> None:
        """Persist an item that failed before remote acceptance."""
        if self.store is None or self.umbrella_task_id is None:
            return
        try:
            self.store.apply_work_item_transition(
                self.umbrella_task_id,
                work_item_key=work_item_key,
                status="failed",
                failure_reason="analysis task failed",
            )
        except _TRACKING_ERRORS as exc:
            await self.fail_tracking(None, exc)

    async def record_mount_failure(
        self,
        work_item_keys: tuple[str, ...],
    ) -> None:
        """Persist every concrete item when a mounted producer fails."""
        for work_item_key in work_item_keys:
            await self.record_work_item_failure(work_item_key)
