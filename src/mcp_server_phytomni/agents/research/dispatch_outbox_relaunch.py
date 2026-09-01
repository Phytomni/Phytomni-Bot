# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Memory-class relaunch and restart recovery for Research outbox rows."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, cast

from ...runtime.sqlite import sqlite_transaction
from ..shared.memory_relaunch import (
    START_COMPUTE_RESOURCE,
    is_memory_class_failure,
    next_compute_resource,
)
from . import dispatch_outbox_storage as _storage
from .input_contracts import ResearchErrorCode

__all__ = [
    "recover_dispatch_outbox",
    "relaunch_memory_exhausted",
]

_TRACKING_FAILED: ResearchErrorCode = "research_run_tracking_failed"
_UNAVAILABLE: ResearchErrorCode = "research_input_resolution_unavailable"
_OUTBOX_FAILURES = (Exception,)
_CANCEL_OPTIONS = ("cancelled", _UNAVAILABLE, ("pending", "leased"))


def _host() -> Any:
    """Load the outbox module after it has finished initializing."""
    return import_module("mcp_server_phytomni.agents.research.dispatch_outbox")


async def relaunch_memory_exhausted(
    outbox: Any,
    dispatch_id: str,
    status_payload: object,
    log_payload: object = None,
    now: datetime | None = None,
) -> Any:
    """Resubmit one accepted child one compute tier higher.

    Keeps the same ``dispatch_id``, discards the failed EI id, and
    CAS-writes the bumped payload plus the new remote task id.
    ``dispatch_once`` / ``reconcile_once`` never take this path.
    """
    timestamp = now or outbox.options.now()
    try:
        record = outbox.load(dispatch_id)
    except KeyError:
        return _ambiguous(dispatch_id)
    blocked = _relaunch_blocked(
        outbox,
        dispatch_id,
        record,
        (status_payload, log_payload),
        timestamp,
    )
    if blocked is not None:
        return blocked
    submit_record = _relaunch_submit_record(record)
    task_id = await _submit_relaunch(outbox, submit_record)
    if not task_id:
        return _existing_disposition(dispatch_id, record)
    accepted = _cas_relaunch(outbox.store, submit_record, task_id, timestamp)
    if accepted is None:
        if not _storage.parent_run_is_live(
            outbox.store.db_path, record.run_id
        ):
            return _cancelled(dispatch_id)
        return _ambiguous(dispatch_id)
    return accepted


async def recover_dispatch_outbox(
    outbox: Any,
    now: datetime,
    limit: int,
    lease_owner: str,
) -> tuple[Any, ...]:
    """Reconcile a bounded pending/expired outbox set after a restart."""
    if limit <= 0:
        return ()
    now_iso = _iso(now)
    with sqlite_transaction(outbox.store.db_path) as connection:
        rows = connection.execute(
            "SELECT outbox_id FROM research_dispatch_outbox "
            "WHERE state='pending' "
            "OR (state IN ('leased','sent') AND "
            "lease_expires_at IS NOT NULL AND "
            "lease_expires_at <= ?) ORDER BY updated_at, outbox_id LIMIT ?",
            (now_iso, limit),
        ).fetchall()
    outcomes: list[Any] = []
    for row in rows:
        dispatch_id = cast(str, row[0])
        try:
            current = outbox.load(dispatch_id)
        except KeyError:
            continue
        if current.state == "sent":
            disposition = await outbox.reconcile_once(
                dispatch_id, lease_owner, now
            )
        else:
            disposition = await outbox.dispatch_once(
                dispatch_id, lease_owner, now
            )
        outcomes.append(disposition.state)
    return tuple(outcomes)


async def _submit_relaunch(outbox: Any, record: Any) -> str | None:
    """Submit one bumped child and return the new remote task id."""
    if outbox.options.submit is None:
        return None
    try:
        response = await _maybe_await(outbox.options.submit(record))
    except _OUTBOX_FAILURES:
        return None
    return _storage.task_id(response)


def _payload_generation(payload: Mapping[str, Any]) -> int:
    """Read a persisted generation, defaulting missing values to 0."""
    raw = payload.get("compute_resource_generation") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _relaunch_blocked(
    outbox: Any,
    dispatch_id: str,
    record: Any,
    failure: tuple[object, object],
    now: datetime,
) -> Any:
    """Return an abort disposition when a relaunch must not submit."""
    status_payload, log_payload = failure
    store = outbox.store
    if not _storage.parent_run_is_live(store.db_path, record.run_id):
        _storage.mark_row(store.db_path, record, _iso(now), _CANCEL_OPTIONS)
        return _cancelled(dispatch_id)
    payload = record.payload
    current = str(payload.get("compute_resource") or START_COMPUTE_RESOURCE)
    if next_compute_resource(current) is None:
        return _existing_disposition(dispatch_id, record)
    if not is_memory_class_failure(status_payload, log_payload):
        return _existing_disposition(dispatch_id, record)
    if _payload_generation(payload) >= 2:
        return _existing_disposition(dispatch_id, record)
    return None


def _relaunch_submit_record(record: Any) -> Any:
    """Copy one child payload and bump compute resource by one tier."""
    payload = dict(record.payload)
    current = str(payload.get("compute_resource") or START_COMPUTE_RESOURCE)
    nxt = next_compute_resource(current)
    if nxt is None:
        return record
    payload["compute_resource"] = nxt
    payload["compute_resource_generation"] = _payload_generation(payload) + 1
    return replace(record, payload=payload)


def _cas_relaunch(
    store: Any,
    record: Any,
    task_id: str,
    now: datetime,
) -> Any:
    """Persist the bumped payload and new remote id on the same row."""
    if not _storage.relaunch_row(
        _storage.RelaunchRequest(
            _storage.SqlContext(
                store.db_path,
                _storage.OUTBOX_ROW_SELECT,
                _storage.parent_live_predicate(),
            ),
            record,
            task_id,
            _iso(now),
        )
    ):
        return None
    return _disposition(record.dispatch_id, "accepted", task_id)


def _existing_disposition(dispatch_id: str, record: Any) -> Any:
    """Return the current non-failed disposition without submitting."""
    if record.state == "accepted":
        return _disposition(dispatch_id, "accepted", record.remote_task_id)
    if record.state == "ambiguous":
        return _ambiguous(dispatch_id)
    if record.state == "cancelled":
        return _cancelled(dispatch_id)
    return _disposition(dispatch_id, "accepted", record.remote_task_id)


def _disposition(
    dispatch_id: str,
    state: str,
    task_id: str | None,
    failure_code: ResearchErrorCode | None = None,
) -> Any:
    return _host().ResearchDispatchDisposition(
        dispatch_id, state, task_id, failure_code
    )


def _ambiguous(dispatch_id: str) -> Any:
    return _disposition(dispatch_id, "ambiguous", None, _TRACKING_FAILED)


def _cancelled(dispatch_id: str) -> Any:
    return _disposition(dispatch_id, "cancelled", None, _UNAVAILABLE)


def _iso(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return normalized.astimezone(UTC).isoformat()


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await cast(Awaitable[object], value)
    return value
