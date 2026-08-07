# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the offline citation SQLite artifact builder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server_phytomni.agents.shared.citation_database import (
    CitationDatabaseError,
    validate_citation_database,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "citation_db.py"
SPEC = importlib.util.spec_from_file_location("citation_db", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
citation_db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(citation_db)

build_citation_database = getattr(citation_db, "build_citation_database")
normalize_source_record = getattr(citation_db, "normalize_source_record")


def _write_jsonl(path: Path, rows: list[object]) -> None:
    """Write compact UTF-8 JSON records one per physical line."""
    path.write_bytes(
        b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
            for row in rows
        )
    )


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the operator CLI with the active test interpreter."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        check=False,
        text=True,
    )


def _valid_record(file_id: str, **values: str) -> dict[str, str]:
    """Build a minimal source row with optional canonical source fields."""
    return {"id": file_id, "TI": "Paper", "DI": "10.1000/example", **values}


def _assert_unchanged(path: Path, expected: bytes) -> None:
    """Assert a failed build never altered an existing destination."""
    assert path.read_bytes() == expected
    assert not list(path.parent.glob(f".{path.name}.*.citation-tmp*"))


def test_normalize_source_record_maps_only_approved_fields() -> None:
    """Canonical normalization trims mapped strings and ignores uploads."""
    assert normalize_source_record(
        {
            "id": " f1 ",
            "AU": " Smith, J ",
            "TI": " Paper ",
            "AR": " e12 ",
            "DI": " 10.1000/x ",
            "rename_TI": "upload.pdf",
            "upload_rename_TI": "other.pdf",
            "AF": "Ignored Author",
            "IS": "1",
            "PD": "January",
            "UT": "WOS:1",
        },
        line_number=7,
    ) == {
        "file_id": "f1",
        "au": "Smith, J",
        "ti": "Paper",
        "so": None,
        "vl": None,
        "bp": None,
        "ep": None,
        "ar": "e12",
        "py": None,
        "di": "10.1000/x",
        "dl": None,
        "pm": None,
    }


@pytest.mark.parametrize(
    ("payload", "line_number"),
    [
        ([], 1),
        ({"id": " "}, 2),
        ({"id": 1}, 3),
        ({"id": "f1", "TI": 1}, 4),
        ({"id": "f1", "DI": False}, 5),
        ({"id": "f1", "TI": None}, 6),
    ],
)
def test_normalize_source_record_requires_objects_and_mapped_strings(
    payload: object,
    line_number: int,
) -> None:
    """Only object rows with a nonblank string ID and mapped strings pass."""
    with pytest.raises(citation_db.CitationBuildError):
        normalize_source_record(payload, line_number=line_number)


def test_normalize_source_record_uses_established_doi_normalization() -> None:
    """Resolver-form DOIs canonicalize without title inference or rewriting."""
    normalized = normalize_source_record(
        {
            "id": "f1",
            "TI": "A  Title",
            "DI": "https://doi.org/10.1000/ABC%2Fxy",
        },
        line_number=1,
    )

    assert normalized["ti"] == "A  Title"
    assert normalized["di"] == "10.1000/ABC/xy"


def test_build_accounts_for_duplicates_conflicts_and_source_quality(
    tmp_path: Path,
) -> None:
    """One small source exercises all schema-v1 accounting equations."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    _write_jsonl(
        source,
        [
            _valid_record("f1", AU="Smith"),
            _valid_record("f1", AU="Smith", rename_TI="renamed.pdf"),
            _valid_record("f2", AU="Variant A"),
            _valid_record("f2", AU="Variant B"),
            _valid_record("f2", AU="Variant A"),
            _valid_record("f3", DI="not-a-doi"),
            {"id": "f4"},
        ],
    )

    metadata = build_citation_database(source, output)

    assert metadata.source_record_count == 7
    assert metadata.unique_id_count == 4
    assert metadata.imported_record_count == 3
    assert metadata.exact_duplicate_row_count == 1
    assert metadata.conflict_id_count == 1
    assert metadata.quarantined_row_count == 3
    assert metadata.missing_doi_count == 1
    assert metadata.invalid_doi_count == 1
    assert metadata.missing_title_count == 1
    assert metadata.unique_id_count == (
        metadata.imported_record_count + metadata.conflict_id_count
    )
    assert metadata.source_record_count == (
        metadata.imported_record_count
        + metadata.exact_duplicate_row_count
        + metadata.quarantined_row_count
    )
    assert validate_citation_database(output) == metadata

    with sqlite3.connect(output) as connection:
        records = connection.execute(
            "SELECT file_id, di FROM citation_records ORDER BY file_id"
        ).fetchall()
        assert records == [
            ("f1", "10.1000/example"),
            ("f3", None),
            ("f4", None),
        ]
        assert connection.execute(
            "SELECT file_id FROM citation_conflicts"
        ).fetchall() == [("f2",)]
        conflict = json.loads(
            connection.execute(
                "SELECT conflict_json FROM citation_conflicts "
                "WHERE file_id = 'f2'"
            ).fetchone()[0]
        )
        assert conflict == {
            "variant_count": 2,
            "source_lines": [3, 4, 5],
            "differing_fields": ["au"],
        }
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall() == [
            ("citation_build_metadata",),
            ("citation_conflicts",),
            ("citation_records",),
        ]


@pytest.mark.parametrize(
    "raw_source",
    [b'{"id":"f1"}\n\xff\n', b'{"id":"f1"}\n{not-json}\n', b"[]\n"],
)
def test_build_rejects_invalid_jsonl_and_preserves_destination(
    tmp_path: Path,
    raw_source: bytes,
) -> None:
    """UTF-8, JSON, and object failures leave a seeded artifact intact."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    source.write_bytes(raw_source)
    original = b"prior artifact"
    output.write_bytes(original)

    with pytest.raises(citation_db.CitationBuildError):
        build_citation_database(source, output)

    _assert_unchanged(output, original)


def test_build_rejects_expected_hash_mismatch_and_preserves_destination(
    tmp_path: Path,
) -> None:
    """A completed source digest must match before replacement occurs."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    _write_jsonl(source, [_valid_record("f1")])
    original = b"prior artifact"
    output.write_bytes(original)

    with pytest.raises(citation_db.CitationBuildError):
        build_citation_database(source, output, expected_sha256="0" * 64)

    _assert_unchanged(output, original)


def test_build_preserves_destination_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The final atomic replacement is the only destination mutation."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    _write_jsonl(source, [_valid_record("f1")])
    original = b"prior artifact"
    output.write_bytes(original)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(citation_db.CitationBuildError):
        build_citation_database(source, output)

    _assert_unchanged(output, original)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "f1", "TI": 1},
        {"id": " "},
        {"id": 1},
    ],
)
def test_build_rejects_invalid_record_and_preserves_destination(
    payload: object,
    tmp_path: Path,
) -> None:
    """Mapped-type and ID validation failures preserve a seeded artifact."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    _write_jsonl(source, [payload])
    original = b"prior artifact"
    output.write_bytes(original)

    with pytest.raises(citation_db.CitationBuildError):
        build_citation_database(source, output)

    _assert_unchanged(output, original)


def test_build_rejects_nonfinite_json_constant_and_preserves_destination(
    tmp_path: Path,
) -> None:
    """NaN in an ignored field is not accepted as strict JSONL."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    source.write_bytes(b'{"id":"f1","ignored":NaN}\n')
    original = b"prior artifact"
    output.write_bytes(original)

    with pytest.raises(citation_db.CitationBuildError):
        build_citation_database(source, output)

    _assert_unchanged(output, original)


def test_build_does_not_remove_preexisting_temp_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exclusive temp creation never deletes a file owned by another run."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    _write_jsonl(source, [_valid_record("f1")])
    monkeypatch.setattr(
        citation_db.secrets, "token_hex", lambda _size: "collision"
    )
    collision = tmp_path / ".citation.sqlite.collision.citation-tmp"
    collision.write_bytes(b"another builder")

    with pytest.raises(citation_db.CitationBuildError):
        build_citation_database(source, output)

    assert collision.read_bytes() == b"another builder"


def test_cli_build_and_validate_emit_only_compact_metadata(
    tmp_path: Path,
) -> None:
    """Both operator commands write one safe compact metadata object."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    _write_jsonl(source, [_valid_record("sensitive-id", TI="Source title")])
    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    build = _run(
        "build",
        "--source",
        str(source),
        "--output",
        str(output),
        "--expected-sha256",
        expected_sha256,
    )

    assert build.returncode == 0, build.stderr
    assert build.stdout.count("\n") == 1
    build_payload = json.loads(build.stdout)
    assert build_payload["source_sha256"] == expected_sha256
    assert "sensitive-id" not in build.stdout + build.stderr
    assert "Source title" not in build.stdout + build.stderr

    validate = _run("validate", "--database", str(output))

    assert validate.returncode == 0, validate.stderr
    assert validate.stdout.count("\n") == 1
    assert json.loads(validate.stdout) == build_payload
    assert "sensitive-id" not in validate.stdout + validate.stderr


@pytest.mark.parametrize("digest", ["A" * 64, "f" * 63, "g" * 64])
def test_cli_rejects_noncanonical_expected_digest(
    digest: str, tmp_path: Path
) -> None:
    """The optional expected source SHA only accepts lowercase hex digests."""
    result = _run(
        "build",
        "--source",
        str(tmp_path / "source.jsonl"),
        "--output",
        str(tmp_path / "citation.sqlite"),
        "--expected-sha256",
        digest,
    )

    assert result.returncode == 2


def test_cli_maps_build_and_validation_failures_to_fixed_classes(
    tmp_path: Path,
) -> None:
    """Operator failures are stable and do not reveal paths or source data."""
    source = tmp_path / "source.jsonl"
    output = tmp_path / "citation.sqlite"
    source.write_bytes(b"{not-json}\n")

    build = _run("build", "--source", str(source), "--output", str(output))
    validate = _run("validate", "--database", str(output))

    assert build.returncode == 1
    assert build.stdout == ""
    assert build.stderr.strip() == "citation_db_build_failed"
    assert str(source) not in build.stderr
    assert validate.returncode == 1
    assert validate.stdout == ""
    assert validate.stderr.strip() == "citation_db_validation_failed"


def test_validate_citation_database_failure_is_reported_by_cli(
    tmp_path: Path,
) -> None:
    """The validation command uses the established read-only validator."""
    database = tmp_path / "bad.sqlite"
    database.write_bytes(b"not sqlite")

    result = _run("validate", "--database", str(database))

    assert result.returncode == 1
    assert result.stderr.strip() == "citation_db_validation_failed"
    with pytest.raises(CitationDatabaseError):
        validate_citation_database(database)
