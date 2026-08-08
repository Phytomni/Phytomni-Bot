# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bounded, deterministic evidence extraction for Research inputs.

Document bodies are deliberately kept in the returned immutable object only.
The metadata projection below is the persistence boundary and never contains
query, hint, or converted-document text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .input_contracts import (
    EvidenceSourceKind,
    ResearchInputFailure,
    SourceSpan,
    research_input_failure,
)
from .input_inventory import (
    ResearchInputInventory,
    ResearchInputSnapshot,
    ResearchInventoryEntry,
)

__all__ = [
    "ConvertedResearchSection",
    "DocumentEvidenceDigest",
    "ExtractedResearchEvidence",
    "MAX_DOCUMENT_BYTES",
    "MAX_TOTAL_DOCUMENT_BYTES",
    "ManagedDocumentConverter",
    "ManagedDocumentDownloader",
    "ManagedDocumentObservation",
    "ResearchEvidenceRequest",
    "ResearchEvidenceUnit",
    "extract_research_evidence",
    "evidence_persistence_metadata",
]

MAX_DOCUMENT_BYTES = 25 * 1024**2
MAX_TOTAL_DOCUMENT_BYTES = 50 * 1024**2
_SAFE_EXTRACTION_MESSAGE = "Research document evidence could not be extracted."


@dataclass(frozen=True, slots=True)
class ConvertedResearchSection:
    """One converter-owned page or deterministic document section."""

    ordinal: int
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class DocumentEvidenceDigest:
    """Persistable digest and coverage IDs for one managed document."""

    document_id: str
    content_digest: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchEvidenceRequest:
    """Immutable extraction input after parser and inventory validation."""

    inventory: ResearchInputInventory
    effective_query: str
    effective_to_original: tuple[int, ...]


class ManagedDocumentDownloader(Protocol):
    """Download one already owner-authorized managed document."""

    def observe(
        self, entry: ResearchInventoryEntry
    ) -> ManagedDocumentObservation:
        """Return the current owner-bound snapshot before body download."""
        raise NotImplementedError

    async def download(self, entry: ResearchInventoryEntry) -> bytes:
        """Return the immutable bytes for one owner-authorized document."""
        raise NotImplementedError

    if not TYPE_CHECKING:

        @property
        def contract_name(self) -> str:
            """Identify the runtime-only downloader contract."""
            return "managed_document_downloader"


class ManagedDocumentConverter(Protocol):
    """Convert one managed document into ordered pages or sections."""

    def convert(
        self, entry: ResearchInventoryEntry, payload: bytes
    ) -> tuple[ConvertedResearchSection, ...]:
        """Return all pages/sections in deterministic source order."""
        raise NotImplementedError

    if not TYPE_CHECKING:

        @property
        def contract_name(self) -> str:
            """Identify the runtime-only converter contract."""
            return "managed_document_converter"


@dataclass(frozen=True, slots=True)
class ManagedDocumentObservation:
    """Current owner-bound identity used to fence a document download."""

    exact_reference: str
    snapshot: ResearchInputSnapshot


@dataclass(frozen=True, slots=True)
class ResearchEvidenceUnit:
    """One transient text-bearing evidence unit with stable identity."""

    evidence_id: str
    source_kind: EvidenceSourceKind
    source_ordinal: int
    source_span: SourceSpan | None
    content_digest: str
    text: str
    dataset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _UnitSpec:
    """Private constructor bundle for one transient evidence unit."""

    evidence_id: str
    source_kind: EvidenceSourceKind
    source_ordinal: int
    source_span: SourceSpan | None
    text: str
    dataset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractedResearchEvidence:
    """All ordered evidence plus its safe, persistable coverage summary."""

    units: tuple[ResearchEvidenceUnit, ...]
    document_digests: tuple[DocumentEvidenceDigest, ...]
    coverage_digest: str

    def persistence_metadata(self) -> dict[str, Any]:
        """Return metadata suitable for private storage without plaintext."""
        return evidence_persistence_metadata(self)

    def to_persisted_metadata(self) -> dict[str, Any]:
        """Alias used by recovery code when serializing evidence metadata."""
        return self.persistence_metadata()


async def extract_research_evidence(
    request: ResearchEvidenceRequest,
    downloader: ManagedDocumentDownloader,
    converter: ManagedDocumentConverter,
) -> ExtractedResearchEvidence:
    """Download each managed document once and preserve every evidence unit."""
    _validate_request(request)
    _preflight_document_limits(request.inventory.documents)
    datasets = tuple(entry.dataset_id for entry in request.inventory.datasets)
    units = list(_query_units(request, datasets))
    document_digests: list[DocumentEvidenceDigest] = []
    total_bytes = 0
    for document_ordinal, entry in enumerate(request.inventory.documents):
        _observe_document(entry, downloader)
        payload = await _download_one(entry, downloader)
        total_bytes = _check_document_size(entry, payload, total_bytes)
        sections = _convert_one(entry, payload, converter)
        document_id = f"document_{document_ordinal + 1:03d}"
        document_units, digest = _document_units(
            document_id, sections, datasets, _is_pdf_entry(entry)
        )
        units.extend(document_units)
        document_digests.append(digest)
    units.extend(_hint_units(request.inventory))
    units.extend(_metadata_units(request.inventory))
    frozen_units = tuple(units)
    return ExtractedResearchEvidence(
        units=frozen_units,
        document_digests=tuple(document_digests),
        coverage_digest=_coverage_digest(frozen_units, document_digests),
    )


def evidence_persistence_metadata(
    evidence: ExtractedResearchEvidence,
) -> dict[str, Any]:
    """Project evidence to IDs/digests only; never retain transient text."""
    return {
        "units": tuple(
            {
                "evidence_id": unit.evidence_id,
                "source_kind": unit.source_kind,
                "source_ordinal": unit.source_ordinal,
                "source_span": _span_metadata(unit.source_span),
                "content_digest": unit.content_digest,
                "dataset_ids": unit.dataset_ids,
            }
            for unit in evidence.units
        ),
        "document_digests": tuple(
            {
                "document_id": digest.document_id,
                "content_digest": digest.content_digest,
                "evidence_ids": digest.evidence_ids,
            }
            for digest in evidence.document_digests
        ),
        "coverage_digest": evidence.coverage_digest,
    }


def _validate_request(request: ResearchEvidenceRequest) -> None:
    """Reject malformed parser/inventory shapes without leaking details."""
    if not isinstance(request, ResearchEvidenceRequest):
        raise _failure()
    if not isinstance(request.effective_query, str):
        raise _failure()
    if not isinstance(request.effective_to_original, tuple):
        raise _failure()
    if len(request.effective_query) != len(request.effective_to_original):
        raise _failure()
    _validate_inventory(request.inventory)
    previous = -1
    for original in request.effective_to_original:
        if _invalid_source_index(original, previous):
            # Original query length is not carried by this interface.  The
            # monotonic/non-negative checks still reject forged source maps.
            raise _failure()
        previous = original


def _invalid_source_index(original: object, previous: int) -> bool:
    """Return whether one effective-to-original map index is malformed."""
    return (
        isinstance(original, bool)
        or not isinstance(original, int)
        or original <= previous
        or original < 0
    )


def _validate_inventory(inventory: ResearchInputInventory) -> None:
    """Validate the immutable entry partitions used by this extractor."""
    if not isinstance(inventory, ResearchInputInventory):
        raise _failure()
    entries = inventory.entries
    if len({entry.dataset_id for entry in entries}) != len(entries):
        raise _failure()
    if (
        tuple(entry for entry in entries if entry.purpose == "document")
        != inventory.documents
    ):
        raise _failure()
    if (
        tuple(entry for entry in entries if entry.purpose == "dataset")
        != inventory.datasets
    ):
        raise _failure()
    if any(not entry.dataset_id for entry in entries):
        raise _failure()


def _preflight_document_limits(
    documents: Sequence[ResearchInventoryEntry],
) -> None:
    """Reject declared document sizes before any provider body is fetched."""
    total_bytes = 0
    for entry in documents:
        _validate_document_entry(entry)
        declared_size = entry.snapshot.size_bytes
        if declared_size > MAX_DOCUMENT_BYTES:
            raise _failure()
        total_bytes += declared_size
        if total_bytes > MAX_TOTAL_DOCUMENT_BYTES:
            raise _failure()


def _query_units(
    request: ResearchEvidenceRequest, datasets: tuple[str, ...]
) -> tuple[ResearchEvidenceUnit, ...]:
    """Split retained query text at removed-source gaps."""
    if not request.effective_query:
        return ()
    units: list[ResearchEvidenceUnit] = []
    start = 0
    mapping = request.effective_to_original
    for index in range(1, len(mapping) + 1):
        if index < len(mapping) and mapping[index] == mapping[index - 1] + 1:
            continue
        text = request.effective_query[start:index]
        original_start = mapping[start]
        original_end = mapping[index - 1] + 1
        span = SourceSpan(original_start, original_end, "query")
        units.append(
            _unit(
                _UnitSpec(
                    evidence_id=f"query_span_{len(units) + 1:03d}",
                    source_kind="query",
                    source_ordinal=len(units),
                    source_span=span,
                    text=text,
                    dataset_ids=datasets,
                )
            )
        )
        start = index
    return tuple(units)


async def _download_one(
    entry: ResearchInventoryEntry, downloader: ManagedDocumentDownloader
) -> bytes:
    """Download once and map all provider details to a safe domain error."""
    _validate_document_entry(entry)
    try:
        payload = await downloader.download(entry)
    except ResearchInputFailure:
        raise
    except Exception as error:
        raise _failure(retryable=True, status=503) from error
    if not isinstance(payload, bytes) or not payload:
        raise _failure()
    return payload


def _observe_document(
    entry: ResearchInventoryEntry,
    downloader: ManagedDocumentDownloader,
) -> ManagedDocumentObservation:
    """Revalidate owner identity and snapshot before downloading bytes."""
    try:
        observation = downloader.observe(entry)
    except ResearchInputFailure:
        raise
    except Exception as error:
        raise _failure(retryable=True, status=503) from error
    if not isinstance(observation, ManagedDocumentObservation):
        raise _failure()
    if (
        not isinstance(observation.exact_reference, str)
        or not isinstance(observation.snapshot, ResearchInputSnapshot)
        or observation.exact_reference != entry.exact_reference
        or observation.snapshot != entry.snapshot
    ):
        raise _failure()
    return observation


def _validate_document_entry(entry: ResearchInventoryEntry) -> None:
    """Check owner snapshot state before a document body is consumed."""
    snapshot = entry.snapshot
    if _invalid_document_entry(entry, snapshot):
        raise _failure()


def _invalid_document_entry(
    entry: ResearchInventoryEntry, snapshot: Any
) -> bool:
    """Return whether an entry lacks a complete owner snapshot."""
    return any(
        (
            entry.purpose != "document",
            snapshot.purpose != "document",
            snapshot.placeholder,
            snapshot.size_bytes <= 0,
            entry.size_bytes != snapshot.size_bytes,
            not snapshot.snapshot_digest,
            not entry.dataset_id,
        )
    )


def _check_document_size(
    entry: ResearchInventoryEntry, payload: bytes, total_bytes: int
) -> int:
    """Enforce raw document bounds before conversion and aggregate memory."""
    if len(payload) != entry.snapshot.size_bytes:
        raise _failure()
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise _failure()
    next_total = total_bytes + len(payload)
    if next_total > MAX_TOTAL_DOCUMENT_BYTES:
        raise _failure()
    return next_total


def _convert_one(
    entry: ResearchInventoryEntry,
    payload: bytes,
    converter: ManagedDocumentConverter,
) -> tuple[ConvertedResearchSection, ...]:
    """Convert once and require complete ordered non-empty output."""
    try:
        sections = converter.convert(entry, payload)
        normalized = tuple(sections)
    except Exception as error:
        raise _failure() from error
    if not normalized:
        raise _failure()
    first_ordinal = normalized[0].ordinal
    if first_ordinal not in (0, 1):
        raise _failure()
    for index, section in enumerate(normalized):
        if _invalid_section(section, first_ordinal + index):
            raise _failure()
    return normalized


def _invalid_section(section: object, expected_ordinal: int) -> bool:
    """Return whether one converter section is empty or out of order."""
    if not isinstance(section, ConvertedResearchSection):
        return True
    return any(
        (
            section.ordinal != expected_ordinal,
            not isinstance(section.label, str),
            not section.label.strip(),
            not isinstance(section.text, str),
            not section.text.strip(),
        )
    )


def _document_units(
    document_id: str,
    sections: tuple[ConvertedResearchSection, ...],
    datasets: tuple[str, ...],
    is_pdf: bool,
) -> tuple[tuple[ResearchEvidenceUnit, ...], DocumentEvidenceDigest]:
    """Build page/section units in converter order."""
    units: list[ResearchEvidenceUnit] = []
    first_ordinal = sections[0].ordinal
    for section in sections:
        display_ordinal = (
            section.ordinal if first_ordinal == 1 else section.ordinal + 1
        )
        suffix = "page" if is_pdf else "section"
        units.append(
            _unit(
                _UnitSpec(
                    evidence_id=(
                        f"{document_id}_{suffix}_{display_ordinal:03d}"
                    ),
                    source_kind="pdf_page" if is_pdf else "document_section",
                    source_ordinal=section.ordinal,
                    source_span=None,
                    text=section.text,
                    dataset_ids=datasets,
                )
            )
        )
    digest = DocumentEvidenceDigest(
        document_id=document_id,
        content_digest=_digest(
            [
                (section.ordinal, section.label, section.text)
                for section in sections
            ]
        ),
        evidence_ids=tuple(unit.evidence_id for unit in units),
    )
    return tuple(units), digest


def _is_pdf_entry(entry: ResearchInventoryEntry) -> bool:
    """Identify PDFs from the trusted safe filename, not converter text."""
    return entry.safe_basename.casefold().endswith(".pdf")


def _hint_units(
    inventory: ResearchInputInventory,
) -> tuple[ResearchEvidenceUnit, ...]:
    """Preserve exact non-empty user hints in inventory order."""
    units: list[ResearchEvidenceUnit] = []
    for entry in inventory.datasets:
        if entry.user_hint:
            units.append(
                _unit(
                    _UnitSpec(
                        evidence_id=f"{entry.dataset_id}_hint_001",
                        source_kind="user_hint",
                        source_ordinal=entry.lane_ordinal,
                        source_span=entry.source_span,
                        text=entry.user_hint,
                        dataset_ids=(entry.dataset_id,),
                    )
                )
            )
    return tuple(units)


def _metadata_units(
    inventory: ResearchInputInventory,
) -> tuple[ResearchEvidenceUnit, ...]:
    """Represent trusted dataset metadata without exact object references."""
    group_members = tuple(entry.dataset_id for entry in inventory.entries)
    units: list[ResearchEvidenceUnit] = []
    for dataset_ordinal, entry in enumerate(inventory.datasets):
        metadata = {
            "dataset_id": entry.dataset_id,
            "safe_basename": entry.safe_basename,
            "compound_suffix": entry.compound_suffix,
            "media_hint": entry.media_hint,
            "size_bytes": entry.size_bytes,
            "dataset_order": dataset_ordinal,
            "inventory_order": inventory.entries.index(entry),
            "group_members": group_members,
        }
        text = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        units.append(
            _unit(
                _UnitSpec(
                    evidence_id=f"{entry.dataset_id}_meta_001",
                    source_kind="dataset_meta",
                    source_ordinal=dataset_ordinal,
                    source_span=None,
                    text=text,
                    dataset_ids=(entry.dataset_id,),
                )
            )
        )
    return tuple(units)


def _unit(spec: _UnitSpec) -> ResearchEvidenceUnit:
    """Construct one immutable unit with a text-derived content digest."""
    return ResearchEvidenceUnit(
        evidence_id=spec.evidence_id,
        source_kind=spec.source_kind,
        source_ordinal=spec.source_ordinal,
        source_span=spec.source_span,
        content_digest=_sha256_text(spec.text),
        text=spec.text,
        dataset_ids=spec.dataset_ids,
    )


def _coverage_digest(
    units: Sequence[ResearchEvidenceUnit],
    documents: Sequence[DocumentEvidenceDigest],
) -> str:
    """Hash IDs, memberships, and digests without serializing plaintext."""
    return _digest(
        {
            "units": [
                {
                    "evidence_id": unit.evidence_id,
                    "source_kind": unit.source_kind,
                    "source_ordinal": unit.source_ordinal,
                    "source_span": _span_metadata(unit.source_span),
                    "content_digest": unit.content_digest,
                    "dataset_ids": unit.dataset_ids,
                }
                for unit in units
            ],
            "documents": [
                {
                    "document_id": item.document_id,
                    "content_digest": item.content_digest,
                    "evidence_ids": item.evidence_ids,
                }
                for item in documents
            ],
        }
    )


def _span_metadata(span: SourceSpan | None) -> dict[str, Any] | None:
    """Return the non-text source mapping for persistence."""
    if span is None:
        return None
    return {"start": span.start, "end": span.end, "grammar": span.grammar}


def _sha256_text(text: str) -> str:
    """Hash UTF-8 content deterministically."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(value: object) -> str:
    """Hash canonical JSON metadata."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failure(
    *, retryable: bool = False, status: int = 422
) -> ResearchInputFailure:
    """Create one bounded failure with no provider/path details."""
    return research_input_failure(
        "research_document_extraction_failed",
        _SAFE_EXTRACTION_MESSAGE,
        http_status_hint=status,
        retryable=retryable,
    )
