# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for owner-scoped durable upload metadata."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.upload_registry import (
    UploadMetadata,
    UploadRegistry,
)

pytestmark = pytest.mark.unit


def _metadata(
    *,
    file_id: str = "upload-1",
    user_id: str = "user-1",
    obs_path: str = "/obs/bucket/key/file.csv",
    purpose: str = "dataset",
) -> UploadMetadata:
    """Build one deterministic metadata record for registry tests."""
    return UploadMetadata(
        file_id=file_id,
        user_id=user_id,
        obs_path=obs_path,
        filename="file.csv",
        purpose=purpose,
        byte_size=12,
        format="csv",
        media_type="text/csv",
        created_at="2026-07-25T00:00:00+00:00",
    )


def test_upload_registry_is_owner_scoped(tmp_path: Path) -> None:
    """A user can resolve only their own upload path."""
    registry = UploadRegistry(str(tmp_path / "tasks.db"))
    metadata = _metadata()

    registry.record(metadata)

    assert registry.get_by_path(metadata.obs_path, owner="user-1") == metadata
    assert registry.get_by_path(metadata.obs_path, owner="user-2") is None


def test_upload_registry_rejects_conflicting_id_or_path(
    tmp_path: Path,
) -> None:
    """Duplicate identity or path is never silently overwritten."""
    registry = UploadRegistry(str(tmp_path / "tasks.db"))
    registry.record(_metadata())

    with pytest.raises(sqlite3.IntegrityError):
        registry.record(_metadata(user_id="user-2", obs_path="/obs/other"))
    with pytest.raises(sqlite3.IntegrityError):
        registry.record(_metadata(file_id="upload-2"))
