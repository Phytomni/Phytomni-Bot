# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable Research work-unit binding and subdivision storage helpers."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlite3 import Row
from typing import TYPE_CHECKING, Any

from .research_input_types import (
    ResearchAdmissionReservation,
    ResearchWorkUnitRecord,
)
from .sqlite import sqlite_transaction
from .task_manager import _expires_at_for

if TYPE_CHECKING:
    from .research_input_store import ResearchInputStore

_VALIDATED_OUTPUT_SQL = (
    "SELECT output_json FROM research_work_units WHERE unit_id = ? AND "
    "state = 'succeeded' AND input_digest = ? AND policy_digest = ? AND "
    "execution_fingerprint = ? AND evidence_digest = ?"
)


@dataclass(frozen=True, slots=True)
class AdmissionLaunchFailure:
    """Safe terminal classification for one committed root admission."""

    code: str
    retryable: bool
    status_hint: int
    stage: str


class _ResearchInputStoreBindings:
    """Mixin exposing the durable resolver binding operations."""

    def load_validated_output(
        self: Any,
        unit_id: str,
        *bindings: str,
        **named: str,
    ) -> dict[str, Any] | None:
        """Load output only when all four durable bindings match."""
        return load_validated_output(self, unit_id, *bindings, **named)

    def replace_work_unit_with_children(
        self: Any,
        record: ResearchWorkUnitRecord,
        children: Sequence[ResearchWorkUnitRecord],
        now: datetime,
    ) -> bool:
        """Atomically close a sent unit and enqueue verified children."""
        return replace_work_unit_with_children(self, record, children, now)

    def mark_admission_launch_failed(
        self: Any,
        run_id: str,
        failure: AdmissionLaunchFailure,
    ) -> bool:
        """Atomically expose a post-commit root-launch failure."""
        return mark_admission_launch_failed(self, run_id, failure)

    def retry_admission(
        self: Any,
        run_id: str,
        owner: str,
        identity_digest: str,
        client_fingerprint: str,
    ) -> ResearchAdmissionReservation | None:
        """Atomically reclaim one retryable idempotent admission."""
        return retry_admission(
            self, run_id, owner, identity_digest, client_fingerprint
        )

    def retry_admission_available(
        self: Any,
        run_id: str,
        owner: str,
        identity_digest: str,
        client_fingerprint: str,
    ) -> bool:
        """Check retry eligibility without changing durable state."""
        return retry_admission_available(
            self, run_id, owner, identity_digest, client_fingerprint
        )


def mark_admission_launch_failed(
    store: ResearchInputStore,
    run_id: str,
    failure: AdmissionLaunchFailure,
) -> bool:
    """Set the parent, resolution, and root work to classified failure."""
    if (
        not run_id
        or not isinstance(failure, AdmissionLaunchFailure)
        or not failure.code
        or not isinstance(failure.status_hint, int)
        or not failure.stage
    ):
        return False
    now = _utc_iso(datetime.now(UTC))
    retry = int(failure.retryable)
    failure_json = json.dumps(
        {
            "code": failure.code,
            "http_status_hint": failure.status_hint,
            "retryable": failure.retryable,
            "stage": failure.stage,
        },
        sort_keys=True,
    )
    root_state = "retryable_failed" if failure.retryable else "terminal_failed"
    with sqlite_transaction(store.db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        parent = connection.execute(
            "UPDATE runs SET status = 'failed', error = ?, stage = NULL, "
            "failure_json = ?, revision = revision + 1, updated_at = ?, "
            "expires_at = ? WHERE run_id = ? AND status = 'running'",
            (
                failure.code,
                failure_json,
                now,
                _expires_at_for("failed", now),
                run_id,
            ),
        )
        if parent.rowcount != 1:
            return False
        resolution = connection.execute(
            "UPDATE research_input_resolutions SET status = 'failed', "
            "last_stage = ?, failure_code = ?, "
            "failure_retryable = ?, "
            "status_hint = ?, updated_at = ?, revision = revision + 1 "
            "WHERE run_id = ? AND COALESCE(cancel_requested, 0) = 0",
            (
                failure.stage,
                failure.code,
                retry,
                failure.status_hint,
                now,
                run_id,
            ),
        )
        root = connection.execute(
            "UPDATE research_work_units SET state = ?, lease_owner = NULL, "
            "lease_expires_at = CASE WHEN ? = 1 THEN ? ELSE NULL END, "
            "failure_code = ?, "
            "failure_retryable = ?, updated_at = ?, revision = revision + 1 "
            "WHERE run_id = ? AND kind = 'resolve_root' AND state = 'pending'",
            (root_state, retry, now, failure.code, retry, now, run_id),
        )
        if resolution.rowcount != 1 or root.rowcount != 1:
            connection.rollback()
            return False
    return True


def retry_admission(
    store: ResearchInputStore,
    run_id: str,
    owner: str,
    identity_digest: str,
    client_fingerprint: str,
) -> ResearchAdmissionReservation | None:
    """CAS one matching retryable failed root back to pending ownership."""
    now = _utc_iso(datetime.now(UTC))
    with sqlite_transaction(store.db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not _retryable_admission(
            connection, run_id, owner, identity_digest, client_fingerprint
        ):
            return None
        parent = connection.execute(
            "UPDATE runs SET status = 'running', error = NULL, "
            "stage = 'input_resolution', failure_json = NULL, "
            "expires_at = NULL, revision = revision + 1, updated_at = ? "
            "WHERE run_id = ? AND status = 'failed'",
            (now, run_id),
        )
        resolution = connection.execute(
            "UPDATE research_input_resolutions SET status = 'pending', "
            "last_stage = NULL, failure_code = NULL, "
            "failure_retryable = NULL, "
            "status_hint = NULL, updated_at = ?, revision = revision + 1 "
            "WHERE run_id = ? AND status = 'failed' AND failure_retryable = 1",
            (now, run_id),
        )
        root = connection.execute(
            "UPDATE research_work_units SET state = 'pending', "
            "lease_owner = NULL, lease_expires_at = NULL, "
            "failure_code = NULL, failure_retryable = NULL, "
            "updated_at = ?, revision = revision + 1 "
            "WHERE run_id = ? AND kind = 'resolve_root' AND "
            "state = 'retryable_failed' AND failure_retryable = 1",
            (now, run_id),
        )
        if (
            parent.rowcount != 1
            or resolution.rowcount != 1
            or root.rowcount != 1
        ):
            connection.rollback()
            return None
    return ResearchAdmissionReservation(run_id, False, "running")


def retry_admission_available(
    store: ResearchInputStore,
    run_id: str,
    owner: str,
    identity_digest: str,
    client_fingerprint: str,
) -> bool:
    """Read retry eligibility before request validation does more I/O."""
    with sqlite_transaction(store.db_path) as connection:
        return _retryable_admission(
            connection, run_id, owner, identity_digest, client_fingerprint
        )


def _retryable_admission(
    connection: Any,
    run_id: str,
    owner: str,
    identity_digest: str,
    client_fingerprint: str,
) -> bool:
    row = connection.execute(
        "SELECT runs.run_id FROM runs JOIN research_idempotency_bindings "
        "ON runs.run_id = research_idempotency_bindings.run_id JOIN "
        "research_input_resolutions ON runs.run_id = "
        "research_input_resolutions.run_id WHERE runs.run_id = ? AND "
        "runs.status = 'failed' AND research_idempotency_bindings.owner = ? "
        "AND research_idempotency_bindings.operation = ? AND "
        "research_idempotency_bindings.idempotency_digest = ? AND "
        "research_idempotency_bindings.client_fingerprint = ? AND "
        "research_input_resolutions.failure_retryable = 1",
        (
            run_id,
            owner,
            "research_input_resolution_v1",
            identity_digest,
            client_fingerprint,
        ),
    ).fetchone()
    return row is not None


def load_validated_output(
    store: ResearchInputStore,
    unit_id: str,
    *bindings: str,
    **named: str,
) -> dict[str, Any] | None:
    """Load output only when all four durable resolver bindings match."""
    names = (
        "input_digest",
        "policy_digest",
        "execution_fingerprint",
        "evidence_digest",
    )
    if named:
        if bindings or set(named) != set(names):
            raise TypeError("four resolver output bindings are required")
        bindings = tuple(named[name] for name in names)
    if len(bindings) != 4:
        raise TypeError("four resolver output bindings are required")
    with sqlite_transaction(store.db_path) as connection:
        row = connection.execute(
            _VALIDATED_OUTPUT_SQL, (unit_id, *bindings)
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    try:
        output = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return output if isinstance(output, dict) else None


def replace_work_unit_with_children(
    store: ResearchInputStore,
    record: ResearchWorkUnitRecord,
    children: Sequence[ResearchWorkUnitRecord],
    now: datetime,
) -> bool:
    """Atomically close a sent unit and enqueue verified child units."""
    child_rows = tuple(children)
    if not child_rows or any(
        child.run_id != record.run_id
        or child.state != "pending"
        or child.unit_id == record.unit_id
        for child in child_rows
    ):
        return False
    store_module = sys.modules[type(store).__module__]
    now_iso = _utc_iso(now)
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            getattr(store_module, "_WORK_SELECT"), (record.unit_id,)
        ).fetchone()
        if (
            row is None
            or row["state"] != "sent"
            or row["lease_owner"] != record.lease_owner
            or row["revision"] != record.revision
        ):
            return False
        for child in child_rows:
            inserted = connection.execute(
                getattr(store_module, "_WORK_INSERT_SQL"),
                getattr(store_module, "_work_insert_values")(child, now_iso),
            )
            if inserted.rowcount != 1:
                raise sqlite3.IntegrityError("research child collision")
        closed = connection.execute(
            "UPDATE research_work_units SET state = 'cancelled', "
            "lease_owner = NULL, lease_expires_at = NULL, "
            "failure_code = 'research_input_resolution_unavailable', "
            "failure_retryable = 0, updated_at = ?, completed_at = ?, "
            "revision = revision + 1 WHERE unit_id = ? AND "
            "lease_owner = ? AND revision = ? AND state = 'sent' AND "
            + getattr(store_module, "_PARENT_LIVE"),
            (now_iso,) * 2
            + (record.unit_id, record.lease_owner, record.revision),
        )
        if closed.rowcount != 1:
            raise sqlite3.IntegrityError("research parent changed")
    return True


def _utc_iso(value: datetime) -> str:
    """Format a recovery timestamp consistently with the store."""
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return value.astimezone(UTC).isoformat()
