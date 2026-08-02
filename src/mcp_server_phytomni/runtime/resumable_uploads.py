# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Additive SQLite state for resumable upload assets."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Literal, NamedTuple, cast

from .sqlite import sqlite_connection, sqlite_transaction

__all__ = [
    "AssetCreateSpec",
    "AssetRecord",
    "CapabilityRecord",
    "CapabilitySecret",
    "PartRecord",
    "ResumableUploadRegistry",
    "ResumableUploadRegistryConfig",
    "UPLOAD_ASSET_PURPOSES",
    "UploadAssetPurpose",
    "UploadStateError",
]

UPLOAD_PROTOCOL = "obs-multipart-v2"
PART_SIZE_BYTES = 128 * 1024**2
MAX_UPLOAD_BYTES = 10 * 1024**3
MAX_ACTIVE_ASSETS = 3
MAX_ACTIVE_PART_REQUESTS = 8
MAX_UNFINISHED_BYTES = 30 * 1024**3
MAX_ACCEPTED_CREATE_BYTES = 100 * 1024**3
SESSION_TTL = timedelta(days=7)
CAPABILITY_TTL = timedelta(minutes=15)

AssetStatus = Literal["uploading", "completed", "aborted", "expired"]
UploadAssetPurpose = Literal["chat_attachment", "dataset", "document"]
UPLOAD_ASSET_PURPOSES = frozenset({"chat_attachment", "dataset", "document"})


@dataclass(frozen=True, slots=True)
class ResumableUploadRegistryConfig:
    """Deployment limits and TTLs for one upload registry instance."""

    max_upload_bytes: int = MAX_UPLOAD_BYTES
    part_size_bytes: int = PART_SIZE_BYTES
    session_ttl: timedelta = SESSION_TTL
    capability_ttl: timedelta = CAPABILITY_TTL


@dataclass(frozen=True, slots=True)
class AssetCreateSpec:
    """Normalized trusted metadata for one asset creation."""

    owner_subject: str
    filename: str
    content_type: str
    size_bytes: int
    purpose: UploadAssetPurpose
    idempotency_key: str


class AssetRecord(NamedTuple):
    """Non-secret persisted state for one upload asset."""

    asset_id: str
    owner_subject: str
    filename: str
    content_type: str
    purpose: str
    size_bytes: int
    part_size_bytes: int
    part_count: int
    status: AssetStatus
    object_key: str
    obs_upload_id: str | None
    idempotency_key: str
    state_version: int
    reserved_bytes: int
    created_at: datetime
    updated_at: datetime
    session_expires_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PartRecord:
    """Authoritative metadata for one accepted part."""

    asset_id: str
    part_number: int
    byte_size: int
    sha256: str
    etag: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    """Non-secret capability metadata after hash verification."""

    asset_id: str
    owner_subject: str
    operations: frozenset[str]
    declared_bytes: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CapabilitySecret:
    """A newly issued raw capability and its non-secret persisted view."""

    raw_token: str
    record: CapabilityRecord


class UploadStateError(RuntimeError):
    """Stable internal state error that carries no storage details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_CREATE_ASSETS_TABLE = """
CREATE TABLE IF NOT EXISTS upload_assets (
    asset_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    purpose TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    part_size_bytes INTEGER NOT NULL,
    part_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    obs_upload_id TEXT,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    reserved_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_expires_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(owner_subject, idempotency_key)
)
"""
_CREATE_PARTS_TABLE = """
CREATE TABLE IF NOT EXISTS upload_parts (
    asset_id TEXT NOT NULL,
    part_number INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    etag TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY(asset_id, part_number),
    FOREIGN KEY(asset_id) REFERENCES upload_assets(asset_id)
)
"""
_CREATE_CAPABILITIES_TABLE = """
CREATE TABLE IF NOT EXISTS upload_capabilities (
    token_hash TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    owner_subject TEXT NOT NULL,
    operations TEXT NOT NULL,
    declared_bytes INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(asset_id) REFERENCES upload_assets(asset_id)
)
"""
_CREATE_IDEMPOTENCY_TABLE = """
CREATE TABLE IF NOT EXISTS upload_idempotency (
    owner_subject TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(owner_subject, idempotency_key),
    FOREIGN KEY(asset_id) REFERENCES upload_assets(asset_id)
)
"""
_CREATE_QUOTA_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS upload_quota_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_subject TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""
_CREATE_PART_LEASES_TABLE = """
CREATE TABLE IF NOT EXISTS upload_part_leases (
    lease_id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(asset_id) REFERENCES upload_assets(asset_id)
)
"""
_CREATE_OWNER_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_upload_assets_owner_status "
    "ON upload_assets(owner_subject, status)"
)
_CREATE_CAPABILITY_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_upload_capabilities_asset "
    "ON upload_capabilities(asset_id, revoked_at)"
)
_CREATE_QUOTA_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_upload_quota_owner_time "
    "ON upload_quota_events(owner_subject, created_at)"
)


class ResumableUploadRegistry:
    """Persist resumable assets without changing the legacy upload table."""

    def __init__(
        self,
        db_path: str,
        config: ResumableUploadRegistryConfig | None = None,
    ) -> None:
        config = config or ResumableUploadRegistryConfig()
        self.db_path = db_path
        self.max_upload_bytes = config.max_upload_bytes
        self.part_size_bytes = config.part_size_bytes
        self.session_ttl = config.session_ttl
        self.capability_ttl = config.capability_ttl
        with sqlite_transaction(db_path) as conn:
            for statement in (
                _CREATE_ASSETS_TABLE,
                _CREATE_PARTS_TABLE,
                _CREATE_CAPABILITIES_TABLE,
                _CREATE_IDEMPOTENCY_TABLE,
                _CREATE_QUOTA_EVENTS_TABLE,
                _CREATE_PART_LEASES_TABLE,
                _CREATE_OWNER_INDEX,
                _CREATE_CAPABILITY_INDEX,
                _CREATE_QUOTA_INDEX,
            ):
                conn.execute(statement)

    def create_or_replay(
        self,
        spec: AssetCreateSpec,
        *,
        now: datetime,
    ) -> tuple[AssetRecord, CapabilitySecret]:
        """Create or replay one owner-scoped idempotent asset."""
        _validate_spec(spec, max_upload_bytes=self.max_upload_bytes)
        created_at = _utc(now)
        fingerprint = _fingerprint(spec)
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT asset_id, request_fingerprint FROM upload_idempotency "
                "WHERE owner_subject = ? AND idempotency_key = ?",
                (spec.owner_subject, spec.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing[1] != fingerprint:
                    raise UploadStateError("upload_state_conflict")
                asset = self._fetch_asset(conn, existing[0])
                return asset, self._issue_capability(
                    conn,
                    asset,
                    now=created_at,
                    operations=("head", "part", "complete", "abort"),
                )

            active_count = conn.execute(
                "SELECT COUNT(*) FROM upload_assets "
                "WHERE owner_subject = ? AND status = 'uploading'",
                (spec.owner_subject,),
            ).fetchone()[0]
            unfinished = conn.execute(
                "SELECT COALESCE(SUM(reserved_bytes), 0) "
                "FROM upload_assets WHERE owner_subject = ? "
                "AND status = 'uploading'",
                (spec.owner_subject,),
            ).fetchone()[0]
            window_start = created_at - timedelta(hours=24)
            accepted = conn.execute(
                "SELECT COALESCE(SUM(byte_size), 0) FROM upload_quota_events "
                "WHERE owner_subject = ? AND event_kind = 'create' "
                "AND created_at >= ?",
                (spec.owner_subject, _iso(window_start)),
            ).fetchone()[0]
            if active_count >= MAX_ACTIVE_ASSETS:
                raise UploadStateError("upload_limit_exceeded")
            if unfinished + spec.size_bytes > MAX_UNFINISHED_BYTES:
                raise UploadStateError("upload_limit_exceeded")
            if accepted + spec.size_bytes > MAX_ACCEPTED_CREATE_BYTES:
                raise UploadStateError("upload_limit_exceeded")

            asset_id = f"file_{secrets.token_hex(16)}"
            object_key = (
                f"agent_data/uploads/{_safe_owner(spec.owner_subject)}"
                f"/{asset_id}"
            )
            session_expires_at = created_at + self.session_ttl
            asset = AssetRecord(
                asset_id=asset_id,
                owner_subject=spec.owner_subject,
                filename=spec.filename,
                content_type=spec.content_type,
                purpose=spec.purpose,
                size_bytes=spec.size_bytes,
                part_size_bytes=self.part_size_bytes,
                part_count=ceil(spec.size_bytes / self.part_size_bytes),
                status="uploading",
                object_key=object_key,
                obs_upload_id=None,
                idempotency_key=spec.idempotency_key,
                state_version=1,
                reserved_bytes=spec.size_bytes,
                created_at=created_at,
                updated_at=created_at,
                session_expires_at=session_expires_at,
                completed_at=None,
            )
            conn.execute(
                "INSERT INTO upload_assets ("
                "asset_id, owner_subject, filename, content_type, purpose, "
                "size_bytes, part_size_bytes, part_count, status, object_key, "
                "obs_upload_id, idempotency_key, request_fingerprint, "
                "state_version, reserved_bytes, created_at, updated_at, "
                "session_expires_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?)",
                (
                    asset.asset_id,
                    asset.owner_subject,
                    asset.filename,
                    asset.content_type,
                    asset.purpose,
                    asset.size_bytes,
                    asset.part_size_bytes,
                    asset.part_count,
                    asset.status,
                    asset.object_key,
                    asset.obs_upload_id,
                    asset.idempotency_key,
                    fingerprint,
                    asset.state_version,
                    asset.reserved_bytes,
                    _iso(asset.created_at),
                    _iso(asset.updated_at),
                    _iso(asset.session_expires_at),
                    None,
                ),
            )
            conn.execute(
                "INSERT INTO upload_idempotency ("
                "owner_subject, idempotency_key, request_fingerprint, "
                "asset_id, created_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    spec.owner_subject,
                    spec.idempotency_key,
                    fingerprint,
                    asset.asset_id,
                    _iso(created_at),
                ),
            )
            conn.execute(
                "INSERT INTO upload_quota_events ("
                "owner_subject, event_kind, byte_size, created_at"
                ") VALUES (?, 'create', ?, ?)",
                (spec.owner_subject, spec.size_bytes, _iso(created_at)),
            )
            return asset, self._issue_capability(
                conn,
                asset,
                now=created_at,
                operations=("head", "part", "complete", "abort"),
            )

    def get_asset(self, asset_id: str, *, owner: str) -> AssetRecord | None:
        """Return an asset only for its canonical owner."""
        with sqlite_connection(self.db_path) as conn:
            asset = conn.execute(
                "SELECT * FROM upload_assets WHERE asset_id = ? "
                "AND owner_subject = ?",
                (asset_id, owner),
            ).fetchone()
        return None if asset is None else _asset_from_row(asset)

    def get_asset_by_id(self, asset_id: str) -> AssetRecord | None:
        """Return one asset for internal cleanup and reconciliation."""
        with sqlite_connection(self.db_path) as conn:
            asset = conn.execute(
                "SELECT * FROM upload_assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return None if asset is None else _asset_from_row(asset)

    def clear_provider_session(self, asset_id: str, *, now: datetime) -> None:
        """Forget an OBS session only after its provider abort succeeds."""
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                "UPDATE upload_assets SET obs_upload_id = NULL, "
                "updated_at = ?, state_version = state_version + 1 "
                "WHERE asset_id = ? AND status IN ('expired', 'aborted')",
                (_iso(_utc(now)), asset_id),
            )

    def get_parts(
        self, asset_id: str, *, owner: str
    ) -> tuple[PartRecord, ...]:
        """Return the owner-scoped authoritative part table."""
        with sqlite_connection(self.db_path) as conn:
            owner_row = conn.execute(
                "SELECT 1 FROM upload_assets WHERE asset_id = ? "
                "AND owner_subject = ?",
                (asset_id, owner),
            ).fetchone()
            if owner_row is None:
                return ()
            rows = conn.execute(
                "SELECT asset_id, part_number, byte_size, sha256, etag, "
                "received_at FROM upload_parts WHERE asset_id = ? "
                "ORDER BY part_number",
                (asset_id,),
            ).fetchall()
        return tuple(_part_from_row(row) for row in rows)

    def get_part(
        self, asset_id: str, part_number: int, *, owner: str
    ) -> PartRecord | None:
        """Return one owner-scoped authoritative part, if present."""
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT p.asset_id, p.part_number, p.byte_size, p.sha256, "
                "p.etag, p.received_at FROM upload_parts AS p "
                "JOIN upload_assets AS a ON a.asset_id = p.asset_id "
                "WHERE p.asset_id = ? AND p.part_number = ? "
                "AND a.owner_subject = ?",
                (asset_id, part_number, owner),
            ).fetchone()
        return None if row is None else _part_from_row(row)

    def set_provider_session(
        self,
        asset_id: str,
        *,
        owner: str,
        obs_upload_id: str,
        now: datetime,
    ) -> AssetRecord:
        """Bind one provider session and accept identical concurrent binds."""
        if not obs_upload_id:
            raise UploadStateError("invalid_upload_metadata")
        bound_at = _utc(now)
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self._fetch_asset(conn, asset_id)
            if asset.owner_subject != owner:
                raise UploadStateError("upload_asset_not_found")
            if asset.status != "uploading":
                raise UploadStateError("upload_state_conflict")
            if asset.obs_upload_id is not None:
                if asset.obs_upload_id == obs_upload_id:
                    return asset
                raise UploadStateError("upload_state_conflict")
            conn.execute(
                "UPDATE upload_assets SET obs_upload_id = ?, "
                "updated_at = ?, state_version = state_version + 1 "
                "WHERE asset_id = ? AND status = 'uploading' "
                "AND obs_upload_id IS NULL",
                (obs_upload_id, _iso(bound_at), asset_id),
            )
            return self._fetch_asset(conn, asset_id)

    def record_part(self, part: PartRecord, *, now: datetime) -> PartRecord:
        """Insert one part or accept an identical retry."""
        _validate_part(part)
        received_at = _utc(now)
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self._fetch_asset(conn, part.asset_id)
            if asset.status != "uploading":
                raise UploadStateError("upload_state_conflict")
            if part.part_number > asset.part_count:
                raise UploadStateError("upload_state_conflict")
            existing = conn.execute(
                "SELECT asset_id, part_number, byte_size, sha256, etag, "
                "received_at FROM upload_parts WHERE asset_id = ? "
                "AND part_number = ?",
                (part.asset_id, part.part_number),
            ).fetchone()
            if existing is not None:
                stored = _part_from_row(existing)
                if (
                    stored.byte_size == part.byte_size
                    and stored.sha256 == part.sha256
                ):
                    return stored
                raise UploadStateError("upload_state_conflict")
            conn.execute(
                "INSERT INTO upload_parts ("
                "asset_id, part_number, byte_size, sha256, etag, received_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    part.asset_id,
                    part.part_number,
                    part.byte_size,
                    part.sha256,
                    part.etag,
                    _iso(received_at),
                ),
            )
            return PartRecord(
                asset_id=part.asset_id,
                part_number=part.part_number,
                byte_size=part.byte_size,
                sha256=part.sha256,
                etag=part.etag,
                received_at=received_at,
            )

    def complete_asset(
        self,
        asset_id: str,
        *,
        owner: str,
        now: datetime,
    ) -> AssetRecord:
        """Mark an asset complete only after all parts are authoritative."""
        completed_at = _utc(now)
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self._fetch_asset(conn, asset_id)
            if asset.owner_subject != owner:
                raise UploadStateError("upload_asset_not_found")
            if asset.status == "completed":
                return asset
            if asset.status != "uploading":
                raise UploadStateError("upload_state_conflict")
            parts = conn.execute(
                "SELECT part_number, byte_size FROM upload_parts "
                "WHERE asset_id = ? ORDER BY part_number",
                (asset_id,),
            ).fetchall()
            if [row[0] for row in parts] != list(
                range(1, asset.part_count + 1)
            ):
                raise UploadStateError("upload_state_conflict")
            if sum(row[1] for row in parts) != asset.size_bytes:
                raise UploadStateError("upload_state_conflict")
            conn.execute(
                "UPDATE upload_assets SET status = 'completed', "
                "reserved_bytes = 0, state_version = state_version + 1, "
                "updated_at = ?, completed_at = ? WHERE asset_id = ? "
                "AND status = 'uploading'",
                (_iso(completed_at), _iso(completed_at), asset_id),
            )
            self._revoke_capabilities(conn, asset_id, completed_at)
            return self._fetch_asset(conn, asset_id)

    def abort_asset(
        self,
        asset_id: str,
        *,
        owner: str,
        now: datetime,
    ) -> AssetRecord:
        """Abort an unfinished asset idempotently."""
        aborted_at = _utc(now)
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self._fetch_asset(conn, asset_id)
            if asset.owner_subject != owner:
                raise UploadStateError("upload_asset_not_found")
            if asset.status in {"completed", "aborted", "expired"}:
                return asset
            conn.execute(
                "UPDATE upload_assets SET status = 'aborted', "
                "reserved_bytes = 0, state_version = state_version + 1, "
                "updated_at = ? WHERE asset_id = ? AND status = 'uploading'",
                (_iso(aborted_at), asset_id),
            )
            self._revoke_capabilities(conn, asset_id, aborted_at)
            return self._fetch_asset(conn, asset_id)

    def cleanup_expired(self, *, now: datetime) -> tuple[str, ...]:
        """Expire unfinished sessions and release reservations safely."""
        expired_at = _utc(now)
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT asset_id FROM upload_assets "
                "WHERE (status = 'uploading' AND session_expires_at <= ?) "
                "OR (status = 'expired' AND obs_upload_id IS NOT NULL)",
                (_iso(expired_at),),
            ).fetchall()
            asset_ids = tuple(row[0] for row in rows)
            for asset_id in asset_ids:
                conn.execute(
                    "UPDATE upload_assets SET status = 'expired', "
                    "reserved_bytes = 0, state_version = state_version + 1, "
                    "updated_at = ? WHERE asset_id = ? "
                    "AND status = 'uploading'",
                    (_iso(expired_at), asset_id),
                )
                self._revoke_capabilities(conn, asset_id, expired_at)
        return asset_ids

    def acquire_part_lease(
        self,
        asset_id: str,
        *,
        owner: str,
        now: datetime,
    ) -> str:
        """Reserve one of the owner's bounded concurrent part slots."""
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self._fetch_asset(conn, asset_id)
            if asset.owner_subject != owner:
                raise UploadStateError("upload_asset_not_found")
            count = conn.execute(
                "SELECT COUNT(*) FROM upload_part_leases "
                "WHERE owner_subject = ?",
                (owner,),
            ).fetchone()[0]
            if count >= MAX_ACTIVE_PART_REQUESTS:
                raise UploadStateError("upload_rate_limited")
            lease_id = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO upload_part_leases ("
                "lease_id, owner_subject, asset_id, created_at"
                ") VALUES (?, ?, ?, ?)",
                (lease_id, owner, asset_id, _iso(_utc(now))),
            )
            return lease_id

    def release_part_lease(self, lease_id: str) -> None:
        """Release one part slot without exposing lease identity."""
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                "DELETE FROM upload_part_leases WHERE lease_id = ?",
                (lease_id,),
            )

    def issue_capability(
        self,
        asset_id: str,
        *,
        owner: str,
        now: datetime,
        operations: Iterable[str],
    ) -> CapabilitySecret:
        """Issue a fresh hash-only capability for an owner-scoped asset."""
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self._fetch_asset(conn, asset_id)
            if asset.owner_subject != owner:
                raise UploadStateError("upload_asset_not_found")
            return self._issue_capability(
                conn, asset, now=_utc(now), operations=tuple(operations)
            )

    def verify_capability(
        self,
        raw_token: str,
        *,
        asset_id: str,
        operation: str,
        now: datetime,
    ) -> CapabilityRecord:
        """Verify a raw token without returning or persisting it."""
        token_hash = _token_hash(raw_token)
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT asset_id, owner_subject, operations, declared_bytes, "
                "expires_at, revoked_at FROM upload_capabilities "
                "WHERE token_hash = ? AND asset_id = ?",
                (token_hash, asset_id),
            ).fetchone()
        if row is None or row[5] is not None:
            raise UploadStateError("upload_capability_invalid")
        expires_at = _parse_time(row[4])
        if expires_at <= _utc(now):
            raise UploadStateError("upload_capability_invalid")
        operations = frozenset(json.loads(row[2]))
        if operation not in operations:
            raise UploadStateError("upload_capability_invalid")
        return CapabilityRecord(
            asset_id=row[0],
            owner_subject=row[1],
            operations=operations,
            declared_bytes=row[3],
            expires_at=expires_at,
        )

    def _issue_capability(
        self,
        conn: sqlite3.Connection,
        asset: AssetRecord,
        *,
        now: datetime,
        operations: Sequence[str],
    ) -> CapabilitySecret:
        """Issue a capability while the caller owns a transaction."""
        if asset.status != "uploading":
            raise UploadStateError("upload_state_conflict")
        raw_token = secrets.token_urlsafe(32)
        expires_at = now + self.capability_ttl
        record = CapabilityRecord(
            asset_id=asset.asset_id,
            owner_subject=asset.owner_subject,
            operations=frozenset(operations),
            declared_bytes=asset.size_bytes,
            expires_at=expires_at,
        )
        conn.execute(
            "INSERT INTO upload_capabilities ("
            "token_hash, asset_id, owner_subject, operations, declared_bytes, "
            "expires_at, created_at, revoked_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                _token_hash(raw_token),
                record.asset_id,
                record.owner_subject,
                json.dumps(sorted(record.operations)),
                record.declared_bytes,
                _iso(record.expires_at),
                _iso(now),
            ),
        )
        return CapabilitySecret(raw_token=raw_token, record=record)

    @staticmethod
    def _fetch_asset(conn: sqlite3.Connection, asset_id: str) -> AssetRecord:
        """Fetch one asset or use the stable not-found state error."""
        row = conn.execute(
            "SELECT * FROM upload_assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            raise UploadStateError("upload_asset_not_found")
        return _asset_from_row(row)

    @staticmethod
    def _revoke_capabilities(
        conn: sqlite3.Connection, asset_id: str, now: datetime
    ) -> None:
        """Revoke all capabilities for one terminal asset transition."""
        conn.execute(
            "UPDATE upload_capabilities SET revoked_at = ? "
            "WHERE asset_id = ? AND revoked_at IS NULL",
            (_iso(now), asset_id),
        )


def _validate_spec(spec: AssetCreateSpec, *, max_upload_bytes: int) -> None:
    """Validate registry-level limits for callers outside Pydantic."""
    if not spec.owner_subject or not spec.filename:
        raise UploadStateError("invalid_upload_metadata")
    if not 0 < spec.size_bytes <= max_upload_bytes:
        raise UploadStateError("upload_limit_exceeded")
    if not spec.idempotency_key:
        raise UploadStateError("invalid_upload_metadata")
    if spec.purpose not in UPLOAD_ASSET_PURPOSES:
        raise UploadStateError("attachment_purpose_invalid")


def _validate_part(part: PartRecord) -> None:
    """Reject malformed persisted part metadata."""
    if part.part_number < 1 or part.byte_size < 0:
        raise UploadStateError("invalid_upload_metadata")
    if len(part.sha256) != 64 or len(part.etag) == 0:
        raise UploadStateError("invalid_upload_metadata")


def _fingerprint(spec: AssetCreateSpec) -> str:
    """Return a stable request fingerprint for idempotency comparison."""
    payload = {
        "content_type": spec.content_type,
        "filename": spec.filename,
        "owner_subject": spec.owner_subject,
        "purpose": spec.purpose,
        "size_bytes": spec.size_bytes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _token_hash(raw_token: str) -> str:
    """Hash a capability with a one-way digest before persistence."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _safe_owner(owner: str) -> str:
    """Generate a non-path owner component for internal object keys."""
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]


def _asset_from_row(row: sqlite3.Row | tuple[object, ...]) -> AssetRecord:
    """Map one positional SQLite row to its typed asset record."""
    return AssetRecord(
        asset_id=cast(str, row[0]),
        owner_subject=cast(str, row[1]),
        filename=cast(str, row[2]),
        content_type=cast(str, row[3]),
        purpose=cast(str, row[4]),
        size_bytes=cast(int, row[5]),
        part_size_bytes=cast(int, row[6]),
        part_count=cast(int, row[7]),
        status=cast(AssetStatus, row[8]),
        object_key=cast(str, row[9]),
        obs_upload_id=cast(str | None, row[10]),
        idempotency_key=cast(str, row[11]),
        state_version=cast(int, row[13]),
        reserved_bytes=cast(int, row[14]),
        created_at=_parse_time(row[15]),
        updated_at=_parse_time(row[16]),
        session_expires_at=_parse_time(row[17]),
        completed_at=None if row[18] is None else _parse_time(row[18]),
    )


def _part_from_row(row: sqlite3.Row | tuple[object, ...]) -> PartRecord:
    """Map one positional SQLite row to a typed part record."""
    return PartRecord(
        asset_id=cast(str, row[0]),
        part_number=cast(int, row[1]),
        byte_size=cast(int, row[2]),
        sha256=cast(str, row[3]),
        etag=cast(str, row[4]),
        received_at=_parse_time(row[5]),
    )


def _utc(value: datetime) -> datetime:
    """Normalize an injected clock to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    """Serialize one UTC timestamp."""
    return _utc(value).isoformat()


def _parse_time(value: object) -> datetime:
    """Parse one trusted persisted UTC timestamp."""
    if not isinstance(value, str):
        raise TypeError("invalid persisted timestamp")
    return _utc(datetime.fromisoformat(value))
