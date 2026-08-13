# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Deterministic immutable inventory for trusted Research inputs."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Literal

from ...runtime.attachment_assets import ResolvedAttachmentBundle
from ...storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectCandidate,
    ResearchObjectMetadataError,
    ResearchObjectMetadataPort,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectVerifyRequest,
    research_object_authority_scope,
)
from .input_contracts import (
    ParsedResearchInput,
    PastedDatasetCandidate,
    ResearchInputFailure,
    SourceSpan,
    research_input_failure,
)
from .scientific_formats import classify_scientific_reference

__all__ = [
    "ManagedResearchAssetSnapshot",
    "ManagedResearchAssetResolver",
    "ResearchInputInventory",
    "ResearchInputSnapshot",
    "ResearchInventoryEntry",
    "ResearchInventoryRequest",
    "build_research_inventory",
    "validate_research_inventory",
    "revalidate_research_inventory",
    "research_inventory_partitions",
    "same_research_inventory_snapshot",
]

_MAX_RESEARCH_REFERENCES = 256
_Purpose = Literal["document", "dataset"]
_Lane = Literal["managed", "pasted"]


@dataclass(frozen=True, slots=True)
class _ManagedResearchAssetSnapshotIdentity:
    """Stable identity fields shared by the public snapshot projection."""

    asset_id: str
    exact_reference: str
    size_bytes: int
    purpose: _Purpose
    completed: bool
    state_version: int


@dataclass(frozen=True, slots=True)
class ManagedResearchAssetSnapshot(_ManagedResearchAssetSnapshotIdentity):
    """One immutable server-owned managed attachment state snapshot."""

    completed_at: str
    etag: str | None
    version_id: str | None
    last_modified: str | None
    snapshot_digest: str


type ManagedResearchAssetResolver = Callable[
    [tuple[str, ...]], tuple[ManagedResearchAssetSnapshot, ...]
]


@dataclass(frozen=True, slots=True)
class _ResearchInputSnapshotIdentity:
    """Stable identity fields for an immutable input snapshot."""

    lane: _Lane
    size_bytes: int
    state_version: int | None
    completed_at: str | None
    etag: str | None


@dataclass(frozen=True, slots=True)
class ResearchInputSnapshot(_ResearchInputSnapshotIdentity):
    """Immutable metadata used to detect input drift before execution."""

    version_id: str | None
    last_modified: str | None
    placeholder: bool
    purpose: _Purpose
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class ResearchInventoryRequest:
    """Trusted parsed and managed inputs plus effective Research limits."""

    parsed_input: ParsedResearchInput
    managed_assets: tuple[ManagedResearchAssetSnapshot, ...]
    configured_bucket: str
    max_managed_references: int
    max_pasted_references: int
    max_combined_references: int


@dataclass(frozen=True, slots=True)
class _ResearchInventoryEntryIdentity:
    """Stable source identity fields for one Research input."""

    dataset_id: str
    lane: _Lane
    lane_ordinal: int
    exact_reference: str
    comparison_digest: str
    safe_basename: str
    compound_suffix: str


@dataclass(frozen=True, slots=True)
class ResearchInventoryEntry(_ResearchInventoryEntryIdentity):
    """One private immutable Research input with deterministic identity."""

    size_bytes: int
    media_hint: str
    purpose: _Purpose
    user_hint: str | None
    source_span: SourceSpan | None
    snapshot: ResearchInputSnapshot
    authority_id: str | None


@dataclass(frozen=True, slots=True)
class ResearchInputInventory:
    """Ordered immutable managed-first, pasted-second Research inputs."""

    entries: tuple[ResearchInventoryEntry, ...]
    documents: tuple[ResearchInventoryEntry, ...]
    datasets: tuple[ResearchInventoryEntry, ...]
    digest: str
    authorities: tuple[ResearchObjectAuthority, ...] = ()


@dataclass(frozen=True, slots=True)
class _DraftEntryIdentity:
    """Stable source identity fields held during inventory construction."""

    dataset_id: str
    lane: _Lane
    lane_ordinal: int
    exact_reference: str
    comparison_key: str
    safe_basename: str
    compound_suffix: str


@dataclass(frozen=True, slots=True)
class _DraftEntry(_DraftEntryIdentity):
    """Validated entry details held before any metadata-port operation."""

    media_hint: str
    purpose: _Purpose
    user_hint: str | None
    source_span: SourceSpan | None
    managed_snapshot: ManagedResearchAssetSnapshot | None


async def build_research_inventory(
    request: ResearchInventoryRequest,
    object_port: ResearchObjectMetadataPort,
) -> ResearchInputInventory:
    """Merge managed assets first and pasted source order second.

    Every duplicate, limit, structural, and managed-state check completes
    before object metadata resolution.  The port is only asked for exact-key
    metadata; it never receives a request to read an object body.
    """
    drafts = _preflight(request)
    authorities = await _resolve_drafts(drafts, object_port)
    entries = _entries_from_drafts(drafts, authorities)
    return _inventory(
        entries,
        tuple(
            authorities[entry.dataset_id]
            for entry in entries
            if entry.dataset_id in authorities
        ),
    )


async def validate_research_inventory(
    request: ResearchInventoryRequest,
    object_port: ResearchObjectMetadataPort,
) -> None:
    """Validate metadata and release provisional authorities immediately.

    Admission validation runs before a durable parent scope exists.  The
    metadata port therefore receives a deterministic provisional scope; its
    authorities must not survive that synchronous validation boundary.  The
    coordinator performs the retained, run-scoped resolution later.
    """
    drafts = _preflight(request)
    authorities: dict[str, ResearchObjectAuthority] = {}
    try:
        authorities = await _resolve_drafts(drafts, object_port)
        _entries_from_drafts(drafts, authorities)
    except BaseException:
        if authorities:
            await _revoke_provisional(
                object_port,
                research_object_authority_scope(_dataset_candidates(drafts)),
                authorities,
            )
        raise
    if authorities:
        await _revoke_provisional(
            object_port,
            research_object_authority_scope(_dataset_candidates(drafts)),
            authorities,
        )


async def _revoke_provisional(
    object_port: ResearchObjectMetadataPort,
    scope: str,
    authorities: Mapping[str, ResearchObjectAuthority],
) -> None:
    """Release one preflight authority set under its original scope."""
    await object_port.revoke(
        ResearchObjectRevokeRequest(
            parent_run_id=f"inventory-{scope}",
            execution_fingerprint=scope,
            authority_ids=tuple(
                authority.authority_id for authority in authorities.values()
            ),
        )
    )


async def revalidate_research_inventory(
    request: ResearchInventoryRequest,
    inventory: ResearchInputInventory,
    object_port: ResearchObjectMetadataPort,
    *,
    managed_asset_resolver: ManagedResearchAssetResolver | None = None,
) -> ResearchInputInventory:
    """Reverify all inputs and reject any immutable snapshot drift.

    Managed assets require an owner-bound resolver so revalidation never trusts
    the old request snapshot after an asset was reclaimed or changed.
    """
    refreshed_request = _request_with_current_managed_assets(
        request, managed_asset_resolver
    )
    drafts = _preflight(refreshed_request)
    authorities = await _verify_drafts(
        drafts,
        inventory.authorities,
        object_port,
    )
    entries = _entries_from_drafts(drafts, authorities)
    refreshed = _inventory(
        entries,
        tuple(
            authorities[entry.dataset_id]
            for entry in entries
            if entry.dataset_id in authorities
        ),
    )
    if not _same_inventory_snapshot(inventory, refreshed):
        raise research_input_failure(
            "research_input_resolution_failed",
            "Research input metadata changed before execution.",
        )
    return refreshed


def same_research_inventory_snapshot(
    previous: ResearchInputInventory, refreshed: ResearchInputInventory
) -> bool:
    """Compare immutable coordinates while allowing authority rotation."""
    if not isinstance(previous, ResearchInputInventory) or not isinstance(
        refreshed, ResearchInputInventory
    ):
        return refreshed == previous
    return _same_inventory_snapshot(previous, refreshed)


def managed_research_assets_from_bundle(
    bundle: ResolvedAttachmentBundle,
) -> tuple[ManagedResearchAssetSnapshot, ...]:
    """Project owner-validated resolved assets into immutable snapshots."""
    snapshots: list[ManagedResearchAssetSnapshot] = []
    for asset in bundle.all_assets:
        if (
            asset.state_version < 1
            or not asset.completed_at
            or asset.size_bytes <= 0
        ):
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research managed attachment state could not be verified.",
            )
        snapshot = ManagedResearchAssetSnapshot(
            asset_id=asset.asset_id,
            exact_reference=_canonical_managed_reference(asset.reference),
            size_bytes=asset.size_bytes,
            purpose=asset.purpose,
            completed=True,
            state_version=asset.state_version,
            completed_at=asset.completed_at,
            etag=None,
            version_id=None,
            last_modified=None,
            snapshot_digest="",
        )
        snapshots.append(
            replace(
                snapshot,
                snapshot_digest=_managed_snapshot_digest(snapshot),
            )
        )
    return tuple(snapshots)


def _canonical_managed_reference(reference: str) -> str:
    """Convert the resolver's obsfs path to Research's opaque URI form."""
    if not reference.startswith("/obs/"):
        return reference
    bucket, separator, key = reference.removeprefix("/obs/").partition("/")
    if not bucket or not separator or not key:
        return reference
    return f"obs://{bucket}/{key}"


def _request_with_current_managed_assets(
    request: ResearchInventoryRequest,
    resolver: ManagedResearchAssetResolver | None,
) -> ResearchInventoryRequest:
    """Replace managed snapshots with current owner-qualified resolution."""
    if not request.managed_assets:
        return request
    if resolver is None:
        raise research_input_failure(
            "research_input_resolution_failed",
            "Research managed attachment state could not be revalidated.",
        )
    asset_ids = tuple(asset.asset_id for asset in request.managed_assets)
    try:
        refreshed_assets = resolver(asset_ids)
    except Exception:
        raise research_input_failure(
            "research_input_resolution_failed",
            "Research managed attachment state could not be revalidated.",
        ) from None
    if tuple(asset.asset_id for asset in refreshed_assets) != asset_ids:
        raise research_input_failure(
            "research_input_resolution_failed",
            "Research managed attachment state could not be revalidated.",
        )
    return replace(request, managed_assets=refreshed_assets)


def _preflight(request: ResearchInventoryRequest) -> tuple[_DraftEntry, ...]:
    """Validate all local inputs before metadata resolution can begin."""
    _validate_limits(request)
    drafts: list[_DraftEntry] = []
    seen: set[str] = set()
    for ordinal, asset in enumerate(request.managed_assets):
        draft = _managed_draft(asset, ordinal, request.configured_bucket)
        _append_unique(drafts, seen, draft)
    for candidate in request.parsed_input.candidates:
        draft = _pasted_draft(
            candidate,
            _candidate_source_span(
                request.parsed_input, candidate.source_start
            ),
            len(drafts) + 1,
        )
        _append_unique(drafts, seen, draft)
    return tuple(drafts)


def _candidate_source_span(
    parsed_input: ParsedResearchInput, source_start: int
) -> SourceSpan:
    """Retain the parser-owned source grammar span for one pasted reference."""
    for span in parsed_input.removed_spans:
        if span.start <= source_start < span.end:
            return span
    raise research_input_failure(
        "research_input_resolution_failed",
        "Research dataset source provenance could not be verified.",
    )


def _validate_limits(request: ResearchInventoryRequest) -> None:
    """Apply lane, effective configuration, and structural count bounds."""
    managed_count = len(request.managed_assets)
    pasted_count = len(request.parsed_input.candidates)
    total_count = managed_count + pasted_count
    limits = (
        request.max_managed_references,
        request.max_pasted_references,
        request.max_combined_references,
    )
    if (
        any(limit < 1 or limit > _MAX_RESEARCH_REFERENCES for limit in limits)
        or managed_count > request.max_managed_references
        or pasted_count > request.max_pasted_references
        or total_count > request.max_combined_references
        or total_count > _MAX_RESEARCH_REFERENCES
    ):
        raise research_input_failure(
            "research_input_limit_exceeded",
            "Research input count exceeds the allowed limit.",
        )


def _managed_draft(
    asset: ManagedResearchAssetSnapshot,
    ordinal: int,
    bucket: str,
) -> _DraftEntry:
    """Validate one server-owned managed asset without metadata I/O."""
    if _managed_state_invalid(asset):
        raise research_input_failure(
            "research_input_resolution_failed",
            "Research managed attachment state could not be verified.",
        )
    comparison_key, basename = _reference_identity(
        asset.exact_reference, bucket
    )
    format_descriptor = classify_scientific_reference(asset.exact_reference)
    suffix = (
        "" if format_descriptor is None else format_descriptor.canonical_suffix
    )
    media_hint = (
        "application/octet-stream"
        if format_descriptor is None
        else format_descriptor.media_hint
    )
    return _DraftEntry(
        dataset_id="",
        lane="managed",
        lane_ordinal=ordinal,
        exact_reference=asset.exact_reference,
        comparison_key=comparison_key,
        safe_basename=basename,
        compound_suffix=suffix,
        media_hint=media_hint,
        purpose=asset.purpose,
        user_hint=None,
        source_span=None,
        managed_snapshot=asset,
    )


def _managed_state_invalid(asset: ManagedResearchAssetSnapshot) -> bool:
    """Return whether a trusted managed snapshot lacks required state."""
    return any(
        (
            not asset.asset_id,
            not asset.completed,
            asset.state_version < 1,
            not asset.completed_at,
            asset.purpose not in ("document", "dataset"),
            asset.size_bytes <= 0,
            not asset.snapshot_digest,
        )
    )


def _pasted_draft(
    candidate: PastedDatasetCandidate,
    source_span: SourceSpan,
    dataset_number: int,
) -> _DraftEntry:
    """Build one parsed pasted dataset draft after parser validation."""
    descriptor = classify_scientific_reference(candidate.exact_reference)
    if descriptor is None:
        raise research_input_failure(
            "research_dataset_format_unsupported",
            "Research dataset format is unsupported.",
        )
    return _DraftEntry(
        dataset_id=f"dataset_{dataset_number:03d}",
        lane="pasted",
        lane_ordinal=candidate.ordinal,
        exact_reference=candidate.exact_reference,
        comparison_key=candidate.comparison_key,
        safe_basename=_safe_basename(candidate.exact_reference),
        compound_suffix=descriptor.canonical_suffix,
        media_hint=descriptor.media_hint,
        purpose="dataset",
        user_hint=candidate.user_hint,
        source_span=source_span,
        managed_snapshot=None,
    )


def _append_unique(
    drafts: list[_DraftEntry], seen: set[str], draft: _DraftEntry
) -> None:
    """Reject duplicates and assign a stable global dataset identity."""
    if draft.comparison_key in seen:
        raise research_input_failure(
            "research_dataset_duplicate", "Research dataset is duplicated."
        )
    seen.add(draft.comparison_key)
    drafts.append(replace(draft, dataset_id=f"dataset_{len(drafts) + 1:03d}"))


async def _resolve_drafts(
    drafts: tuple[_DraftEntry, ...], object_port: ResearchObjectMetadataPort
) -> dict[str, ResearchObjectAuthority]:
    """Resolve exact-key metadata for datasets only after local preflight."""
    candidates = _dataset_candidates(drafts)
    if not candidates:
        return {}
    scope = research_object_authority_scope(candidates)
    try:
        authorities = await object_port.resolve(
            ResearchObjectResolveRequest(
                parent_run_id=f"inventory-{scope}",
                execution_fingerprint=scope,
                objects=candidates,
            )
        )
    except ResearchObjectMetadataError as error:
        raise research_input_failure(
            "research_dataset_not_found",
            "Research dataset metadata could not be verified.",
        ) from error
    return _authority_map(candidates, authorities)


async def _verify_drafts(
    drafts: tuple[_DraftEntry, ...],
    persisted: tuple[ResearchObjectAuthority, ...],
    object_port: ResearchObjectMetadataPort,
) -> dict[str, ResearchObjectAuthority]:
    """Verify provisional authorities without minting a second grant set."""
    candidates = _dataset_candidates(drafts)
    if not candidates:
        if persisted:
            raise _metadata_resolution_failure()
        return {}
    persisted_by_dataset = _authority_map(candidates, persisted)
    scope = research_object_authority_scope(candidates)
    try:
        authorities = await object_port.verify(
            ResearchObjectVerifyRequest(
                parent_run_id=f"inventory-{scope}",
                execution_fingerprint=scope,
                authorities=tuple(
                    persisted_by_dataset[candidate.dataset_id]
                    for candidate in candidates
                ),
            )
        )
    except ResearchObjectMetadataError as error:
        raise _metadata_resolution_failure() from error
    return _authority_map(candidates, authorities)


def _dataset_candidates(
    drafts: tuple[_DraftEntry, ...],
) -> tuple[ResearchObjectCandidate, ...]:
    """Project ordered dataset drafts into exact-key metadata candidates."""
    return tuple(
        ResearchObjectCandidate(
            dataset_id=draft.dataset_id,
            exact_reference=draft.exact_reference,
            compound_suffix=draft.compound_suffix,
        )
        for draft in drafts
        if draft.purpose == "dataset"
    )


def _authority_map(
    candidates: tuple[ResearchObjectCandidate, ...],
    authorities: tuple[ResearchObjectAuthority, ...],
) -> dict[str, ResearchObjectAuthority]:
    """Validate one complete, unique authority set by dataset identity."""
    requested_ids = {candidate.dataset_id for candidate in candidates}
    authorities_by_dataset: dict[str, ResearchObjectAuthority] = {}
    for authority in authorities:
        if _invalid_authority(
            authority, requested_ids, authorities_by_dataset
        ):
            raise research_input_failure(
                "research_input_resolution_failed",
                "Research dataset metadata could not be verified.",
            )
        authorities_by_dataset[authority.dataset_id] = authority
    if (
        len(authorities) != len(candidates)
        or set(authorities_by_dataset) != requested_ids
        or len({authority.authority_id for authority in authorities})
        != len(authorities)
    ):
        raise _metadata_resolution_failure()
    return authorities_by_dataset


def _metadata_resolution_failure() -> ResearchInputFailure:
    """Build the stable failure for malformed metadata authority sets."""
    return research_input_failure(
        "research_input_resolution_failed",
        "Research dataset metadata could not be verified.",
    )


def _invalid_authority(
    authority: ResearchObjectAuthority,
    requested_ids: set[str],
    resolved: dict[str, ResearchObjectAuthority],
) -> bool:
    """Return whether one authority violates its exact-key contract."""
    return any(
        (
            not isinstance(authority.authority_id, str),
            not authority.authority_id.strip(),
            authority.dataset_id not in requested_ids,
            authority.snapshot.dataset_id != authority.dataset_id,
            authority.snapshot.placeholder,
            authority.dataset_id in resolved,
        )
    )


def _entries_from_drafts(
    drafts: tuple[_DraftEntry, ...],
    authorities: dict[str, ResearchObjectAuthority],
) -> tuple[ResearchInventoryEntry, ...]:
    """Merge local managed state and exact-key metadata into entries."""
    entries: list[ResearchInventoryEntry] = []
    for draft in drafts:
        authority = authorities.get(draft.dataset_id)
        snapshot = _entry_snapshot(draft, authority)
        entries.append(
            ResearchInventoryEntry(
                dataset_id=draft.dataset_id,
                lane=draft.lane,
                lane_ordinal=draft.lane_ordinal,
                exact_reference=draft.exact_reference,
                comparison_digest=_digest(draft.comparison_key),
                safe_basename=draft.safe_basename,
                compound_suffix=draft.compound_suffix,
                size_bytes=snapshot.size_bytes,
                media_hint=draft.media_hint,
                purpose=draft.purpose,
                user_hint=draft.user_hint,
                source_span=draft.source_span,
                snapshot=snapshot,
                authority_id=(
                    None if authority is None else authority.authority_id
                ),
            )
        )
    return tuple(entries)


def _entry_snapshot(
    draft: _DraftEntry, authority: ResearchObjectAuthority | None
) -> ResearchInputSnapshot:
    """Construct one immutable entry snapshot from trusted metadata."""
    managed = draft.managed_snapshot
    if draft.purpose == "document":
        if managed is None:
            raise AssertionError("pasted inputs are always datasets")
        return ResearchInputSnapshot(
            lane=draft.lane,
            size_bytes=managed.size_bytes,
            state_version=managed.state_version,
            completed_at=managed.completed_at,
            etag=managed.etag,
            version_id=managed.version_id,
            last_modified=managed.last_modified,
            placeholder=managed.size_bytes == 0,
            purpose=managed.purpose,
            snapshot_digest=_managed_snapshot_digest(managed),
        )
    if authority is None:
        raise research_input_failure(
            "research_input_resolution_failed",
            "Research dataset metadata could not be verified.",
        )
    object_snapshot = authority.snapshot
    if (
        managed is not None
        and managed.size_bytes != object_snapshot.size_bytes
    ):
        raise research_input_failure(
            "research_input_resolution_failed",
            "Research managed attachment state could not be verified.",
        )
    return ResearchInputSnapshot(
        lane=draft.lane,
        size_bytes=object_snapshot.size_bytes,
        state_version=None if managed is None else managed.state_version,
        completed_at=None if managed is None else managed.completed_at,
        etag=object_snapshot.etag,
        version_id=object_snapshot.version_id,
        last_modified=object_snapshot.last_modified,
        placeholder=object_snapshot.placeholder,
        purpose=draft.purpose,
        snapshot_digest=_digest(
            {
                "managed": (
                    None
                    if managed is None
                    else _managed_snapshot_digest(managed)
                ),
                "object": object_snapshot.snapshot_digest,
            }
        ),
    )


def research_inventory_partitions(
    entries: tuple[ResearchInventoryEntry, ...],
    *,
    digest: str,
    authorities: tuple[ResearchObjectAuthority, ...] = (),
) -> ResearchInputInventory:
    """Build immutable purpose partitions from already trusted entries."""
    return ResearchInputInventory(
        entries=entries,
        documents=tuple(
            entry for entry in entries if entry.purpose == "document"
        ),
        datasets=tuple(
            entry for entry in entries if entry.purpose == "dataset"
        ),
        digest=digest,
        authorities=authorities,
    )


def _inventory(
    entries: tuple[ResearchInventoryEntry, ...],
    authorities: tuple[ResearchObjectAuthority, ...] = (),
) -> ResearchInputInventory:
    """Build immutable purpose partitions and a non-secret digest."""
    digest = _digest(
        [
            {
                "comparison_digest": entry.comparison_digest,
                "dataset_id": entry.dataset_id,
                "lane": entry.lane,
                "lane_ordinal": entry.lane_ordinal,
                "purpose": entry.purpose,
                "snapshot_digest": entry.snapshot.snapshot_digest,
            }
            for entry in entries
        ]
    )
    return research_inventory_partitions(
        entries, digest=digest, authorities=authorities
    )


def _same_inventory_snapshot(
    previous: ResearchInputInventory, refreshed: ResearchInputInventory
) -> bool:
    """Compare every immutable entry field except renewed authority tokens."""
    if previous.digest != refreshed.digest or len(previous.entries) != len(
        refreshed.entries
    ):
        return False
    return all(
        replace(before, authority_id=None) == replace(after, authority_id=None)
        for before, after in zip(
            previous.entries, refreshed.entries, strict=True
        )
    )


def _managed_snapshot_digest(asset: ManagedResearchAssetSnapshot) -> str:
    """Digest all managed state used to reject revalidation drift."""
    return _digest(
        {
            "asset_id": asset.asset_id,
            "completed": asset.completed,
            "completed_at": asset.completed_at,
            "etag": asset.etag,
            "exact_reference": asset.exact_reference,
            "last_modified": asset.last_modified,
            "purpose": asset.purpose,
            "size_bytes": asset.size_bytes,
            "source_snapshot_digest": asset.snapshot_digest,
            "state_version": asset.state_version,
            "version_id": asset.version_id,
        }
    )


def _reference_identity(reference: str, bucket: str) -> tuple[str, str]:
    """Validate a configured-bucket OBS reference and comparison data."""
    if not isinstance(reference, str) or not bucket or _has_control(reference):
        raise research_input_failure(
            "research_dataset_path_invalid",
            "Research dataset path is invalid.",
        )
    prefix = "obs://"
    if reference[: len(prefix)].casefold() != prefix:
        raise research_input_failure(
            "research_dataset_path_invalid",
            "Research dataset path is invalid.",
        )
    reference_bucket, separator, key = reference[6:].partition("/")
    segments = key.split("/")
    if (
        not separator
        or reference_bucket.casefold() != bucket.casefold()
        or not key
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        raise research_input_failure(
            "research_dataset_path_invalid",
            "Research dataset path is invalid.",
        )
    normalized_key = unicodedata.normalize("NFC", key)
    return f"obs://{bucket.lower()}/{normalized_key}", _safe_basename(
        reference
    )


def _safe_basename(reference: str) -> str:
    """Return the final validated POSIX path element without rewriting it."""
    name = PurePosixPath(reference).name
    if not name or name in (".", ".."):
        raise research_input_failure(
            "research_dataset_path_invalid",
            "Research dataset path is invalid.",
        )
    return name


def _has_control(value: str) -> bool:
    """Reject every Unicode control character in private exact references."""
    return any(unicodedata.category(character) == "Cc" for character in value)


def _digest(value: object) -> str:
    """Return one canonical SHA-256 digest without retaining raw references."""
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
