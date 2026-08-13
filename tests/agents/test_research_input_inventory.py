# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for deterministic immutable Research input inventories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Literal

import pytest

from mcp_server_phytomni.agents.research.input_contracts import (
    ParsedResearchInput,
    PastedDatasetCandidate,
    ResearchInputFailure,
    SourceSpan,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
    ResearchInventoryRequest,
    build_research_inventory,
    revalidate_research_inventory,
)
from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectResolveRequest,
    ResearchObjectRevokeRequest,
    ResearchObjectSnapshot,
    ResearchObjectVerifyRequest,
)

pytestmark = pytest.mark.agent


class RecordingResearchObjectPort:
    """Deterministic metadata port that records resolution boundaries."""

    def __init__(self) -> None:
        self.resolve_calls: list[ResearchObjectResolveRequest] = []
        self.verify_calls: list[ResearchObjectVerifyRequest] = []
        self.revoke_calls: list[ResearchObjectRevokeRequest] = []
        self.authority_override: tuple[ResearchObjectAuthority, ...] | None = (
            None
        )
        self.metadata_etag: str | None = None
        self.rotated_authority_id: str | None = None

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return one immutable non-placeholder authority per candidate."""
        self.resolve_calls.append(request)
        authorities = tuple(
            ResearchObjectAuthority(
                dataset_id=candidate.dataset_id,
                authority_id=f"authority-{candidate.dataset_id}",
                snapshot=ResearchObjectSnapshot(
                    dataset_id=candidate.dataset_id,
                    size_bytes=17,
                    etag=self.metadata_etag or f"etag-{index}",
                    version_id=None,
                    last_modified="2026-08-08T00:00:00+00:00",
                    placeholder=False,
                    snapshot_digest=f"snapshot-{index}",
                ),
            )
            for index, candidate in enumerate(request.objects, start=1)
        )
        return (
            authorities
            if self.authority_override is None
            else self.authority_override
        )

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return current metadata and an optional rotated authority ID."""
        self.verify_calls.append(request)
        return tuple(
            replace(
                authority,
                authority_id=(
                    self.rotated_authority_id or authority.authority_id
                ),
                snapshot=(
                    replace(
                        authority.snapshot,
                        etag=self.metadata_etag,
                        snapshot_digest="snapshot-changed",
                    )
                    if self.metadata_etag is not None
                    else authority.snapshot
                ),
            )
            for authority in request.authorities
        )

    async def revoke(self, request: ResearchObjectRevokeRequest) -> None:
        """Accept no-op revocation for this stateless fake."""
        self.revoke_calls.append(request)


def _managed(
    asset_id: str,
    reference: str,
    *,
    purpose: Literal["dataset", "document"] = "dataset",
) -> ManagedResearchAssetSnapshot:
    """Build one valid server-owned managed Research asset snapshot."""
    return ManagedResearchAssetSnapshot(
        asset_id=asset_id,
        exact_reference=reference,
        size_bytes=17,
        purpose=purpose,
        completed=True,
        state_version=1,
        completed_at="2026-08-08T00:00:00+00:00",
        etag="managed-etag",
        version_id="managed-v1",
        last_modified="2026-08-08T00:00:00+00:00",
        snapshot_digest="managed-snapshot",
    )


def _parsed(*references: str) -> ParsedResearchInput:
    """Build a source-ordered parse result without involving storage."""
    return ParsedResearchInput(
        original_query_digest="query-digest",
        original_query_length=0,
        effective_query="question",
        effective_to_original=(),
        removed_spans=tuple(
            SourceSpan(
                index * 10,
                index * 10 + len(reference),
                "standalone_tab",
            )
            for index, reference in enumerate(references)
        ),
        candidates=tuple(
            PastedDatasetCandidate(
                exact_reference=reference,
                comparison_key=reference.casefold(),
                user_hint=f"hint-{index}",
                source_start=index * 10,
                source_end=index * 10 + len(reference),
                ordinal=index,
            )
            for index, reference in enumerate(references)
        ),
    )


def _request(
    *,
    parsed_input: ParsedResearchInput | None = None,
    managed_assets: tuple[ManagedResearchAssetSnapshot, ...] = (),
    max_managed_references: int = 64,
    max_pasted_references: int = 128,
    max_combined_references: int = 256,
) -> ResearchInventoryRequest:
    """Build one bounded inventory request with the configured bucket."""
    return ResearchInventoryRequest(
        parsed_input=parsed_input or _parsed("obs://dev-bucket/pasted.tsv"),
        managed_assets=managed_assets,
        configured_bucket="dev-bucket",
        max_managed_references=max_managed_references,
        max_pasted_references=max_pasted_references,
        max_combined_references=max_combined_references,
    )


def _managed_resolver(
    snapshots: tuple[ManagedResearchAssetSnapshot, ...],
) -> Callable[[tuple[str, ...]], tuple[ManagedResearchAssetSnapshot, ...]]:
    """Bind one deterministic owner-qualified managed resolver fake."""
    expected_ids = tuple(snapshot.asset_id for snapshot in snapshots)

    def resolve(
        asset_ids: tuple[str, ...],
    ) -> tuple[ManagedResearchAssetSnapshot, ...]:
        """Return only the trusted current snapshots for expected IDs."""
        assert asset_ids == expected_ids
        return snapshots

    return resolve


async def test_inventory_is_managed_first_and_pasted_source_ordered() -> None:
    """Inventory IDs, lane ordinals, metadata, and digest are deterministic."""
    parsed = parse_research_input(
        "question\nobs://dev-bucket/pasted-a.tsv\tfirst\n"
        "obs://dev-bucket/pasted-b.fastq.gz\tsecond",
        "dev-bucket",
    )
    request = _request(
        parsed_input=parsed,
        managed_assets=(
            _managed("file_b", "obs://dev-bucket/managed-b.tsv"),
            _managed("file_a", "obs://dev-bucket/managed-a.fastq.gz"),
        ),
    )
    port = RecordingResearchObjectPort()

    inventory = await build_research_inventory(request, port)
    repeated = await build_research_inventory(
        request, RecordingResearchObjectPort()
    )

    assert [entry.dataset_id for entry in inventory.entries] == [
        "dataset_001",
        "dataset_002",
        "dataset_003",
        "dataset_004",
    ]
    assert [
        (entry.lane, entry.lane_ordinal) for entry in inventory.entries
    ] == [
        ("managed", 0),
        ("managed", 1),
        ("pasted", 0),
        ("pasted", 1),
    ]
    assert [entry.exact_reference for entry in inventory.entries] == [
        "obs://dev-bucket/managed-b.tsv",
        "obs://dev-bucket/managed-a.fastq.gz",
        "obs://dev-bucket/pasted-a.tsv",
        "obs://dev-bucket/pasted-b.fastq.gz",
    ]
    assert [entry.safe_basename for entry in inventory.entries] == [
        "managed-b.tsv",
        "managed-a.fastq.gz",
        "pasted-a.tsv",
        "pasted-b.fastq.gz",
    ]
    assert [entry.compound_suffix for entry in inventory.entries] == [
        ".tsv",
        ".fastq.gz",
        ".tsv",
        ".fastq.gz",
    ]
    assert [entry.media_hint for entry in inventory.entries] == [
        "application/octet-stream",
        "application/gzip",
        "application/octet-stream",
        "application/gzip",
    ]
    assert inventory.datasets == inventory.entries
    assert not inventory.documents
    assert inventory.digest == repeated.digest
    assert len(port.resolve_calls) == 1


async def test_cross_lane_duplicate_fails_before_object_resolution() -> None:
    """A managed/pasted duplicate fails before metadata or provider calls."""
    port = RecordingResearchObjectPort()
    request = _request(
        parsed_input=_parsed("obs://dev-bucket/a.tsv"),
        managed_assets=(_managed("file_duplicate", "obs://DEV-BUCKET/a.tsv"),),
    )

    with pytest.raises(ResearchInputFailure) as caught:
        await build_research_inventory(request, port)

    assert caught.value.code == "research_dataset_duplicate"
    assert not port.resolve_calls


@pytest.mark.parametrize(
    (
        "managed_count",
        "pasted_count",
        "pasted_limit",
        "combined_limit",
        "error_code",
    ),
    (
        (64, 0, 128, 256, None),
        (65, 0, 128, 256, "research_input_limit_exceeded"),
        (0, 128, 128, 256, None),
        (0, 129, 128, 256, "research_input_limit_exceeded"),
        (64, 192, 256, 256, None),
        (64, 193, 256, 256, "research_input_limit_exceeded"),
        (64, 193, 256, 257, "research_input_limit_exceeded"),
    ),
)
async def test_inventory_enforces_lane_and_structural_count_bounds(
    managed_count: int,
    pasted_count: int,
    pasted_limit: int,
    combined_limit: int,
    error_code: str | None,
) -> None:
    """Per-lane, configured combined, and structural maxima fail before I/O."""
    request = _request(
        parsed_input=_parsed(
            *(
                f"obs://dev-bucket/pasted-{index}.tsv"
                for index in range(pasted_count)
            )
        ),
        managed_assets=tuple(
            _managed(
                f"file_{index:03d}",
                f"obs://dev-bucket/managed-{index}.tsv",
            )
            for index in range(managed_count)
        ),
        max_pasted_references=pasted_limit,
        max_combined_references=combined_limit,
    )
    port = RecordingResearchObjectPort()

    if error_code is None:
        inventory = await build_research_inventory(request, port)
        assert len(inventory.entries) == managed_count + pasted_count
        assert len(port.resolve_calls) == 1
    else:
        with pytest.raises(ResearchInputFailure) as caught:
            await build_research_inventory(request, port)
        assert caught.value.code == error_code
        assert not port.resolve_calls


async def test_inventory_accepts_unrecognized_managed_dataset_format() -> None:
    """Trusted server classification opens the native path for any format."""
    port = RecordingResearchObjectPort()
    managed = replace(
        _managed("file_bad", "obs://dev-bucket/managed-object"),
        snapshot_digest="managed-bad",
    )

    inventory = await build_research_inventory(
        _request(parsed_input=_parsed(), managed_assets=(managed,)),
        port,
    )

    assert len(inventory.entries) == 1
    assert inventory.entries[0].exact_reference == (
        "obs://dev-bucket/managed-object"
    )
    assert not inventory.entries[0].compound_suffix
    assert len(port.resolve_calls) == 1


@pytest.mark.parametrize("purpose", ("document", "dataset"))
async def test_inventory_rejects_zero_byte_managed_assets(
    purpose: Literal["dataset", "document"],
) -> None:
    """Zero-byte managed assets are placeholders, not inputs."""
    port = RecordingResearchObjectPort()
    empty = replace(
        _managed(
            "file_empty",
            "obs://dev-bucket/managed.tsv",
            purpose=purpose,
        ),
        size_bytes=0,
    )

    with pytest.raises(ResearchInputFailure) as caught:
        await build_research_inventory(
            _request(parsed_input=_parsed(), managed_assets=(empty,)),
            port,
        )

    assert caught.value.code == "research_input_resolution_failed"
    assert not port.resolve_calls


async def test_revalidation_requires_managed_resolver() -> None:
    """Managed assets never revalidate from their initial request snapshot."""
    managed = _managed("file_bound", "obs://dev-bucket/managed.tsv")
    request = _request(parsed_input=_parsed(), managed_assets=(managed,))
    port = RecordingResearchObjectPort()
    inventory = await build_research_inventory(request, port)

    with pytest.raises(ResearchInputFailure) as caught:
        await revalidate_research_inventory(request, inventory, port)

    assert caught.value.code == "research_input_resolution_failed"


async def test_revalidation_rejects_current_managed_asset_drift() -> None:
    """Revalidation fails for immutable current managed state drift."""
    managed = _managed("file_bound", "obs://dev-bucket/managed.tsv")
    request = _request(parsed_input=_parsed(), managed_assets=(managed,))
    port = RecordingResearchObjectPort()
    inventory = await build_research_inventory(request, port)
    changed_snapshots = (
        (replace(managed, completed=False),),
        (replace(managed, purpose="document"),),
        (replace(managed, exact_reference="obs://dev-bucket/other.tsv"),),
        (replace(managed, size_bytes=18),),
        (replace(managed, state_version=2),),
        (replace(managed, completed_at="2026-08-09T00:00:00+00:00"),),
        (replace(managed, etag="changed-etag"),),
    )

    for refreshed_assets in changed_snapshots:
        with pytest.raises(ResearchInputFailure) as caught:
            await revalidate_research_inventory(
                request,
                inventory,
                port,
                managed_asset_resolver=_managed_resolver(refreshed_assets),
            )
        assert caught.value.code == "research_input_resolution_failed"


async def test_revalidation_rejects_missing_current_managed_asset() -> None:
    """Reclaimed and foreign owner resolution never reuse request snapshots."""
    managed = _managed("file_bound", "obs://dev-bucket/managed.tsv")
    request = _request(parsed_input=_parsed(), managed_assets=(managed,))
    port = RecordingResearchObjectPort()
    inventory = await build_research_inventory(request, port)

    with pytest.raises(ResearchInputFailure) as caught:
        await revalidate_research_inventory(
            request,
            inventory,
            port,
            managed_asset_resolver=_managed_resolver(()),
        )

    assert caught.value.code == "research_input_resolution_failed"


async def test_revalidation_rejects_current_object_metadata_drift() -> None:
    """Exact-key object metadata is re-resolved with managed snapshots."""
    managed = _managed("file_bound", "obs://dev-bucket/managed.tsv")
    request = _request(parsed_input=_parsed(), managed_assets=(managed,))
    port = RecordingResearchObjectPort()
    inventory = await build_research_inventory(request, port)
    port.metadata_etag = "changed-object-etag"

    with pytest.raises(ResearchInputFailure) as caught:
        await revalidate_research_inventory(
            request,
            inventory,
            port,
            managed_asset_resolver=_managed_resolver((managed,)),
        )

    assert caught.value.code == "research_input_resolution_failed"


async def test_revalidation_verifies_without_minting_a_second_authority() -> (
    None
):
    """Final snapshot fencing rotates the existing provisional authority."""
    request = _request(parsed_input=_parsed("obs://dev-bucket/pasted.tsv"))
    port = RecordingResearchObjectPort()
    inventory = await build_research_inventory(request, port)
    port.rotated_authority_id = "authority-rotated"

    refreshed = await revalidate_research_inventory(request, inventory, port)

    assert len(port.resolve_calls) == 1
    assert len(port.verify_calls) == 1
    assert port.verify_calls[0].authorities == inventory.authorities
    assert refreshed.authorities[0].authority_id == "authority-rotated"
    assert refreshed.entries[0].authority_id == "authority-rotated"
    assert not port.revoke_calls


@pytest.mark.parametrize(
    "invalid_kind",
    ("empty_authority", "crossed_authority", "crossed_snapshot"),
)
async def test_inventory_rejects_malformed_metadata_authorities(
    invalid_kind: str,
) -> None:
    """Authorities bind a nonempty ID to one requested candidate snapshot."""
    port = RecordingResearchObjectPort()
    request = _request(parsed_input=_parsed("obs://dev-bucket/pasted.tsv"))
    authority_dataset_id = (
        "dataset_999" if invalid_kind == "crossed_authority" else "dataset_001"
    )
    snapshot_dataset_id = (
        "dataset_999" if invalid_kind != "empty_authority" else "dataset_001"
    )
    port.authority_override = (
        ResearchObjectAuthority(
            dataset_id=authority_dataset_id,
            authority_id="" if invalid_kind == "empty_authority" else "auth-1",
            snapshot=ResearchObjectSnapshot(
                dataset_id=snapshot_dataset_id,
                size_bytes=17,
                etag="etag",
                version_id=None,
                last_modified="2026-08-08T00:00:00+00:00",
                placeholder=False,
                snapshot_digest="bad-snapshot",
            ),
        ),
    )

    with pytest.raises(ResearchInputFailure) as caught:
        await build_research_inventory(request, port)

    assert caught.value.code == "research_input_resolution_failed"
