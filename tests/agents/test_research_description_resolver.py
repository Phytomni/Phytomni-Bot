# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for grounded Research observation validation and reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.agents.research.description_resolver import (
    ResearchDescriptionResolver,
    ResearchObservation,
    ResearchObservationResponse,
    ResearchResolutionRequest,
    ResearchResolutionResponse,
    ResolvedResearchDataset,
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
    serialized = canonical_json_bytes(
        {
            "evidence_ids": evidence_ids,
            "dataset_ids": dataset_ids,
            "text": "facts",
        }
    )
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


@dataclass
class _ProviderState:
    """Mutable behavior options held outside the recording provider."""

    outputs: list[dict[str, Any]]
    query_result: dict[str, Any] | None = None
    failure: Exception | None = None
    failures_remaining: int = 0
    query_failure: Exception | None = None


class _RecordingProvider:
    """Record opaque work units and return deterministic model payloads."""

    def __init__(
        self, outputs: tuple[dict[str, Any], ...], **options: Any
    ) -> None:
        self.state = _ProviderState(
            outputs=list(outputs),
            query_result=options.get("query_result"),
            failure=options.get("failure"),
            failures_remaining=options.get("failures_remaining", 0),
            query_failure=options.get("query_failure"),
        )
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
        if self.state.failures_remaining:
            self.state.failures_remaining -= 1
            raise RuntimeError("transient provider failure")
        if self.state.failure is not None:
            raise self.state.failure
        return self.state.outputs.pop(0)

    async def query(self, request_identity: str) -> dict[str, Any] | None:
        """Return the explicitly configured provider status result."""
        self.query_calls.append(request_identity)
        if self.state.query_failure is not None:
            raise self.state.query_failure
        return self.state.query_result


@dataclass
class _RecordingRepository:
    """Record mark-before-send and validated-output persistence calls."""

    cached: dict[tuple[str, str, str], dict[str, Any]] | None = None
    failure: Exception | None = None

    def __post_init__(self) -> None:
        """Initialize operation and lookup ledgers."""
        self.events: list[tuple[str, str]] = []
        self.lookups: list[tuple[str, str, str]] = []
        self._latest: dict[str, tuple[str, str]] = {}

    def load_validated_output(
        self,
        unit_id: str,
        input_digest: str,
        policy_digest: str,
        *bindings: str,
    ) -> dict[str, Any] | None:
        """Return only an exact input/policy-bound cached payload."""
        if self.failure is not None:
            raise self.failure
        del bindings
        self.lookups.append((unit_id, input_digest, policy_digest))
        self._latest[unit_id] = (input_digest, policy_digest)
        if self.cached is None:
            return None
        return self.cached.get((unit_id, input_digest, policy_digest))

    def mark_sent(
        self,
        unit_id: str,
        lease_owner: str,
        expected_revision: int,
        **options: object,
    ) -> int:
        """Record the durable sent transition before provider invocation."""
        if self.failure is not None:
            raise self.failure
        del lease_owner, expected_revision, options
        self.events.append(("mark", unit_id))
        return 1

    def settle_validated(
        self,
        unit_id: str,
        lease_owner: str,
        expected_revision: int,
        output: dict[str, Any],
        **options: object,
    ) -> bool:
        """Persist a validated payload under its exact lookup binding."""
        if self.failure is not None:
            raise self.failure
        del lease_owner, expected_revision, options
        self.events.append(("settle", unit_id))
        if self.cached is None:
            self.cached = {}
        self.cached[(unit_id, *self._latest[unit_id])] = output
        return True


class _RecoveryHook:
    """Bounded request-recovery fixture used by the resolver seam."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    async def recover_request(self) -> object:
        """Return a bounded recovery result for the resolver request."""
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return {"reclaimed": 0}

    @property
    def contract_name(self) -> str:
        """Identify the request-recovery fixture contract."""
        return "request-recovery-fixture"


class _ReusingExecutor:
    """Durable-executor adapter fixture that never invokes the provider."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def execute(self, unit_id: str, lease_owner: str) -> object:
        """Return a reusable disposition without provider I/O."""
        self.calls.append((unit_id, lease_owner))
        return type("Disposition", (), {"state": "reused"})()

    @property
    def contract_name(self) -> str:
        """Identify the durable-executor fixture contract."""
        return "reusing-executor-fixture"


def _resolver(
    provider: _RecordingProvider, repository: _RecordingRepository
) -> ResearchDescriptionResolver:
    """Bind the provider to the repository and expose the domain seam."""
    provider.repository = repository
    return ResearchDescriptionResolver(provider, repository)


def test_resolver_contract_name_remains_compatible() -> None:
    """The durable options do not remove the existing domain contract."""
    resolver = ResearchDescriptionResolver(
        _RecordingProvider(()), _RecordingRepository()
    )
    assert resolver.contract_name == "research_description_resolver"
    assert resolver.durable_execution_enabled() is False


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


def _single_dataset_request(
    policy: ResearchResolverPolicy | None = None,
) -> ResearchResolutionRequest:
    """Build the one-unit request shared by durable resolver tests."""
    return _request(
        _inventory("dataset_001"),
        (_evidence("evidence_001", ("dataset_001",)),),
        (_unit("unit_001", ("evidence_001",), ("dataset_001",)),),
        policy or _policy(),
    )


def _single_observation_output() -> dict[str, Any]:
    """Build one valid output for the single-dataset resolver request."""
    return {
        "observations": [
            {
                "dataset_id": "dataset_001",
                "claim": "Expression measurements are available.",
                "confidence": "high",
                "evidence_ids": ["evidence_001"],
            }
        ]
    }


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
    output = _single_observation_output()
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
async def test_durable_executor_projects_reused_output_without_provider() -> (
    None
):
    """The durable adapter owns execution while the resolver only projects."""
    request = _valid_request()
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
    second_output = {
        "observations": [
            {
                "dataset_id": "dataset_002",
                "claim": "Sample metadata are available.",
                "confidence": "medium",
                "evidence_ids": ["evidence_002"],
            }
        ]
    }
    repository = _RecordingRepository()
    await _resolver(
        _RecordingProvider((output, second_output)), repository
    ).resolve(request, "owner")
    executor = _ReusingExecutor()
    provider = _RecordingProvider(())
    result = await ResearchDescriptionResolver(
        provider, repository, executor=executor
    ).resolve(request, "owner")

    assert len(result.datasets) == 2
    assert [unit_id for unit_id, _owner in executor.calls] == [
        "unit_001",
        "unit_002",
    ]
    assert not provider.requests


@pytest.mark.asyncio
async def test_request_recovery_hook_is_bounded_and_sanitized() -> None:
    """Request recovery runs once and never leaks hook exception text."""
    provider = _RecordingProvider(())
    hook = _RecoveryHook(RuntimeError("private recovery details"))
    resolver = ResearchDescriptionResolver(
        provider, _RecordingRepository(), recovery_hook=hook
    )

    with pytest.raises(Exception) as caught:
        await resolver.resolve(_valid_request(), "owner")

    assert hook.calls == 1
    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_unavailable"
    )
    assert "private recovery details" not in str(caught.value)


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


def test_public_dtos_are_strict_and_bounded() -> None:
    """Provider DTOs reject coercion and every unbounded collection/text."""
    observation = {
        "dataset_id": "dataset_001",
        "claim": "supported fact",
        "confidence": "high",
        "evidence_ids": ["evidence_001"],
    }
    with pytest.raises(ValidationError):
        ResearchObservation.model_validate(
            {**observation, "evidence_ids": ("evidence_001",)}
        )
    with pytest.raises(ValidationError):
        ResearchObservation.model_validate(
            {**observation, "claim": "x" * 4097}
        )
    with pytest.raises(ValidationError):
        ResearchObservationResponse.model_validate(
            {"observations": [observation] * 257}
        )
    with pytest.raises(ValidationError):
        ResolvedResearchDataset.model_validate(
            {
                "id": "dataset_001",
                "description": "x" * 4097,
                "confidence": "high",
                "evidence_ids": ["evidence_001"],
            }
        )
    with pytest.raises(ValidationError):
        ResearchResolutionResponse.model_validate(
            {
                "datasets": [
                    {
                        "id": "dataset_001",
                        "description": "supported fact",
                        "confidence": "high",
                        "evidence_ids": ["evidence_001"],
                    }
                ]
                * 257
            }
        )


@pytest.mark.asyncio
async def test_rejects_private_coordinates_in_provider_payload() -> None:
    """A forged serialized unit cannot carry an OBS reference externally."""
    request = _valid_request()
    first = request.work_plan.observation_units[0]
    private_payload = canonical_json_bytes(
        {
            "dataset_ids": first.dataset_ids,
            "evidence_ids": first.evidence_ids,
            "exact_reference": "obs://secret/private/dataset.csv",
        }
    )
    forged = replace(
        first,
        serialized_request=private_payload,
        serialized_bytes=len(private_payload),
    )
    forged_request = _request(
        request.inventory,
        request.evidence.units,
        (forged, request.work_plan.observation_units[1]),
        request.policy,
    )
    provider = _RecordingProvider(())

    with pytest.raises(Exception) as caught:
        await _resolver(provider, _RecordingRepository()).resolve(
            forged_request, "owner"
        )

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    assert not provider.requests


@pytest.mark.asyncio
async def test_generic_provider_exception_is_sanitized() -> None:
    """Unexpected provider exceptions never cross the safe error boundary."""
    provider = _RecordingProvider(
        ({},), failure=Exception("SECRET_PROVIDER_BODY")
    )

    with pytest.raises(Exception) as caught:
        await _resolver(provider, _RecordingRepository()).resolve(
            _valid_request(), "owner"
        )

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_unavailable"
    )
    assert "SECRET_PROVIDER_BODY" not in str(caught.value)


@pytest.mark.asyncio
async def test_status_and_idempotency_recovery_use_same_identity() -> None:
    """Capabilities reconcile without a new logical charge."""
    policy = _policy(
        provider_idempotency_supported=True,
        provider_status_query_supported=True,
    )
    request = _valid_request(policy)
    status_output = {
        "status": "succeeded",
        "result": {
            "observations": [
                {
                    "dataset_id": "dataset_001",
                    "claim": "Expression measurements are available.",
                    "confidence": "high",
                    "evidence_ids": ["evidence_001"],
                }
            ]
        },
    }
    provider = _RecordingProvider(
        (
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
        ),
        query_result=status_output,
        failures_remaining=1,
    )

    result = await _resolver(provider, _RecordingRepository()).resolve(
        request, "owner"
    )

    assert [item.id for item in result.datasets] == [
        "dataset_002",
        "dataset_001",
    ]
    assert len(provider.query_calls) == 1
    assert len(provider.query_calls[0]) == 64


@pytest.mark.asyncio
async def test_idempotency_capability_allows_same_unit_retry_only() -> None:
    """A declared idempotency key permits retrying the exact unit identity."""
    policy = _policy(provider_idempotency_supported=True)
    request = _valid_request(policy)
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
        ),
        failures_remaining=1,
    )

    result = await _resolver(provider, _RecordingRepository()).resolve(
        request, "owner"
    )

    assert len(result.datasets) == 2
    assert [unit.unit_id for unit in provider.requests] == [
        "unit_001",
        "unit_001",
        "unit_002",
    ]


@pytest.mark.asyncio
async def test_cache_binding_includes_inventory_context() -> None:
    """A changed immutable inventory cannot reuse an old unit output."""
    request = _request(
        _inventory("dataset_001"),
        (_evidence("evidence_001", ("dataset_001",)),),
        (_unit("unit_001", ("evidence_001",), ("dataset_001",)),),
        _policy(),
    )
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
    repository = _RecordingRepository()
    first_provider = _RecordingProvider((output,))
    await _resolver(first_provider, repository).resolve(request, "owner")

    changed_inventory = replace(request.inventory, digest="changed-inventory")
    changed_request = replace(request, inventory=changed_inventory)
    second_provider = _RecordingProvider((output,))
    await _resolver(second_provider, repository).resolve(
        changed_request, "owner"
    )

    assert len(second_provider.requests) == 1
