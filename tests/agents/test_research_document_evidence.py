# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for complete, bounded Research document evidence extraction."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mcp_server_phytomni.agents.research.document_evidence import (
    MAX_DOCUMENT_BYTES,
    MAX_TOTAL_DOCUMENT_BYTES,
    ConvertedResearchSection,
    ManagedDocumentObservation,
    ResearchEvidenceRequest,
    evidence_persistence_metadata,
    extract_research_evidence,
)
from mcp_server_phytomni.agents.research.input_contracts import SourceSpan
from mcp_server_phytomni.agents.research.input_inventory import (
    ResearchInputInventory,
    ResearchInputSnapshot,
    ResearchInventoryEntry,
)

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
    snapshot = ResearchInputSnapshot(
        lane="managed" if purpose == "document" else "pasted",
        size_bytes=size,
        state_version=1 if purpose == "document" else None,
        completed_at=(
            "2026-08-08T00:00:00+00:00" if purpose == "document" else None
        ),
        etag="etag" if purpose == "document" else None,
        version_id="v1" if purpose == "document" else None,
        last_modified=None,
        placeholder=False,
        purpose=purpose,  # type: ignore[arg-type]
        snapshot_digest=f"snapshot-{dataset_id}",
    )
    return ResearchInventoryEntry(
        dataset_id=dataset_id,
        lane=snapshot.lane,
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
        purpose=purpose,  # type: ignore[arg-type]
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
        snapshot=snapshot,
        authority_id=None,
    )


def _request(
    entries: tuple[ResearchInventoryEntry, ...],
    query: str = "Research question",
) -> ResearchEvidenceRequest:
    """Build the immutable request consumed by the extractor."""
    return ResearchEvidenceRequest(
        inventory=ResearchInputInventory(
            entries=entries,
            documents=tuple(
                entry for entry in entries if entry.purpose == "document"
            ),
            datasets=tuple(
                entry for entry in entries if entry.purpose == "dataset"
            ),
            digest="inventory-digest",
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

    async def download(self, entry: ResearchInventoryEntry) -> bytes:
        """Return the fixture payload for one document."""
        self.calls.append(entry.dataset_id)
        return self.payloads[entry.dataset_id]


@dataclass
class _Converter:
    sections: dict[str, tuple[ConvertedResearchSection, ...]]

    def __post_init__(self) -> None:
        """Initialize a call ledger for conversion assertions."""
        self.calls: list[str] = []

    def convert(
        self, entry: ResearchInventoryEntry, payload: bytes
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
    """Per-document and aggregate limits are independent and inclusive."""
    oversized = _entry(
        "document_001", "large.pdf", "document", MAX_DOCUMENT_BYTES + 1
    )
    downloader = _Downloader({"document_001": b"x" * (MAX_DOCUMENT_BYTES + 1)})
    converter = _Converter(
        {"document_001": (ConvertedResearchSection(0, "page", "x"),)}
    )
    with pytest.raises(Exception):
        await extract_research_evidence(
            _request((oversized,)), downloader, converter
        )

    entries = tuple(
        _entry(f"document_{index:03d}", f"{index}.pdf", "document", 1)
        for index in range(1, 4)
    )
    entries = tuple(
        _entry(entry.dataset_id, entry.safe_basename, "document", size)
        for entry, size in zip(
            entries,
            (MAX_DOCUMENT_BYTES, MAX_DOCUMENT_BYTES, 1),
            strict=True,
        )
    )
    downloader = _Downloader(
        {
            "document_001": b"x" * MAX_DOCUMENT_BYTES,
            "document_002": b"x" * MAX_DOCUMENT_BYTES,
            "document_003": b"x",
        }
    )
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
    assert MAX_TOTAL_DOCUMENT_BYTES == 2 * MAX_DOCUMENT_BYTES


async def test_declared_oversize_fails_before_any_download() -> None:
    """Trusted size limits reject before the downloader can allocate bytes."""
    entry = _entry(
        "document_001", "large.pdf", "document", MAX_DOCUMENT_BYTES + 1
    )
    downloader = _Downloader({"document_001": b"x" * (MAX_DOCUMENT_BYTES + 1)})
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
