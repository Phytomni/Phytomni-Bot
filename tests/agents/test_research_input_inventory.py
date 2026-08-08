# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for deterministic immutable Research input inventories."""

from __future__ import annotations

from dataclasses import replace

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

    async def resolve(
        self, request: ResearchObjectResolveRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return one immutable non-placeholder authority per candidate."""
        self.resolve_calls.append(request)
        return tuple(
            ResearchObjectAuthority(
                dataset_id=candidate.dataset_id,
                authority_id=f"authority-{candidate.dataset_id}",
                snapshot=ResearchObjectSnapshot(
                    dataset_id=candidate.dataset_id,
                    size_bytes=17,
                    etag=f"etag-{index}",
                    version_id=None,
                    last_modified="2026-08-08T00:00:00+00:00",
                    placeholder=False,
                    snapshot_digest=f"snapshot-{index}",
                ),
            )
            for index, candidate in enumerate(request.objects, start=1)
        )

    async def verify(
        self, request: ResearchObjectVerifyRequest
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Return supplied authorities for this resolve-only inventory fake."""
        return request.authorities

    async def revoke(self, request: ResearchObjectRevokeRequest) -> None:
        """Accept no-op revocation for this stateless fake."""


def _managed(
    asset_id: str,
    reference: str,
    *,
    purpose: str = "dataset",
) -> ManagedResearchAssetSnapshot:
    """Build one valid server-owned managed Research asset snapshot."""
    return ManagedResearchAssetSnapshot(
        asset_id=asset_id,
        exact_reference=reference,
        size_bytes=17,
        purpose=purpose,  # type: ignore[arg-type]
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


async def test_inventory_rejects_invalid_metadata_and_format() -> None:
    """Placeholders and format-invalid managed snapshots fail closed."""
    port = RecordingResearchObjectPort()
    unsupported = replace(
        _managed("file_bad", "obs://dev-bucket/managed.exe"),
        snapshot_digest="managed-bad",
    )

    with pytest.raises(ResearchInputFailure) as caught:
        await build_research_inventory(
            _request(parsed_input=_parsed(), managed_assets=(unsupported,)),
            port,
        )

    assert caught.value.code == "research_dataset_format_unsupported"
    assert not port.resolve_calls
