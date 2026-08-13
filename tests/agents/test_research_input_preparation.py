# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the final opaque-id Research input join."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research import (
    input_coordinator,
    input_preparation,
)
from mcp_server_phytomni.agents.research.description_resolver import (
    ResearchResolutionResponse,
    ResolvedResearchDataset,
)
from mcp_server_phytomni.agents.research.document_evidence import (
    ConvertedResearchSection,
    ManagedDocumentObservation,
    ResearchEvidenceRequest,
    extract_research_evidence,
)
from mcp_server_phytomni.agents.research.input_contracts import (
    ParsedResearchInput,
    PastedDatasetCandidate,
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
    SourceSpan,
)
from mcp_server_phytomni.agents.research.input_coordinator import (
    ResearchInputCoordinator,
    build_sqlite_resume_loader,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
    ResearchInputInventory,
    ResearchInventoryEntry,
    ResearchInventoryRequest,
    build_research_inventory,
    research_inventory_partitions,
    revalidate_research_inventory,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchAuthority,
    PreparedResearchInput,
    join_prepared_research_input,
    prepare_research_input_for_remote_inspection,
    with_execution_fingerprint,
    with_revalidated_authorities,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectSnapshot,
)
from tests.support.research_fakes import research_inventory_entry

pytestmark = pytest.mark.agent


def _entry(
    dataset_id: str,
    ordinal: int,
    *,
    purpose: str = "dataset",
    lane: str = "pasted",
) -> ResearchInventoryEntry:
    """Build a trusted inventory entry with opaque identity."""
    return research_inventory_entry(
        dataset_id=dataset_id,
        lane=lane,
        lane_ordinal=ordinal,
        exact_reference=f"obs://trusted/{dataset_id}.tsv",
        comparison_digest=f"comparison-{dataset_id}",
        safe_basename=f"{dataset_id}.tsv",
        compound_suffix=".tsv",
        size_bytes=10,
        media_hint="text/tab-separated-values",
        purpose=purpose,
        user_hint=None,
        source_span=None,
        state_version=1 if lane == "managed" else None,
        completed_at=(
            "2026-08-08T00:00:00+00:00" if lane == "managed" else None
        ),
        etag="etag" if lane == "managed" else None,
        version_id="v1" if lane == "managed" else None,
        authority_id=f"authority-{dataset_id}" if lane == "pasted" else None,
    )


def _inventory(*entries: ResearchInventoryEntry) -> ResearchInputInventory:
    """Build an ordered immutable inventory."""
    return research_inventory_partitions(entries, digest="inventory-digest")


def _resolution(*items: tuple[str, str]) -> ResearchResolutionResponse:
    """Build a strict resolver result with opaque IDs only."""
    response = ResearchResolutionResponse(
        effective_query="find differential expression",
        datasets=[
            ResolvedResearchDataset(
                id=dataset_id,
                description=description,
                confidence="high",
                evidence_ids=[f"evidence-{dataset_id}"],
            )
            for dataset_id, description in items
        ],
    )
    return response


def test_join_keeps_inventory_order_and_frozen_trusted_references() -> None:
    """Descriptions join by ID while references come only from inventory."""
    document = _entry("document_001", 0, purpose="document", lane="managed")
    first = _entry("dataset_002", 1)
    second = _entry("dataset_003", 2)

    prepared = join_prepared_research_input(
        _inventory(document, first, second),
        _resolution(
            ("dataset_002", "treated RNA-seq"),
            ("dataset_003", "control RNA-seq"),
        ),
    )

    assert isinstance(prepared, PreparedResearchInput)
    assert prepared.effective_query == "find differential expression"
    assert prepared.obs_file_list == (document.exact_reference,)
    assert tuple(prepared.data_list) == (
        first.exact_reference,
        second.exact_reference,
    )
    assert tuple(prepared.data_list.values()) == (
        "treated RNA-seq",
        "control RNA-seq",
    )
    assert prepared.authority_ids == (first.authority_id, second.authority_id)
    assert prepared.execution_fingerprint
    with pytest.raises(TypeError):
        cast(Any, prepared.data_list)["obs://model-added/path"] = "unsafe"


def test_remote_inspection_keeps_empty_dataset_hints_and_trusted_paths() -> (
    None
):
    """A file-only request leaves descriptions for the remote model."""
    document = _entry("document_001", 0, purpose="document", lane="managed")
    dataset = _entry("dataset_002", 1)

    prepared = prepare_research_input_for_remote_inspection(
        _inventory(document, dataset),
        effective_query="",
    )

    assert prepared.effective_query == ""
    assert prepared.obs_file_list == (document.exact_reference,)
    assert dict(prepared.data_list) == {dataset.exact_reference: ""}
    assert prepared.execution_fingerprint


def test_join_carries_exact_private_authority_binding() -> None:
    """The prepared object retains the authority needed for restart verify."""
    entry = _entry("dataset_002", 0)
    authority = ResearchObjectAuthority(
        dataset_id=entry.dataset_id,
        authority_id="grant-dataset-002",
        snapshot=ResearchObjectSnapshot(
            dataset_id=entry.dataset_id,
            size_bytes=10,
            etag="etag-1",
            version_id="version-1",
            last_modified="2026-08-08T00:00:00+00:00",
            placeholder=False,
            snapshot_digest="snapshot-dataset-002",
        ),
    )
    entry = replace(entry, authority_id=authority.authority_id)
    inventory = ResearchInputInventory(
        entries=(entry,),
        documents=(),
        datasets=(entry,),
        digest="inventory-digest",
        authorities=(authority,),
    )

    prepared = join_prepared_research_input(
        inventory, _resolution((entry.dataset_id, "grounded description"))
    )

    assert prepared.authorities == (
        PreparedResearchAuthority(
            dataset_id=entry.dataset_id,
            exact_reference=entry.exact_reference,
            compound_suffix=entry.compound_suffix,
            authority=authority,
        ),
    )


def test_revalidation_rotates_prepared_authority_without_identity_drift() -> (
    None
):
    """A verified provisional rotation reaches durable preparation."""
    entry = _entry("dataset_002", 0)
    snapshot = ResearchObjectSnapshot(
        dataset_id=entry.dataset_id,
        size_bytes=10,
        etag="etag-1",
        version_id="version-1",
        last_modified="2026-08-08T00:00:00+00:00",
        placeholder=False,
        snapshot_digest="snapshot-dataset-002",
    )
    initial_authority = ResearchObjectAuthority(
        entry.dataset_id,
        "grant-initial",
        snapshot,
    )
    initial_entry = replace(entry, authority_id=initial_authority.authority_id)
    inventory = ResearchInputInventory(
        entries=(initial_entry,),
        documents=(),
        datasets=(initial_entry,),
        digest="inventory-digest",
        authorities=(initial_authority,),
    )
    prepared = join_prepared_research_input(
        inventory,
        _resolution((entry.dataset_id, "grounded description")),
    )
    rotated_authority = replace(
        initial_authority,
        authority_id="grant-rotated",
    )
    rotated_entry = replace(
        initial_entry,
        authority_id=rotated_authority.authority_id,
    )
    refreshed = replace(
        inventory,
        entries=(rotated_entry,),
        datasets=(rotated_entry,),
        authorities=(rotated_authority,),
    )

    rotated = with_revalidated_authorities(prepared, refreshed)

    assert rotated.execution_fingerprint == prepared.execution_fingerprint
    assert rotated.authority_ids == ("grant-rotated",)
    assert rotated.authorities[0].authority == rotated_authority


@pytest.mark.parametrize(
    "items",
    [
        (("dataset_002", "one"), ("dataset_002", "duplicate")),
        (("dataset_999", "model-added"), ("dataset_003", "missing")),
        (("dataset_002", "only"),),
    ],
)
def test_join_rejects_model_added_missing_or_duplicate_ids(
    items: tuple[tuple[str, str], ...],
) -> None:
    """The final join fails closed when resolver IDs do not match inventory."""
    inventory = _inventory(_entry("dataset_002", 0), _entry("dataset_003", 1))
    with pytest.raises(Exception) as caught:
        join_prepared_research_input(inventory, _resolution(*items))
    assert getattr(caught.value, "code") == "research_input_resolution_failed"
    assert "obs://" not in str(caught.value)


def test_join_does_not_use_internal_evidence_ids_as_native_keys() -> None:
    """Evidence IDs remain resolver-internal and never become data paths."""
    inventory = _inventory(_entry("dataset_002", 0))
    resolution = _resolution(("dataset_002", "grounded description"))
    resolution = resolution.model_copy(
        update={
            "datasets": [
                resolution.datasets[0].model_copy(
                    update={"evidence_ids": ["internal-evidence-001"]}
                )
            ]
        }
    )
    prepared = join_prepared_research_input(inventory, resolution)
    assert "internal-evidence-001" not in prepared.data_list
    assert "internal-evidence-001" not in prepared.data_list.values()


def test_execution_fingerprint_changes_for_every_reuse_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventory, policy, evidence, work, query, and schema all bind reuse."""
    entry = _entry("dataset_002", 0)
    inventory = _inventory(entry)
    resolution = _resolution(("dataset_002", "grounded description"))
    prepared = join_prepared_research_input(inventory, resolution)
    bound = with_execution_fingerprint(
        prepared,
        effective_query="find differential expression",
        evidence_digest="evidence-1",
        work_digest="work-1",
        policy_fingerprint="policy-1",
    )

    changed_snapshot = replace(
        entry,
        snapshot=replace(entry.snapshot, snapshot_digest="snapshot-changed"),
    )
    changed_inventory = join_prepared_research_input(
        _inventory(changed_snapshot), resolution
    )
    assert (
        changed_inventory.execution_fingerprint
        != prepared.execution_fingerprint
    )

    variants = (
        with_execution_fingerprint(
            prepared,
            effective_query="a different query",
            evidence_digest="evidence-1",
            work_digest="work-1",
            policy_fingerprint="policy-1",
        ),
        with_execution_fingerprint(
            prepared,
            effective_query="find differential expression",
            evidence_digest="evidence-2",
            work_digest="work-1",
            policy_fingerprint="policy-1",
        ),
        with_execution_fingerprint(
            prepared,
            effective_query="find differential expression",
            evidence_digest="evidence-1",
            work_digest="work-2",
            policy_fingerprint="policy-1",
        ),
        with_execution_fingerprint(
            prepared,
            effective_query="find differential expression",
            evidence_digest="evidence-1",
            work_digest="work-1",
            policy_fingerprint="policy-2",
        ),
    )
    assert all(
        variant.execution_fingerprint != bound.execution_fingerprint
        for variant in variants
    )

    monkeypatch.setattr(
        input_preparation,
        "PREPARATION_SCHEMA_VERSION",
        input_preparation.PREPARATION_SCHEMA_VERSION + 1,
    )
    assert (
        with_execution_fingerprint(
            prepared,
            effective_query="find differential expression",
            evidence_digest="evidence-1",
            work_digest="work-1",
            policy_fingerprint="policy-1",
        ).execution_fingerprint
        != bound.execution_fingerprint
    )


def test_preparation_rejects_invalid_resolution_and_document_lane() -> None:
    """Reject non-resolver values and unmanaged document partitions."""
    inventory = _inventory(_entry("dataset_002", 0))
    with pytest.raises(Exception) as caught:
        join_prepared_research_input(inventory, cast(Any, object()))
    assert getattr(caught.value, "code") == "research_input_resolution_failed"

    unmanaged = _entry("document_001", 0, purpose="document", lane="pasted")
    with pytest.raises(Exception) as caught:
        join_prepared_research_input(
            _inventory(unmanaged, _entry("dataset_002", 1)),
            _resolution(("dataset_002", "dataset description")),
        )
    assert getattr(caught.value, "code") == "research_input_resolution_failed"


def test_preparation_rejects_empty_description_and_remote_query_type() -> None:
    """Reject empty resolver descriptions and non-string remote queries."""
    with pytest.raises(Exception) as caught:
        join_prepared_research_input(
            _inventory(_entry("dataset_002", 0)),
            _resolution(("dataset_002", "   ")),
        )
    assert getattr(caught.value, "code") == "research_input_resolution_failed"

    with pytest.raises(Exception) as caught:
        prepare_research_input_for_remote_inspection(
            _inventory(_entry("dataset_002", 0)),
            effective_query=cast(Any, None),
        )
    assert getattr(caught.value, "code") == "research_input_resolution_failed"

    unmanaged = _entry("document_001", 0, purpose="document", lane="pasted")
    with pytest.raises(Exception) as caught:
        prepare_research_input_for_remote_inspection(
            _inventory(unmanaged, _entry("dataset_002", 1))
        )
    assert getattr(caught.value, "code") == "research_input_resolution_failed"


def test_fingerprint_rejects_wrong_prepared_value_and_query_type() -> None:
    """Fingerprint binding accepts only the immutable preparation contract."""
    with pytest.raises(Exception) as caught:
        with_execution_fingerprint(cast(Any, object()))
    assert getattr(caught.value, "code") == "research_input_resolution_failed"

    prepared = prepare_research_input_for_remote_inspection(
        _inventory(_entry("dataset_002", 0))
    )
    with pytest.raises(Exception) as caught:
        with_execution_fingerprint(prepared, effective_query=cast(Any, 1))
    assert getattr(caught.value, "code") == "research_input_resolution_failed"


@pytest.mark.parametrize(
    "kind",
    [
        "not_inventory",
        "duplicate_id",
        "duplicate_reference",
        "datasets",
        "documents",
        "blank_reference",
        "blank_dataset_id",
    ],
)
def test_inventory_validation_rejects_each_partition_corruption(
    kind: str,
) -> None:
    """Every inventory identity and partition invariant fails closed."""
    document = _entry("document_001", 0, purpose="document", lane="managed")
    dataset = _entry("dataset_002", 1)
    inventory = _inventory(document, dataset)
    if kind == "not_inventory":
        value: Any = object()
    elif kind == "duplicate_id":
        value = replace(
            inventory, entries=(dataset, replace(dataset, lane_ordinal=2))
        )
    elif kind == "duplicate_reference":
        other = replace(dataset, dataset_id="dataset_003")
        value = replace(
            inventory,
            entries=(
                document,
                replace(other, exact_reference=dataset.exact_reference),
            ),
        )
    elif kind == "datasets":
        value = replace(inventory, datasets=())
    elif kind == "documents":
        value = replace(inventory, documents=())
    elif kind == "blank_reference":
        value = replace(
            inventory,
            entries=(replace(document, exact_reference="   "), dataset),
        )
    else:
        value = replace(
            inventory, entries=(replace(document, dataset_id=""), dataset)
        )

    validator = getattr(input_preparation, "_validate_inventory")
    with pytest.raises(Exception) as caught:
        validator(value)
    assert getattr(caught.value, "code") == "research_input_resolution_failed"


def test_preparation_rejects_bad_resolution_items_and_authority() -> None:
    """Reject malformed resolver rows and authorities from another dataset."""
    index = getattr(input_preparation, "_resolution_by_id")
    with pytest.raises(Exception) as caught:
        index([cast(Any, object())])
    assert getattr(caught.value, "code") == "research_input_resolution_failed"

    with pytest.raises(Exception) as caught:
        index(
            [
                ResolvedResearchDataset.model_construct(
                    id=" ", description="description"
                )
            ]
        )
    assert getattr(caught.value, "code") == "research_input_resolution_failed"

    entry = _entry("dataset_002", 0)
    authority = ResearchObjectAuthority(
        dataset_id="dataset_other",
        authority_id="authority-other",
        snapshot=ResearchObjectSnapshot(
            dataset_id="dataset_other",
            size_bytes=10,
            etag="etag",
            version_id="version",
            last_modified="2026-08-08T00:00:00+00:00",
            placeholder=False,
            snapshot_digest="snapshot-other",
        ),
    )
    inventory = replace(_inventory(entry), authorities=(authority,))
    with pytest.raises(Exception) as caught:
        join_prepared_research_input(
            inventory, _resolution((entry.dataset_id, "description"))
        )
    assert getattr(caught.value, "code") == "research_input_resolution_failed"


_RESTART_SENTINEL = "RESEARCH_RESTART_DOCUMENT_PLAINTEXT_SENTINEL"


def _restart_managed() -> tuple[ManagedResearchAssetSnapshot, ...]:
    """Build two owner-bound managed documents with fixed body sizes."""

    def asset(
        asset_id: str, reference: str, size: int
    ) -> ManagedResearchAssetSnapshot:
        return ManagedResearchAssetSnapshot(
            asset_id=asset_id,
            exact_reference=reference,
            size_bytes=size,
            purpose="document",
            completed=True,
            state_version=1,
            completed_at="2026-08-09T00:00:00+00:00",
            etag=f"{asset_id}-etag",
            version_id=f"{asset_id}-v1",
            last_modified="2026-08-09T00:00:00+00:00",
            snapshot_digest=f"{asset_id}-snapshot",
        )

    return (
        asset("managed-paper", "obs://dev-bucket/paper.pdf", 8),
        asset("managed-table", "obs://dev-bucket/table.xlsx", 7),
    )


def _restart_parsed() -> ParsedResearchInput:
    """Build one effective query and one pasted exact-key candidate."""
    query = "restart query"
    reference = "obs://dev-bucket/pasted.tsv"
    start = len(query) + 1
    return ParsedResearchInput(
        original_query_digest="r" * 64,
        original_query_length=start + len(reference),
        effective_query=query,
        effective_to_original=tuple(range(len(query))),
        removed_spans=(
            SourceSpan(start, start + len(reference), "standalone_tab"),
        ),
        candidates=(
            PastedDatasetCandidate(
                exact_reference=reference,
                comparison_key=reference.casefold(),
                user_hint="pasted hint",
                source_start=start,
                source_end=start + len(reference),
                ordinal=0,
            ),
        ),
    )


def _restart_request(
    parsed: ParsedResearchInput,
    managed: tuple[ManagedResearchAssetSnapshot, ...],
) -> ResearchInventoryRequest:
    """Build the trusted request that is serialized into SQLite metadata."""
    return ResearchInventoryRequest(
        parsed_input=parsed,
        managed_assets=managed,
        configured_bucket="dev-bucket",
        max_managed_references=64,
        max_pasted_references=128,
        max_combined_references=256,
    )


class _RestartHeadPort:
    """Concrete exact-key metadata port used by both inventory passes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.references_by_authority: dict[str, str] = {}

    async def resolve(
        self, request: Any
    ) -> tuple[ResearchObjectAuthority, ...]:
        """Record exact references and return stable current HEAD metadata."""
        self.calls.append(
            tuple(item.exact_reference for item in request.objects)
        )
        authorities = tuple(
            ResearchObjectAuthority(
                item.dataset_id,
                f"grant-{item.dataset_id}",
                ResearchObjectSnapshot(
                    item.dataset_id,
                    17,
                    "pasted-etag",
                    "pasted-v1",
                    "2026-08-09T00:00:00+00:00",
                    False,
                    f"pasted-{item.dataset_id}",
                ),
            )
            for item in request.objects
        )
        self.references_by_authority.update(
            {
                authority.authority_id: candidate.exact_reference
                for candidate, authority in zip(
                    request.objects, authorities, strict=True
                )
            }
        )
        return authorities

    async def verify(self, request: Any) -> tuple[Any, ...]:
        """Record the exact references re-read by authority verification."""
        self.calls.append(
            tuple(
                self.references_by_authority[authority.authority_id]
                for authority in request.authorities
            )
        )
        return request.authorities

    async def revoke(self, request: Any) -> None:
        """Release no local authority after the test."""
        del request


class _RestartManagedResolver:
    """Owner-qualified managed snapshot resolver with an audit ledger."""

    def __init__(
        self, assets: tuple[ManagedResearchAssetSnapshot, ...]
    ) -> None:
        self.assets = assets
        self.calls: list[tuple[str, ...]] = []

    @property
    def call_count(self) -> int:
        """Expose the number of owner-resolution calls."""
        return len(self.calls)

    def __call__(
        self, asset_ids: tuple[str, ...]
    ) -> tuple[ManagedResearchAssetSnapshot, ...]:
        """Return only the current snapshots for the requested owner IDs."""
        self.calls.append(asset_ids)
        expected = tuple(asset.asset_id for asset in self.assets)
        if asset_ids != expected:
            raise AssertionError("managed asset owner resolution drifted")
        return self.assets


class _RestartDocumentPort:
    """Downloader and converter that record every page/section pass."""

    def __init__(self) -> None:
        self.payloads = {"dataset_001": b"paper123", "dataset_002": b"table12"}
        self.pages = {
            "dataset_001": (
                ConvertedResearchSection(0, "page 1", _RESTART_SENTINEL),
                ConvertedResearchSection(1, "page 2", _RESTART_SENTINEL),
            ),
            "dataset_002": (
                ConvertedResearchSection(0, "sheet 1", _RESTART_SENTINEL),
                ConvertedResearchSection(1, "sheet 2", _RESTART_SENTINEL),
            ),
        }
        self.observed: list[str] = []
        self.downloaded: list[str] = []
        self.converted: list[str] = []

    def observe(
        self, entry: ResearchInventoryEntry
    ) -> ManagedDocumentObservation:
        """Verify the current owner snapshot before body download."""
        self.observed.append(entry.dataset_id)
        return ManagedDocumentObservation(
            entry.exact_reference, entry.snapshot
        )

    async def download(self, entry: ResearchInventoryEntry) -> bytes:
        """Return the complete body for one immutable document."""
        self.downloaded.append(entry.dataset_id)
        return self.payloads[entry.dataset_id]

    def convert(
        self, entry: ResearchInventoryEntry, payload: bytes
    ) -> tuple[ConvertedResearchSection, ...]:
        """Re-extract all deterministic pages or sections."""
        self.converted.append(entry.dataset_id)
        assert payload == self.payloads[entry.dataset_id]
        return self.pages[entry.dataset_id]

    def clear(self) -> None:
        """Start a fresh process-pass ledger."""
        self.observed.clear()
        self.downloaded.clear()
        self.converted.clear()


def _restart_resolution(
    request: ResearchCoordinatorRequest,
) -> ResearchResolutionResponse:
    """Produce a strict typed response for the current inventory."""
    inventory = request.inventory_request
    assert isinstance(inventory, ResearchInputInventory)
    return ResearchResolutionResponse(
        effective_query=request.effective_query,
        datasets=[
            ResolvedResearchDataset(
                id=entry.dataset_id,
                description=f"Grounded description for {entry.dataset_id}.",
                confidence="high",
                evidence_ids=[f"{entry.dataset_id}_meta_001"],
            )
            for entry in inventory.datasets
        ],
    )


@dataclass
class _RestartPersistence:
    """Persist only safe evidence metadata through the real SQLite store."""

    store: ResearchInputStore
    parsed: ParsedResearchInput
    managed: tuple[ManagedResearchAssetSnapshot, ...]
    calls: list[dict[str, Any]]

    @property
    def call_count(self) -> int:
        """Expose planning persistence calls."""
        return len(self.calls)

    async def persist(self, run_id: str, **values: Any) -> None:
        """Write a single real resolution row; replay only records the call."""
        self.calls.append(values)
        if len(self.calls) != 1:
            return
        prepared = values["prepared"]
        self.store.persist_resolution(
            run_id,
            original_query_digest=self.parsed.original_query_digest,
            original_query_length=self.parsed.original_query_length,
            effective_query=prepared.effective_query,
            source_map={
                "parsed_input": asdict(self.parsed),
                "evidence_identity": values["evidence"],
            },
            parsed_candidates=[
                asdict(candidate) for candidate in self.parsed.candidates
            ],
            managed_snapshot=[asdict(asset) for asset in self.managed],
            evidence_digest=prepared.evidence_digest,
            work_digest="w" * 64,
            policy_digest="p" * 64,
            execution_fingerprint=prepared.execution_fingerprint,
        )


@dataclass
class _RestartPorts:
    """Concrete inventory, extraction, and owner revalidation ports."""

    request: ResearchInventoryRequest
    head: _RestartHeadPort
    managed: _RestartManagedResolver
    documents: _RestartDocumentPort
    evidence: list[Any]

    async def metadata(self, request: ResearchCoordinatorRequest) -> Any:
        """Resolve current inventory through the exact-key metadata port."""
        return await build_research_inventory(
            request.inventory_request, self.head
        )

    async def extract(self, request: ResearchCoordinatorRequest) -> Any:
        """Run real document download and page/section extraction."""
        result = await extract_research_evidence(
            ResearchEvidenceRequest(
                inventory=request.inventory_request,
                effective_query=request.effective_query,
                effective_to_original=(
                    self.request.parsed_input.effective_to_original
                ),
            ),
            self.documents,
            self.documents,
        )
        self.evidence.append(result)
        return result

    async def resolve(
        self, request: ResearchCoordinatorRequest
    ) -> ResearchResolutionResponse:
        """Resolve current opaque IDs through the typed local resolver."""
        return _restart_resolution(request)

    async def revalidate(
        self, request: ResearchCoordinatorRequest
    ) -> ResearchInputInventory:
        """Rebuild inventory, re-HEAD pasted objects, and re-resolve IDs."""
        return await revalidate_research_inventory(
            self.request,
            request.inventory_request,
            self.head,
            managed_asset_resolver=self.managed,
        )


def _restart_dependencies(
    ports: _RestartPorts, persistence: _RestartPersistence
) -> ResearchCoordinatorDependencies:
    """Bind concrete restart ports to the coordinator contract."""
    return ResearchCoordinatorDependencies(
        build_inventory=ports.metadata,
        extract_evidence=ports.extract,
        resolve_descriptions=ports.resolve,
        revalidate_inventory=ports.revalidate,
        persist_planning=persistence.persist,
    )


def _restart_persisted_request(
    row: dict[str, Any],
    dependencies: ResearchCoordinatorDependencies,
) -> ResearchCoordinatorRequest:
    """Rebuild a request from safe SQLite metadata and no document text."""
    source_map = row["source_map"]
    parsed_data = source_map["parsed_input"]
    parsed = ParsedResearchInput(
        original_query_digest=parsed_data["original_query_digest"],
        original_query_length=parsed_data["original_query_length"],
        effective_query=parsed_data["effective_query"],
        effective_to_original=tuple(parsed_data["effective_to_original"]),
        removed_spans=tuple(
            SourceSpan(**span) for span in parsed_data["removed_spans"]
        ),
        candidates=tuple(
            PastedDatasetCandidate(**candidate)
            for candidate in parsed_data["candidates"]
        ),
    )
    managed = tuple(
        ManagedResearchAssetSnapshot(**asset)
        for asset in row["managed_snapshot"]
    )
    return ResearchCoordinatorRequest(
        run_id=row["run_id"],
        inventory_request=_restart_request(parsed, managed),
        evidence=source_map["evidence_identity"],
        dependencies=dependencies,
        effective_query=row["effective_query"],
        evidence_digest=row["evidence_digest"],
        work_digest=row["work_digest"],
        policy_fingerprint=row["policy_digest"] or "",
    )


def _assert_restart_safe(loaded: dict[str, Any], database: str) -> None:
    """Prove decoded and raw SQLite state contain no document plaintext."""
    serialized = json.dumps(loaded, sort_keys=True)
    assert _RESTART_SENTINEL not in serialized
    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT source_map_json, candidates_json, managed_snapshot_json "
            "FROM research_input_resolutions WHERE run_id = ?",
            ("run-restart",),
        ).fetchone()
    assert raw is not None
    assert _RESTART_SENTINEL not in json.dumps(raw)


@pytest.mark.asyncio
async def test_restart_rebuilds_from_sqlite_without_reusing_document_plaintext(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restart reloads safe metadata and re-runs every owner-bound I/O seam."""
    database = str(tmp_path / "restart.db")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="run-restart",
            user_id="owner",
            agent="research",
            origin="api",
        )
    )
    store = ResearchInputStore(database)
    parsed = _restart_parsed()
    managed = _restart_managed()
    request = _restart_request(parsed, managed)
    ports = _RestartPorts(
        request,
        _RestartHeadPort(),
        _RestartManagedResolver(managed),
        _RestartDocumentPort(),
        [],
    )
    persistence = _RestartPersistence(store, parsed, managed, [])
    dependencies = _restart_dependencies(ports, persistence)
    initial = ResearchCoordinatorRequest(
        run_id="run-restart",
        inventory_request=request,
        dependencies=dependencies,
        effective_query=parsed.effective_query,
    )

    async def recover() -> None:
        """Keep unrelated process recovery outside this boundary test."""

    monkeypatch.setattr(
        input_coordinator, "recover_registered_request", recover
    )
    await ResearchInputCoordinator(initial).run("run-restart", "worker-1")
    ports.managed.calls.clear()
    ports.head.calls.clear()
    ports.documents.clear()
    loaded = store.load_resolution("run-restart")
    assert loaded is not None
    _assert_restart_safe(loaded, database)
    persisted_identity = loaded["source_map_json"]["evidence_identity"]

    def factory(row: Any) -> ResearchCoordinatorRequest:
        """Reject any plaintext if it appears in the store response."""
        serialized = json.dumps(row, sort_keys=True)
        assert _RESTART_SENTINEL not in serialized
        return _restart_persisted_request(row, dependencies)

    await ResearchInputCoordinator().resume_after_restart(
        "run-restart", "worker-2", build_sqlite_resume_loader(store, factory)
    )

    assert len(ports.evidence) == 2
    assert persisted_identity["coverage_digest"] == (
        ports.evidence[1].coverage_digest
    )
    assert json.dumps(persisted_identity, sort_keys=True) == json.dumps(
        ports.evidence[1].persistence_metadata(), sort_keys=True
    )
    assert ports.evidence[0].document_digests == (
        ports.evidence[1].document_digests
    )
    assert ports.documents.observed == ["dataset_001", "dataset_002"]
    assert ports.documents.downloaded == ["dataset_001", "dataset_002"]
    assert ports.documents.converted == ["dataset_001", "dataset_002"]
    assert ports.managed.calls == [("managed-paper", "managed-table")]
    assert ports.head.calls == [
        ("obs://dev-bucket/pasted.tsv",),
        ("obs://dev-bucket/pasted.tsv",),
    ]
    assert persistence.call_count == 2
    assert _RESTART_SENTINEL not in json.dumps(
        persistence.calls[1]["evidence"], sort_keys=True
    )
