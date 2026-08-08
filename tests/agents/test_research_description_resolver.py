# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for grounded Research observation validation and reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.agents.research.description_resolver import (
    ResearchDescriptionResolver,
    ResearchObservationResponse,
    ResearchResolutionRequest,
    ResearchResolutionResponse,
)
from mcp_server_phytomni.agents.research.document_evidence import (
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ResearchInputInventory,
    ResearchInputSnapshot,
    ResearchInventoryEntry,
)
from mcp_server_phytomni.agents.research.resolver_policy import (
    ResearchResolverObservationUnit,
    ResearchResolverPolicy,
    ResearchResolverWorkPlan,
    canonical_json_bytes,
)

pytestmark = pytest.mark.agent


def _policy(**changes: object) -> ResearchResolverPolicy:
    """Build a small deterministic policy for resolver tests."""
    values: dict[str, object] = {
        "schema_version": 1,
        "model_id": "phyto-research",
        "context_token_limit": 256,
        "output_token_reserve": 16,
        "prompt_token_overhead": 8,
        "schema_token_overhead": 8,
        "safety_margin_tokens": 8,
        "max_serialized_request_bytes": 512,
        "max_description_chars": 200,
        "overlap_chars": 8,
        "provider_identity": "test-provider",
        "provider_idempotency_supported": False,
        "provider_status_query_supported": False,
    }
    values.update(changes)
    return ResearchResolverPolicy(**values)  # type: ignore[arg-type]


def _entry(dataset_id: str, ordinal: int) -> ResearchInventoryEntry:
    """Build an opaque dataset inventory entry."""
    snapshot = ResearchInputSnapshot(
        lane="pasted",
        size_bytes=1,
        state_version=None,
        completed_at=None,
        etag=None,
        version_id=None,
        last_modified=None,
        placeholder=False,
        purpose="dataset",
        snapshot_digest=f"snapshot-{dataset_id}",
    )
    return ResearchInventoryEntry(
        dataset_id=dataset_id,
        lane="pasted",
        lane_ordinal=ordinal,
        exact_reference=f"obs://dev-bucket/private/{dataset_id}.csv",
        comparison_digest=f"comparison-{dataset_id}",
        safe_basename=f"{dataset_id}.csv",
        compound_suffix=".csv",
        size_bytes=1,
        media_hint="text/csv",
        purpose="dataset",
        user_hint=None,
        source_span=None,
        snapshot=snapshot,
        authority_id=f"authority-{dataset_id}",
    )


def _inventory(*dataset_ids: str) -> ResearchInputInventory:
    """Build an immutable inventory in caller-provided order."""
    entries = tuple(
        _entry(dataset_id, index)
        for index, dataset_id in enumerate(dataset_ids)
    )
    return ResearchInputInventory(
        entries=entries,
        documents=(),
        datasets=entries,
        digest="inventory-digest",
    )


def _evidence(
    evidence_id: str, dataset_ids: tuple[str, ...], ordinal: int = 0
) -> ResearchEvidenceUnit:
    """Build one transient evidence unit with no private coordinates."""
    return ResearchEvidenceUnit(
        evidence_id=evidence_id,
        source_kind="query",
        source_ordinal=ordinal,
        source_span=None,
        content_digest=f"digest-{evidence_id}",
        text=f"facts for {evidence_id}",
        dataset_ids=dataset_ids,
    )


def _unit(
    unit_id: str, evidence_ids: tuple[str, ...], dataset_ids: tuple[str, ...]
) -> ResearchResolverObservationUnit:
    """Build one planned resolver unit with an opaque serialized payload."""
    serialized = json.dumps(
        {
            "evidence_ids": evidence_ids,
            "dataset_ids": dataset_ids,
            "text": "facts",
        },
        sort_keys=True,
    ).encode()
    return ResearchResolverObservationUnit(
        unit_id=unit_id,
        evidence_ids=evidence_ids,
        dataset_ids=dataset_ids,
        serialized_request=serialized,
        serialized_bytes=len(serialized),
        estimated_tokens=1,
    )


def _request(
    inventory: ResearchInputInventory,
    evidence_units: tuple[ResearchEvidenceUnit, ...],
    units: tuple[ResearchResolverObservationUnit, ...],
    policy: ResearchResolverPolicy,
) -> ResearchResolutionRequest:
    """Build a resolution request bound to the policy fingerprint."""
    evidence = ExtractedResearchEvidence(
        units=evidence_units, document_digests=(), coverage_digest="coverage"
    )
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "policy_fingerprint": policy.fingerprint(),
                "required_evidence_ids": tuple(
                    unit.evidence_id for unit in evidence_units
                ),
                "units": [
                    {
                        "evidence_ids": unit.evidence_ids,
                        "request_digest": hashlib.sha256(
                            unit.serialized_request
                        ).hexdigest(),
                        "unit_id": unit.unit_id,
                    }
                    for unit in units
                ],
            }
        )
    ).hexdigest()
    plan = ResearchResolverWorkPlan(
        observation_units=units,
        required_evidence_ids=tuple(
            unit.evidence_id for unit in evidence_units
        ),
        policy_fingerprint=policy.fingerprint(),
        digest=digest,
    )
    return ResearchResolutionRequest(inventory, evidence, plan, policy)


class _RecordingProvider:
    """Record opaque work units and return deterministic model payloads."""

    def __init__(
        self,
        outputs: tuple[dict[str, Any], ...],
        query_result: dict[str, Any] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.outputs = list(outputs)
        self.query_result = query_result
        self.failure = failure
        self.requests: list[ResearchResolverObservationUnit] = []
        self.query_calls: list[str] = []
        self.repository: _RecordingRepository | None = None

    async def invoke(
        self,
        unit: ResearchResolverObservationUnit,
        policy: ResearchResolverPolicy,
    ) -> dict[str, Any]:
        """Record a request after local sent state and return its payload."""
        del policy
        assert self.repository is None or self.repository.events[-1] == (
            "mark",
            unit.unit_id,
        )
        self.requests.append(unit)
        if self.failure is not None:
            raise self.failure
        return self.outputs.pop(0)

    async def query(self, request_identity: str) -> dict[str, Any] | None:
        """Return the explicitly configured provider status result."""
        self.query_calls.append(request_identity)
        return self.query_result


@dataclass
class _RecordingRepository:
    """Record mark-before-send and validated-output persistence calls."""

    cached: dict[tuple[str, str, str], dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        """Initialize operation and lookup ledgers."""
        self.events: list[tuple[str, str]] = []
        self.lookups: list[tuple[str, str, str]] = []
        self._latest: dict[str, tuple[str, str]] = {}

    def load_validated_output(
        self, unit_id: str, input_digest: str, policy_digest: str
    ) -> dict[str, Any] | None:
        """Return only an exact input/policy-bound cached payload."""
        self.lookups.append((unit_id, input_digest, policy_digest))
        self._latest[unit_id] = (input_digest, policy_digest)
        if self.cached is None:
            return None
        return self.cached.get((unit_id, input_digest, policy_digest))

    def mark_sent(
        self, unit_id: str, lease_owner: str, expected_revision: int
    ) -> int:
        """Record the durable sent transition before provider invocation."""
        del lease_owner, expected_revision
        self.events.append(("mark", unit_id))
        return 1

    def settle_validated(
        self,
        unit_id: str,
        lease_owner: str,
        expected_revision: int,
        output: dict[str, Any],
    ) -> bool:
        """Persist a validated payload under its exact lookup binding."""
        del lease_owner, expected_revision
        self.events.append(("settle", unit_id))
        if self.cached is None:
            self.cached = {}
        self.cached[(unit_id, *self._latest[unit_id])] = output
        return True


def _resolver(
    provider: _RecordingProvider, repository: _RecordingRepository
) -> ResearchDescriptionResolver:
    """Bind the provider to the repository and expose the domain seam."""
    provider.repository = repository
    return ResearchDescriptionResolver(provider, repository)


def _valid_request(
    policy: ResearchResolverPolicy | None = None,
) -> ResearchResolutionRequest:
    """Build a two-dataset request with one complete unit per dataset."""
    policy = policy or _policy()
    inventory = _inventory("dataset_002", "dataset_001")
    evidence = (
        _evidence("evidence_001", ("dataset_001",)),
        _evidence("evidence_002", ("dataset_002",), 1),
    )
    units = (
        _unit("unit_001", ("evidence_001",), ("dataset_001",)),
        _unit("unit_002", ("evidence_002",), ("dataset_002",)),
    )
    return _request(inventory, evidence, units, policy)


@pytest.mark.asyncio
async def test_resolves_order_without_private_coordinates() -> None:
    """Reconcile all units and return exactly the immutable inventory order."""
    provider = _RecordingProvider(
        (
            {
                "observations": [
                    {
                        "dataset_id": "dataset_001",
                        "claim": "Expression measurements are available.",
                        "confidence": "high",
                        "evidence_ids": ["evidence_001"],
                    }
                ]
            },
            {
                "observations": [
                    {
                        "dataset_id": "dataset_002",
                        "claim": "Sample metadata are available.",
                        "confidence": "medium",
                        "evidence_ids": ["evidence_002"],
                    }
                ]
            },
        )
    )
    repository = _RecordingRepository()

    result = await _resolver(provider, repository).resolve(
        _valid_request(), "lease-owner"
    )

    assert [item.id for item in result.datasets] == [
        "dataset_002",
        "dataset_001",
    ]
    serialized = json.dumps(
        [json.loads(unit.serialized_request) for unit in provider.requests],
        sort_keys=True,
    )
    assert "obs://" not in serialized
    assert "dev-bucket" not in serialized
    assert "comparison-dataset_001" not in serialized
    assert "authority-dataset_001" not in serialized


@pytest.mark.asyncio
async def test_rejects_unknown_and_cross_unit_evidence_without_recharge() -> (
    None
):
    """Invalid content is terminal and never invokes the provider twice."""
    request = _valid_request()
    provider = _RecordingProvider(
        (
            {
                "observations": [
                    {
                        "dataset_id": "dataset_001",
                        "claim": "Expression measurements are available.",
                        "confidence": "high",
                        "evidence_ids": ["evidence_002"],
                    }
                ]
            },
        )
    )
    repository = _RecordingRepository()

    with pytest.raises(Exception) as caught:
        await _resolver(provider, repository).resolve(request, "lease-owner")

    assert (
        getattr(caught.value, "code", None)
        == "research_input_resolution_failed"
    )
    assert len(provider.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim",
    [
        "",
        "unknown",
        "obs://dev-bucket/private/dataset_001.csv",
        "dataset_id=dataset_001; path=/tmp/input.csv",
        "get_data_list(data_list=['dataset_001'])",
    ],
)
async def test_rejects_filler_paths_and_native_argument_claims(
    claim: str,
) -> None:
    """A model cannot turn a description into a path or native argument."""
    provider = _RecordingProvider(
        (
            {
                "observations": [
                    {
                        "dataset_id": "dataset_001",
                        "claim": claim,
                        "confidence": "high",
                        "evidence_ids": ["evidence_001"],
                    }
                ]
            },
            {"observations": []},
        )
    )
    repository = _RecordingRepository()
    request = _valid_request()

    with pytest.raises(Exception) as caught:
        await _resolver(provider, repository).resolve(request, "lease-owner")

    assert (
        getattr(caught.value, "code", None)
        == "research_input_resolution_failed"
    )
    assert len(provider.requests) == 1


def test_schemas_forbid_extra_keys_and_bound_final_descriptions() -> None:
    """Public response schemas reject extras and enforce the configured cap."""
    with pytest.raises(ValidationError):
        ResearchObservationResponse.model_validate(
            {"observations": [], "extra": True}
        )
    with pytest.raises(ValidationError):
        ResearchResolutionResponse.model_validate(
            {
                "datasets": [
                    {
                        "id": "dataset_001",
                        "description": "x" * 11,
                        "confidence": "high",
                        "evidence_ids": ["evidence_001"],
                    }
                ],
                "extra": True,
            }
        )


@pytest.mark.asyncio
async def test_accepts_honest_ambiguity_and_reduces_conflicting_claims() -> (
    None
):
    """Conflicts become explicit low-confidence text."""
    provider = _RecordingProvider(
        (
            {
                "observations": [
                    {
                        "dataset_id": "dataset_001",
                        "claim": (
                            "Supported facts: expression values are present."
                        ),
                        "confidence": "medium",
                        "evidence_ids": ["evidence_001"],
                    }
                ]
            },
            {
                "observations": [
                    {
                        "dataset_id": "dataset_001",
                        "claim": (
                            "Supported facts: expression values are absent."
                        ),
                        "confidence": "medium",
                        "evidence_ids": ["evidence_002"],
                    },
                    {
                        "dataset_id": "dataset_002",
                        "claim": (
                            "Supported facts: sample metadata are present. "
                            "Missing: treatment labels."
                        ),
                        "confidence": "low",
                        "evidence_ids": ["evidence_002"],
                    },
                ]
            },
        )
    )
    repository = _RecordingRepository()
    request = _request(
        _inventory("dataset_001", "dataset_002"),
        (
            _evidence("evidence_001", ("dataset_001",)),
            _evidence("evidence_002", ("dataset_001", "dataset_002"), 1),
        ),
        (
            _unit("unit_001", ("evidence_001",), ("dataset_001",)),
            _unit(
                "unit_002",
                ("evidence_002",),
                ("dataset_001", "dataset_002"),
            ),
        ),
        _policy(),
    )

    result = await _resolver(provider, repository).resolve(request, "owner")

    first, second = result.datasets
    assert first.confidence == "low"
    assert "Supported facts" in first.description
    assert "conflicting" in first.description.casefold()
    assert second.confidence == "low"
    assert "Missing" in second.description


@pytest.mark.asyncio
async def test_mark_before_send_and_validated_output_reuse() -> None:
    """A valid persisted result is reused without another external call."""
    output = {
        "observations": [
            {
                "dataset_id": "dataset_001",
                "claim": "Expression measurements are available.",
                "confidence": "high",
                "evidence_ids": ["evidence_001"],
            }
        ]
    }
    request = _valid_request()
    provider = _RecordingProvider((output, output))
    repository = _RecordingRepository()
    resolver = _resolver(provider, repository)

    first_request = _request(
        _inventory("dataset_001"),
        (_evidence("evidence_001", ("dataset_001",)),),
        (_unit("unit_001", ("evidence_001",), ("dataset_001",)),),
        request.policy,
    )
    await resolver.resolve(first_request, "owner")
    await resolver.resolve(first_request, "owner")

    assert provider.requests == [first_request.work_plan.observation_units[0]]
    assert repository.events == [("mark", "unit_001"), ("settle", "unit_001")]


@pytest.mark.asyncio
async def test_provider_exception_without_query_fails_closed() -> None:
    """An ambiguous provider call is unavailable and is never retried."""
    provider = _RecordingProvider(({},), failure=RuntimeError("secret body"))
    repository = _RecordingRepository()

    with pytest.raises(Exception) as caught:
        await _resolver(provider, repository).resolve(
            _valid_request(), "owner"
        )

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_unavailable"
    )
    assert "secret body" not in str(caught.value)
    assert not provider.query_calls
