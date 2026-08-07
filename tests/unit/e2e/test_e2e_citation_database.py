# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Generated citation artifact lifecycle for live e2e subprocesses."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from e2e.helpers import citation_database as helper
from tests.support.citation_database import create_valid_citation_database

from mcp_server_phytomni.agents.shared.citation_database import (
    resolve_citation_database_path,
    validate_citation_database,
)
from mcp_server_phytomni.config import CitationConfig

pytestmark = pytest.mark.unit


def _clear_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove both operator aliases for one generated-artifact test."""
    monkeypatch.delenv("CITATION_DB_PATH", raising=False)
    monkeypatch.delenv("PHYTOMNI_CITATION_DB_PATH", raising=False)


def test_absent_aliases_generate_and_validate_empty_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty valid database is inherited by real e2e subprocesses."""
    _clear_aliases(monkeypatch)

    with helper.configured_e2e_citation_database(tmp_path) as database:
        assert database is not None
        assert database.is_file()
        assert os.environ["CITATION_DB_PATH"] == str(database)
        assert "PHYTOMNI_CITATION_DB_PATH" not in os.environ
        metadata = validate_citation_database(database)
        assert metadata.source_sha256 == hashlib.sha256(b"").hexdigest()
        assert metadata.source_record_count == 0
        assert metadata.unique_id_count == 0
        assert metadata.imported_record_count == 0
        assert metadata.exact_duplicate_row_count == 0
        assert metadata.conflict_id_count == 0
        assert metadata.quarantined_row_count == 0
        assert metadata.missing_doi_count == 0
        assert metadata.invalid_doi_count == 0
        assert metadata.missing_title_count == 0


@pytest.mark.parametrize(
    "alias",
    ("CITATION_DB_PATH", "PHYTOMNI_CITATION_DB_PATH"),
)
def test_explicit_operator_alias_is_untouched(
    alias: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Either nonblank operator alias bypasses generation and validation."""
    _clear_aliases(monkeypatch)
    operator_database = tmp_path / "operator.sqlite"
    operator_database.write_bytes(b"operator-owned")
    monkeypatch.setenv(alias, str(operator_database))

    def unexpected_builder(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("builder reached explicit operator path")

    monkeypatch.setattr(helper, "build_citation_database", unexpected_builder)

    with helper.configured_e2e_citation_database(tmp_path) as database:
        assert database is None
        assert operator_database.read_bytes() == b"operator-owned"
        assert os.environ[alias] == str(operator_database)

    assert operator_database.read_bytes() == b"operator-owned"
    assert os.environ[alias] == str(operator_database)


def test_blank_plain_alias_preserves_prefixed_operator_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E bypass and serving config agree on mixed alias precedence."""
    operator_database = create_valid_citation_database(
        tmp_path / "operator.sqlite"
    )
    monkeypatch.setenv("CITATION_DB_PATH", "   ")
    monkeypatch.setenv(
        "PHYTOMNI_CITATION_DB_PATH",
        str(operator_database),
    )

    def unexpected_builder(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("builder reached prefixed operator path")

    monkeypatch.setattr(helper, "build_citation_database", unexpected_builder)
    resolve_citation_database_path.cache_clear()
    try:
        with helper.configured_e2e_citation_database(tmp_path) as database:
            assert database is None
            resolved_path = CitationConfig().CITATION_DB_PATH
            assert resolved_path == str(operator_database)
            assert validate_citation_database().schema_version == 1
    finally:
        resolve_citation_database_path.cache_clear()

    assert os.environ["CITATION_DB_PATH"] == "   "
    assert os.environ["PHYTOMNI_CITATION_DB_PATH"] == str(operator_database)


def test_cleanup_restores_environment_and_removes_only_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated files disappear while prior state and neighbors remain."""
    monkeypatch.setenv("CITATION_DB_PATH", "   ")
    monkeypatch.setenv("PHYTOMNI_CITATION_DB_PATH", "")
    neighbor = tmp_path / "operator-neighbor.sqlite"
    neighbor.write_bytes(b"keep")

    with helper.configured_e2e_citation_database(tmp_path) as database:
        assert database is not None
        generated_database = database
        generated_root = database.parent
        assert generated_database.exists()
        assert os.environ["CITATION_DB_PATH"] == str(database)
        assert os.environ["PHYTOMNI_CITATION_DB_PATH"] == ""

    assert os.environ["CITATION_DB_PATH"] == "   "
    assert os.environ["PHYTOMNI_CITATION_DB_PATH"] == ""
    assert not generated_database.exists()
    assert not generated_root.exists()
    assert neighbor.read_bytes() == b"keep"


def test_builder_failure_cleans_owned_files_without_deleting_operator_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed generated build cannot remove an unrelated artifact."""
    _clear_aliases(monkeypatch)
    operator_database = tmp_path / "operator.sqlite"
    operator_database.write_bytes(b"operator-owned")

    def fail_builder(source: Path, output: Path) -> object:
        assert source.parent == output.parent
        output.write_bytes(b"partial")
        raise RuntimeError("builder failed")

    monkeypatch.setattr(helper, "build_citation_database", fail_builder)

    with (
        pytest.raises(RuntimeError, match="builder failed"),
        helper.configured_e2e_citation_database(tmp_path),
    ):
        raise AssertionError("failed builder yielded")

    assert operator_database.read_bytes() == b"operator-owned"
    assert {path.name for path in tmp_path.iterdir()} == {"operator.sqlite"}
    assert "CITATION_DB_PATH" not in os.environ
    assert "PHYTOMNI_CITATION_DB_PATH" not in os.environ
