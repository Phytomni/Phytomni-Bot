# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Build and validate offline SQLite artifacts for citation metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

from mcp_server_phytomni.agents.shared.citation_database import (
    CITATION_DATABASE_SCHEMA,
    CITATION_SCHEMA_VERSION,
    CitationBuildMetadata,
    CitationDatabaseError,
    validate_citation_database,
)
from mcp_server_phytomni.agents.shared.citation_metadata import (
    CITATION_RECORD_FIELDS,
    CITATION_SOURCE_FIELDS,
    normalize_doi,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TEMPORARY_TABLE = "_citation_variants"
_TEMPORARY_SUFFIX = ".citation-tmp"
_CANONICAL_TO_SOURCE_FIELD = {
    canonical: source for source, canonical in CITATION_SOURCE_FIELDS.items()
}

_VARIANT_TABLE_SCHEMA = """
CREATE TABLE _citation_variants (
    file_id TEXT NOT NULL,
    fingerprint BLOB NOT NULL,
    canonical_json TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL,
    source_lines TEXT NOT NULL,
    au TEXT, ti TEXT, so TEXT, vl TEXT, bp TEXT, ep TEXT,
    ar TEXT, py TEXT, di TEXT, dl TEXT, pm TEXT,
    PRIMARY KEY (file_id, fingerprint)
) WITHOUT ROWID;
"""


class CitationBuildError(RuntimeError):
    """A bounded offline-build failure that never includes source content."""


def _reject_json_constant(_value: str) -> NoReturn:
    """Reject non-standard JSON constants without exposing their spelling."""
    raise CitationBuildError()


def normalize_source_record(
    payload: object,
    *,
    line_number: int,
) -> dict[str, str | None]:
    """Map one strict source object to the canonical citation record shape."""
    if line_number < 1:
        raise CitationBuildError()
    if not isinstance(payload, dict):
        raise CitationBuildError()

    raw_file_id = payload.get("id")
    if not isinstance(raw_file_id, str):
        raise CitationBuildError()
    file_id = raw_file_id.strip()
    if not file_id:
        raise CitationBuildError()

    record: dict[str, str | None] = {"file_id": file_id}
    for field in CITATION_RECORD_FIELDS:
        source_field = _CANONICAL_TO_SOURCE_FIELD.get(field)
        if source_field is None:
            record[field] = None
            continue
        if source_field not in payload:
            record[field] = None
        else:
            raw_value = payload[source_field]
            if not isinstance(raw_value, str):
                raise CitationBuildError()
            record[field] = raw_value.strip() or None

    raw_doi = record["di"]
    record["di"] = normalize_doi(raw_doi)
    return record


def build_citation_database(
    source: Path,
    output: Path,
    *,
    expected_sha256: str | None = None,
) -> CitationBuildMetadata:
    """Stream source JSONL into one validated SQLite artifact atomically."""
    if expected_sha256 is not None and not _is_lowercase_sha256(
        expected_sha256
    ):
        raise CitationBuildError()

    source_path = _source_path(source)
    output_path = _output_path(output)
    if source_path == output_path:
        raise CitationBuildError()
    temporary_path = _temporary_path(output_path)
    temporary_created = False
    connection: sqlite3.Connection | None = None
    try:
        temporary_path.open("xb").close()
        temporary_created = True
        connection = sqlite3.connect(temporary_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(CITATION_DATABASE_SCHEMA)
        connection.executescript(_VARIANT_TABLE_SCHEMA)

        source_sha256, quality_counts, source_record_count = _ingest_source(
            connection,
            source_path,
        )
        if expected_sha256 is not None and source_sha256 != expected_sha256:
            raise CitationBuildError()

        metadata = _materialize_artifact(
            connection,
            source_sha256=source_sha256,
            source_record_count=source_record_count,
            quality_counts=quality_counts,
        )
        connection.execute(f"DROP TABLE {_TEMPORARY_TABLE}")
        connection.commit()
        connection.execute("VACUUM")
        connection.execute(f"PRAGMA user_version={CITATION_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        connection = None

        validate_citation_database(temporary_path)
        _fsync_file(temporary_path)
        _fsync_directory(output_path.parent)
        os.replace(temporary_path, output_path)
        return metadata
    except Exception as exc:
        if isinstance(exc, CitationBuildError):
            raise
        raise CitationBuildError() from None
    finally:
        if connection is not None:
            with suppress(sqlite3.Error, OSError):
                connection.close()
        if temporary_created:
            _cleanup_temporary_files(temporary_path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the intentionally small offline operator CLI contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser("build")
    build.add_argument("--source", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--expected-sha256", type=_parse_lowercase_sha256)

    validate = subcommands.add_parser("validate")
    validate.add_argument("--database", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one build or read-only validation without leaking artifact data."""
    args = parse_args(argv)
    try:
        if args.command == "build":
            metadata = build_citation_database(
                args.source,
                args.output,
                expected_sha256=args.expected_sha256,
            )
        else:
            metadata = validate_citation_database(args.database)
    except CitationBuildError:
        print("citation_db_build_failed", file=sys.stderr)
        return 1
    except (CitationDatabaseError, OSError, sqlite3.Error):
        print("citation_db_validation_failed", file=sys.stderr)
        return 1
    print(
        json.dumps(
            asdict(metadata),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _ingest_source(
    connection: sqlite3.Connection,
    source: Path,
) -> tuple[str, dict[str, int], int]:
    """Read binary JSONL while incrementally hashing and accounting quality."""
    digest = hashlib.sha256()
    quality_counts = {
        "missing_doi_count": 0,
        "invalid_doi_count": 0,
        "missing_title_count": 0,
    }
    source_record_count = 0
    try:
        with source.open("rb") as source_file:
            for line_number, raw_line in enumerate(source_file, start=1):
                digest.update(raw_line)
                try:
                    payload = json.loads(
                        raw_line.decode("utf-8"),
                        parse_constant=_reject_json_constant,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise CitationBuildError() from None
                record = normalize_source_record(
                    payload, line_number=line_number
                )
                _count_source_quality(payload, record, quality_counts)
                _upsert_variant(connection, record, line_number)
                source_record_count += 1
    except OSError:
        raise CitationBuildError() from None
    return digest.hexdigest(), quality_counts, source_record_count


def _count_source_quality(
    payload: object,
    record: dict[str, str | None],
    quality_counts: dict[str, int],
) -> None:
    """Count source-row DOI and title quality before variant resolution."""
    if record["ti"] is None:
        quality_counts["missing_title_count"] += 1
    raw_doi = payload.get("DI") if isinstance(payload, dict) else None
    if raw_doi is None or (isinstance(raw_doi, str) and not raw_doi.strip()):
        quality_counts["missing_doi_count"] += 1
    elif record["di"] is None:
        quality_counts["invalid_doi_count"] += 1


def _upsert_variant(
    connection: sqlite3.Connection,
    record: dict[str, str | None],
    line_number: int,
) -> None:
    """Persist one canonical variant without retaining source identifiers."""
    canonical_json = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical_json.encode("utf-8")).digest()
    existing = connection.execute(
        "SELECT canonical_json FROM _citation_variants "
        "WHERE file_id = ? AND fingerprint = ?",
        (record["file_id"], fingerprint),
    ).fetchone()
    if existing is not None:
        if existing["canonical_json"] != canonical_json:
            raise CitationBuildError()
        connection.execute(
            "UPDATE _citation_variants SET occurrence_count = "
            "occurrence_count + 1, source_lines = source_lines || ',' || ? "
            "WHERE file_id = ? AND fingerprint = ?",
            (str(line_number), record["file_id"], fingerprint),
        )
        return
    connection.execute(
        "INSERT INTO _citation_variants ("
        "file_id, fingerprint, canonical_json, occurrence_count, "
        "source_lines, "
        + ", ".join(CITATION_RECORD_FIELDS)
        + ") VALUES ("
        + ", ".join("?" for _ in range(5 + len(CITATION_RECORD_FIELDS)))
        + ")",
        (
            record["file_id"],
            fingerprint,
            canonical_json,
            1,
            str(line_number),
            *(record[field] for field in CITATION_RECORD_FIELDS),
        ),
    )


def _materialize_artifact(
    connection: sqlite3.Connection,
    *,
    source_sha256: str,
    source_record_count: int,
    quality_counts: dict[str, int],
) -> CitationBuildMetadata:
    """Materialize records, conflicts, and the singleton metadata row."""
    _insert_conflicts(connection)
    record_columns = ", ".join(("file_id", *CITATION_RECORD_FIELDS))
    connection.execute(
        "INSERT INTO citation_records ("
        + record_columns
        + ") SELECT "
        + record_columns
        + " FROM _citation_variants WHERE file_id IN ("
        "SELECT file_id FROM _citation_variants GROUP BY file_id "
        "HAVING COUNT(*) = 1)"
    )
    imported_record_count = _count_rows(connection, "citation_records")
    conflict_id_count = _count_rows(connection, "citation_conflicts")
    unique_id_count = int(
        connection.execute(
            "SELECT COUNT(DISTINCT file_id) FROM _citation_variants"
        ).fetchone()[0]
    )
    exact_duplicate_row_count = _count_variant_occurrences(
        connection,
        "COUNT(*) = 1",
        subtract_one=True,
    )
    quarantined_row_count = _count_variant_occurrences(
        connection,
        "COUNT(*) > 1",
        subtract_one=False,
    )
    metadata = CitationBuildMetadata(
        schema_version=CITATION_SCHEMA_VERSION,
        source_sha256=source_sha256,
        source_record_count=source_record_count,
        unique_id_count=unique_id_count,
        imported_record_count=imported_record_count,
        exact_duplicate_row_count=exact_duplicate_row_count,
        conflict_id_count=conflict_id_count,
        quarantined_row_count=quarantined_row_count,
        missing_doi_count=quality_counts["missing_doi_count"],
        invalid_doi_count=quality_counts["invalid_doi_count"],
        missing_title_count=quality_counts["missing_title_count"],
    )
    connection.execute(
        "INSERT INTO citation_build_metadata ("
        "schema_version, source_sha256, source_record_count, unique_id_count, "
        "imported_record_count, exact_duplicate_row_count, conflict_id_count, "
        "quarantined_row_count, missing_doi_count, invalid_doi_count, "
        "missing_title_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(asdict(metadata).values()),
    )
    return metadata


def _insert_conflicts(connection: sqlite3.Connection) -> None:
    """Quarantine ambiguous identifiers with line and field evidence."""
    query = (
        "SELECT file_id, COUNT(*) AS variant_count FROM _citation_variants "
        "GROUP BY file_id HAVING COUNT(*) > 1"
    )
    for group in connection.execute(query):
        file_id = group["file_id"]
        variants = connection.execute(
            "SELECT source_lines, "
            + ", ".join(CITATION_RECORD_FIELDS)
            + " FROM _citation_variants WHERE file_id = ? "
            "ORDER BY fingerprint",
            (file_id,),
        )
        source_lines: list[int] = []
        values_by_field: dict[str, set[str | None]] = {
            field: set() for field in CITATION_RECORD_FIELDS
        }
        for variant in variants:
            source_lines.extend(
                int(value) for value in variant["source_lines"].split(",")
            )
            for field in CITATION_RECORD_FIELDS:
                values_by_field[field].add(variant[field])
        conflict_json = json.dumps(
            {
                "variant_count": group["variant_count"],
                "source_lines": sorted(source_lines),
                "differing_fields": [
                    field
                    for field in CITATION_RECORD_FIELDS
                    if len(values_by_field[field]) > 1
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO citation_conflicts (file_id, conflict_json) "
            "VALUES (?, ?)",
            (file_id, conflict_json),
        )


def _count_variant_occurrences(
    connection: sqlite3.Connection,
    group_condition: str,
    *,
    subtract_one: bool,
) -> int:
    """Count grouped variant occurrences without retaining Python IDs."""
    adjustment = "occurrence_count - 1" if subtract_one else "occurrence_count"
    row = connection.execute(
        "SELECT COALESCE(SUM("
        + adjustment
        + "), 0) FROM _citation_variants WHERE file_id IN ("
        "SELECT file_id FROM _citation_variants GROUP BY file_id HAVING "
        + group_condition
        + ")"
    ).fetchone()
    return int(row[0])


def _count_rows(connection: sqlite3.Connection, table_name: str) -> int:
    """Return a row count for one module-owned table name."""
    return int(
        connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    )


def _source_path(source: Path) -> Path:
    """Resolve a readable regular JSONL source without exposing paths."""
    try:
        resolved = source.expanduser().resolve(strict=True)
    except OSError:
        raise CitationBuildError() from None
    if not resolved.is_file() or not os.access(resolved, os.R_OK):
        raise CitationBuildError()
    return resolved


def _output_path(output: Path) -> Path:
    """Resolve an output directory for atomic target replacement."""
    try:
        parent = output.expanduser().parent.resolve(strict=True)
    except OSError:
        raise CitationBuildError() from None
    if not parent.is_dir():
        raise CitationBuildError()
    return parent / output.name


def _temporary_path(output: Path) -> Path:
    """Return a collision-resistant, builder-owned temporary sibling path."""
    return output.with_name(
        f".{output.name}.{secrets.token_hex(16)}{_TEMPORARY_SUFFIX}"
    )


def _cleanup_temporary_files(temporary_path: Path) -> None:
    """Remove only temporary files allocated by this builder instance."""
    for path in (
        temporary_path,
        Path(f"{temporary_path}-journal"),
        Path(f"{temporary_path}-shm"),
        Path(f"{temporary_path}-wal"),
    ):
        with suppress(FileNotFoundError, OSError):
            path.unlink()


def _fsync_file(path: Path) -> None:
    """Flush a closed artifact where the local filesystem supports fsync."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        with suppress(OSError):
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    """Flush the destination directory where directory fsync is supported."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        with suppress(OSError):
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_lowercase_sha256(value: str) -> str:
    """Adapt the public expected-digest validation to argparse's error path."""
    if not _is_lowercase_sha256(value):
        raise argparse.ArgumentTypeError(
            "expected SHA-256 must be lowercase hex"
        )
    return value


def _is_lowercase_sha256(value: str) -> bool:
    """Return whether one value is the exact public SHA-256 spelling."""
    return isinstance(value, str) and bool(_SHA256_PATTERN.fullmatch(value))


if __name__ == "__main__":
    raise SystemExit(main())
