# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for additive resumable-upload SQLite state."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.resumable_uploads import (
    AssetCreateSpec,
    PartRecord,
    ResumableUploadRegistry,
    UploadStateError,
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
