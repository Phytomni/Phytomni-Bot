# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for additive resumable-upload SQLite state."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from mcp_server_phytomni.api.schemas import (
    AssetDescriptor,
    UploadStatusResponse,
)
from mcp_server_phytomni.runtime.resumable_uploads import (
    _ASSET_COLUMNS,
    _CREATE_ASSETS_TABLE,
    _CREATE_PARTS_TABLE,
    AssetCreateSpec,
    AssetRecord,
    CapabilityAuthorization,
    PartRecord,
    ResumableUploadRegistry,
    ResumableUploadRegistryConfig,
    UploadAssetPurpose,
    UploadStateError,
    _asset_from_row,
    _initialize_activation_column,
)

pytestmark = pytest.mark.unit


NOW = datetime(2026, 8, 1, tzinfo=UTC)


class _ActivationMigrationErrorConnection:
    """Model a schema check that observes a competing migration afterwards."""

    def __init__(self, error: sqlite3.OperationalError) -> None:
        self.error = error
        self.schema_checks = 0
        self._statements: list[str] = []

    def execute(self, statement: str) -> list[tuple[int, str]]:
        """Return the raced schema view or raise the controlled ALTER error."""
        self._statements.append(statement)
        if statement == "PRAGMA table_info(upload_assets)":
            self.schema_checks += 1
            return [] if self.schema_checks == 1 else [(0, "activated_at")]
        if statement.startswith("ALTER TABLE"):
            raise self.error
        return []

    def recorded_statements(self) -> tuple[str, ...]:
        """Return the SQL observed by this controlled connection."""
        return tuple(self._statements)


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


def _stored_activation(
    registry: ResumableUploadRegistry, asset_id: str
) -> str | None:
    """Read one private activation marker directly from SQLite."""
    with sqlite3.connect(registry.db_path) as conn:
        return cast(
            str | None,
            conn.execute(
                "SELECT activated_at FROM upload_assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0],
        )


def _assert_state_error(code: str, operation: Callable[[], object]) -> None:
    """Run one operation and assert its stable registry error code."""
    with pytest.raises(UploadStateError) as error:
        operation()
    assert error.value.code == code


@dataclass(frozen=True, slots=True)
class _LegacyAsset:
    """One upload row shaped for the schema before activation persistence."""

    asset_id: str
    status: str
    created_at: datetime
    size_bytes: int = 3
    part_count: int = 1
    completed_at: datetime | None = None


def _insert_legacy_asset(
    conn: sqlite3.Connection,
    asset: _LegacyAsset,
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
            asset.asset_id,
            "owner-1",
            "legacy.fa",
            "application/octet-stream",
            "chat_attachment",
            asset.size_bytes,
            3,
            asset.part_count,
            asset.status,
            f"agent_data/uploads/owner-1/{asset.asset_id}",
            None,
            f"key-{asset.asset_id}",
            f"fingerprint-{asset.asset_id}",
            1,
            asset.size_bytes if asset.status == "uploading" else 0,
            asset.created_at.isoformat(),
            asset.created_at.isoformat(),
            (asset.created_at + timedelta(days=7)).isoformat(),
            (
                None
                if asset.completed_at is None
                else asset.completed_at.isoformat()
            ),
        ),
    )


def _build_legacy_registry_db(db_path: Path) -> datetime:
    """Create the pre-activation schema and representative historical rows."""
    first_part_at = NOW - timedelta(hours=2)
    later_part_at = first_part_at + timedelta(minutes=1)
    with sqlite3.connect(db_path) as conn:
        legacy_assets_ddl = _CREATE_ASSETS_TABLE.replace(
            "    activated_at TEXT,\n", ""
        )
        conn.execute(legacy_assets_ddl)
        conn.execute(_CREATE_PARTS_TABLE)
        _insert_legacy_asset(
            conn,
            _LegacyAsset(
                asset_id="uploading-with-part",
                status="uploading",
                created_at=NOW - timedelta(hours=3),
                size_bytes=6,
                part_count=2,
            ),
        )
        _insert_legacy_asset(
            conn,
            _LegacyAsset(
                asset_id="recent-zero-part",
                status="uploading",
                created_at=NOW - timedelta(minutes=5),
            ),
        )
        _insert_legacy_asset(
            conn,
            _LegacyAsset(
                asset_id="old-zero-part",
                status="uploading",
                created_at=NOW - timedelta(days=8),
            ),
        )
        _insert_legacy_asset(
            conn,
            _LegacyAsset(
                asset_id="completed-row",
                status="completed",
                created_at=NOW - timedelta(days=1),
                completed_at=NOW - timedelta(hours=1),
            ),
        )
        _insert_legacy_asset(
            conn,
            _LegacyAsset(
                asset_id="aborted-row",
                status="aborted",
                created_at=NOW - timedelta(days=1),
            ),
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
    status_fields: set[str] = set(UploadStatusResponse.model_fields.keys())
    descriptor_fields: set[str] = set(AssetDescriptor.model_fields.keys())
    assert "activated_at" not in status_fields
    assert "activated_at" not in descriptor_fields


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
    """Concurrent constructors preserve one migrated marker."""
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


def test_activation_migration_accepts_verified_duplicate_column_race() -> None:
    """A duplicate-column error is safe only when a competing migration won."""
    conn = _ActivationMigrationErrorConnection(
        sqlite3.OperationalError("duplicate column name: activated_at")
    )

    _initialize_activation_column(cast(sqlite3.Connection, conn))

    assert conn.schema_checks == 2
    assert any(
        statement.startswith("UPDATE upload_assets")
        for statement in conn.recorded_statements()
    )


def test_activation_migration_propagates_unrelated_sqlite_error() -> None:
    """A later visible column cannot mask an unrelated ALTER failure."""
    conn = _ActivationMigrationErrorConnection(
        sqlite3.OperationalError("database is locked")
    )

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _initialize_activation_column(cast(sqlite3.Connection, conn))


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


def test_authorization_activates_only_data_plane_takeover_operations(
    tmp_path: Path,
) -> None:
    """Only HEAD, part, and complete prove browser takeover."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    activation_time = NOW + timedelta(seconds=1)

    for index, operation in enumerate(("head", "part", "complete")):
        asset, secret = registry.create_or_replay(
            _spec(key=f"activate-{index}"), now=NOW
        )
        record, authorized = registry.authorize_capability(
            secret.raw_token,
            asset_id=asset.asset_id,
            authorization=CapabilityAuthorization(operation, True),
            now=activation_time,
        )
        assert record.owner_subject == "owner-1"
        assert authorized.activated_at == activation_time
        assert _stored_activation(registry, asset.asset_id) == (
            activation_time.isoformat()
        )
        registry.abort_asset(asset.asset_id, owner="owner-1", now=NOW)

    untouched, original = registry.create_or_replay(
        _spec(key="non-activation"), now=NOW
    )
    replay, replay_secret = registry.create_or_replay(
        _spec(key="non-activation"), now=NOW
    )
    renewed = registry.issue_capability(
        untouched.asset_id,
        owner="owner-1",
        now=NOW,
        operations=("head", "part", "complete", "abort"),
    )
    registry.authorize_capability(
        renewed.raw_token,
        asset_id=untouched.asset_id,
        authorization=CapabilityAuthorization("abort", False),
        now=activation_time,
    )

    assert replay.asset_id == untouched.asset_id
    assert original.raw_token != replay_secret.raw_token
    assert _stored_activation(registry, untouched.asset_id) is None


def test_repeated_authorization_does_not_refresh_activation(
    tmp_path: Path,
) -> None:
    """The first takeover timestamp is immutable across later requests."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    asset, secret = registry.create_or_replay(_spec(), now=NOW)
    first_clock = NOW + timedelta(seconds=1)
    second_clock = NOW + timedelta(minutes=2)

    _record, first = registry.authorize_capability(
        secret.raw_token,
        asset_id=asset.asset_id,
        authorization=CapabilityAuthorization("head", True),
        now=first_clock,
    )
    _record, second = registry.authorize_capability(
        secret.raw_token,
        asset_id=asset.asset_id,
        authorization=CapabilityAuthorization("part", True),
        now=second_clock,
    )

    assert first.activated_at == first_clock
    assert second.activated_at == first_clock
    assert second.session_expires_at == asset.session_expires_at


def test_provisional_deadline_boundary_is_exact(tmp_path: Path) -> None:
    """An unactivated row is valid before, but not at, its deadline."""
    registry = ResumableUploadRegistry(
        str(tmp_path / "tasks.db"),
        ResumableUploadRegistryConfig(capability_ttl=timedelta(hours=4)),
    )
    before_asset, before_secret = registry.create_or_replay(
        _spec(key="before"), now=NOW
    )
    due_asset, due_secret = registry.create_or_replay(
        _spec(key="due"), now=NOW
    )
    deadline = NOW + timedelta(minutes=180)

    _record, authorized = registry.authorize_capability(
        before_secret.raw_token,
        asset_id=before_asset.asset_id,
        authorization=CapabilityAuthorization("abort", False),
        now=deadline - timedelta(microseconds=1),
    )
    assert authorized.status == "uploading"
    _assert_state_error(
        "upload_capability_invalid",
        lambda: registry.authorize_capability(
            due_secret.raw_token,
            asset_id=due_asset.asset_id,
            authorization=CapabilityAuthorization("abort", False),
            now=deadline,
        ),
    )
    assert registry.get_asset(due_asset.asset_id, owner="owner-1") == due_asset
    assert due_asset.asset_id in registry.cleanup_expired(now=deadline)
    expired = registry.get_asset(due_asset.asset_id, owner="owner-1")
    assert expired is not None
    assert expired.status == "expired"
    assert expired.reserved_bytes == 0


def test_normal_session_deadline_boundary_is_exact(tmp_path: Path) -> None:
    """Activation cannot refresh or bypass the normal session deadline."""
    registry = ResumableUploadRegistry(
        str(tmp_path / "tasks.db"),
        ResumableUploadRegistryConfig(
            session_ttl=timedelta(hours=1),
            capability_ttl=timedelta(hours=2),
            provisional_ttl=timedelta(hours=3),
        ),
    )
    before_asset, before_secret = registry.create_or_replay(
        _spec(key="before"), now=NOW
    )
    due_asset, due_secret = registry.create_or_replay(
        _spec(key="due"), now=NOW
    )
    for asset, secret in (
        (before_asset, before_secret),
        (due_asset, due_secret),
    ):
        registry.authorize_capability(
            secret.raw_token,
            asset_id=asset.asset_id,
            authorization=CapabilityAuthorization("head", True),
            now=NOW + timedelta(seconds=1),
        )
    deadline = NOW + timedelta(hours=1)

    _record, authorized = registry.authorize_capability(
        before_secret.raw_token,
        asset_id=before_asset.asset_id,
        authorization=CapabilityAuthorization("head", True),
        now=deadline - timedelta(microseconds=1),
    )
    assert authorized.status == "uploading"
    due_before_deadline = registry.get_asset(
        due_asset.asset_id, owner="owner-1"
    )
    assert due_before_deadline is not None
    _assert_state_error(
        "upload_capability_invalid",
        lambda: registry.authorize_capability(
            due_secret.raw_token,
            asset_id=due_asset.asset_id,
            authorization=CapabilityAuthorization("head", True),
            now=deadline,
        ),
    )
    assert (
        registry.get_asset(due_asset.asset_id, owner="owner-1")
        == due_before_deadline
    )
    assert due_asset.asset_id in registry.cleanup_expired(now=deadline)
    due = registry.get_asset(due_asset.asset_id, owner="owner-1")
    assert due is not None
    assert due.status == "expired"
    assert due.activated_at == NOW + timedelta(seconds=1)


def test_activation_and_cleanup_race_has_one_valid_winner(
    tmp_path: Path,
) -> None:
    """Serialized activation and cleanup cannot create a split-brain row."""
    registry = ResumableUploadRegistry(
        str(tmp_path / "tasks.db"),
        ResumableUploadRegistryConfig(capability_ttl=timedelta(hours=4)),
    )
    asset, secret = registry.create_or_replay(_spec(), now=NOW)
    deadline = NOW + timedelta(minutes=180)
    barrier = Barrier(2)

    def activate() -> str:
        barrier.wait()
        try:
            registry.authorize_capability(
                secret.raw_token,
                asset_id=asset.asset_id,
                authorization=CapabilityAuthorization("head", True),
                now=deadline - timedelta(microseconds=1),
            )
        except UploadStateError as error:
            return error.code
        return "activated"

    def cleanup() -> tuple[str, ...]:
        barrier.wait()
        return registry.cleanup_expired(now=deadline)

    with ThreadPoolExecutor(max_workers=2) as executor:
        activation_future = executor.submit(activate)
        cleanup_future = executor.submit(cleanup)
        activation_result = activation_future.result()
        cleanup_result = cleanup_future.result()

    final = registry.get_asset(asset.asset_id, owner="owner-1")
    assert final is not None
    assert (final.status, final.activated_at) in {
        ("uploading", deadline - timedelta(microseconds=1)),
        ("expired", None),
    }
    if final.status == "uploading":
        assert activation_result == "activated"
        assert cleanup_result == ()
    else:
        assert activation_result == "upload_capability_invalid"
        assert cleanup_result == (asset.asset_id,)

    registry.cleanup_expired(now=NOW + timedelta(days=8))
    _assert_state_error(
        "upload_capability_invalid",
        lambda: registry.authorize_capability(
            secret.raw_token,
            asset_id=asset.asset_id,
            authorization=CapabilityAuthorization("head", True),
            now=NOW + timedelta(days=8),
        ),
    )
    terminal = registry.get_asset(asset.asset_id, owner="owner-1")
    assert terminal is not None
    assert terminal.status == "expired"


def test_create_reclaims_due_owner_rows_before_quota_checks(
    tmp_path: Path,
) -> None:
    """One create transaction reclaims all due provisional allocations."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    old_assets = [
        registry.create_or_replay(_spec(key=f"old-{index}"), now=NOW)[0]
        for index in range(3)
    ]

    created, _secret = registry.create_or_replay(
        _spec(key="replacement"), now=NOW + timedelta(minutes=180)
    )

    assert created.status == "uploading"
    reclaimed = [
        registry.get_asset(item.asset_id, owner="owner-1")
        for item in old_assets
    ]
    assert all(item is not None for item in reclaimed)
    assert [item.status for item in reclaimed if item is not None] == [
        "expired",
        "expired",
        "expired",
    ]
    with sqlite3.connect(registry.db_path) as conn:
        event_count, accepted_bytes = conn.execute(
            "SELECT COUNT(*), SUM(byte_size) FROM upload_quota_events "
            "WHERE owner_subject = ? AND event_kind = 'create'",
            ("owner-1",),
        ).fetchone()
    assert event_count == 4
    assert accepted_bytes == 12


def test_activated_zero_part_rows_keep_slots_until_normal_ttl(
    tmp_path: Path,
) -> None:
    """Takeover preserves active reservations until the normal deadline."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    for index in range(3):
        asset, secret = registry.create_or_replay(
            _spec(key=f"active-{index}"), now=NOW
        )
        registry.authorize_capability(
            secret.raw_token,
            asset_id=asset.asset_id,
            authorization=CapabilityAuthorization("head", True),
            now=NOW + timedelta(seconds=1),
        )

    _assert_state_error(
        "upload_limit_exceeded",
        lambda: registry.create_or_replay(
            _spec(key="fourth"), now=NOW + timedelta(minutes=181)
        ),
    )


def test_idempotency_replays_preserve_terminal_error_distinctions(
    tmp_path: Path,
) -> None:
    """Terminal idempotency mappings never allocate a replacement row."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    _expired, _secret = registry.create_or_replay(
        _spec(key="expired"), now=NOW
    )
    completed, _secret = registry.create_or_replay(
        _spec(key="completed"), now=NOW
    )
    aborted, _secret = registry.create_or_replay(_spec(key="aborted"), now=NOW)
    registry.record_part(_part(completed.asset_id), now=NOW)
    registry.complete_asset(completed.asset_id, owner="owner-1", now=NOW)
    registry.abort_asset(aborted.asset_id, owner="owner-1", now=NOW)
    registry.cleanup_expired(now=NOW + timedelta(minutes=180))

    _assert_state_error(
        "upload_session_expired",
        lambda: registry.create_or_replay(_spec(key="expired"), now=NOW),
    )
    for key in ("completed", "aborted"):
        with pytest.raises(UploadStateError) as error:
            registry.create_or_replay(_spec(key=key), now=NOW)
        assert error.value.code == "upload_state_conflict"
    with sqlite3.connect(registry.db_path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM upload_assets").fetchone()[0]
            == 3
        )


def test_capability_renewal_cannot_revive_stale_provisional_row(
    tmp_path: Path,
) -> None:
    """Renewal classifies and terminalizes a due unactivated allocation."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    asset, _secret = registry.create_or_replay(_spec(), now=NOW)
    renewed = registry.issue_capability(
        asset.asset_id,
        owner="owner-1",
        now=NOW + timedelta(minutes=179),
        operations=("head", "part", "complete", "abort"),
    )
    assert renewed.record.expires_at == NOW + timedelta(minutes=180)

    _assert_state_error(
        "upload_session_expired",
        lambda: registry.issue_capability(
            asset.asset_id,
            owner="owner-1",
            now=NOW + timedelta(minutes=180),
            operations=("head", "part", "complete", "abort"),
        ),
    )

    expired = registry.get_asset(asset.asset_id, owner="owner-1")
    assert expired is not None
    assert expired.status == "expired"
    assert expired.activated_at is None


def test_shared_terminalization_releases_all_ephemeral_state(
    tmp_path: Path,
) -> None:
    """Every terminal transition uses the same reservation cleanup rules."""
    registry = ResumableUploadRegistry(str(tmp_path / "tasks.db"))
    completed, _secret = registry.create_or_replay(
        _spec(key="completed"), now=NOW
    )
    aborted, _secret = registry.create_or_replay(_spec(key="aborted"), now=NOW)
    expired, _secret = registry.create_or_replay(_spec(key="expired"), now=NOW)
    for asset in (completed, aborted, expired):
        registry.acquire_part_lease(asset.asset_id, owner="owner-1", now=NOW)
    registry.record_part(_part(completed.asset_id), now=NOW)
    registry.complete_asset(completed.asset_id, owner="owner-1", now=NOW)
    registry.abort_asset(aborted.asset_id, owner="owner-1", now=NOW)
    registry.cleanup_expired(now=NOW + timedelta(minutes=180))

    with sqlite3.connect(registry.db_path) as conn:
        rows = conn.execute(
            "SELECT status, state_version, reserved_bytes "
            "FROM upload_assets ORDER BY status"
        ).fetchall()
        live_capabilities = conn.execute(
            "SELECT COUNT(*) FROM upload_capabilities WHERE revoked_at IS NULL"
        ).fetchone()[0]
        leases = conn.execute(
            "SELECT COUNT(*) FROM upload_part_leases"
        ).fetchone()[0]
    assert rows == [
        ("aborted", 2, 0),
        ("completed", 2, 0),
        ("expired", 2, 0),
    ]
    assert live_capabilities == 0
    assert leases == 0


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
