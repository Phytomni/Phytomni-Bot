# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private SQLite schema mechanics for resumable upload state."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

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
    activated_at TEXT,
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
_ASSET_COLUMNS = (
    "asset_id, owner_subject, filename, content_type, purpose, size_bytes, "
    "part_size_bytes, part_count, status, object_key, obs_upload_id, "
    "idempotency_key, state_version, reserved_bytes, created_at, updated_at, "
    "session_expires_at, completed_at, activated_at"
)


def _initialize_activation_column(conn: sqlite3.Connection) -> None:
    """Add and backfill the internal activation marker exactly once."""
    if not _upload_assets_has_column(conn, "activated_at"):
        try:
            conn.execute(
                "ALTER TABLE upload_assets ADD COLUMN activated_at TEXT"
            )
        except sqlite3.OperationalError as error:
            if not _is_duplicate_activation_column_error(
                error
            ) or not _upload_assets_has_column(conn, "activated_at"):
                raise
    conn.execute(
        "UPDATE upload_assets SET activated_at = ("
        "SELECT MIN(received_at) FROM upload_parts "
        "WHERE upload_parts.asset_id = upload_assets.asset_id"
        ") WHERE activated_at IS NULL AND status = 'uploading' "
        "AND EXISTS (SELECT 1 FROM upload_parts "
        "WHERE upload_parts.asset_id = upload_assets.asset_id)"
    )


def _upload_assets_has_column(conn: sqlite3.Connection, column: str) -> bool:
    """Return whether the current upload-asset schema includes one column."""
    return any(
        row[1] == column
        for row in conn.execute("PRAGMA table_info(upload_assets)")
    )


def _is_duplicate_activation_column_error(
    error: sqlite3.OperationalError,
) -> bool:
    """Recognize only SQLite's expected concurrent-column migration error."""
    return str(error).strip().lower() == "duplicate column name: activated_at"


def _asset_values_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> tuple[object, ...]:
    """Convert one projected SQLite row into typed asset field values."""
    return (
        cast(str, row[0]),
        cast(str, row[1]),
        cast(str, row[2]),
        cast(str, row[3]),
        cast(str, row[4]),
        cast(int, row[5]),
        cast(int, row[6]),
        cast(int, row[7]),
        cast(str, row[8]),
        cast(str, row[9]),
        cast(str | None, row[10]),
        cast(str, row[11]),
        cast(int, row[12]),
        cast(int, row[13]),
        _parse_time(row[14]),
        _parse_time(row[15]),
        _parse_time(row[16]),
        None if row[17] is None else _parse_time(row[17]),
        None if row[18] is None else _parse_time(row[18]),
    )


def _build_part_from_row[PartRecordT](
    row: sqlite3.Row | tuple[object, ...],
    factory: Callable[[str, int, int, str, str, datetime], PartRecordT],
) -> PartRecordT:
    """Construct one typed part record from its explicit projection."""
    return factory(
        cast(str, row[0]),
        cast(int, row[1]),
        cast(int, row[2]),
        cast(str, row[3]),
        cast(str, row[4]),
        _parse_time(row[5]),
    )


def _parse_time(value: object) -> datetime:
    """Parse one trusted persisted UTC timestamp."""
    if not isinstance(value, str):
        raise TypeError("invalid persisted timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
