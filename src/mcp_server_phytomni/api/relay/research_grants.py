# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Operator-private, run-bound grants for Research input objects.

The store is intentionally separate from relay auditing.  It persists the
exact reference only in the private grant table and exposes one safe failure
for absent, expired, revoked, or incorrectly bound grants.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from ...runtime.sqlite import sqlite_connection
from ...storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectSnapshot,
)

__all__ = [
    "RESEARCH_GRANT_PURGE_GRACE",
    "RESEARCH_GRANT_ROTATE_BEFORE",
    "RESEARCH_GRANT_TTL",
    "ResearchGrantError",
    "ResearchGrantRecord",
    "ResearchGrantResolve",
    "ResearchGrantRevoke",
    "ResearchGrantStore",
    "ResearchGrantVerify",
]

RESEARCH_GRANT_TTL = timedelta(minutes=180)
RESEARCH_GRANT_ROTATE_BEFORE = timedelta(minutes=15)
RESEARCH_GRANT_PURGE_GRACE = timedelta(hours=24)

_GRANT_FAILURE = "Research object grant could not be verified."
_KEY_SCHEMA = "research-object-key/v1"
_SNAPSHOT_SCHEMA = "research-object-grant-snapshot/v1"


class ResearchGrantError(Exception):
    """Safe failure for unavailable or incorrectly bound object grants."""

    def __init__(self) -> None:
        super().__init__(_GRANT_FAILURE)


@dataclass(frozen=True, slots=True)
class ResearchGrantResolve:
    """Request to create or replay grants for one parent run."""

    principal_key_prefix: str
    parent_run_id: str
    execution_fingerprint: str
    objects: tuple[ResearchObjectCandidate, ...]


@dataclass(frozen=True, slots=True)
class ResearchGrantVerify:
    """Request to verify or rotate grants for one parent run."""

    principal_key_prefix: str
    parent_run_id: str
    execution_fingerprint: str
    authorities: tuple[ResearchObjectAuthority, ...]


@dataclass(frozen=True, slots=True)
class ResearchGrantRevoke:
    """Request to revoke grants bound to one parent run."""

    principal_key_prefix: str
    parent_run_id: str
    execution_fingerprint: str
    grant_ids: tuple[str, ...]


# Public DTO fields are prescribed by the run-bound grant protocol.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True, slots=True)
class ResearchGrantRecord:
    """One durable private grant and its immutable safe snapshot."""

    grant_id: str
    principal_key_prefix: str
    parent_run_id: str
    execution_fingerprint: str
    dataset_id: str
    exact_reference: str
    key_digest: str
    snapshot: ResearchObjectSnapshot
    state: Literal["active", "revoked", "expired"]
    expires_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class _GrantInsert:
    """Private input bundle for an atomic new-grant insertion."""

    request: ResearchGrantResolve
    candidate: ResearchObjectCandidate
    key_digest: str
    snapshot: ResearchObjectSnapshot
    now: datetime
    revision: int = 1


class ResearchGrantStore:
    """Persist run-bound Research grants without exposing audit query paths."""

    def __init__(self, db_path: str) -> None:
        """Initialize additive private grant storage at ``db_path``."""
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_object_grants (
                    grant_id TEXT PRIMARY KEY,
                    principal_key_prefix TEXT NOT NULL,
                    parent_run_id TEXT NOT NULL,
                    execution_fingerprint TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    exact_reference TEXT NOT NULL,
                    key_digest TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT
                )
                """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_grants_identity "
                "ON research_object_grants("
                "principal_key_prefix, parent_run_id, execution_fingerprint, "
                "dataset_id, key_digest, snapshot_digest, state)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_grants_expiry "
                "ON research_object_grants(expires_at, state)"
            )

    def resolve_or_replay(
        self, request: ResearchGrantResolve, now: datetime
    ) -> tuple[ResearchGrantRecord, ...]:
        """Create or return run-bound grants in one immediate transaction."""
        self._validate_scope(
            request.principal_key_prefix,
            request.parent_run_id,
            request.execution_fingerprint,
        )
        now = _utc(now)
        candidates = tuple(
            _candidate_identity(candidate) for candidate in request.objects
        )
        if not candidates:
            return ()
        if len({candidate[0] for candidate in candidates}) != len(candidates):
            raise ResearchGrantError()
        with self._immediate_transaction() as conn:
            records: list[ResearchGrantRecord] = []
            for candidate, key_digest, snapshot in candidates:
                row = conn.execute(
                    """
                    SELECT * FROM research_object_grants
                    WHERE principal_key_prefix = ? AND parent_run_id = ?
                    AND execution_fingerprint = ? AND dataset_id = ?
                    AND key_digest = ? AND snapshot_digest = ?
                    AND state = 'active'
                    ORDER BY revision DESC LIMIT 1
                    """,
                    (
                        request.principal_key_prefix,
                        request.parent_run_id,
                        request.execution_fingerprint,
                        candidate.dataset_id,
                        key_digest,
                        snapshot.snapshot_digest,
                    ),
                ).fetchone()
                if row is not None:
                    record = _record_from_row(row)
                    if record.expires_at > now:
                        records.append(record)
                        continue
                    self._expire(conn, record.grant_id, record.revision, now)
                records.append(
                    self._insert_grant(
                        conn,
                        _GrantInsert(
                            request,
                            candidate,
                            key_digest,
                            snapshot,
                            now,
                        ),
                    )
                )
            return tuple(records)

    def verify_or_rotate(
        self, request: ResearchGrantVerify, now: datetime
    ) -> tuple[ResearchGrantRecord, ...]:
        """Verify bindings/snapshots and rotate only expiring active grants."""
        self._validate_scope(
            request.principal_key_prefix,
            request.parent_run_id,
            request.execution_fingerprint,
        )
        now = _utc(now)
        verified: list[ResearchGrantRecord] = []
        with self._immediate_transaction() as conn:
            records = [
                self._matching_active_record(conn, request, authority)
                for authority in request.authorities
            ]
            expired = [
                record for record in records if record.expires_at <= now
            ]
            if expired:
                for record in expired:
                    self._expire(conn, record.grant_id, record.revision, now)
            else:
                for authority, record in zip(
                    request.authorities, records, strict=True
                ):
                    if record.expires_at - now > RESEARCH_GRANT_ROTATE_BEFORE:
                        verified.append(record)
                        continue
                    self._revoke(conn, record.grant_id, record.revision, now)
                    candidate = ResearchObjectCandidate(
                        dataset_id=record.dataset_id,
                        exact_reference=record.exact_reference,
                        compound_suffix="verified",
                    )
                    verified.append(
                        self._insert_grant(
                            conn,
                            _GrantInsert(
                                ResearchGrantResolve(
                                    request.principal_key_prefix,
                                    request.parent_run_id,
                                    request.execution_fingerprint,
                                    (candidate,),
                                ),
                                candidate,
                                record.key_digest,
                                authority.snapshot,
                                now,
                                record.revision + 1,
                            ),
                        )
                    )
        if expired:
            raise ResearchGrantError()
        return tuple(verified)

    def revoke(self, request: ResearchGrantRevoke, now: datetime) -> None:
        """Revoke matching grants without revealing foreign rows."""
        self._validate_scope(
            request.principal_key_prefix,
            request.parent_run_id,
            request.execution_fingerprint,
        )
        now = _utc(now)
        with self._immediate_transaction() as conn:
            for grant_id in request.grant_ids:
                cursor = conn.execute(
                    """
                    UPDATE research_object_grants
                    SET state = 'revoked', revision = revision + 1,
                        revoked_at = ?, updated_at = ?
                    WHERE grant_id = ? AND principal_key_prefix = ?
                    AND parent_run_id = ? AND execution_fingerprint = ?
                    AND state = 'active'
                    """,
                    (
                        _iso(now),
                        _iso(now),
                        grant_id,
                        request.principal_key_prefix,
                        request.parent_run_id,
                        request.execution_fingerprint,
                    ),
                )
                if cursor.rowcount == 0:
                    row = conn.execute(
                        "SELECT principal_key_prefix, parent_run_id, "
                        "execution_fingerprint FROM research_object_grants "
                        "WHERE grant_id = ?",
                        (grant_id,),
                    ).fetchone()
                    if row is not None and tuple(row) != (
                        request.principal_key_prefix,
                        request.parent_run_id,
                        request.execution_fingerprint,
                    ):
                        raise ResearchGrantError()

    def purge_expired(self, now: datetime) -> int:
        """Delete grants expired or revoked beyond the bounded grace period."""
        cutoff = _utc(now) - RESEARCH_GRANT_PURGE_GRACE
        with self._immediate_transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM research_object_grants WHERE "
                "(state = 'revoked' AND revoked_at < ?) OR "
                "(state = 'expired' AND expires_at < ?)",
                (_iso(cutoff), _iso(cutoff)),
            )
            return cursor.rowcount

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a private connection with named rows."""
        with sqlite_connection(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            yield conn

    @contextmanager
    def _immediate_transaction(
        self,
    ) -> Generator[sqlite3.Connection, None, None]:
        """Serialize grant mutations and roll back failed state transitions."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    @staticmethod
    def _validate_scope(
        principal_key_prefix: str,
        parent_run_id: str,
        execution_fingerprint: str,
    ) -> None:
        """Reject incomplete binding inputs through the one safe failure."""
        if not all(
            isinstance(value, str) and value
            for value in (
                principal_key_prefix,
                parent_run_id,
                execution_fingerprint,
            )
        ):
            raise ResearchGrantError()

    def _insert_grant(
        self,
        conn: sqlite3.Connection,
        insertion: _GrantInsert,
    ) -> ResearchGrantRecord:
        """Insert one fresh opaque grant under the current transaction."""
        grant_id = secrets.token_urlsafe(24)
        expires_at = insertion.now + RESEARCH_GRANT_TTL
        snapshot_json = _snapshot_json(insertion.snapshot)
        conn.execute(
            """
            INSERT INTO research_object_grants (
                grant_id, principal_key_prefix, parent_run_id,
                execution_fingerprint, dataset_id, exact_reference, key_digest,
                snapshot_json, snapshot_digest, state, expires_at, revision,
                created_at, updated_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL)
            """,
            (
                grant_id,
                insertion.request.principal_key_prefix,
                insertion.request.parent_run_id,
                insertion.request.execution_fingerprint,
                insertion.candidate.dataset_id,
                insertion.candidate.exact_reference,
                insertion.key_digest,
                snapshot_json,
                insertion.snapshot.snapshot_digest,
                _iso(expires_at),
                insertion.revision,
                _iso(insertion.now),
                _iso(insertion.now),
            ),
        )
        return ResearchGrantRecord(
            grant_id=grant_id,
            principal_key_prefix=insertion.request.principal_key_prefix,
            parent_run_id=insertion.request.parent_run_id,
            execution_fingerprint=insertion.request.execution_fingerprint,
            dataset_id=insertion.candidate.dataset_id,
            exact_reference=insertion.candidate.exact_reference,
            key_digest=insertion.key_digest,
            snapshot=insertion.snapshot,
            state="active",
            expires_at=expires_at,
            revision=insertion.revision,
        )

    def _matching_active_record(
        self,
        conn: sqlite3.Connection,
        request: ResearchGrantVerify,
        authority: ResearchObjectAuthority,
    ) -> ResearchGrantRecord:
        """Return one matching active grant or fail without revealing scope."""
        row = conn.execute(
            """
            SELECT * FROM research_object_grants
            WHERE grant_id = ? AND principal_key_prefix = ?
            AND parent_run_id = ? AND execution_fingerprint = ?
            """,
            (
                authority.authority_id,
                request.principal_key_prefix,
                request.parent_run_id,
                request.execution_fingerprint,
            ),
        ).fetchone()
        if row is None:
            raise ResearchGrantError()
        record = _record_from_row(row)
        if (
            record.state != "active"
            or record.dataset_id != authority.dataset_id
            or record.snapshot != authority.snapshot
        ):
            raise ResearchGrantError()
        return record

    @staticmethod
    def _revoke(
        conn: sqlite3.Connection,
        grant_id: str,
        revision: int,
        now: datetime,
    ) -> None:
        """Revoke one active grant with a revision fence."""
        cursor = conn.execute(
            """
            UPDATE research_object_grants
            SET state = 'revoked', revision = revision + 1,
                revoked_at = ?, updated_at = ?
            WHERE grant_id = ? AND state = 'active' AND revision = ?
            """,
            (_iso(now), _iso(now), grant_id, revision),
        )
        if cursor.rowcount != 1:
            raise ResearchGrantError()

    @staticmethod
    def _expire(
        conn: sqlite3.Connection,
        grant_id: str,
        revision: int,
        now: datetime,
    ) -> None:
        """Mark one stale active grant expired with a revision fence."""
        cursor = conn.execute(
            """
            UPDATE research_object_grants
            SET state = 'expired', revision = revision + 1, updated_at = ?
            WHERE grant_id = ? AND state = 'active' AND revision = ?
            """,
            (_iso(now), grant_id, revision),
        )
        if cursor.rowcount != 1:
            raise ResearchGrantError()


def _candidate_identity(
    candidate: ResearchObjectCandidate,
) -> tuple[ResearchObjectCandidate, str, ResearchObjectSnapshot]:
    """Build a versioned key identity and opaque initial snapshot."""
    if not candidate.dataset_id or not candidate.exact_reference:
        raise ResearchGrantError()
    key = _normalized_key(candidate.exact_reference)
    key_digest = _digest({"key": key, "schema": _KEY_SCHEMA})
    snapshot = ResearchObjectSnapshot(
        dataset_id=candidate.dataset_id,
        size_bytes=0,
        etag=None,
        version_id=None,
        last_modified=None,
        placeholder=False,
        snapshot_digest=_digest(
            {
                "dataset_id": candidate.dataset_id,
                "key_digest": key_digest,
                "schema": _SNAPSHOT_SCHEMA,
            }
        ),
    )
    return candidate, key_digest, snapshot


def _normalized_key(reference: str) -> str:
    """Return the exact object-key component without a bucket alias."""
    if reference[:6].casefold() != "obs://":
        raise ResearchGrantError()
    bucket, separator, key = reference[6:].partition("/")
    if not bucket or not separator or not key or "?" in key or "#" in key:
        raise ResearchGrantError()
    return key


def _snapshot_json(snapshot: ResearchObjectSnapshot) -> str:
    """Serialize safe immutable metadata in canonical compact JSON."""
    return json.dumps(
        {
            "dataset_id": snapshot.dataset_id,
            "etag": snapshot.etag,
            "last_modified": snapshot.last_modified,
            "placeholder": snapshot.placeholder,
            "size_bytes": snapshot.size_bytes,
            "snapshot_digest": snapshot.snapshot_digest,
            "version_id": snapshot.version_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _record_from_row(row: sqlite3.Row) -> ResearchGrantRecord:
    """Project a private grant row into its typed record representation."""
    snapshot = json.loads(row["snapshot_json"])
    state = row["state"]
    if state not in {"active", "revoked", "expired"}:
        raise ResearchGrantError()
    return ResearchGrantRecord(
        grant_id=row["grant_id"],
        principal_key_prefix=row["principal_key_prefix"],
        parent_run_id=row["parent_run_id"],
        execution_fingerprint=row["execution_fingerprint"],
        dataset_id=row["dataset_id"],
        exact_reference=row["exact_reference"],
        key_digest=row["key_digest"],
        snapshot=ResearchObjectSnapshot(**snapshot),
        state=state,
        expires_at=datetime.fromisoformat(row["expires_at"]),
        revision=row["revision"],
    )


def _digest(value: object) -> str:
    """Return a canonical SHA-256 digest for a versioned grant identity."""
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchGrantError()
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    """Return the canonical UTC timestamp representation for SQLite."""
    return _utc(value).isoformat()
