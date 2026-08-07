# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for exact read-only citation SQLite validation and lookup."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import pytest
from tests.support.citation_database import create_valid_citation_database

from mcp_server_phytomni.agents.shared import citation_database
from mcp_server_phytomni.agents.shared.citation_database import (
    CITATION_APPLICATION_TABLES,
    CITATION_BUILD_METADATA_FIELDS,
    CITATION_LOOKUP_BATCH_SIZE,
    CITATION_RECORD_FIELDS,
    CITATION_SCHEMA_VERSION,
    CitationBuildMetadata,
    CitationDatabaseArtifactError,
    CitationDatabaseConfigurationError,
    CitationDatabaseFormatError,
    CitationDatabaseIntegrityError,
    CitationDatabaseLookupError,
    CitationDatabaseMetadataError,
    CitationDatabaseSchemaError,
    lookup_citation_records,
    resolve_citation_database_path,
    validate_citation_database,
)

pytestmark = pytest.mark.unit


def _record(file_id: str, **overrides: str | None) -> dict[str, str | None]:
    """Return one complete canonical citation record."""
    record: dict[str, str | None] = {
        field: f"{field}-{file_id}" for field in CITATION_RECORD_FIELDS
    }
    record.update(overrides)
    return {"file_id": file_id, **record}


@contextmanager
def _connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a mutable test connection and close it afterwards."""
    connection = sqlite3.connect(path)
    try:
        yield connection
    finally:
        connection.close()


def _assert_stable_error(
    error: Exception,
    code: str,
    *forbidden: str,
) -> None:
    """Verify the public failure retains neither inputs nor driver details."""
    assert str(error) == f"{code}: verify CITATION_DB_PATH"
    assert all(marker not in str(error) for marker in forbidden)


def test_build_metadata_has_declared_dataclass_fields() -> None:
    """Metadata fields remain visible to dataclass reflection and export."""
    metadata = CitationBuildMetadata(
        1,
        "a" * 64,
        4,
        3,
        2,
        1,
        1,
        0,
        1,
        0,
        1,
    )
    expected_fields = (
        "schema_version",
        "source_sha256",
        "source_record_count",
        "unique_id_count",
        "imported_record_count",
        "exact_duplicate_row_count",
        "conflict_id_count",
        "quarantined_row_count",
        "missing_doi_count",
        "invalid_doi_count",
        "missing_title_count",
    )

    assert tuple(field.name for field in fields(metadata)) == expected_fields
    assert tuple(asdict(metadata)) == expected_fields
    assert asdict(metadata)["conflict_id_count"] == 1


def test_readonly_connection_closes_on_query_only_initialization_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed query-only setup closes its connection before raising."""

    class _FailingConnection:
        """Connection fake that fails while query-only mode is enabled."""

        row_factory: object | None = None

        def __init__(self) -> None:
            """Track whether serving code closes this connection."""
            self.closed = False

        def execute(self, _query: str) -> None:
            """Raise the bounded SQLite initialization failure."""
            raise sqlite3.OperationalError("query_only setup failed")

        def close(self) -> None:
            """Record the required cleanup call."""
            self.closed = True

    connection = _FailingConnection()
    monkeypatch.setattr(
        citation_database.sqlite3,
        "connect",
        lambda *_args, **_kwargs: connection,
    )
    connect_read_only = getattr(citation_database, "_connect_read_only")

    with pytest.raises(sqlite3.OperationalError):
        connect_read_only(tmp_path / "citation.sqlite")

    assert connection.closed


def test_resolver_requires_config_without_creating_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing config fails lazily and never creates a SQLite artifact."""
    target = tmp_path / "not-created.sqlite"
    monkeypatch.delenv("CITATION_DB_PATH", raising=False)
    monkeypatch.delenv("PHYTOMNI_CITATION_DB_PATH", raising=False)
    resolve_citation_database_path.cache_clear()

    with pytest.raises(CitationDatabaseConfigurationError) as exc:
        resolve_citation_database_path()

    _assert_stable_error(
        exc.value,
        "citation_db_configuration_missing",
        str(target),
    )
    assert not target.exists()
    resolve_citation_database_path.cache_clear()


def test_resolver_returns_one_normalized_absolute_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured artifact resolves to its normalized absolute path."""
    path = create_valid_citation_database(tmp_path / "citation.sqlite")
    monkeypatch.setenv("CITATION_DB_PATH", str(path.parent / "." / path.name))
    resolve_citation_database_path.cache_clear()

    resolved = resolve_citation_database_path()

    assert resolved == path.resolve(strict=True)
    assert resolved.is_absolute() and resolved.is_file()
    resolve_citation_database_path.cache_clear()


def test_validate_accepts_exact_schema_and_metadata(tmp_path: Path) -> None:
    """One schema-v1 artifact satisfies every serving invariant."""
    path = create_valid_citation_database(
        tmp_path / "citation.sqlite",
        records=(_record("first"), _record("second")),
    )
    metadata = validate_citation_database(path)

    assert metadata.schema_version == CITATION_SCHEMA_VERSION
    assert metadata.source_sha256 == "a" * 64
    assert metadata.source_record_count == 2
    assert metadata.unique_id_count == 2
    assert metadata.imported_record_count == 2
    assert metadata.exact_duplicate_row_count == 0
    assert metadata.conflict_id_count == 0
    assert metadata.quarantined_row_count == 0
    assert metadata.missing_doi_count == 0
    assert metadata.invalid_doi_count == 0
    assert metadata.missing_title_count == 0
    assert metadata.unique_id_count == (
        metadata.imported_record_count + metadata.conflict_id_count
    )
    assert metadata.source_record_count == (
        metadata.imported_record_count
        + metadata.exact_duplicate_row_count
        + metadata.quarantined_row_count
    )

    with _connection(path) as connection:
        tables = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            )
        }
        assert set(tables) == CITATION_APPLICATION_TABLES
        assert "WITHOUT ROWID" in tables["citation_records"].upper()
        assert "WITHOUT ROWID" in tables["citation_conflicts"].upper()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert _column_names(connection, "citation_records") == (
            "file_id",
            *CITATION_RECORD_FIELDS,
        )
        assert _column_names(connection, "citation_conflicts") == (
            "file_id",
            "conflict_json",
        )
        assert _column_names(connection, "citation_build_metadata") == (
            CITATION_BUILD_METADATA_FIELDS
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM citation_records"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM citation_conflicts"
            ).fetchone()[0]
            == 0
        )


def test_validate_accepts_factory_conflict_metadata(tmp_path: Path) -> None:
    """A conflict-containing factory artifact needs no metadata repair."""
    path = create_valid_citation_database(
        tmp_path / "citation.sqlite",
        records=(_record("record"),),
        conflicts=({"file_id": "conflict"},),
    )

    metadata = validate_citation_database(path)

    assert metadata.source_record_count == 1
    assert metadata.unique_id_count == 2
    assert metadata.imported_record_count == 1
    assert metadata.exact_duplicate_row_count == 0
    assert metadata.conflict_id_count == 1
    assert metadata.quarantined_row_count == 0
    assert metadata.source_record_count == (
        metadata.imported_record_count
        + metadata.exact_duplicate_row_count
        + metadata.quarantined_row_count
    )
    assert metadata.unique_id_count == (
        metadata.imported_record_count + metadata.conflict_id_count
    )


def test_validate_accepts_imported_duplicate_and_quarantined_conflict(
    tmp_path: Path,
) -> None:
    """Source accounting uses imported rows, not unique IDs."""
    path = create_valid_citation_database(
        tmp_path / "citation.sqlite",
        records=(_record("first"), _record("second"), _record("third")),
        conflicts=({"file_id": "conflict"},),
    )
    with _connection(path) as connection:
        connection.execute(
            "UPDATE citation_build_metadata SET "
            "source_record_count=7, unique_id_count=4, "
            "imported_record_count=3, exact_duplicate_row_count=1, "
            "conflict_id_count=1, quarantined_row_count=3"
        )
        connection.commit()

    metadata = validate_citation_database(path)

    assert metadata.source_record_count == 7
    assert metadata.unique_id_count == 4
    assert metadata.imported_record_count == 3
    assert metadata.exact_duplicate_row_count == 1
    assert metadata.conflict_id_count == 1
    assert metadata.quarantined_row_count == 3


def _column_names(
    connection: sqlite3.Connection,
    table_name: str,
) -> tuple[str, ...]:
    """Return one table's ordered SQLite column names."""
    return tuple(
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    )


def test_validate_rejects_non_sqlite(tmp_path: Path) -> None:
    """A text file is not a serving citation database."""
    path = tmp_path / "not-sqlite.sqlite"
    path.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(CitationDatabaseFormatError) as exc:
        validate_citation_database(path)

    _assert_stable_error(
        exc.value,
        "citation_db_invalid_sqlite",
        str(path),
        "file is not a database",
    )


def test_validate_rejects_wrong_user_version(citation_db_path: Path) -> None:
    """Only schema version one is compatible with this runtime."""
    with _connection(citation_db_path) as connection:
        connection.execute("PRAGMA user_version=2")
        connection.commit()

    with pytest.raises(CitationDatabaseSchemaError) as exc:
        validate_citation_database(citation_db_path)

    _assert_stable_error(
        exc.value, "citation_db_schema_mismatch", str(citation_db_path)
    )


@pytest.mark.parametrize(
    "statement",
    (
        "ALTER TABLE citation_records ADD COLUMN unexpected TEXT",
        "DROP TABLE citation_conflicts",
    ),
)
def test_validate_rejects_extra_or_wrong_columns(
    citation_db_path: Path,
    statement: str,
) -> None:
    """Extra columns and missing application tables are schema mismatches."""
    with _connection(citation_db_path) as connection:
        connection.execute(statement)
        connection.commit()

    with pytest.raises(CitationDatabaseSchemaError):
        validate_citation_database(citation_db_path)


@pytest.mark.parametrize("metadata_rows", (0, 2))
def test_validate_rejects_missing_or_multiple_metadata_rows(
    citation_db_path: Path,
    metadata_rows: int,
) -> None:
    """Metadata is a singleton contract, never an optional history table."""
    with _connection(citation_db_path) as connection:
        connection.execute("DELETE FROM citation_build_metadata")
        if metadata_rows == 2:
            connection.execute(
                "INSERT INTO citation_build_metadata VALUES "
                "(1, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
                ("a" * 64,),
            )
            connection.execute(
                "INSERT INTO citation_build_metadata VALUES "
                "(1, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0)",
                ("b" * 64,),
            )
        connection.commit()

    with pytest.raises(CitationDatabaseMetadataError):
        validate_citation_database(citation_db_path)


def test_validate_rejects_count_equation_mismatch(
    citation_db_path: Path,
) -> None:
    """Derived metadata counts must agree with each other and table rows."""
    with _connection(citation_db_path) as connection:
        connection.execute(
            "UPDATE citation_build_metadata SET unique_id_count=1"
        )
        connection.commit()

    with pytest.raises(CitationDatabaseMetadataError):
        validate_citation_database(citation_db_path)


def test_validate_rejects_non_ok_integrity_result(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing integrity check maps to its dedicated public error."""
    real_connect = getattr(citation_database, "_connect_read_only")

    class _IntegrityConnection:
        """Wrap one connection while substituting the integrity result."""

        def __init__(self, connection: sqlite3.Connection) -> None:
            """Store the underlying read-only connection."""
            self._connection = connection

        def execute(self, query: str, *args: Any) -> Any:
            """Return a failing result for the integrity pragma only."""
            if query == "PRAGMA integrity_check":
                return self._connection.execute("SELECT 'not ok'")
            return self._connection.execute(query, *args)

        def close(self) -> None:
            """Close the underlying connection."""
            self._connection.close()

    monkeypatch.setattr(
        citation_database,
        "_connect_read_only",
        lambda path: _IntegrityConnection(real_connect(path)),
    )

    with pytest.raises(CitationDatabaseIntegrityError) as exc:
        validate_citation_database(citation_db_path)

    _assert_stable_error(exc.value, "citation_db_integrity_failed")


@pytest.mark.asyncio
async def test_errors_never_include_path_sql_id_or_driver_body(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every public SQLite error remains bounded and path-free."""
    hostile_id = "id'; SELECT secret_token FROM hidden; --"

    def broken_connection(_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("driver body SELECT secret_token")

    monkeypatch.setattr(
        citation_database, "_connect_read_only", broken_connection
    )

    with pytest.raises(CitationDatabaseLookupError) as exc:
        await lookup_citation_records([hostile_id])

    _assert_stable_error(
        exc.value,
        "citation_db_lookup_failed",
        str(citation_db_path),
        "SELECT secret_token",
        hostile_id,
        "driver body",
    )


@pytest.mark.asyncio
async def test_lookup_no_ids_returns_empty_without_opening_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty lookup is complete without config or a connection attempt."""
    monkeypatch.setattr(
        citation_database,
        "_connect_read_only",
        lambda _path: pytest.fail("must not open SQLite for no IDs"),
    )

    result = await lookup_citation_records([])

    assert result.records == {}
    assert result.outcome == "complete"


@pytest.mark.asyncio
async def test_lookup_deduplicates_in_first_request_order(
    citation_db_path: Path,
) -> None:
    """Repeated IDs do not change the first-seen result ordering."""
    with _connection(citation_db_path) as connection:
        connection.execute("DELETE FROM citation_build_metadata")
        connection.execute(
            "INSERT INTO citation_build_metadata VALUES "
            "(1, ?, 2, 2, 2, 0, 0, 0, 0, 0, 0)",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO citation_records (file_id, au) VALUES (?, ?)",
            ("second", "AU2"),
        )
        connection.execute(
            "INSERT INTO citation_records (file_id, au) VALUES (?, ?)",
            ("first", "AU1"),
        )
        connection.commit()

    result = await lookup_citation_records(["second", "first", "second"])

    assert list(result.records) == ["second", "first"]
    assert result.records["second"]["au"] == "AU2"


@pytest.mark.asyncio
async def test_lookup_binds_sql_punctuation_id_only(
    citation_db_path: Path,
) -> None:
    """A quote-bearing ID is a value, not a fragment of query text."""
    hostile_id = "paper'; DROP TABLE citation_records; --"
    replacement = create_valid_citation_database(
        citation_db_path.with_name("replacement.sqlite"),
        records=(_record(hostile_id, au="safe"), _record("other")),
    )
    os.replace(replacement, citation_db_path)

    result = await lookup_citation_records([hostile_id, "other"])

    assert list(result.records) == [hostile_id, "other"]
    assert result.records[hostile_id]["au"] == "safe"


@pytest.mark.asyncio
async def test_lookup_chunks_901_ids_and_selects_canonical_columns(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serving uses two bound batches and only the canonical record columns."""
    del citation_db_path
    calls: list[tuple[str, tuple[object, ...]]] = []
    real_connect = getattr(citation_database, "_connect_read_only")

    class _RecordingConnection:
        """Record bound queries while forwarding them to SQLite."""

        def __init__(self, connection: sqlite3.Connection) -> None:
            """Store the underlying read-only connection."""
            self._connection = connection

        def execute(
            self, query: str, parameters: tuple[object, ...] = ()
        ) -> Any:
            """Record and execute one parameterized query."""
            calls.append((query, parameters))
            return self._connection.execute(query, parameters)

        def close(self) -> None:
            """Close the underlying connection."""
            self._connection.close()

    monkeypatch.setattr(
        citation_database,
        "_connect_read_only",
        lambda path: _RecordingConnection(real_connect(path)),
    )

    ids = [f"id-{index}" for index in range(CITATION_LOOKUP_BATCH_SIZE + 1)]
    result = await lookup_citation_records(ids)

    selects = [call for call in calls if call[0].startswith("SELECT")]
    assert result.records == {}
    assert [len(parameters) for _query, parameters in selects] == [900, 1]
    expected_columns = ",".join(("file_id", *CITATION_RECORD_FIELDS))
    assert all(
        query.startswith(f"SELECT {expected_columns} ") for query, _ in selects
    )


@pytest.mark.asyncio
async def test_lookup_connections_are_query_only_and_closed(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every serving connection is read-only, query-only, and short-lived."""
    del citation_db_path
    real_connect = getattr(citation_database, "_connect_read_only")
    observed: list[sqlite3.Connection] = []

    class _ObservedConnection:
        """Observe query-only state and close timing for one connection."""

        def __init__(self, connection: sqlite3.Connection) -> None:
            """Store the underlying read-only connection."""
            self._connection = connection

        def execute(self, query: str, *args: Any) -> Any:
            """Assert query-only state before forwarding the query."""
            if query.startswith("SELECT"):
                assert (
                    self._connection.execute("PRAGMA query_only").fetchone()[0]
                    == 1
                )
            return self._connection.execute(query, *args)

        def close(self) -> None:
            """Record and close the underlying connection."""
            observed.append(self._connection)
            self._connection.close()

    monkeypatch.setattr(
        citation_database,
        "_connect_read_only",
        lambda path: _ObservedConnection(real_connect(path)),
    )

    await lookup_citation_records(["missing"])

    assert len(observed) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        observed[0].execute("SELECT 1")


@pytest.mark.asyncio
async def test_lookup_observes_atomic_artifact_replacement(
    citation_db_path: Path,
) -> None:
    """The next short-lived read observes a database installed by replace."""
    first = create_valid_citation_database(
        citation_db_path.with_name("first.sqlite"),
        records=(_record("paper", au="first"),),
    )
    os.replace(first, citation_db_path)
    assert (await lookup_citation_records(["paper"])).records["paper"][
        "au"
    ] == "first"

    second = create_valid_citation_database(
        citation_db_path.with_name("second.sqlite"),
        records=(_record("paper", au="second"),),
    )
    os.replace(second, citation_db_path)

    assert (await lookup_citation_records(["paper"])).records["paper"][
        "au"
    ] == "second"


@pytest.mark.asyncio
async def test_lookup_missing_path_is_not_created_by_read_only_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing configured artifact cannot become an empty SQLite database."""
    path = tmp_path / "missing.sqlite"
    monkeypatch.setenv("CITATION_DB_PATH", str(path))
    resolve_citation_database_path.cache_clear()

    with pytest.raises(CitationDatabaseArtifactError):
        await lookup_citation_records(["paper"])

    assert not path.exists()
    resolve_citation_database_path.cache_clear()


@pytest.mark.asyncio
async def test_lookup_discards_partial_results_on_bounded_driver_failure(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later batch failure cannot leak earlier fetched records."""
    del citation_db_path
    real_connect = getattr(citation_database, "_connect_read_only")
    calls = 0

    class _FailingConnection:
        """Raise one bounded SQLite error on the second select batch."""

        def __init__(self, connection: sqlite3.Connection) -> None:
            """Store the underlying read-only connection."""
            self._connection = connection

        def execute(self, query: str, *args: Any) -> Any:
            """Fail the second select while forwarding all other calls."""
            nonlocal calls
            if query.startswith("SELECT"):
                calls += 1
                if calls == 2:
                    raise sqlite3.OperationalError("driver body")
            return self._connection.execute(query, *args)

        def close(self) -> None:
            """Close the underlying connection."""
            self._connection.close()

    monkeypatch.setattr(
        citation_database,
        "_connect_read_only",
        lambda path: _FailingConnection(real_connect(path)),
    )
    ids = [f"id-{index}" for index in range(CITATION_LOOKUP_BATCH_SIZE + 1)]

    with pytest.raises(CitationDatabaseLookupError) as exc:
        await lookup_citation_records(ids)

    _assert_stable_error(exc.value, "citation_db_lookup_failed", "driver body")
