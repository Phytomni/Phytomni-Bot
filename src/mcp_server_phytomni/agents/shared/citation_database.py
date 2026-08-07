# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Read-only runtime access to a validated citation SQLite artifact."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from ...config import CitationConfig
from .citation_metadata import CITATION_RECORD_FIELDS

CITATION_SCHEMA_VERSION = 1
CITATION_LOOKUP_BATCH_SIZE = 900
CITATION_APPLICATION_TABLES = frozenset(
    {"citation_records", "citation_conflicts", "citation_build_metadata"}
)

_RECORD_COLUMNS = (
    ("file_id", "TEXT", 1, 1),
    *((field, "TEXT", 0, 0) for field in CITATION_RECORD_FIELDS),
)
_CONFLICT_COLUMNS = (
    ("file_id", "TEXT", 1, 1),
    ("conflict_json", "TEXT", 1, 0),
)
_METADATA_COLUMNS = (
    ("schema_version", "INTEGER", 1, 0),
    ("source_sha256", "TEXT", 1, 0),
    ("source_record_count", "INTEGER", 1, 0),
    ("unique_id_count", "INTEGER", 1, 0),
    ("imported_record_count", "INTEGER", 1, 0),
    ("exact_duplicate_row_count", "INTEGER", 1, 0),
    ("conflict_id_count", "INTEGER", 1, 0),
    ("quarantined_row_count", "INTEGER", 1, 0),
    ("missing_doi_count", "INTEGER", 1, 0),
    ("invalid_doi_count", "INTEGER", 1, 0),
    ("missing_title_count", "INTEGER", 1, 0),
)
_METADATA_FIELD_NAMES = tuple(column[0] for column in _METADATA_COLUMNS)
_SOURCE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CITATION_BUILD_METADATA_FIELDS = _METADATA_FIELD_NAMES

CITATION_DATABASE_SCHEMA = """
CREATE TABLE citation_records (
    file_id TEXT NOT NULL PRIMARY KEY,
    au TEXT,
    ti TEXT,
    so TEXT,
    vl TEXT,
    bp TEXT,
    ep TEXT,
    ar TEXT,
    py TEXT,
    di TEXT,
    dl TEXT,
    pm TEXT
) WITHOUT ROWID;
CREATE TABLE citation_conflicts (
    file_id TEXT NOT NULL PRIMARY KEY,
    conflict_json TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE citation_build_metadata (
    schema_version INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_record_count INTEGER NOT NULL,
    unique_id_count INTEGER NOT NULL,
    imported_record_count INTEGER NOT NULL,
    exact_duplicate_row_count INTEGER NOT NULL,
    conflict_id_count INTEGER NOT NULL,
    quarantined_row_count INTEGER NOT NULL,
    missing_doi_count INTEGER NOT NULL,
    invalid_doi_count INTEGER NOT NULL,
    missing_title_count INTEGER NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class _CitationBuildMetadataSource:
    """Schema-v1 source accounting fields shared by citation metadata."""

    schema_version: int
    source_sha256: str
    source_record_count: int
    unique_id_count: int
    imported_record_count: int
    exact_duplicate_row_count: int


@dataclass(frozen=True, slots=True)
class CitationBuildMetadata(_CitationBuildMetadataSource):
    """Validated singleton metadata attached to a citation artifact."""

    conflict_id_count: int
    quarantined_row_count: int
    missing_doi_count: int
    invalid_doi_count: int
    missing_title_count: int


class CitationDatabaseError(RuntimeError):
    """Bounded, path-free base error for citation database serving."""

    code: str

    def __init__(self) -> None:
        """Expose only the stable remediation message to callers."""
        super().__init__(f"{self.code}: verify CITATION_DB_PATH")


class CitationDatabaseConfigurationError(CitationDatabaseError):
    """The optional citation artifact path was not configured."""

    code = "citation_db_configuration_missing"


class CitationDatabaseArtifactError(CitationDatabaseError):
    """The configured citation artifact cannot be used as a regular file."""

    code = "citation_db_artifact_unavailable"


class CitationDatabaseFormatError(CitationDatabaseError):
    """The artifact is not a readable SQLite database."""

    code = "citation_db_invalid_sqlite"


class CitationDatabaseSchemaError(CitationDatabaseError):
    """The SQLite version or table layout does not match schema v1."""

    code = "citation_db_schema_mismatch"


class CitationDatabaseMetadataError(CitationDatabaseError):
    """The singleton artifact metadata fails its count invariants."""

    code = "citation_db_metadata_mismatch"


class CitationDatabaseIntegrityError(CitationDatabaseError):
    """SQLite's integrity check did not report a healthy artifact."""

    code = "citation_db_integrity_failed"


class CitationDatabaseLookupError(CitationDatabaseError):
    """A bounded read-only citation lookup could not complete."""

    code = "citation_db_lookup_failed"


ERROR_CODE_BY_CLASS = {
    CitationDatabaseConfigurationError: "citation_db_configuration_missing",
    CitationDatabaseArtifactError: "citation_db_artifact_unavailable",
    CitationDatabaseFormatError: "citation_db_invalid_sqlite",
    CitationDatabaseSchemaError: "citation_db_schema_mismatch",
    CitationDatabaseMetadataError: "citation_db_metadata_mismatch",
    CitationDatabaseIntegrityError: "citation_db_integrity_failed",
    CitationDatabaseLookupError: "citation_db_lookup_failed",
}


@dataclass(frozen=True, slots=True)
class CitationLookupResult:
    """Complete lookup projection for requested citation record IDs."""

    records: Mapping[str, Mapping[str, str | None]]
    outcome: Literal["complete"] = "complete"


@lru_cache(maxsize=1)
def resolve_citation_database_path() -> Path:
    """Return the configured regular-file artifact without opening SQLite."""
    raw_path = CitationConfig().CITATION_DB_PATH
    if raw_path is None:
        raise CitationDatabaseConfigurationError()
    return _normalize_database_path(Path(raw_path))


def validate_citation_database(
    path: Path | None = None,
) -> CitationBuildMetadata:
    """Validate one schema-v1 citation artifact via a read-only connection."""
    database_path = (
        resolve_citation_database_path()
        if path is None
        else _normalize_database_path(path)
    )
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only(database_path)
        return _validate_connection(connection)
    except (sqlite3.Error, OSError):
        raise CitationDatabaseFormatError() from None
    finally:
        if connection is not None:
            connection.close()


async def lookup_citation_records(
    file_ids: Sequence[str],
) -> CitationLookupResult:
    """Fetch canonical records with short-lived, parameterized SQLite reads."""
    unique_ids = tuple(dict.fromkeys(file_ids))
    if not unique_ids:
        return CitationLookupResult(records={})
    return await asyncio.to_thread(_lookup_citation_records, unique_ids)


def _normalize_database_path(path: Path) -> Path:
    """Resolve one artifact target while rejecting unavailable file types."""
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError:
        raise CitationDatabaseArtifactError() from None
    if not resolved.is_file() or not os.access(resolved, os.R_OK):
        raise CitationDatabaseArtifactError()
    return resolved


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """Open a regular citation artifact in read-only, query-only mode."""
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro",
        uri=True,
        check_same_thread=True,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
    except (sqlite3.Error, OSError):
        with suppress(sqlite3.Error, OSError):
            connection.close()
        raise
    return connection


def _validate_connection(
    connection: sqlite3.Connection,
) -> CitationBuildMetadata:
    """Check exact schema, metadata equations, keys, and SQLite integrity."""
    if _pragma_value(connection, "user_version") != CITATION_SCHEMA_VERSION:
        raise CitationDatabaseSchemaError()
    _validate_application_tables(connection)
    _validate_table_columns(connection, "citation_records", _RECORD_COLUMNS)
    _validate_table_columns(
        connection,
        "citation_conflicts",
        _CONFLICT_COLUMNS,
    )
    _validate_table_columns(
        connection,
        "citation_build_metadata",
        _METADATA_COLUMNS,
    )
    _validate_keyed_tables_without_rowid(connection)
    _validate_nonblank_keys(connection)
    metadata = _load_metadata(connection)
    _validate_metadata_equations(connection, metadata)
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise CitationDatabaseIntegrityError()
    return metadata


def _pragma_value(connection: sqlite3.Connection, name: str) -> object:
    """Return a scalar pragma value from a module-owned pragma name."""
    row = connection.execute(f"PRAGMA {name}").fetchone()
    return None if row is None else row[0]


def _validate_application_tables(connection: sqlite3.Connection) -> None:
    """Require exactly the three application tables owned by schema v1."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    if {str(row[0]) for row in rows} != CITATION_APPLICATION_TABLES:
        raise CitationDatabaseSchemaError()


def _validate_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
    expected: tuple[tuple[str, str, int, int], ...],
) -> None:
    """Require the exact columns, types, nullability, and primary keys."""
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    actual = tuple(
        (str(row[1]), str(row[2]), int(row[3]), int(row[5])) for row in rows
    )
    if actual != expected:
        raise CitationDatabaseSchemaError()


def _validate_keyed_tables_without_rowid(
    connection: sqlite3.Connection,
) -> None:
    """Require both file-id keyed tables to remain rowid-free."""
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name IN ('citation_records', "
        "'citation_conflicts')"
    ).fetchall()
    if len(rows) != 2 or any(
        "WITHOUT ROWID" not in str(row[1]).upper() for row in rows
    ):
        raise CitationDatabaseSchemaError()


def _validate_nonblank_keys(connection: sqlite3.Connection) -> None:
    """Reject blank IDs in either keyed application table."""
    for table_name in ("citation_records", "citation_conflicts"):
        count = connection.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE trim(file_id) = ''"
        ).fetchone()[0]
        if count:
            raise CitationDatabaseMetadataError()


def _load_metadata(connection: sqlite3.Connection) -> CitationBuildMetadata:
    """Load and type-check the exactly-one artifact metadata row."""
    rows = connection.execute(
        f"SELECT {','.join(_METADATA_FIELD_NAMES)} "
        "FROM citation_build_metadata"
    ).fetchall()
    if len(rows) != 1:
        raise CitationDatabaseMetadataError()
    values = tuple(rows[0])
    source_sha256 = values[1]
    count_values = values[0], *values[2:]
    if (
        not isinstance(source_sha256, str)
        or _SOURCE_SHA256_PATTERN.fullmatch(source_sha256) is None
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in count_values
        )
    ):
        raise CitationDatabaseMetadataError()
    return CitationBuildMetadata(*values)


def _validate_metadata_equations(
    connection: sqlite3.Connection,
    metadata: CitationBuildMetadata,
) -> None:
    """Verify table counts and source accounting equations for schema v1."""
    record_count = connection.execute(
        "SELECT COUNT(*) FROM citation_records"
    ).fetchone()[0]
    conflict_count = connection.execute(
        "SELECT COUNT(*) FROM citation_conflicts"
    ).fetchone()[0]
    if (
        metadata.schema_version != CITATION_SCHEMA_VERSION
        or metadata.imported_record_count != record_count
        or metadata.conflict_id_count != conflict_count
        or metadata.unique_id_count
        != metadata.imported_record_count + metadata.conflict_id_count
        or metadata.source_record_count
        != metadata.imported_record_count
        + metadata.exact_duplicate_row_count
        + metadata.quarantined_row_count
    ):
        raise CitationDatabaseMetadataError()


def _lookup_citation_records(
    unique_ids: tuple[str, ...],
) -> CitationLookupResult:
    """Run all lookup batches synchronously for one worker-thread call."""
    database_path = resolve_citation_database_path()
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only(database_path)
        records: dict[str, Mapping[str, str | None]] = {}
        columns = ("file_id", *CITATION_RECORD_FIELDS)
        for start in range(0, len(unique_ids), CITATION_LOOKUP_BATCH_SIZE):
            end = min(start + CITATION_LOOKUP_BATCH_SIZE, len(unique_ids))
            chunk = unique_ids[start:end]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"SELECT {','.join(columns)} FROM citation_records "
                f"WHERE file_id IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for row in rows:
                file_id = str(row["file_id"])
                records[file_id] = {
                    field: row[field] for field in CITATION_RECORD_FIELDS
                }
        ordered_records = {
            file_id: records[file_id]
            for file_id in unique_ids
            if file_id in records
        }
        return CitationLookupResult(records=ordered_records)
    except (sqlite3.Error, OSError):
        raise CitationDatabaseLookupError() from None
    finally:
        if connection is not None:
            connection.close()


__all__ = [
    "CITATION_APPLICATION_TABLES",
    "CITATION_BUILD_METADATA_FIELDS",
    "CITATION_DATABASE_SCHEMA",
    "CITATION_LOOKUP_BATCH_SIZE",
    "CITATION_RECORD_FIELDS",
    "CITATION_SCHEMA_VERSION",
    "CitationBuildMetadata",
    "CitationDatabaseArtifactError",
    "CitationDatabaseConfigurationError",
    "CitationDatabaseError",
    "CitationDatabaseFormatError",
    "CitationDatabaseIntegrityError",
    "CitationDatabaseLookupError",
    "CitationDatabaseMetadataError",
    "CitationDatabaseSchemaError",
    "CitationLookupResult",
    "ERROR_CODE_BY_CLASS",
    "lookup_citation_records",
    "resolve_citation_database_path",
    "validate_citation_database",
]
