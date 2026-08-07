# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for additive resumable-upload SQLite state."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from mcp_server_phytomni.api.schemas import (
    AssetDescriptor,
    UploadStatusResponse,
)
from mcp_server_phytomni.runtime.resumable_uploads import (
    _ASSET_COLUMNS,
    AssetCreateSpec,
    AssetRecord,
    PartRecord,
    ResumableUploadRegistry,
    UploadAssetPurpose,
    UploadStateError,
    _asset_from_row,
)

pytestmark = pytest.mark.unit


NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _spec(
    *,
    owner: str = "owner-1",
    size_bytes: int = 3,
    key: str = "key-1",
    filename: str = "sample.fa",
) -> AssetCreateSpec:
    """Build one valid normalized asset specification."""
    return AssetCreateSpec(
        owner_subject=owner,
        filename=filename,
        content_type="application/octet-stream",
        size_bytes=size_bytes,
        purpose="chat_attachment",
        idempotency_key=key,
    )


def _part(
    asset_id: str, *, number: int = 1, digest: str = "a" * 64
) -> PartRecord:
    """Build one deterministic part record."""
    return PartRecord(
        asset_id=asset_id,
        part_number=number,
        byte_size=3,
        sha256=digest,
        etag=f"etag-{number}",
        received_at=NOW,
    )


_LEGACY_ASSETS_DDL = """
CREATE TABLE upload_assets (
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
_LEGACY_PARTS_DDL = """
CREATE TABLE upload_parts (
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


def _insert_legacy_asset(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    status: str,
    created_at: datetime,
    size_bytes: int = 3,
    part_count: int = 1,
    completed_at: datetime | None = None,
) -> None:
    """Insert a valid row from the upload schema before activation tracking."""
    conn.execute(
        "INSERT INTO upload_assets ("
        "asset_id, owner_subject, filename, content_type, purpose, "
        "size_bytes, part_size_bytes, part_count, status, object_key, "
        "obs_upload_id, idempotency_key, request_fingerprint, state_version, "
        "reserved_bytes, created_at, updated_at, session_expires_at, "
        "completed_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            asset_id,
            "owner-1",
            "legacy.fa",
            "application/octet-stream",
            "chat_attachment",
            size_bytes,
            3,
            part_count,
            status,
            f"agent_data/uploads/owner-1/{asset_id}",
            None,
            f"key-{asset_id}",
            f"fingerprint-{asset_id}",
            1,
            size_bytes if status == "uploading" else 0,
            created_at.isoformat(),
            created_at.isoformat(),
            (created_at + timedelta(days=7)).isoformat(),
            None if completed_at is None else completed_at.isoformat(),
        ),
    )


def _build_legacy_registry_db(db_path: Path) -> datetime:
    """Create the pre-activation schema and representative historical rows."""
    first_part_at = NOW - timedelta(hours=2)
    later_part_at = first_part_at + timedelta(minutes=1)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_LEGACY_ASSETS_DDL)
        conn.execute(_LEGACY_PARTS_DDL)
        _insert_legacy_asset(
            conn,
            asset_id="uploading-with-part",
            status="uploading",
            created_at=NOW - timedelta(hours=3),
            size_bytes=6,
            part_count=2,
        )
        _insert_legacy_asset(
            conn,
            asset_id="recent-zero-part",
            status="uploading",
            created_at=NOW - timedelta(minutes=5),
        )
        _insert_legacy_asset(
            conn,
            asset_id="old-zero-part",
            status="uploading",
            created_at=NOW - timedelta(days=8),
        )
        _insert_legacy_asset(
            conn,
            asset_id="completed-row",
            status="completed",
            created_at=NOW - timedelta(days=1),
            completed_at=NOW - timedelta(hours=1),
        )
        _insert_legacy_asset(
            conn,
            asset_id="aborted-row",
            status="aborted",
            created_at=NOW - timedelta(days=1),
        )
        conn.execute(
            "INSERT INTO upload_parts ("
            "asset_id, part_number, byte_size, sha256, etag, received_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                "uploading-with-part",
                1,
                3,
                "a" * 64,
                "legacy-etag",
                later_part_at.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO upload_parts ("
            "asset_id, part_number, byte_size, sha256, etag, received_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                "uploading-with-part",
                2,
                3,
                "b" * 64,
                "legacy-etag-2",
                first_part_at.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO upload_parts ("
            "asset_id, part_number, byte_size, sha256, etag, received_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                "completed-row",
                1,
                3,
                "c" * 64,
                "completed-etag",
                (NOW - timedelta(hours=2)).isoformat(),
            ),
        )
    return first_part_at


def test_fresh_activation_column_is_internal_and_starts_null(
    tmp_path: Path,
) -> None:
    """Fresh rows reserve a private marker outside public responses."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    asset, _secret = registry.create_or_replay(_spec(), now=NOW)

    with sqlite3.connect(registry.db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(upload_assets)")
        }
        activated_at = conn.execute(
            "SELECT activated_at FROM upload_assets WHERE asset_id = ?",
            (asset.asset_id,),
        ).fetchone()[0]

    assert "activated_at" in columns
    assert asset.activated_at is None
    assert activated_at is None
    assert "activated_at" not in UploadStatusResponse.model_fields
    assert "activated_at" not in AssetDescriptor.model_fields


def test_legacy_initialization_backfills_only_part_bearing_uploads(
    tmp_path: Path,
) -> None:
    """Migration uses authoritative part evidence, never row age alone."""
    db_path = tmp_path / "legacy.db"
    first_part_at = _build_legacy_registry_db(db_path)

    ResumableUploadRegistry(str(db_path))

    with sqlite3.connect(db_path) as conn:
        activation_by_asset = dict(
            conn.execute(
                "SELECT asset_id, activated_at FROM upload_assets "
                "ORDER BY asset_id"
            )
        )

    assert activation_by_asset == {
        "aborted-row": None,
        "completed-row": None,
        "old-zero-part": None,
        "recent-zero-part": None,
        "uploading-with-part": first_part_at.isoformat(),
    }


def test_activation_migration_is_repeatable_and_concurrent(
    tmp_path: Path,
) -> None:
    """Concurrent constructors converge on one marker without overwrites."""
    db_path = tmp_path / "legacy.db"
    first_part_at = _build_legacy_registry_db(db_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(ResumableUploadRegistry, str(db_path))
            for _index in range(2)
        ]
        for future in futures:
            future.result()
    ResumableUploadRegistry(str(db_path))
    ResumableUploadRegistry(str(db_path))

    with sqlite3.connect(db_path) as conn:
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(upload_assets)")
        ]
        conn.execute(
            "UPDATE upload_assets SET activated_at = ? WHERE asset_id = ?",
            ("2026-01-01T00:00:00+00:00", "uploading-with-part"),
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(ResumableUploadRegistry, str(db_path))
            for _index in range(2)
        ]
        for future in futures:
            future.result()

    with sqlite3.connect(db_path) as conn:
        activated_at = conn.execute(
            "SELECT activated_at FROM upload_assets WHERE asset_id = ?",
            ("uploading-with-part",),
        ).fetchone()[0]

    assert columns.count("activated_at") == 1
    assert first_part_at.isoformat() != activated_at
    assert activated_at == "2026-01-01T00:00:00+00:00"


def test_explicit_asset_projection_reconstructs_every_record_field(
    tmp_path: Path,
) -> None:
    """The explicit row order maps every persisted record field exactly."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    created, _secret = registry.create_or_replay(_spec(), now=NOW)

    with sqlite3.connect(registry.db_path) as conn:
        row = conn.execute(
            f"SELECT {_ASSET_COLUMNS} FROM upload_assets WHERE asset_id = ?",
            (created.asset_id,),
        ).fetchone()

    assert row is not None
    assert len(row) == len(AssetRecord._fields)
    assert _asset_from_row(row) == created


def test_registry_is_additive_and_owner_scoped(tmp_path: Path) -> None:
    """New tables coexist with the legacy completed-upload table."""
    db_path = tmp_path / "tasks.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE user_uploads (file_id TEXT PRIMARY KEY)")
    registry = ResumableUploadRegistry(str(db_path))

    asset, secret = registry.create_or_replay(_spec(), now=NOW)

    assert registry.get_asset(asset.asset_id, owner="owner-1") == asset
    assert registry.get_asset(asset.asset_id, owner="owner-2") is None
    assert secret.raw_token
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        raw_rows = conn.execute(
            "SELECT token_hash FROM upload_capabilities"
        ).fetchall()
    assert "user_uploads" in tables
    assert secret.raw_token not in {row[0] for row in raw_rows}


def test_create_is_idempotent_without_double_charging(tmp_path: Path) -> None:
    """The same owner/key returns one asset and one create-volume event."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    first, first_secret = registry.create_or_replay(_spec(), now=NOW)
    replay, replay_secret = registry.create_or_replay(_spec(), now=NOW)

    assert replay.asset_id == first.asset_id
    assert replay_secret.raw_token != first_secret.raw_token
    with sqlite3.connect(registry.db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM upload_quota_events"
            ).fetchone()[0]
            == 1
        )

    with pytest.raises(UploadStateError, match="upload_state_conflict"):
        registry.create_or_replay(_spec(size_bytes=4), now=NOW)


def test_registry_rejects_invalid_purpose_outside_pydantic(
    tmp_path: Path,
) -> None:
    """Reject invalid purposes from callers that bypass request schemas."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    with pytest.raises(UploadStateError) as error:
        registry.create_or_replay(
            replace(
                _spec(),
                purpose=cast(UploadAssetPurpose, "not-supported"),
            ),
            now=NOW,
        )
    assert error.value.code == "attachment_purpose_invalid"


def test_owner_and_quota_limits_fail_closed(tmp_path: Path) -> None:
    """Active and unfinished reservations cannot exceed configured limits."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    for index in range(3):
        registry.create_or_replay(_spec(key=f"key-{index}"), now=NOW)
    with pytest.raises(UploadStateError, match="upload_limit_exceeded"):
        registry.create_or_replay(_spec(key="key-4"), now=NOW)


def test_part_retry_conflict_and_authoritative_completion(
    tmp_path: Path,
) -> None:
    """Retries are idempotent, conflicts fail, and parts are authoritative."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    asset, _secret = registry.create_or_replay(_spec(size_bytes=3), now=NOW)
    part = _part(asset.asset_id)
    assert registry.record_part(part, now=NOW) == part
    assert registry.record_part(part, now=NOW) == part
    with pytest.raises(UploadStateError, match="upload_state_conflict"):
        registry.record_part(_part(asset.asset_id, digest="b" * 64), now=NOW)
    assert (
        registry.complete_asset(
            asset.asset_id, owner="owner-1", now=NOW
        ).status
        == "completed"
    )
    assert (
        registry.complete_asset(
            asset.asset_id, owner="owner-1", now=NOW
        ).status
        == "completed"
    )


def test_complete_requires_all_parts_and_abort_is_idempotent(
    tmp_path: Path,
) -> None:
    """Incomplete completion is rejected and abort releases the reservation."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    asset, _secret = registry.create_or_replay(
        _spec(size_bytes=128 * 1024**2 + 1), now=NOW
    )
    registry.record_part(_part(asset.asset_id), now=NOW)
    with pytest.raises(UploadStateError, match="upload_state_conflict"):
        registry.complete_asset(asset.asset_id, owner="owner-1", now=NOW)
    aborted = registry.abort_asset(asset.asset_id, owner="owner-1", now=NOW)
    assert aborted.status == "aborted"
    assert (
        registry.abort_asset(asset.asset_id, owner="owner-1", now=NOW).status
        == "aborted"
    )


def test_capability_scope_expiry_and_revocation(tmp_path: Path) -> None:
    """Capabilities are scoped and become invalid after terminal state."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    asset, secret = registry.create_or_replay(_spec(), now=NOW)
    verified = registry.verify_capability(
        secret.raw_token,
        asset_id=asset.asset_id,
        operation="part",
        now=NOW,
    )
    assert verified.asset_id == asset.asset_id
    with pytest.raises(UploadStateError, match="upload_capability_invalid"):
        registry.verify_capability(
            secret.raw_token,
            asset_id="file_other",
            operation="part",
            now=NOW,
        )
    with pytest.raises(UploadStateError, match="upload_capability_invalid"):
        registry.verify_capability(
            secret.raw_token,
            asset_id=asset.asset_id,
            operation="part",
            now=NOW + timedelta(minutes=16),
        )


def test_cleanup_is_restart_safe_and_does_not_touch_completed(
    tmp_path: Path,
) -> None:
    """A new registry instance expires only unfinished assets."""
    db_path = tmp_path / "tasks.db"
    registry = ResumableUploadRegistry(str(db_path))
    expired, _secret = registry.create_or_replay(_spec(), now=NOW)
    completed, _secret = registry.create_or_replay(_spec(key="key-2"), now=NOW)
    registry.record_part(_part(completed.asset_id), now=NOW)
    registry.complete_asset(completed.asset_id, owner="owner-1", now=NOW)

    restarted = ResumableUploadRegistry(str(db_path))
    assert restarted.cleanup_expired(now=NOW + timedelta(days=8)) == (
        expired.asset_id,
    )
    expired_asset = restarted.get_asset(expired.asset_id, owner="owner-1")
    completed_asset = restarted.get_asset(completed.asset_id, owner="owner-1")
    assert expired_asset is not None
    assert completed_asset is not None
    assert expired_asset.status == "expired"
    assert completed_asset.status == "completed"
