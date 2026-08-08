# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the final opaque-id Research input join."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mcp_server_phytomni.agents.research import input_preparation
from mcp_server_phytomni.agents.research.description_resolver import (
    ResearchResolutionResponse,
    ResolvedResearchDataset,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ResearchInputInventory,
    ResearchInputSnapshot,
    ResearchInventoryEntry,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchAuthority,
    PreparedResearchInput,
    join_prepared_research_input,
    with_execution_fingerprint,
)
from mcp_server_phytomni.storage.research_objects import (
    ResearchObjectAuthority,
    ResearchObjectSnapshot,
)

pytestmark = pytest.mark.agent


def _entry(
    dataset_id: str,
    ordinal: int,
    *,
    purpose: str = "dataset",
    lane: str = "pasted",
) -> ResearchInventoryEntry:
    """Build a trusted inventory entry with opaque identity."""
    snapshot = ResearchInputSnapshot(
        lane=lane,  # type: ignore[arg-type]
        size_bytes=10,
        state_version=1 if lane == "managed" else None,
        completed_at=(
            "2026-08-08T00:00:00+00:00" if lane == "managed" else None
        ),
        etag="etag" if lane == "managed" else None,
        version_id="v1" if lane == "managed" else None,
        last_modified=None,
        placeholder=False,
        purpose=purpose,  # type: ignore[arg-type]
        snapshot_digest=f"snapshot-{dataset_id}",
    )
    return ResearchInventoryEntry(
        dataset_id=dataset_id,
        lane=lane,  # type: ignore[arg-type]
        lane_ordinal=ordinal,
        exact_reference=f"obs://trusted/{dataset_id}.tsv",
        comparison_digest=f"comparison-{dataset_id}",
        safe_basename=f"{dataset_id}.tsv",
        compound_suffix=".tsv",
        size_bytes=10,
        media_hint="text/tab-separated-values",
        purpose=purpose,  # type: ignore[arg-type]
        user_hint=None,
        source_span=None,
        snapshot=snapshot,
        authority_id=f"authority-{dataset_id}" if lane == "pasted" else None,
    )


def _inventory(*entries: ResearchInventoryEntry) -> ResearchInputInventory:
    """Build an ordered immutable inventory."""
    return ResearchInputInventory(
        entries=entries,
        documents=tuple(
            entry for entry in entries if entry.purpose == "document"
        ),
        datasets=tuple(
            entry for entry in entries if entry.purpose == "dataset"
        ),
        digest="inventory-digest",
    )


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
        prepared.data_list["obs://model-added/path"] = (  # type: ignore[index]
            "unsafe"
        )


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
