# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Owner-scoped durable metadata for accepted OBS uploads."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .sqlite import sqlite_transaction

__all__ = ["UploadMetadata", "UploadRegistry"]


@dataclass(frozen=True)
class _UploadIdentity:
    """Identity fields for one accepted upload."""

    file_id: str
    user_id: str
    obs_path: str


@dataclass(frozen=True)
class _UploadDescription:
    """Content metadata for one accepted upload."""

    filename: str
    purpose: str
    byte_size: int
    format: str
    media_type: str
    created_at: str


@dataclass(frozen=True, slots=True)
class UploadMetadata(_UploadIdentity, _UploadDescription):
    """Trusted metadata that authorizes one later attachment reference."""


_CREATE_UPLOADS_TABLE = """
CREATE TABLE IF NOT EXISTS user_uploads (
    file_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    obs_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    purpose TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    format TEXT NOT NULL,
    media_type TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""
_CREATE_OWNER_PATH_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_user_uploads_owner_path "
    "ON user_uploads(user_id, obs_path)"
)


class UploadRegistry:
    """Persist and resolve upload metadata within the shared task database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with sqlite_transaction(db_path) as conn:
            conn.execute(_CREATE_UPLOADS_TABLE)
            conn.execute(_CREATE_OWNER_PATH_INDEX)

    def record(self, metadata: UploadMetadata) -> None:
        """Insert one metadata row without replacing existing identities."""
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                "INSERT INTO user_uploads ("
                "file_id, user_id, obs_path, filename, purpose, byte_size, "
                "format, media_type, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metadata.file_id,
                    metadata.user_id,
                    metadata.obs_path,
                    metadata.filename,
                    metadata.purpose,
                    metadata.byte_size,
                    metadata.format,
                    metadata.media_type,
                    metadata.created_at,
                ),
            )

    def get_by_path(
        self,
        obs_path: str,
        *,
        owner: str,
    ) -> UploadMetadata | None:
        """Return a path only when it belongs to the authenticated owner."""
        with sqlite_transaction(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT file_id, user_id, obs_path, filename, purpose, "
                "byte_size, format, media_type, created_at "
                "FROM user_uploads WHERE user_id = ? AND obs_path = ?",
                (owner, obs_path),
            ).fetchone()
        if row is None:
            return None
        return UploadMetadata(
            file_id=row["file_id"],
            user_id=row["user_id"],
            obs_path=row["obs_path"],
            filename=row["filename"],
            purpose=row["purpose"],
            byte_size=row["byte_size"],
            format=row["format"],
            media_type=row["media_type"],
            created_at=row["created_at"],
        )
