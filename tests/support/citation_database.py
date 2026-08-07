# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Synthetic schema-v1 citation database artifacts for unit tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

from mcp_server_phytomni.agents.shared.citation_database import (
    CITATION_BUILD_METADATA_FIELDS,
    CITATION_DATABASE_SCHEMA,
    CITATION_RECORD_FIELDS,
)


def create_valid_citation_database(
    path: Path,
    *,
    records: Sequence[Mapping[str, str | None]] = (),
    conflicts: Sequence[Mapping[str, object]] = (),
) -> Path:
    """Create one schema-v1 test artifact and return its path."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(CITATION_DATABASE_SCHEMA)
        _insert_records(connection, records)
        _insert_conflicts(connection, conflicts)
        record_count = len(records)
        conflict_count = len(conflicts)
        metadata = (
            1,
            "a" * 64,
            record_count + conflict_count,
            record_count + conflict_count,
            record_count,
            0,
            conflict_count,
            0,
            0,
            0,
            0,
        )
        placeholders = ",".join("?" for _ in CITATION_BUILD_METADATA_FIELDS)
        connection.execute(
            "INSERT INTO citation_build_metadata "
            f"({','.join(CITATION_BUILD_METADATA_FIELDS)}) "
            f"VALUES ({placeholders})",
            metadata,
        )
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    finally:
        connection.close()
    return path


def _insert_records(
    connection: sqlite3.Connection,
    records: Sequence[Mapping[str, str | None]],
) -> None:
    """Insert test records using the canonical serving columns."""
    columns = ("file_id", *CITATION_RECORD_FIELDS)
    placeholders = ",".join("?" for _ in columns)
    for record in records:
        connection.execute(
            f"INSERT INTO citation_records ({','.join(columns)}) "
            f"VALUES ({placeholders})",
            tuple(record.get(column) for column in columns),
        )


def _insert_conflicts(
    connection: sqlite3.Connection,
    conflicts: Sequence[Mapping[str, object]],
) -> None:
    """Insert one serialized conflict payload for every conflicted id."""
    for conflict in conflicts:
        connection.execute(
            "INSERT INTO citation_conflicts (file_id, conflict_json) "
            "VALUES (?, ?)",
            (
                str(conflict["file_id"]),
                str(conflict.get("conflict_json", "{}")),
            ),
        )
