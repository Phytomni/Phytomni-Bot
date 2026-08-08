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
from dataclasses import dataclass, field
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
_GRANT_SCHEMA_VERSION = 3
_LEGACY_EXPIRES_AT = "1970-01-01T00:00:00+00:00"
_MIGRATION_COLUMNS = {
    "principal_key_prefix": "TEXT NOT NULL DEFAULT ''",
    "parent_run_id": "TEXT NOT NULL DEFAULT ''",
    "execution_fingerprint": "TEXT NOT NULL DEFAULT ''",
    "dataset_id": "TEXT NOT NULL DEFAULT ''",
    "source_authority_id": "TEXT NOT NULL DEFAULT ''",
    "exact_reference": "TEXT NOT NULL DEFAULT ''",
    "key_digest": "TEXT NOT NULL DEFAULT ''",
    "snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    "snapshot_digest": "TEXT NOT NULL DEFAULT ''",
    "state": "TEXT NOT NULL DEFAULT 'expired'",
    "expires_at": ("TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'"),
    "revision": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
    "updated_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
    "revoked_at": "TEXT",
    "grant_schema_version": "INTEGER NOT NULL DEFAULT 0",
}


class ResearchGrantError(Exception):
    """Safe failure for unavailable or incorrectly bound object grants."""

    def __init__(self) -> None:
        super().__init__(_GRANT_FAILURE)


@dataclass(frozen=True, slots=True)
class ResearchGrantResolve:
    """Private request to create or replay grants for one parent run.

    ``authorities`` comes directly from ``ResearchObjectMetadataPort.resolve``.
    It is positional with ``objects`` so the store can persist the observed
    immutable snapshot for each exact private reference.
    """

    principal_key_prefix: str
    parent_run_id: str
    execution_fingerprint: str
    objects: tuple[ResearchObjectCandidate, ...]
    authorities: tuple[ResearchObjectAuthority, ...] = ()


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


@dataclass(frozen=True, slots=True)
class _ResearchGrantRecordBinding:
    """Stable run and dataset binding fields for a public grant record."""

    grant_id: str
    principal_key_prefix: str
    parent_run_id: str
    execution_fingerprint: str
    dataset_id: str


@dataclass(frozen=True, slots=True)
class ResearchGrantRecord(_ResearchGrantRecordBinding):
    """One safe public projection of a durable private grant."""

    key_digest: str
    snapshot: ResearchObjectSnapshot
    state: Literal["active", "revoked", "expired"]
    expires_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class _StoredGrantRecord:
    """Private row projection retaining rotation-only grant bindings."""

    record: ResearchGrantRecord
    exact_reference: str = field(repr=False)
    source_authority_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _GrantInsert:
    """Private input bundle for an atomic new-grant insertion."""

    request: ResearchGrantResolve
    candidate: ResearchObjectCandidate
    source_authority_id: str
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
        try:
            with self._connect() as conn:
                self._initialize_schema(conn)
        except sqlite3.Error:
            raise ResearchGrantError() from None

    @staticmethod
    def _initialize_schema(conn: sqlite3.Connection) -> None:
        """Additively initialize or fail-close migrate the private table."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            ResearchGrantStore._apply_schema_migration(conn)
            conn.execute("COMMIT")
        except (ResearchGrantError, sqlite3.Error):
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _apply_schema_migration(conn: sqlite3.Connection) -> None:
        """Apply every grant-schema change inside one migration transaction."""
        conn.execute("""
                CREATE TABLE IF NOT EXISTS research_object_grants (
                    grant_id TEXT PRIMARY KEY,
                    principal_key_prefix TEXT NOT NULL,
                    parent_run_id TEXT NOT NULL,
                    execution_fingerprint TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    source_authority_id TEXT NOT NULL,
                    exact_reference TEXT NOT NULL,
                    key_digest TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT,
                    grant_schema_version INTEGER NOT NULL DEFAULT 3
                )
            """)
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(research_object_grants)"
            )
        }
        if "grant_id" not in columns:
            raise ResearchGrantError()
        legacy = False
        for name, definition in _MIGRATION_COLUMNS.items():
            if name not in columns:
                conn.execute(
                    "ALTER TABLE research_object_grants ADD COLUMN "
                    f"{name} {definition}"
                )
                legacy = True
        if legacy:
            conn.execute(
                """
                UPDATE research_object_grants
                SET state = 'expired', expires_at = ?, updated_at = ?,
                    revision = CASE WHEN revision < 1 THEN 1 ELSE revision END,
                    grant_schema_version = 0
                """,
                (_LEGACY_EXPIRES_AT, _LEGACY_EXPIRES_AT),
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_grants_v3_identity "
            "ON research_object_grants("
            "principal_key_prefix, parent_run_id, execution_fingerprint, "
            "dataset_id, key_digest, snapshot_digest, state, "
            "grant_schema_version)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_grants_expiry "
            "ON research_object_grants(expires_at, state)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_grant_schema_versions (
                schema_name TEXT PRIMARY KEY,
                version INTEGER NOT NULL
            )
            """)
        conn.execute(
            """
            INSERT INTO research_grant_schema_versions (schema_name, version)
            VALUES ('research_object_grants', ?)
            ON CONFLICT(schema_name) DO UPDATE SET version = excluded.version
            """,
            (_GRANT_SCHEMA_VERSION,),
        )

    def resolve_or_replay(
        self, request: ResearchGrantResolve, now: datetime
    ) -> tuple[ResearchGrantRecord, ...]:
        """Create or return run-bound grants in one immediate transaction."""
        if not isinstance(request, ResearchGrantResolve):
            raise ResearchGrantError()
        self._validate_scope(
            request.principal_key_prefix,
            request.parent_run_id,
            request.execution_fingerprint,
        )
        now = _utc(now)
        candidates = _resolve_identities(request)
        if not candidates:
            return ()
        if len({candidate[0] for candidate in candidates}) != len(candidates):
            raise ResearchGrantError()
        with self._immediate_transaction() as conn:
            records: list[ResearchGrantRecord] = []
            for (
                candidate,
                source_authority_id,
                key_digest,
                snapshot,
            ) in candidates:
                row = conn.execute(
                    """
                    SELECT * FROM research_object_grants
                    WHERE principal_key_prefix = ? AND parent_run_id = ?
                    AND execution_fingerprint = ? AND dataset_id = ?
                    AND key_digest = ? AND snapshot_digest = ?
                    AND state = 'active'
                    AND grant_schema_version = ?
                    ORDER BY revision DESC LIMIT 1
                    """,
                    (
                        request.principal_key_prefix,
                        request.parent_run_id,
                        request.execution_fingerprint,
                        candidate.dataset_id,
                        key_digest,
                        snapshot.snapshot_digest,
                        _GRANT_SCHEMA_VERSION,
                    ),
                ).fetchone()
                if row is not None:
                    stored = _stored_record_from_row(row)
                    if stored.record.expires_at > now:
                        records.append(stored.record)
                        continue
                    self._expire(
                        conn,
                        stored.record.grant_id,
                        stored.record.revision,
                        now,
                    )
                records.append(
                    self._insert_grant(
                        conn,
                        _GrantInsert(
                            request,
                            candidate,
                            source_authority_id,
                            key_digest,
                            snapshot,
                            now,
                        ),
                    ).record
                )
            return tuple(records)

    def verify_or_rotate(
        self, request: ResearchGrantVerify, now: datetime
    ) -> tuple[ResearchGrantRecord, ...]:
        """Verify bindings/snapshots and rotate only expiring active grants."""
        if not isinstance(request, ResearchGrantVerify):
            raise ResearchGrantError()
        self._validate_scope(
            request.principal_key_prefix,
            request.parent_run_id,
            request.execution_fingerprint,
        )
        now = _utc(now)
        authorities = _validated_authorities(request.authorities)
        verified: list[ResearchGrantRecord] = []
        with self._immediate_transaction() as conn:
            records = [
                self._matching_active_record(conn, request, authority)
                for authority in authorities
            ]
            expired = [
                stored for stored in records if stored.record.expires_at <= now
            ]
            if expired:
                for stored in expired:
                    self._expire(
                        conn,
                        stored.record.grant_id,
                        stored.record.revision,
                        now,
                    )
            else:
                for authority, stored in zip(
                    authorities, records, strict=True
                ):
                    record = stored.record
                    if record.expires_at - now > RESEARCH_GRANT_ROTATE_BEFORE:
                        verified.append(record)
                        continue
                    self._revoke(conn, record.grant_id, record.revision, now)
                    candidate = ResearchObjectCandidate(
                        dataset_id=record.dataset_id,
                        exact_reference=stored.exact_reference,
                        compound_suffix="verified",
                    )
                    source_authority = ResearchObjectAuthority(
                        dataset_id=record.dataset_id,
                        authority_id=stored.source_authority_id,
                        snapshot=authority.snapshot,
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
                                    (source_authority,),
                                ),
                                candidate,
                                stored.source_authority_id,
                                record.key_digest,
                                authority.snapshot,
                                now,
                                record.revision + 1,
                            ),
                        ).record
                    )
        if expired:
            raise ResearchGrantError()
        return tuple(verified)

    def revoke(self, request: ResearchGrantRevoke, now: datetime) -> None:
        """Revoke matching grants without revealing foreign rows."""
        if (
            not isinstance(request, ResearchGrantRevoke)
            or not isinstance(request.grant_ids, tuple)
            or not all(
                isinstance(grant_id, str) and grant_id
                for grant_id in request.grant_ids
            )
        ):
            raise ResearchGrantError()
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
                    AND grant_schema_version = ?
                    """,
                    (
                        _iso(now),
                        _iso(now),
                        grant_id,
                        request.principal_key_prefix,
                        request.parent_run_id,
                        request.execution_fingerprint,
                        _GRANT_SCHEMA_VERSION,
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
    ) -> _StoredGrantRecord:
        """Insert one fresh opaque grant under the current transaction."""
        grant_id = secrets.token_urlsafe(24)
        expires_at = insertion.now + RESEARCH_GRANT_TTL
        snapshot_json = _snapshot_json(insertion.snapshot)
        conn.execute(
            """
            INSERT INTO research_object_grants (
                grant_id, principal_key_prefix, parent_run_id,
                execution_fingerprint, dataset_id, source_authority_id,
                exact_reference, key_digest,
                snapshot_json, snapshot_digest, state, expires_at, revision,
                created_at, updated_at, revoked_at, grant_schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active',
                ?, ?, ?, ?, NULL, ?)
            """,
            (
                grant_id,
                insertion.request.principal_key_prefix,
                insertion.request.parent_run_id,
                insertion.request.execution_fingerprint,
                insertion.candidate.dataset_id,
                insertion.source_authority_id,
                insertion.candidate.exact_reference,
                insertion.key_digest,
                snapshot_json,
                insertion.snapshot.snapshot_digest,
                _iso(expires_at),
                insertion.revision,
                _iso(insertion.now),
                _iso(insertion.now),
                _GRANT_SCHEMA_VERSION,
            ),
        )
        return _StoredGrantRecord(
            ResearchGrantRecord(
                grant_id=grant_id,
                principal_key_prefix=insertion.request.principal_key_prefix,
                parent_run_id=insertion.request.parent_run_id,
                execution_fingerprint=insertion.request.execution_fingerprint,
                dataset_id=insertion.candidate.dataset_id,
                key_digest=insertion.key_digest,
                snapshot=insertion.snapshot,
                state="active",
                expires_at=expires_at,
                revision=insertion.revision,
            ),
            insertion.candidate.exact_reference,
            insertion.source_authority_id,
        )

    def _matching_active_record(
        self,
        conn: sqlite3.Connection,
        request: ResearchGrantVerify,
        authority: ResearchObjectAuthority,
    ) -> _StoredGrantRecord:
        """Return one matching active grant or fail without revealing scope."""
        authority, _ = _validated_authority(authority)
        row = conn.execute(
            """
            SELECT * FROM research_object_grants
            WHERE grant_id = ? AND principal_key_prefix = ?
            AND parent_run_id = ? AND execution_fingerprint = ?
            AND grant_schema_version = ?
            """,
            (
                authority.authority_id,
                request.principal_key_prefix,
                request.parent_run_id,
                request.execution_fingerprint,
                _GRANT_SCHEMA_VERSION,
            ),
        ).fetchone()
        if row is None:
            raise ResearchGrantError()
        stored = _stored_record_from_row(row)
        record = stored.record
        if (
            record.state != "active"
            or record.dataset_id != authority.dataset_id
            or record.snapshot != authority.snapshot
        ):
            raise ResearchGrantError()
        return stored

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
            AND grant_schema_version = ?
            """,
            (_iso(now), _iso(now), grant_id, revision, _GRANT_SCHEMA_VERSION),
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
            AND grant_schema_version = ?
            """,
            (_iso(now), grant_id, revision, _GRANT_SCHEMA_VERSION),
        )
        if cursor.rowcount != 1:
            raise ResearchGrantError()


def _resolve_identities(
    request: ResearchGrantResolve,
) -> tuple[
    tuple[ResearchObjectCandidate, str, str, ResearchObjectSnapshot], ...
]:
    """Bind each private reference to one real metadata-port authority."""
    if (
        not isinstance(request, ResearchGrantResolve)
        or not isinstance(request.objects, tuple)
        or not isinstance(request.authorities, tuple)
        or len(request.objects) != len(request.authorities)
    ):
        raise ResearchGrantError()
    identities: list[
        tuple[ResearchObjectCandidate, str, str, ResearchObjectSnapshot]
    ] = []
    source_authority_ids: set[str] = set()
    for raw_candidate, raw_authority in zip(
        request.objects, request.authorities, strict=True
    ):
        candidate = _validated_candidate(raw_candidate)
        authority, snapshot = _validated_authority(raw_authority)
        source_authority_id = authority.authority_id
        if source_authority_id in source_authority_ids:
            raise ResearchGrantError()
        source_authority_ids.add(source_authority_id)
        if authority.dataset_id != candidate.dataset_id:
            raise ResearchGrantError()
        if snapshot.dataset_id != candidate.dataset_id or snapshot.placeholder:
            raise ResearchGrantError()
        key_digest = _digest(
            {
                "key": _normalized_key(candidate.exact_reference),
                "schema": _KEY_SCHEMA,
            }
        )
        identities.append(
            (candidate, source_authority_id, key_digest, snapshot)
        )
    return tuple(identities)


def _validated_candidate(candidate: object) -> ResearchObjectCandidate:
    """Require a complete candidate before inspecting its private reference."""
    if not isinstance(candidate, ResearchObjectCandidate) or not all(
        isinstance(value, str) and value
        for value in (
            candidate.dataset_id,
            candidate.exact_reference,
            candidate.compound_suffix,
        )
    ):
        raise ResearchGrantError()
    return candidate


def _validated_authorities(
    authorities: object,
) -> tuple[ResearchObjectAuthority, ...]:
    """Validate verification authorities without leaking shape errors."""
    if not isinstance(authorities, tuple):
        raise ResearchGrantError()
    validated: list[ResearchObjectAuthority] = []
    authority_ids: set[str] = set()
    for authority in authorities:
        checked, _ = _validated_authority(authority)
        if checked.authority_id in authority_ids:
            raise ResearchGrantError()
        authority_ids.add(checked.authority_id)
        validated.append(checked)
    return tuple(validated)


def _validated_authority(
    authority: object,
) -> tuple[ResearchObjectAuthority, ResearchObjectSnapshot]:
    """Validate an authority and immutable snapshot before attribute use."""
    if not isinstance(authority, ResearchObjectAuthority):
        raise ResearchGrantError()
    if not all(
        isinstance(value, str) and value
        for value in (authority.dataset_id, authority.authority_id)
    ):
        raise ResearchGrantError()
    snapshot = authority.snapshot
    if not _is_valid_snapshot(snapshot):
        raise ResearchGrantError()
    return authority, snapshot


def _is_valid_snapshot(snapshot: object) -> bool:
    """Return whether ``snapshot`` has the immutable metadata-port shape."""
    if not isinstance(snapshot, ResearchObjectSnapshot):
        return False
    if not all(
        isinstance(value, str) and value
        for value in (snapshot.dataset_id, snapshot.snapshot_digest)
    ):
        return False
    if (
        not isinstance(snapshot.size_bytes, int)
        or isinstance(snapshot.size_bytes, bool)
        or snapshot.size_bytes < 0
    ):
        return False
    if not isinstance(snapshot.placeholder, bool):
        return False
    return all(
        value is None or isinstance(value, str)
        for value in (
            snapshot.etag,
            snapshot.version_id,
            snapshot.last_modified,
        )
    )


def _normalized_key(reference: str) -> str:
    """Return the exact object-key component without a bucket alias."""
    if reference[:6].casefold() != "obs://":
        raise ResearchGrantError()
    bucket, separator, key = reference[6:].partition("/")
    if not bucket or not separator or not key:
        raise ResearchGrantError()
    if "@" in bucket:
        raise ResearchGrantError()
    if any(marker in key for marker in ("?", "#")):
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


def _stored_record_from_row(row: sqlite3.Row) -> _StoredGrantRecord:
    """Load one private row while exposing only a safe typed projection."""
    try:
        snapshot = json.loads(row["snapshot_json"])
        state = row["state"]
        if state not in {"active", "revoked", "expired"}:
            raise ValueError
        resolved_snapshot = ResearchObjectSnapshot(**snapshot)
        if not _is_valid_snapshot(resolved_snapshot):
            raise ValueError
        source_authority_id = row["source_authority_id"]
        if not isinstance(source_authority_id, str) or not source_authority_id:
            raise ValueError
        record = ResearchGrantRecord(
            grant_id=row["grant_id"],
            principal_key_prefix=row["principal_key_prefix"],
            parent_run_id=row["parent_run_id"],
            execution_fingerprint=row["execution_fingerprint"],
            dataset_id=row["dataset_id"],
            key_digest=row["key_digest"],
            snapshot=resolved_snapshot,
            state=state,
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revision=row["revision"],
        )
        return _StoredGrantRecord(
            record,
            row["exact_reference"],
            source_authority_id,
        )
    except (KeyError, TypeError, ValueError):
        raise ResearchGrantError() from None


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
