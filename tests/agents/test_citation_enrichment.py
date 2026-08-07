# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the shared SQLite bibliographic citation enricher."""

import logging
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.shared import (
    citation_database,
    citation_enrichment,
)
from mcp_server_phytomni.agents.shared.citation_database import (
    CitationDatabaseLookupError,
)
from mcp_server_phytomni.agents.shared.citation_metadata import (
    CITATION_RECORD_FIELDS,
    CITATION_STATUS_KEY,
)
from mcp_server_phytomni.runtime.request_context import (
    bind_request_id,
    reset_request_var,
)


@pytest.fixture(autouse=True)
def _run_lookup_in_test_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the real synchronous SQLite lookup without an executor thread."""
    sync_lookup = getattr(citation_database, "_lookup_citation_records")

    async def lookup(file_ids: list[str]):
        unique_ids = tuple(dict.fromkeys(file_ids))
        return sync_lookup(unique_ids)

    monkeypatch.setattr(
        citation_enrichment,
        "lookup_citation_records",
        lookup,
    )


def _insert_record(path: Path, **record: str | None) -> None:
    columns = ("file_id", *CITATION_RECORD_FIELDS)
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"INSERT INTO citation_records ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            tuple(record.get(column) for column in columns),
        )


@pytest.mark.asyncio
async def test_enrich_merges_all_canonical_fields_and_matched_status(
    citation_db_path: Path,
) -> None:
    """A matching SQLite record projects every canonical citation field."""
    record = {"file_id": "f1"}
    record.update(
        {field: f"{field}-value" for field in CITATION_RECORD_FIELDS}
    )
    _insert_record(citation_db_path, **record)
    docs = [{"file_id": "f1", "title": "A title.pdf"}]

    await citation_enrichment.enrich_cited_doc_list(docs)

    assert {field: docs[0][field] for field in CITATION_RECORD_FIELDS} == {
        field: f"{field}-value" for field in CITATION_RECORD_FIELDS
    }
    assert docs[0][CITATION_STATUS_KEY] == "matched"
    assert docs[0]["title"] == "A title.pdf"


@pytest.mark.asyncio
async def test_enrich_marks_normal_and_quarantined_misses(
    citation_db_path: Path,
) -> None:
    """Absent and quarantined IDs retain retrieval data and become missing."""
    with sqlite3.connect(citation_db_path) as connection:
        connection.execute(
            "INSERT INTO citation_conflicts (file_id, conflict_json) "
            "VALUES (?, ?)",
            ("quarantined", "{}"),
        )
    docs = [
        {"file_id": "absent", "title": "A"},
        {"file_id": "quarantined", "title": "B"},
    ]

    await citation_enrichment.enrich_cited_doc_list(docs)

    assert [doc[CITATION_STATUS_KEY] for doc in docs] == ["missing", "missing"]
    assert [doc["title"] for doc in docs] == ["A", "B"]


@pytest.mark.asyncio
async def test_enrich_queries_duplicate_ids_once(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate document IDs preserve order while requiring one lookup ID."""
    _insert_record(citation_db_path, file_id="f1", au="Smith J")
    lookup = citation_enrichment.lookup_citation_records
    observed: list[list[str]] = []

    async def capture(file_ids: list[str]):
        observed.append(file_ids)
        return await lookup(file_ids)

    monkeypatch.setattr(
        citation_enrichment, "lookup_citation_records", capture
    )
    docs = [{"file_id": " f1 "}, {"file_id": "f1"}]

    await citation_enrichment.enrich_cited_doc_list(docs)

    assert observed == [["f1"]]
    assert [doc["au"] for doc in docs] == ["Smith J", "Smith J"]


@pytest.mark.asyncio
async def test_enrich_skips_docs_without_or_with_blank_file_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No lookup or status is produced for missing and blank identifiers."""
    lookup = AsyncMock()
    monkeypatch.setattr(citation_enrichment, "lookup_citation_records", lookup)
    docs = [{"title": "missing"}, {"file_id": "  ", "title": "blank"}]

    await citation_enrichment.enrich_cited_doc_list(docs)

    lookup.assert_not_awaited()
    assert all(CITATION_STATUS_KEY not in doc for doc in docs)


@pytest.mark.asyncio
async def test_enrich_preserves_non_mapping_elements(
    citation_db_path: Path,
) -> None:
    """Non-mapping items do not prevent an adjacent document from merging."""
    _insert_record(citation_db_path, file_id="f1", au="Smith J")
    docs: list[object] = ["not-a-dict", {"file_id": "f1", "title": "T"}]

    await citation_enrichment.enrich_cited_doc_list(
        docs  # type: ignore[arg-type]
    )

    assert docs[0] == "not-a-dict"
    assert docs[1] == {
        "file_id": "f1",
        "title": "T",
        "au": "Smith J",
        CITATION_STATUS_KEY: "matched",
    }


@pytest.mark.asyncio
async def test_enrich_marks_whole_batch_failed_without_partial_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed database failure marks every eligible document consistently."""
    monkeypatch.setattr(
        citation_enrichment,
        "lookup_citation_records",
        AsyncMock(side_effect=CitationDatabaseLookupError()),
    )
    docs = [{"file_id": "f1", "title": "A"}, {"file_id": "f2", "title": "B"}]

    await citation_enrichment.enrich_cited_doc_list(docs)

    assert docs == [
        {"file_id": "f1", "title": "A", CITATION_STATUS_KEY: "lookup_failed"},
        {"file_id": "f2", "title": "B", CITATION_STATUS_KEY: "lookup_failed"},
    ]


@pytest.mark.asyncio
async def test_enrich_logs_only_fixed_class_and_request_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Lookup errors log only the fixed event class and request identifier."""
    sentinels = (
        "/secret/path.sqlite",
        "SELECT secret",
        "file-secret",
        "value-secret",
    )
    error = CitationDatabaseLookupError()
    error.__cause__ = RuntimeError(" ".join(sentinels))
    monkeypatch.setattr(
        citation_enrichment,
        "lookup_citation_records",
        AsyncMock(side_effect=error),
    )
    token = bind_request_id("request-sentinel")
    try:
        with caplog.at_level(logging.WARNING):
            await citation_enrichment.enrich_cited_doc_list(
                [{"file_id": "file-secret"}]
            )
    finally:
        reset_request_var(token)

    assert "citation_metadata_lookup_failed" in caplog.text
    assert "request-sentinel" in caplog.text
    assert all(sentinel not in caplog.text for sentinel in sentinels)


@pytest.mark.asyncio
async def test_enrich_does_not_swallow_programming_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected defects remain visible to callers."""
    monkeypatch.setattr(
        citation_enrichment,
        "lookup_citation_records",
        AsyncMock(side_effect=AssertionError("programming error")),
    )

    with pytest.raises(AssertionError, match="programming error"):
        await citation_enrichment.enrich_cited_doc_list([{"file_id": "f1"}])
