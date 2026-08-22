# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for complete, bounded Research document evidence extraction."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import get_type_hints

import pytest

from mcp_server_phytomni.agents.research.document_evidence import (
    ConvertedResearchSection,
    ManagedDocumentConverter,
    ManagedDocumentDownloader,
    ManagedDocumentObservation,
    ManagedDocumentPayload,
    ResearchEvidenceRequest,
    evidence_persistence_metadata,
    extract_research_evidence,
    research_evidence_coverage_digest,
)
from mcp_server_phytomni.agents.research.input_contracts import SourceSpan
from mcp_server_phytomni.agents.research.input_inventory import (
    ResearchInputSnapshot,
    ResearchInventoryEntry,
    research_inventory_partitions,
)
from mcp_server_phytomni.runtime.resumable_uploads import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_TOTAL_BYTES,
)
from tests.support.research_fakes import research_inventory_entry

pytestmark = pytest.mark.agent


@dataclass(frozen=True)
class _EntryOptions:
    """Optional source hint and lane ordinal for one test entry."""

    hint: str | None = None
    ordinal: int = 0


def _entry(
    dataset_id: str,
    name: str,
    purpose: str,
    size: int,
    options: _EntryOptions | None = None,
) -> ResearchInventoryEntry:
    """Build an owner-validated inventory entry without storage I/O."""
    options = options or _EntryOptions()
    document = purpose == "document"
    return research_inventory_entry(
        dataset_id=dataset_id,
        lane="managed" if document else "pasted",
        lane_ordinal=options.ordinal,
        exact_reference=f"obs://bucket/{name}",
        comparison_digest=f"comparison-{dataset_id}",
        safe_basename=name,
        compound_suffix="." + name.split(".")[-1],
        size_bytes=size,
        media_hint=(
            "application/pdf"
            if name.endswith(".pdf")
            else "application/octet-stream"
        ),
        purpose=purpose,
        user_hint=options.hint,
        source_span=(
            SourceSpan(
                options.ordinal,
                options.ordinal + len(name),
                "standalone_tab",
            )
            if options.hint
            else None
        ),
        state_version=1 if document else None,
        completed_at="2026-08-08T00:00:00+00:00" if document else None,
        etag="etag" if document else None,
        version_id="v1" if document else None,
        authority_id=None,
    )


def _request(
    entries: tuple[ResearchInventoryEntry, ...],
    query: str = "Research question",
) -> ResearchEvidenceRequest:
    """Build the immutable request consumed by the extractor."""
    return ResearchEvidenceRequest(
        inventory=research_inventory_partitions(
            entries, digest="inventory-digest"
        ),
        effective_query=query,
        effective_to_original=tuple(range(len(query))),
    )


@dataclass
class _Downloader:
    payloads: dict[str, bytes]
    observations: dict[str, ManagedDocumentObservation] | None = None

    def __post_init__(self) -> None:
        """Initialize a call ledger for download assertions."""
        self.calls: list[str] = []
        self.observe_calls: list[str] = []

    def observe(
        self, entry: ResearchInventoryEntry
    ) -> ManagedDocumentObservation:
        """Return the current owner snapshot for one document."""
        self.observe_calls.append(entry.dataset_id)
        if self.observations and entry.dataset_id in self.observations:
            return self.observations[entry.dataset_id]
        return ManagedDocumentObservation(
            exact_reference=entry.exact_reference,
            snapshot=entry.snapshot,
        )

    async def download(
        self, entry: ResearchInventoryEntry
    ) -> ManagedDocumentPayload:
        """Stage the fixture payload as a local file."""
        self.calls.append(entry.dataset_id)
        body = self.payloads[entry.dataset_id]
        handle, path = tempfile.mkstemp(prefix="research-evidence-")
        try:
            os.write(handle, body)
        finally:
            os.close(handle)
        return ManagedDocumentPayload(
            path=path, size_bytes=len(body), cleanup=True
        )


@dataclass
class _Converter:
    sections: dict[str, tuple[ConvertedResearchSection, ...]]

    def __post_init__(self) -> None:
        """Initialize a call ledger for conversion assertions."""
        self.calls: list[str] = []

    def convert(
        self,
        entry: ResearchInventoryEntry,
        payload: ManagedDocumentPayload,
    ) -> tuple[ConvertedResearchSection, ...]:
        """Return fixture sections in their configured source order."""
        del payload
        self.calls.append(entry.dataset_id)
        return self.sections[entry.dataset_id]


async def test_evidence_is_ordered_and_stable() -> None:
    """Query/pages/sections/hints/metadata retain deterministic order."""
    pdf = _entry("document_001", "paper.pdf", "document", 5)
    workbook = _entry("document_002", "table.xlsx", "document", 4)
    dataset = _entry(
        "dataset_003",
        "matrix.tsv",
        "dataset",
        7,
        _EntryOptions(hint="treated samples"),
    )
    request = _request((pdf, workbook, dataset))
    downloader = _Downloader(
        {"document_001": b"paper", "document_002": b"book"}
    )
    converter = _Converter(
        {
            "document_001": (
                ConvertedResearchSection(0, "page 1", "page one"),
                ConvertedResearchSection(1, "page 2", "page two"),
            ),
            "document_002": (
                ConvertedResearchSection(0, "sheet 1", "sheet one"),
            ),
        }
    )

    evidence = await extract_research_evidence(request, downloader, converter)

    assert [unit.source_kind for unit in evidence.units] == [
        "query",
        "pdf_page",
        "pdf_page",
        "document_section",
        "user_hint",
        "dataset_meta",
    ]
    assert [unit.evidence_id for unit in evidence.units] == [
        "query_span_001",
        "document_001_page_001",
        "document_001_page_002",
        "document_002_section_001",
        "dataset_003_hint_001",
        "dataset_003_meta_001",
    ]
    assert downloader.calls == ["document_001", "document_002"]
    assert converter.calls == downloader.calls
    assert evidence.document_digests[0].evidence_ids == (
        "document_001_page_001",
        "document_001_page_002",
    )


async def test_query_evidence_skips_whitespace_only_gap_spans() -> None:
    """Removed-reference gaps do not become empty evidence units."""
    dataset = _entry("dataset_001", "matrix.csv", "dataset", 7)
    query = "Question\n\n"
    request = ResearchEvidenceRequest(
        inventory=research_inventory_partitions(
            (dataset,), digest="inventory-digest"
        ),
        effective_query=query,
        effective_to_original=(*range(8), 100, 200),
    )

    evidence = await extract_research_evidence(
        request,
        _Downloader({}),
        _Converter({}),
    )

    query_units = [
        unit for unit in evidence.units if unit.source_kind == "query"
    ]
    assert [
        (unit.evidence_id, unit.source_ordinal, unit.text)
        for unit in query_units
    ] == [("query_span_001", 0, "Question")]


async def test_extracted_evidence_is_shared_without_redownload() -> None:
    """A single extraction result supports both downstream consumers."""
    entries = (
        _entry("document_001", "one.pdf", "document", 3),
        _entry("document_002", "two.pdf", "document", 3),
        _entry("dataset_003", "table.xlsx", "dataset", 100),
    )
    downloader = _Downloader({"document_001": b"one", "document_002": b"two"})
    converter = _Converter(
        {
            "document_001": (ConvertedResearchSection(1, "page 1", "one"),),
            "document_002": (ConvertedResearchSection(1, "page 1", "two"),),
        }
    )
    evidence = await extract_research_evidence(
        _request(entries), downloader, converter
    )

    tuple(unit.evidence_id for unit in evidence.units)
    tuple(unit.content_digest for unit in evidence.units)
    assert downloader.calls == ["document_001", "document_002"]
    assert converter.calls == downloader.calls


@pytest.mark.parametrize(
    "payload, status",
    [
        (b"changed", 422),
        (b"", 422),
    ],
)
async def test_changed_or_empty_document_fails_safely(
    payload: bytes, status: int
) -> None:
    """Snapshot drift and empty download never expose provider details."""
    entry = _entry("document_001", "paper.pdf", "document", 5)
    downloader = _Downloader({"document_001": payload})
    converter = _Converter({"document_001": ()})

    with pytest.raises(Exception) as caught:
        await extract_research_evidence(
            _request((entry,)), downloader, converter
        )

    error = caught.value
    assert getattr(error, "code") == "research_document_extraction_failed"
    assert getattr(error, "http_status_hint") == status
    assert "obs://" not in str(error)


async def test_malformed_pdf_and_converter_failure_are_safe() -> None:
    """Empty converted pages and converter exceptions map to one safe code."""
    entry = _entry("document_001", "paper.pdf", "document", 5)
    downloader = _Downloader({"document_001": b"paper"})
    converter = _Converter(
        {"document_001": (ConvertedResearchSection(0, "page 1", " "),)}
    )
    with pytest.raises(Exception) as caught:
        await extract_research_evidence(
            _request((entry,)), downloader, converter
        )
    assert (
        getattr(caught.value, "code") == "research_document_extraction_failed"
    )


async def test_document_and_total_limits() -> None:
    """Per-document and aggregate limits share the upload-plane ceiling."""
    oversized = _entry(
        "document_001", "large.pdf", "document", MAX_UPLOAD_BYTES + 1
    )
    downloader = _Downloader({"document_001": b"x"})
    converter = _Converter(
        {"document_001": (ConvertedResearchSection(0, "page", "x"),)}
    )
    with pytest.raises(Exception):
        await extract_research_evidence(
            _request((oversized,)), downloader, converter
        )
    assert not downloader.observe_calls
    assert not downloader.calls
    assert not converter.calls

    overflow_count = MAX_UPLOAD_TOTAL_BYTES // MAX_UPLOAD_BYTES + 1
    entries = tuple(
        _entry(
            f"document_{index:03d}",
            f"{index}.pdf",
            "document",
            MAX_UPLOAD_BYTES,
        )
        for index in range(1, overflow_count + 1)
    )
    downloader = _Downloader({entry.dataset_id: b"x" for entry in entries})
    converter = _Converter(
        {
            entry.dataset_id: (ConvertedResearchSection(0, "page", "x"),)
            for entry in entries
        }
    )
    with pytest.raises(Exception):
        await extract_research_evidence(
            _request(entries), downloader, converter
        )
    assert not downloader.observe_calls
    assert not downloader.calls
    assert not converter.calls


async def test_declared_oversize_fails_before_any_download() -> None:
    """Trusted size limits reject before the downloader can allocate bytes."""
    entry = _entry(
        "document_001", "large.pdf", "document", MAX_UPLOAD_BYTES + 1
    )
    downloader = _Downloader({"document_001": b"x"})
    converter = _Converter(
        {"document_001": (ConvertedResearchSection(0, "page", "x"),)}
    )

    with pytest.raises(Exception):
        await extract_research_evidence(
            _request((entry,)), downloader, converter
        )

    assert not downloader.observe_calls
    assert not downloader.calls
    assert not converter.calls


async def test_same_length_observed_snapshot_drift_fails_safely() -> None:
    """Changed version/reference metadata is rejected before body download."""
    entry = _entry("document_001", "paper.pdf", "document", 5)
    changed = ManagedDocumentObservation(
        exact_reference="obs://bucket/replaced.pdf",
        snapshot=ResearchInputSnapshot(
            lane="managed",
            size_bytes=5,
            state_version=2,
            completed_at=entry.snapshot.completed_at,
            etag="changed-etag",
            version_id="changed-version",
            last_modified=entry.snapshot.last_modified,
            placeholder=False,
            purpose="document",
            snapshot_digest="changed-digest",
        ),
    )
    downloader = _Downloader(
        {"document_001": b"other"}, {"document_001": changed}
    )
    converter = _Converter(
        {"document_001": (ConvertedResearchSection(0, "page", "text"),)}
    )

    with pytest.raises(Exception) as caught:
        await extract_research_evidence(
            _request((entry,)), downloader, converter
        )

    assert (
        getattr(caught.value, "code") == "research_document_extraction_failed"
    )
    assert not downloader.calls
    assert not converter.calls


async def test_persistence_projection_excludes_plaintext() -> None:
    """Only bounded IDs/digests/membership cross the persistence boundary."""
    entry = _entry("document_001", "paper.pdf", "document", 5)
    evidence = await extract_research_evidence(
        _request((entry,), query="private query"),
        _Downloader({"document_001": b"paper"}),
        _Converter(
            {
                "document_001": (
                    ConvertedResearchSection(0, "page", "secret text"),
                )
            }
        ),
    )
    metadata = evidence_persistence_metadata(evidence)
    serialized = repr(metadata)
    assert "private query" not in serialized
    assert "secret text" not in serialized
    assert "obs://" not in serialized
    assert set(metadata) == {"units", "document_digests", "coverage_digest"}
    assert (
        research_evidence_coverage_digest(
            evidence.units, evidence.document_digests
        )
        == evidence.coverage_digest
    )


def test_document_ports_are_file_backed() -> None:
    """Evidence download and conversion take a local file, not a bytes body."""
    download_hints = get_type_hints(ManagedDocumentDownloader.download)
    convert_hints = get_type_hints(ManagedDocumentConverter.convert)
    assert download_hints["return"] is ManagedDocumentPayload
    assert convert_hints["payload"] is ManagedDocumentPayload


async def test_bytes_download_is_rejected_before_conversion() -> None:
    """The old in-memory body contract cannot reach the converter."""
    entry = _entry("document_001", "paper.pdf", "document", 5)

    class _BytesDownloader:
        def observe(
            self, observed: ResearchInventoryEntry
        ) -> ManagedDocumentObservation:
            """Return the owner snapshot without staging a file."""
            return ManagedDocumentObservation(
                exact_reference=observed.exact_reference,
                snapshot=observed.snapshot,
            )

        async def download(self, observed: ResearchInventoryEntry) -> bytes:
            """Return the historic bytes body that extraction must reject."""
            del observed
            return b"paper"

    converter = _Converter(
        {"document_001": (ConvertedResearchSection(0, "page", "text"),)}
    )
    with pytest.raises(Exception) as caught:
        await extract_research_evidence(
            _request((entry,)), _BytesDownloader(), converter
        )
    assert (
        getattr(caught.value, "code") == "research_document_extraction_failed"
    )
    assert not converter.calls
