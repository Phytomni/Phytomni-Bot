# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Additive SQLite state for resumable upload assets."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Literal, NamedTuple

from ._resumable_upload_sqlite import (
    _ASSET_COLUMNS,
    _CREATE_ASSETS_TABLE,
    _CREATE_CAPABILITIES_TABLE,
    _CREATE_CAPABILITY_INDEX,
    _CREATE_IDEMPOTENCY_TABLE,
    _CREATE_OWNER_INDEX,
    _CREATE_PART_LEASES_TABLE,
    _CREATE_PARTS_TABLE,
    _CREATE_QUOTA_EVENTS_TABLE,
    _CREATE_QUOTA_INDEX,
    _activate_asset,
    _asset_values_from_row,
    _build_capability_from_row,
    _build_part_from_row,
    _clear_provider_session,
    _discard_unbound_allocation,
    _fetch_capability_row,
    _initialize_activation_column,
    _parse_time,
    _pending_provider_asset_ids,
)
from .sqlite import sqlite_connection, sqlite_transaction

__all__ = [
    "AssetCreateSpec",
    "AssetRecord",
    "CapabilityAuthorization",
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
# Upload routes are v2-only; this mirrors Web's protocol version constant.
UPLOAD_PROTOCOL_VERSION = 2
PART_SIZE_BYTES = 128 * 1024**2
MAX_UPLOAD_BYTES = 10 * 1024**3
MAX_ACTIVE_ASSETS = 3
MAX_ACTIVE_PART_REQUESTS = 8
MAX_UNFINISHED_BYTES = 30 * 1024**3
MAX_ACCEPTED_CREATE_BYTES = 100 * 1024**3
SESSION_TTL = timedelta(days=7)
CAPABILITY_TTL = timedelta(minutes=15)
PROVISIONAL_TTL = timedelta(minutes=180)

AssetStatus = Literal["uploading", "completed", "aborted", "expired"]
UploadAssetPurpose = Literal["chat_attachment", "dataset", "document"]
UPLOAD_ASSET_PURPOSES = frozenset({"chat_attachment", "dataset", "document"})
ExpiryReason = Literal["normal_deadline", "provisional_deadline"]

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResumableUploadRegistryConfig:
    """Deployment limits and TTLs for one upload registry instance."""

    max_upload_bytes: int = MAX_UPLOAD_BYTES
    part_size_bytes: int = PART_SIZE_BYTES
    session_ttl: timedelta = SESSION_TTL
    capability_ttl: timedelta = CAPABILITY_TTL
    provisional_ttl: timedelta = PROVISIONAL_TTL


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
    activated_at: datetime | None


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


class CapabilityAuthorization(NamedTuple):
    """Operation scope plus the caller's explicit activation decision."""

    operation: str
    activate: bool


class UploadStateError(RuntimeError):
    """Stable internal state error that carries no storage details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
        self.provisional_ttl = config.provisional_ttl
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
            _initialize_activation_column(conn)

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
        result: tuple[AssetRecord, CapabilitySecret] | None = None
        error_code: str | None = None
        expiry_counts: dict[ExpiryReason, int] = {}
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            _expired_ids, expiry_counts = self._expire_due_assets(
                conn,
                now=created_at,
                owner=spec.owner_subject,
            )
            existing = conn.execute(
                "SELECT asset_id, request_fingerprint FROM upload_idempotency "
                "WHERE owner_subject = ? AND idempotency_key = ?",
                (spec.owner_subject, spec.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing[1] != fingerprint:
                    error_code = "upload_state_conflict"
                else:
                    asset = self._fetch_asset(conn, existing[0])
                    error_code = _terminal_error(asset)
                    if error_code is None:
                        result = (
                            asset,
                            self._issue_capability(
                                conn,
                                asset,
                                now=created_at,
                                operations=(
                                    "head",
                                    "part",
                                    "complete",
                                    "abort",
                                ),
                            ),
                        )
            else:
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
                accepted = conn.execute(
                    "SELECT COALESCE(SUM(byte_size), 0) "
                    "FROM upload_quota_events WHERE owner_subject = ? "
                    "AND event_kind = 'create' AND created_at >= ?",
                    (
                        spec.owner_subject,
                        _iso(created_at - timedelta(hours=24)),
                    ),
                ).fetchone()[0]
                if (
                    active_count >= MAX_ACTIVE_ASSETS
                    or unfinished + spec.size_bytes > MAX_UNFINISHED_BYTES
                    or accepted + spec.size_bytes > MAX_ACCEPTED_CREATE_BYTES
                ):
                    error_code = "upload_limit_exceeded"
                else:
                    asset = self._insert_asset(
                        conn,
                        spec,
                        fingerprint=fingerprint,
                        created_at=created_at,
                    )
                    result = (
                        asset,
                        self._issue_capability(
                            conn,
                            asset,
                            now=created_at,
                            operations=(
                                "head",
                                "part",
                                "complete",
                                "abort",
                            ),
                        ),
                    )
        _report_expirations(expiry_counts)
        if error_code is not None:
            raise UploadStateError(error_code)
        if result is None:
            raise RuntimeError("upload create transaction produced no result")
        return result

    def _insert_asset(
        self,
        conn: sqlite3.Connection,
        spec: AssetCreateSpec,
        *,
        fingerprint: str,
        created_at: datetime,
    ) -> AssetRecord:
        """Insert one accepted asset while the caller owns the transaction."""
        asset_id = f"file_{secrets.token_hex(16)}"
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
            object_key=(
                f"agent_data/uploads/{_safe_owner(spec.owner_subject)}"
                f"/{asset_id}"
            ),
            obs_upload_id=None,
            idempotency_key=spec.idempotency_key,
            state_version=1,
            reserved_bytes=spec.size_bytes,
            created_at=created_at,
            updated_at=created_at,
            session_expires_at=created_at + self.session_ttl,
            completed_at=None,
            activated_at=None,
        )
        conn.execute(
            "INSERT INTO upload_assets ("
            "asset_id, owner_subject, filename, content_type, purpose, "
            "size_bytes, part_size_bytes, part_count, status, object_key, "
            "obs_upload_id, idempotency_key, request_fingerprint, "
            "state_version, reserved_bytes, created_at, updated_at, "
            "session_expires_at, completed_at, activated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)",
            (
                *asset[:12],
                fingerprint,
                asset.state_version,
                asset.reserved_bytes,
                _iso(asset.created_at),
                _iso(asset.updated_at),
                _iso(asset.session_expires_at),
                None,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO upload_idempotency ("
            "owner_subject, idempotency_key, request_fingerprint, "
            "asset_id, created_at) VALUES (?, ?, ?, ?, ?)",
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
        return asset

    def get_asset(self, asset_id: str, *, owner: str) -> AssetRecord | None:
        """Return an asset only for its canonical owner."""
        with sqlite_connection(self.db_path) as conn:
            asset = conn.execute(
                f"SELECT {_ASSET_COLUMNS} "
                "FROM upload_assets WHERE asset_id = ? "
                "AND owner_subject = ?",
                (asset_id, owner),
            ).fetchone()
        return None if asset is None else _asset_from_row(asset)

    def get_asset_by_id(self, asset_id: str) -> AssetRecord | None:
        """Return one asset for internal cleanup and reconciliation."""
        with sqlite_connection(self.db_path) as conn:
            asset = conn.execute(
                f"SELECT {_ASSET_COLUMNS} "
                "FROM upload_assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        return None if asset is None else _asset_from_row(asset)

    def clear_provider_session(self, asset_id: str, *, now: datetime) -> None:
        """Forget an OBS session only after its provider abort succeeds."""
        with sqlite_transaction(self.db_path) as conn:
            _clear_provider_session(conn, asset_id, _iso(_utc(now)))

    def discard_unbound_allocation(self, asset_id: str, *, owner: str) -> bool:
        """Discard one pristine allocation without erasing accepted volume."""
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            return _discard_unbound_allocation(conn, asset_id, owner)

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
        return tuple(_build_part_from_row(row, PartRecord) for row in rows)

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
        return None if row is None else _build_part_from_row(row, PartRecord)

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
                stored = _build_part_from_row(existing, PartRecord)
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
            return self._terminalize_asset(
                conn,
                asset,
                status="completed",
                now=completed_at,
            )

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
            return self._terminalize_asset(
                conn,
                asset,
                status="aborted",
                now=aborted_at,
            )

    def cleanup_expired(self, *, now: datetime) -> tuple[str, ...]:
        """Expire due rows and discover every pending terminal cleanup."""
        expired_at = _utc(now)
        expiry_counts: dict[ExpiryReason, int]
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            newly_expired, expiry_counts = self._expire_due_assets(
                conn, now=expired_at
            )
            pending_provider = _pending_provider_asset_ids(conn)
            asset_ids = tuple(
                dict.fromkeys((*newly_expired, *pending_provider))
            )
        _report_expirations(expiry_counts)
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
        current = _utc(now)
        result: CapabilitySecret | None = None
        error_code: str | None = None
        expiry_counts: dict[ExpiryReason, int] = {}
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = self._fetch_asset(conn, asset_id)
            if asset.owner_subject != owner:
                raise UploadStateError("upload_asset_not_found")
            reason = self._deadline_reason(asset, current)
            if reason is not None:
                self._terminalize_asset(
                    conn, asset, status="expired", now=current
                )
                expiry_counts[reason] = 1
                error_code = "upload_session_expired"
            else:
                error_code = _terminal_error(asset)
                if error_code is None:
                    result = self._issue_capability(
                        conn,
                        asset,
                        now=current,
                        operations=tuple(operations),
                    )
        _report_expirations(expiry_counts)
        if error_code is not None:
            raise UploadStateError(error_code)
        if result is None:
            raise RuntimeError("capability transaction produced no result")
        return result

    def authorize_capability(
        self,
        raw_token: str,
        *,
        asset_id: str,
        authorization: CapabilityAuthorization,
        now: datetime,
    ) -> tuple[CapabilityRecord, AssetRecord]:
        """Authorize one operation and optionally record browser takeover."""
        current = _utc(now)
        result: tuple[CapabilityRecord, AssetRecord] | None = None
        error_code: str | None = None
        expiry_counts: dict[ExpiryReason, int] = {}
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _fetch_capability_row(conn, _token_hash(raw_token), asset_id)
            if row is None:
                raise UploadStateError("upload_capability_invalid")
            operations = frozenset(json.loads(row[2]))
            if authorization.operation not in operations:
                raise UploadStateError("upload_capability_invalid")
            asset = self._fetch_asset(conn, asset_id)
            if row[1] != asset.owner_subject:
                raise UploadStateError("upload_capability_invalid")
            expires_at = _parse_time(row[4])
            if expires_at <= current or (
                row[5] is not None
                and not (
                    authorization.operation == "abort"
                    and asset.status == "aborted"
                )
            ):
                raise UploadStateError("upload_capability_invalid")
            if (
                authorization.operation == "abort"
                and asset.status == "aborted"
            ):
                result = (
                    _build_capability_from_row(
                        row, operations, CapabilityRecord
                    ),
                    asset,
                )
            else:
                reason = self._deadline_reason(asset, current)
                if reason is not None:
                    self._terminalize_asset(
                        conn, asset, status="expired", now=current
                    )
                    expiry_counts[reason] = 1
                    error_code = "upload_session_expired"
                else:
                    error_code = _terminal_error(asset)
                    if error_code is None:
                        if (
                            authorization.activate
                            and asset.activated_at is None
                        ):
                            _activate_asset(
                                conn,
                                asset.asset_id,
                                _iso(current),
                                _iso(asset.created_at + self.provisional_ttl),
                            )
                            asset = self._fetch_asset(conn, asset_id)
                        result = (
                            _build_capability_from_row(
                                row, operations, CapabilityRecord
                            ),
                            asset,
                        )
        _report_expirations(expiry_counts)
        if error_code is not None:
            raise UploadStateError(error_code)
        if result is None:
            raise RuntimeError("authorization transaction produced no result")
        return result

    def verify_capability(
        self,
        raw_token: str,
        *,
        asset_id: str,
        operation: str,
        now: datetime,
    ) -> CapabilityRecord:
        """Verify a raw token without returning or persisting it."""
        record, _asset = self.authorize_capability(
            raw_token,
            asset_id=asset_id,
            authorization=CapabilityAuthorization(operation, False),
            now=now,
        )
        return record

    def _effective_deadline(self, asset: AssetRecord) -> datetime:
        """Return the immutable deadline that currently governs one row."""
        return min(
            asset.session_expires_at,
            (
                asset.session_expires_at
                if asset.activated_at is not None
                else asset.created_at + self.provisional_ttl
            ),
        )

    def _deadline_reason(
        self, asset: AssetRecord, now: datetime
    ) -> ExpiryReason | None:
        """Classify a due upload without changing its persisted state."""
        if asset.status != "uploading":
            return None
        normal_deadline = asset.session_expires_at
        if asset.activated_at is None:
            provisional_deadline = asset.created_at + self.provisional_ttl
            if provisional_deadline <= normal_deadline:
                deadline = provisional_deadline
                reason: ExpiryReason = "provisional_deadline"
            else:
                deadline = normal_deadline
                reason = "normal_deadline"
        else:
            deadline = normal_deadline
            reason = "normal_deadline"
        return reason if deadline <= now else None

    def _expire_due_assets(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime,
        owner: str | None = None,
    ) -> tuple[tuple[str, ...], dict[ExpiryReason, int]]:
        """Terminalize due rows while the caller holds the write lock."""
        statement = (
            f"SELECT {_ASSET_COLUMNS} FROM upload_assets "
            "WHERE status = 'uploading'"
        )
        parameters: tuple[str, ...] = ()
        if owner is not None:
            statement += " AND owner_subject = ?"
            parameters = (owner,)
        statement += " ORDER BY created_at, asset_id"
        rows = conn.execute(statement, parameters).fetchall()
        expired_ids: list[str] = []
        counts: dict[ExpiryReason, int] = {}
        for row in rows:
            asset = _asset_from_row(row)
            reason = self._deadline_reason(asset, now)
            if reason is None:
                continue
            self._terminalize_asset(
                conn,
                asset,
                status="expired",
                now=now,
            )
            expired_ids.append(asset.asset_id)
            counts[reason] = counts.get(reason, 0) + 1
        return tuple(expired_ids), counts

    def _terminalize_asset(
        self,
        conn: sqlite3.Connection,
        asset: AssetRecord,
        *,
        status: Literal["completed", "aborted", "expired"],
        now: datetime,
    ) -> AssetRecord:
        """Apply every terminal-state side effect in one transaction."""
        if asset.status != "uploading":
            return asset
        completed_at = _iso(now) if status == "completed" else None
        conn.execute(
            "UPDATE upload_assets SET status = ?, reserved_bytes = 0, "
            "state_version = state_version + 1, updated_at = ?, "
            "completed_at = ? WHERE asset_id = ? AND status = 'uploading'",
            (status, _iso(now), completed_at, asset.asset_id),
        )
        self._revoke_capabilities(conn, asset.asset_id, now)
        conn.execute(
            "DELETE FROM upload_part_leases WHERE asset_id = ?",
            (asset.asset_id,),
        )
        return self._fetch_asset(conn, asset.asset_id)

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
        expires_at = min(
            now + self.capability_ttl,
            self._effective_deadline(asset),
        )
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
            f"SELECT {_ASSET_COLUMNS} FROM upload_assets WHERE asset_id = ?",
            (asset_id,),
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


def _terminal_error(asset: AssetRecord) -> str | None:
    """Map persisted terminal states to stable replay errors."""
    if asset.status == "uploading":
        return None
    return (
        "upload_session_expired"
        if asset.status == "expired"
        else "upload_state_conflict"
    )


def _report_expirations(counts: dict[ExpiryReason, int]) -> None:
    """Report aggregate expiry reasons without logging row identities."""
    for reason, count in sorted(counts.items()):
        _LOGGER.info("Upload sessions expired: %s=%d", reason, count)


def _safe_owner(owner: str) -> str:
    """Generate a non-path owner component for internal object keys."""
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]


def _asset_from_row(row: sqlite3.Row | tuple[object, ...]) -> AssetRecord:
    return AssetRecord._make(_asset_values_from_row(row))


def _utc(value: datetime) -> datetime:
    """Normalize an injected clock to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    """Serialize one UTC timestamp."""
    return _utc(value).isoformat()
