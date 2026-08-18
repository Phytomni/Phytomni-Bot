# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable Research work-unit binding and subdivision storage helpers."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from sqlite3 import Row
from typing import TYPE_CHECKING, Any, Literal, cast

from .research_input_types import (
    ResearchAdmissionReservation,
    ResearchWorkUnitRecord,
)
from .sqlite import sqlite_transaction
from .task_manager import _expires_at_for

if TYPE_CHECKING:
    from .research_input_store import ResearchInputStore


_GRANT_REVOKE_COLUMNS = (
    ("run_id", "TEXT NOT NULL"),
    ("authority_parent_run_id", "TEXT NOT NULL DEFAULT ''"),
    ("execution_fingerprint", "TEXT NOT NULL"),
    ("grant_ids_json", "TEXT NOT NULL"),
    ("state", "TEXT NOT NULL DEFAULT 'pending'"),
    ("schema_version", "INTEGER NOT NULL DEFAULT 1"),
    ("created_at", "TEXT NOT NULL DEFAULT ''"),
    ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    ("revoked_at", "TEXT"),
)
_GRANT_REVOKE_CONSTRAINTS = (
    ",PRIMARY KEY(run_id,execution_fingerprint),FOREIGN KEY(run_id)"
    " REFERENCES runs(run_id)"
)
RESEARCH_GRANT_REVOKE_TABLE = (
    "research_grant_revocations",
    _GRANT_REVOKE_COLUMNS,
    _GRANT_REVOKE_CONSTRAINTS,
)


def _split_words(value: str, separator: str | None = None) -> tuple[str, ...]:
    return tuple(value.split(separator))


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


class ResearchCancellationNotFoundError(LookupError):
    """Raised when an owner cannot see the requested run."""


ResearchCancellationNotFound = ResearchCancellationNotFoundError


class ResearchCancellationUnsupportedError(ValueError):
    """Raised when cancellation is requested for a non-Research run."""


ResearchCancellationUnsupported = ResearchCancellationUnsupportedError


class ResearchCancellationConflictError(ValueError):
    """Raised when cancellation crossed a non-reversible boundary."""

    code = "research_cancel_conflict"


ResearchCancellationConflict = ResearchCancellationConflictError


@dataclass(frozen=True, slots=True)
class ResearchCancellationOutcome:
    """Durable, owner-scoped cancellation result."""

    run_id: str
    status: Literal["cancelled"]
    replay: bool
    revision: int


@dataclass(frozen=True, slots=True)
class ResearchGrantRevocation:
    """Exact durable authority binding awaiting terminal cleanup."""

    owner_run_id: str
    parent_run_id: str
    execution_fingerprint: str
    grant_ids: tuple[str, ...]


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

    def cancel_research_run(
        self,
        run_id: str,
        owner: str,
        expected_revision: int,
    ) -> ResearchCancellationOutcome:
        """Cancel one Research parent before a remote send boundary."""
        return cancel_research_run(self, run_id, owner, expected_revision)

    def is_cancel_requested(self, run_id: str) -> bool:
        """Return whether the durable cancellation barrier is set."""
        return is_cancel_requested(self, run_id)

    def pending_grant_revocations(
        self, limit: int = 32
    ) -> tuple[ResearchGrantRevocation, ...]:
        """Return a bounded opaque view of pending grant revocations."""
        return pending_grant_revocations(self, limit)

    def mark_grant_revoked(
        self,
        revocation: ResearchGrantRevocation,
        now: datetime | None = None,
    ) -> int:
        """Complete a pending grant revoke with a private CAS update."""
        return mark_grant_revoked(self, revocation, now)


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
            "WHERE run_id = ? AND kind = 'resolve_root' "
            "AND state IN ('pending', 'leased')",
            (root_state, retry, now, failure.code, retry, now, run_id),
        )
        if resolution.rowcount != 1 or root.rowcount != 1:
            connection.rollback()
            return False
        queue_grants(connection, run_id, now)
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


def is_cancel_requested(store: Any, run_id: str) -> bool:
    """Read the durable barrier used to discard late callback results."""
    with sqlite_transaction(store.db_path) as connection:
        row = connection.execute(
            "SELECT status, EXISTS (SELECT 1 FROM "
            "research_input_resolutions AS resolution WHERE "
            "resolution.run_id = runs.run_id AND "
            "COALESCE(resolution.cancel_requested, 0) <> 0) "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return row is not None and (row[0] == "cancelled" or bool(row[1]))


def _grant_ids(value: object) -> tuple[str, ...]:
    """Decode only bounded opaque grant identifiers from private JSON."""
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(item for item in decoded if isinstance(item, str) and item)


def queue_grants(
    connection: Any, run_id: str, now: str, changed: int = 1
) -> None:
    """Persist exact authority bindings, never operator-private grant state."""
    if changed <= 0:
        return
    private_schema = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'research_input_resolutions'"
    ).fetchone()
    if private_schema is None:
        return
    resolution = connection.execute(
        "SELECT resolution.final_projection_json FROM "
        "research_input_resolutions AS resolution JOIN runs "
        "ON resolution.run_id = runs.run_id WHERE resolution.run_id = ? "
        "AND runs.status IN ('succeeded', 'failed', 'cancelled')",
        (run_id,),
    ).fetchone()
    if resolution is None:
        return
    groups: dict[tuple[str, str], set[str]] = {}
    try:
        projection = json.loads(resolution[0] or "{}")
    except (TypeError, ValueError):
        projection = {}
    if isinstance(projection, Mapping):
        _append_grant_group(
            groups,
            projection.get("research_grant_binding"),
            projection.get("authority_ids"),
        )
    rows = connection.execute(
        "SELECT payload_json, grant_ids_json FROM research_dispatch_outbox "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, Mapping):
            _append_grant_group(
                groups,
                payload.get("research_grant_binding"),
                row[1],
            )
    for (authority_parent, fingerprint), grant_ids in groups.items():
        encoded = json.dumps(sorted(grant_ids), separators=(",", ":"))
        connection.execute(
            "INSERT INTO research_grant_revocations ("
            "run_id, authority_parent_run_id, execution_fingerprint, "
            "grant_ids_json, state, schema_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', 1, ?, ?) "
            "ON CONFLICT(run_id, execution_fingerprint) DO UPDATE SET "
            "authority_parent_run_id = excluded.authority_parent_run_id, "
            "grant_ids_json = excluded.grant_ids_json, state = 'pending', "
            "updated_at = excluded.updated_at, revoked_at = NULL",
            (run_id, authority_parent, fingerprint, encoded, now, now),
        )


def _append_grant_group(
    groups: dict[tuple[str, str], set[str]],
    raw_binding: object,
    raw_grant_ids: object,
) -> None:
    """Add one validated private binding and its opaque grant IDs."""
    if not isinstance(raw_binding, Mapping):
        return
    parent = raw_binding.get("parent_run_id")
    fingerprint = raw_binding.get("execution_fingerprint")
    grant_ids = _grant_ids(raw_grant_ids)
    if (
        not isinstance(parent, str)
        or not parent
        or not isinstance(fingerprint, str)
        or not fingerprint
        or not grant_ids
    ):
        return
    groups.setdefault((parent, fingerprint), set()).update(grant_ids)


def pending_grant_revocations(
    store: Any, limit: int = 32
) -> tuple[ResearchGrantRevocation, ...]:
    """Return bounded opaque grant groups awaiting revocation."""
    if limit <= 0:
        return ()
    with sqlite_transaction(store.db_path) as connection:
        rows = connection.execute(
            "SELECT run_id, authority_parent_run_id, "
            "execution_fingerprint, grant_ids_json "
            "FROM research_grant_revocations WHERE state = 'pending' "
            "ORDER BY run_id, execution_fingerprint LIMIT ?",
            (limit,),
        ).fetchall()
    return tuple(
        ResearchGrantRevocation(
            run_id,
            authority_parent,
            fingerprint,
            grant_ids,
        )
        for run_id, authority_parent, fingerprint, encoded in rows
        if isinstance(run_id, str)
        and run_id
        and isinstance(authority_parent, str)
        and authority_parent
        and isinstance(fingerprint, str)
        and fingerprint
        and (grant_ids := _grant_ids(encoded))
    )


def mark_grant_revoked(
    store: Any,
    revocation: ResearchGrantRevocation,
    now: datetime | None = None,
) -> int:
    """Finish a pending grant revoke with an idempotent private CAS."""
    if not revocation.grant_ids:
        return 0
    timestamp = _utc_iso(now or datetime.now(UTC))
    with sqlite_transaction(store.db_path) as connection:
        row = connection.execute(
            "SELECT grant_ids_json FROM research_grant_revocations WHERE "
            "run_id = ? AND authority_parent_run_id = ? AND "
            "execution_fingerprint = ? AND state = 'pending'",
            (
                revocation.owner_run_id,
                revocation.parent_run_id,
                revocation.execution_fingerprint,
            ),
        ).fetchone()
        if row is None or set(_grant_ids(row[0])) != set(revocation.grant_ids):
            return 0
        cursor = connection.execute(
            "UPDATE research_grant_revocations SET state = 'revoked', "
            "updated_at = ?, revoked_at = ? WHERE run_id = ? AND "
            "authority_parent_run_id = ? AND execution_fingerprint = ? "
            "AND state = 'pending'",
            (
                timestamp,
                timestamp,
                revocation.owner_run_id,
                revocation.parent_run_id,
                revocation.execution_fingerprint,
            ),
        )
    return cursor.rowcount


def _valid_cancel_arguments(
    run_id: object, owner: object, expected_revision: object
) -> bool:
    """Validate owner and CAS arguments without widening the error surface."""
    return (
        isinstance(run_id, str)
        and bool(run_id)
        and isinstance(owner, str)
        and bool(owner)
        and isinstance(expected_revision, int)
        and not isinstance(expected_revision, bool)
        and expected_revision >= 0
    )


def cancel_research_run(
    store: Any,
    run_id: str,
    owner: str,
    expected_revision: int,
) -> ResearchCancellationOutcome:
    """CAS-cancel a Research parent, including after a remote send."""
    if not _valid_cancel_arguments(run_id, owner, expected_revision):
        raise ResearchCancellationConflict()
    now = _utc_iso(datetime.now(UTC))
    expires_at = _expires_at_for("failed", now)
    with sqlite_transaction(store.db_path, timeout=30.0) as connection:
        connection.row_factory = Row
        connection.execute("BEGIN IMMEDIATE")
        parent = connection.execute(
            "SELECT status, agent, revision FROM runs "
            "WHERE run_id = ? AND user_id = ?",
            (run_id, owner),
        ).fetchone()
        if parent is None:
            raise ResearchCancellationNotFound(run_id)
        status, agent, current_revision = (
            cast(str, parent["status"]),
            cast(str, parent["agent"]),
            int(parent["revision"]),
        )
        if agent != "research":
            raise ResearchCancellationUnsupported(
                "run cancellation is supported only for Research"
            )
        if status == "cancelled":
            return ResearchCancellationOutcome(
                run_id, "cancelled", True, current_revision
            )
        if status != "running" or current_revision != expected_revision:
            raise ResearchCancellationConflict()
        connection.execute(
            "UPDATE research_input_resolutions SET cancel_requested = 1, "
            "status = 'cancelled', updated_at = ?, revision = revision + 1 "
            "WHERE run_id = ? AND COALESCE(cancel_requested, 0) = 0",
            (now, run_id),
        )
        connection.execute(
            "UPDATE research_work_units SET state = 'cancelled', "
            "lease_owner = NULL, lease_expires_at = NULL, completed_at = ?, "
            "updated_at = ?, revision = revision + 1 WHERE run_id = ? "
            "AND state IN ('pending', 'leased', 'retryable_failed', "
            "'sent', 'ambiguous')",
            (now, now, run_id),
        )
        connection.execute(
            "UPDATE research_work_units SET state = 'cancelled', "
            "lease_owner = NULL, lease_expires_at = NULL, completed_at = ?, "
            "updated_at = ?, revision = revision + 1 WHERE run_id = ? "
            "AND kind = 'dispatch' AND state = 'succeeded'",
            (now, now, run_id),
        )
        connection.execute(
            "UPDATE research_dispatch_outbox SET state = 'cancelled', "
            "lease_owner = NULL, lease_expires_at = NULL, completed_at = ?, "
            "updated_at = ?, revision = revision + 1 WHERE run_id = ? "
            "AND state IN ('pending', 'leased', 'sent', 'ambiguous', "
            "'accepted')",
            (now, now, run_id),
        )
        updated = connection.execute(
            "UPDATE runs SET status = 'cancelled', stage = NULL, "
            "error = NULL, "
            "expires_at = ?, updated_at = ?, revision = revision + 1 "
            "WHERE run_id = ? AND user_id = ? AND agent = 'research' "
            "AND status = 'running' AND revision = ?",
            (expires_at, now, run_id, owner, expected_revision),
        )
        if updated.rowcount != 1:
            raise ResearchCancellationConflict()
        queue_grants(connection, run_id, now)
    return ResearchCancellationOutcome(
        run_id, "cancelled", False, expected_revision + 1
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
