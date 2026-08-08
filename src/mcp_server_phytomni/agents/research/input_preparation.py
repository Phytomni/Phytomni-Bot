# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Join trusted Research inventory entries with grounded descriptions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from ...storage.research_objects import ResearchObjectAuthority
from .description_resolver import (
    ResearchResolutionResponse,
    ResolvedResearchDataset,
)
from .input_contracts import ResearchInputFailure, research_input_failure
from .input_inventory import ResearchInputInventory
from .resolver_policy import canonical_json_bytes

__all__ = [
    "PREPARATION_SCHEMA_VERSION",
    "PreparedResearchAuthority",
    "PreparedResearchInput",
    "join_prepared_research_input",
    "with_execution_fingerprint",
]

PREPARATION_SCHEMA_VERSION = 1
_SAFE_MESSAGE = "Research input resolution failed."


@dataclass(frozen=True, slots=True)
class PreparedResearchAuthority:
    """Persistable exact-reference binding for one opaque authority."""

    dataset_id: str
    exact_reference: str
    compound_suffix: str
    authority: ResearchObjectAuthority


@dataclass(frozen=True, slots=True)
class _PreparedResearchIdentity:
    """Stable native input identity independent of private grant rotation."""

    effective_query: str
    obs_file_list: tuple[str, ...]
    data_list: MappingProxyType[str, str]
    inventory_digest: str
    evidence_digest: str
    execution_fingerprint: str
    authority_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedResearchInput(_PreparedResearchIdentity):
    """Final immutable native Research input after the opaque-ID join."""

    authorities: tuple[PreparedResearchAuthority, ...] = ()


def join_prepared_research_input(
    inventory: ResearchInputInventory,
    resolution: ResearchResolutionResponse,
) -> PreparedResearchInput:
    """Join grounded descriptions to frozen trusted references by dataset ID.

    The resolver controls descriptions only.  Every native path is copied from
    the immutable inventory, so an observation can never add, remove, or
    rewrite a storage coordinate.
    """
    _validate_inventory(inventory)
    if not isinstance(resolution, ResearchResolutionResponse):
        raise _failure()
    resolved = _resolution_by_id(resolution.datasets)
    expected = tuple(entry.dataset_id for entry in inventory.datasets)
    if set(resolved) != set(expected) or len(resolved) != len(expected):
        raise _failure()
    data: dict[str, str] = {}
    for entry in inventory.datasets:
        item = resolved.get(entry.dataset_id)
        if item is None or not _valid_description(item.description):
            raise _failure()
        data[entry.exact_reference] = item.description.strip()
    documents = tuple(
        entry.exact_reference
        for entry in inventory.documents
        if entry.purpose == "document" and entry.lane == "managed"
    )
    if len(documents) != len(inventory.documents):
        raise _failure()
    authority_ids = tuple(
        entry.authority_id for entry in inventory.entries if entry.authority_id
    )
    authorities = _prepared_authorities(inventory)
    query = resolution.effective_query
    fingerprint = _join_fingerprint(inventory, resolution, query)
    return PreparedResearchInput(
        effective_query=query,
        obs_file_list=documents,
        data_list=MappingProxyType(data),
        inventory_digest=inventory.digest,
        evidence_digest="",
        execution_fingerprint=fingerprint,
        authority_ids=authority_ids,
        authorities=authorities,
    )


def with_execution_fingerprint(
    prepared: PreparedResearchInput,
    *,
    effective_query: str | None = None,
    evidence_digest: str = "",
    work_digest: str = "",
    policy_fingerprint: str = "",
) -> PreparedResearchInput:
    """Bind evidence, policy, and work-plan identity to preparation."""
    if not isinstance(prepared, PreparedResearchInput):
        raise _failure()
    query = (
        prepared.effective_query
        if effective_query is None
        else effective_query
    )
    if not isinstance(query, str):
        raise _failure()
    value = {
        "evidence_digest": evidence_digest,
        "inventory_digest": prepared.inventory_digest,
        "policy_fingerprint": policy_fingerprint,
        "prepared": _prepared_identity(prepared, query),
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "work_digest": work_digest,
    }
    return replace(
        prepared,
        effective_query=query,
        evidence_digest=evidence_digest,
        execution_fingerprint=_digest(value),
    )


def _validate_inventory(inventory: ResearchInputInventory) -> None:
    """Reject forged or internally inconsistent inventory partitions."""
    if not isinstance(inventory, ResearchInputInventory):
        raise _failure()
    if len({entry.dataset_id for entry in inventory.entries}) != len(
        inventory.entries
    ):
        raise _failure()
    if len({entry.exact_reference for entry in inventory.entries}) != len(
        inventory.entries
    ):
        raise _failure()
    if (
        tuple(
            entry for entry in inventory.entries if entry.purpose == "dataset"
        )
        != inventory.datasets
    ):
        raise _failure()
    if (
        tuple(
            entry for entry in inventory.entries if entry.purpose == "document"
        )
        != inventory.documents
    ):
        raise _failure()
    if any(
        not entry.exact_reference.strip() or not entry.dataset_id.strip()
        for entry in inventory.entries
    ):
        raise _failure()


def _resolution_by_id(
    datasets: list[ResolvedResearchDataset],
) -> dict[str, ResolvedResearchDataset]:
    """Index strict resolver output and reject duplicate/empty IDs."""
    result: dict[str, ResolvedResearchDataset] = {}
    for item in datasets:
        if not isinstance(item, ResolvedResearchDataset):
            raise _failure()
        if item.id in result or not item.id.strip():
            raise _failure()
        result[item.id] = item
    return result


def _valid_description(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _prepared_identity(
    prepared: PreparedResearchInput, effective_query: str
) -> dict[str, Any]:
    return {
        "authorities": tuple(
            (
                item.dataset_id,
                item.exact_reference,
                item.compound_suffix,
                item.authority.snapshot.snapshot_digest,
            )
            for item in prepared.authorities
        ),
        "data_list": tuple(prepared.data_list.items()),
        "effective_query_digest": _digest(effective_query),
        "inventory_digest": prepared.inventory_digest,
        "obs_file_list": prepared.obs_file_list,
    }


def _join_fingerprint(
    inventory: ResearchInputInventory,
    resolution: ResearchResolutionResponse,
    effective_query: str,
) -> str:
    resolved = {item.id: item for item in resolution.datasets}
    return _digest(
        {
            "inventory": _inventory_identity(inventory),
            "query_digest": _digest(effective_query),
            "resolution": [
                resolved[entry.dataset_id].model_dump(mode="json")
                for entry in inventory.datasets
            ],
            "schema_version": PREPARATION_SCHEMA_VERSION,
        }
    )


def _inventory_identity(
    inventory: ResearchInputInventory,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "dataset_id": entry.dataset_id,
            "lane": entry.lane,
            "purpose": entry.purpose,
            "reference_digest": _digest(entry.exact_reference),
            "snapshot_digest": entry.snapshot.snapshot_digest,
            "size_bytes": entry.size_bytes,
        }
        for entry in inventory.entries
    )


def _prepared_authorities(
    inventory: ResearchInputInventory,
) -> tuple[PreparedResearchAuthority, ...]:
    """Join durable exact references to their private authority snapshots."""
    by_dataset = {
        authority.dataset_id: authority for authority in inventory.authorities
    }
    if inventory.authorities and set(by_dataset) != {
        entry.dataset_id for entry in inventory.datasets
    }:
        raise _failure()
    return tuple(
        PreparedResearchAuthority(
            dataset_id=entry.dataset_id,
            exact_reference=entry.exact_reference,
            compound_suffix=entry.compound_suffix,
            authority=by_dataset[entry.dataset_id],
        )
        for entry in inventory.datasets
        if entry.dataset_id in by_dataset
    )


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _failure() -> ResearchInputFailure:
    return research_input_failure(
        "research_input_resolution_failed",
        _SAFE_MESSAGE,
        http_status_hint=422,
        retryable=False,
    )
