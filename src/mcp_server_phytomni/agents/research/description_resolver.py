# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Validate opaque Research observations and reconcile grounded descriptions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .document_evidence import (
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
)
from .input_contracts import (
    ResearchConfidence,
    ResearchInputFailure,
    research_input_failure,
)
from .input_inventory import ResearchInputInventory
from .resolver_policy import (
    ResearchResolverObservationUnit,
    ResearchResolverPolicy,
    ResearchResolverWorkPlan,
    canonical_json_bytes,
)

__all__ = [
    "ResearchDescriptionResolver",
    "ResearchObservation",
    "ResearchObservationResponse",
    "ResearchResolutionRequest",
    "ResearchResolutionResponse",
    "ResearchResolverProvider",
    "ResearchWorkRepository",
    "ResolvedResearchDataset",
]

_SAFE_FAILURE_MESSAGE = "Research input resolution failed."
_SAFE_UNAVAILABLE_MESSAGE = "Research input resolution is unavailable."
_GENERIC_CLAIMS = frozenset(
    {
        "unknown",
        "n/a",
        "na",
        "none",
        "not available",
        "no information",
        "insufficient information",
        "cannot determine",
        "not specified",
        "unspecified",
        "no description",
        "unknown dataset",
    }
)
_PATH_OR_ARGUMENT = re.compile(
    r"(?:"
    r"(?:obs|s3|gs|file|https?)://"
    r"|(?:^|[\s=:])/(?:[A-Za-z0-9._-]+/)+"
    r"|\b[A-Za-z]:[\\/]"
    r"|\b(?:exact_reference|comparison_digest|authority_id|grant_id|"
    r"object_key|asset_id|file_path|obs_file_list|data_list)\b"
    r"|\b(?:path|reference|native)\s*[:=]"
    r")",
    re.IGNORECASE,
)
_SUPPORTED_MARKERS = (
    "supported facts",
    "known facts",
    "observed",
    "evidence shows",
    "present",
    "available",
)
_UNCERTAINTY_MARKERS = (
    "missing",
    "conflict",
    "conflicting",
    "ambiguous",
    "uncertain",
    "not established",
    "cannot distinguish",
    "unknown",
)
_CONFIDENCE_RANK: dict[ResearchConfidence, int] = {
    "high": 2,
    "medium": 1,
    "low": 0,
}


class ResearchObservation(BaseModel):
    """One bounded provider observation grounded in one work unit."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    confidence: ResearchConfidence
    evidence_ids: list[str] = Field(min_length=1)


class ResearchObservationResponse(BaseModel):
    """Strict response envelope returned by one resolver work unit."""

    model_config = ConfigDict(extra="forbid")

    observations: list[ResearchObservation]


class ResolvedResearchDataset(BaseModel):
    """One final grounded description before the native join removes IDs."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: ResearchConfidence
    evidence_ids: list[str] = Field(min_length=1)


class ResearchResolutionResponse(BaseModel):
    """Strict ordered final output for every inventory dataset."""

    model_config = ConfigDict(extra="forbid")

    datasets: list[ResolvedResearchDataset]


@dataclass(frozen=True, slots=True)
class ResearchResolutionRequest:
    """Immutable evidence, inventory, work-plan, and policy binding."""

    inventory: ResearchInputInventory
    evidence: ExtractedResearchEvidence
    work_plan: ResearchResolverWorkPlan
    policy: ResearchResolverPolicy


class ResearchResolverProvider(Protocol):
    """Injected provider boundary for one opaque resolver work unit."""

    async def invoke(
        self,
        unit: ResearchResolverObservationUnit,
        policy: ResearchResolverPolicy,
    ) -> dict[str, Any]:
        """Invoke the provider with no trusted inventory entry metadata."""
        raise NotImplementedError

    async def query(self, request_identity: str) -> dict[str, Any] | None:
        """Query a provider call only when policy explicitly permits it."""
        raise NotImplementedError


class ResearchWorkRepository(Protocol):
    """Storage boundary for validated work output and sent transitions."""

    def load_validated_output(
        self, unit_id: str, input_digest: str, policy_digest: str
    ) -> dict[str, Any] | None:
        """Load output bound to both the exact request and policy."""
        raise NotImplementedError

    def mark_sent(
        self, unit_id: str, lease_owner: str, expected_revision: int
    ) -> int:
        """Commit the sent state immediately before external invocation."""
        raise NotImplementedError

    def settle_validated(
        self,
        unit_id: str,
        lease_owner: str,
        expected_revision: int,
        output: dict[str, Any],
    ) -> bool:
        """CAS-settle one validated provider result."""
        raise NotImplementedError


class _ResolutionContractError(ValueError):
    """Private validation sentinel that never crosses the domain boundary."""


class ResearchDescriptionResolver:
    """Resolve complete evidence into ordered, grounded dataset descriptions."""

    def __init__(
        self,
        provider: ResearchResolverProvider,
        repository: ResearchWorkRepository,
    ) -> None:
        self.provider = provider
        self.repository = repository

    @property
    def contract_name(self) -> str:
        """Identify the strict description-resolution domain seam."""
        return "research_description_resolver"

    async def resolve(
        self, request: ResearchResolutionRequest, lease_owner: str
    ) -> ResearchResolutionResponse:
        """Return one grounded dataset result in immutable inventory order."""
        try:
            context = _validate_request(request, lease_owner)
            observations: list[ResearchObservation] = []
            completed: set[str] = set()
            for unit in request.work_plan.observation_units:
                response = await self._resolve_unit(
                    unit, request, lease_owner, context
                )
                observations.extend(response.observations)
                completed.update(unit.evidence_ids)
            _ensure_complete_coverage(
                completed, request.work_plan.required_evidence_ids
            )
            return _reconcile(
                observations,
                request.inventory,
                request.work_plan.required_evidence_ids,
                request.policy.max_description_chars,
            )
        except _ResolutionContractError:
            raise _failure() from None

    async def _resolve_unit(
        self,
        unit: ResearchResolverObservationUnit,
        request: ResearchResolutionRequest,
        lease_owner: str,
        context: _ResolutionContext,
    ) -> ResearchObservationResponse:
        """Load, send, validate, and persist exactly one work unit."""
        input_digest = _digest_bytes(unit.serialized_request)
        policy_digest = context.policy_digest
        try:
            cached = self.repository.load_validated_output(
                unit.unit_id, input_digest, policy_digest
            )
        except (RuntimeError, ValueError, TypeError, OSError):
            raise _unavailable() from None
        if cached is not None:
            return _validated_response(cached, unit, context)

        try:
            revision = self.repository.mark_sent(unit.unit_id, lease_owner, 0)
        except (RuntimeError, ValueError, TypeError, OSError):
            raise _unavailable() from None
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise _unavailable()

        try:
            raw = await self.provider.invoke(unit, request.policy)
        except (RuntimeError, ValueError, TypeError, OSError):
            raw = await self._query_after_exception(
                unit, request.policy, input_digest
            )
        response = _validated_response(raw, unit, context)
        try:
            settled = self.repository.settle_validated(
                unit.unit_id,
                lease_owner,
                revision,
                response.model_dump(mode="json"),
            )
        except (RuntimeError, ValueError, TypeError, OSError):
            raise _unavailable() from None
        if settled is not True:
            raise _unavailable()
        return response

    async def _query_after_exception(
        self,
        unit: ResearchResolverObservationUnit,
        policy: ResearchResolverPolicy,
        request_identity: str,
    ) -> dict[str, Any]:
        """Use explicit status reconciliation or fail an ambiguous call."""
        del unit
        if not policy.provider_status_query_supported:
            raise _unavailable()
        try:
            queried = await self.provider.query(request_identity)
        except (RuntimeError, ValueError, TypeError, OSError):
            raise _unavailable() from None
        if queried is None:
            raise _unavailable()
        if not isinstance(queried, dict):
            raise _unavailable()
        return queried


@dataclass(frozen=True, slots=True)
class _ResolutionContext:
    """Validated request metadata needed for every unit boundary."""

    policy_digest: str
    evidence_by_id: dict[str, ResearchEvidenceUnit]
    max_description_chars: int


def _validate_request(
    request: ResearchResolutionRequest, lease_owner: str
) -> _ResolutionContext:
    """Validate all local coverage and opaque identity bindings."""
    if (
        not isinstance(request, ResearchResolutionRequest)
        or not isinstance(request.policy, ResearchResolverPolicy)
        or not isinstance(request.work_plan, ResearchResolverWorkPlan)
        or not lease_owner
    ):
        raise _ResolutionContractError
    inventory_ids = _inventory_dataset_ids(request.inventory)
    evidence_by_id = _evidence_index(request.evidence)
    plan = request.work_plan
    policy_digest = request.policy.fingerprint()
    if plan.policy_fingerprint != policy_digest:
        raise _ResolutionContractError
    if set(plan.required_evidence_ids) != set(evidence_by_id):
        raise _ResolutionContractError
    if len(plan.required_evidence_ids) != len(set(plan.required_evidence_ids)):
        raise _ResolutionContractError
    if plan.digest != _expected_plan_digest(plan):
        raise _ResolutionContractError
    _validate_units(plan, evidence_by_id, inventory_ids, request.policy)
    return _ResolutionContext(
        policy_digest=policy_digest,
        evidence_by_id=evidence_by_id,
        max_description_chars=request.policy.max_description_chars,
    )


def _inventory_dataset_ids(
    inventory: ResearchInputInventory,
) -> tuple[str, ...]:
    """Return unique ordered dataset IDs from the immutable inventory."""
    if not isinstance(inventory, ResearchInputInventory):
        raise _ResolutionContractError
    entries = inventory.entries
    datasets = inventory.datasets
    if len({entry.dataset_id for entry in entries}) != len(entries):
        raise _ResolutionContractError
    if (
        tuple(entry for entry in entries if entry.purpose == "dataset")
        != datasets
    ):
        raise _ResolutionContractError
    if not datasets or any(not entry.dataset_id for entry in datasets):
        raise _ResolutionContractError
    return tuple(entry.dataset_id for entry in datasets)


def _evidence_index(
    evidence: ExtractedResearchEvidence,
) -> dict[str, ResearchEvidenceUnit]:
    """Index immutable evidence units while rejecting duplicate IDs."""
    if not isinstance(evidence, ExtractedResearchEvidence):
        raise _ResolutionContractError
    indexed: dict[str, ResearchEvidenceUnit] = {}
    for unit in evidence.units:
        if not unit.evidence_id or unit.evidence_id in indexed:
            raise _ResolutionContractError
        indexed[unit.evidence_id] = unit
    if not indexed:
        raise _ResolutionContractError
    return indexed


def _validate_units(
    plan: ResearchResolverWorkPlan,
    evidence_by_id: dict[str, ResearchEvidenceUnit],
    inventory_ids: tuple[str, ...],
    policy: ResearchResolverPolicy,
) -> None:
    """Ensure every planned unit is bounded and covers known evidence only."""
    if not plan.observation_units:
        raise _ResolutionContractError
    seen_units: set[str] = set()
    covered: set[str] = set()
    valid_inventory = set(inventory_ids)
    for unit in plan.observation_units:
        if _invalid_unit(unit, seen_units, valid_inventory, policy):
            raise _ResolutionContractError
        seen_units.add(unit.unit_id)
        for evidence_id in unit.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                raise _ResolutionContractError
            if tuple(evidence.dataset_ids) != tuple(unit.dataset_ids):
                raise _ResolutionContractError
            covered.add(evidence_id)
    if covered != set(plan.required_evidence_ids):
        raise _ResolutionContractError


def _invalid_unit(
    unit: ResearchResolverObservationUnit,
    seen_units: set[str],
    valid_inventory: set[str],
    policy: ResearchResolverPolicy,
) -> bool:
    """Return whether one unit violates identity, membership, or budgets."""
    invalid_identity = not unit.unit_id or unit.unit_id in seen_units
    invalid_membership = (
        not unit.evidence_ids
        or len(unit.evidence_ids) != len(set(unit.evidence_ids))
        or not unit.dataset_ids
        or not set(unit.dataset_ids).issubset(valid_inventory)
    )
    token_budget = policy.context_token_limit - sum(
        (
            policy.output_token_reserve,
            policy.prompt_token_overhead,
            policy.schema_token_overhead,
            policy.safety_margin_tokens,
        )
    )
    invalid_budget = (
        unit.serialized_bytes != len(unit.serialized_request)
        or unit.serialized_bytes < 1
        or unit.serialized_bytes > policy.max_serialized_request_bytes
        or unit.estimated_tokens < 0
        or unit.estimated_tokens > token_budget
    )
    return invalid_identity or invalid_membership or invalid_budget


def _expected_plan_digest(plan: ResearchResolverWorkPlan) -> str:
    """Recompute the planner digest before trusting work-unit coverage."""
    value = {
        "policy_fingerprint": plan.policy_fingerprint,
        "required_evidence_ids": plan.required_evidence_ids,
        "units": [
            {
                "evidence_ids": unit.evidence_ids,
                "request_digest": _digest_bytes(unit.serialized_request),
                "unit_id": unit.unit_id,
            }
            for unit in plan.observation_units
        ],
    }
    return _digest_bytes(canonical_json_bytes(value))


def _validated_response(
    raw: object,
    unit: ResearchResolverObservationUnit,
    context: _ResolutionContext,
) -> ResearchObservationResponse:
    """Parse and ground one strict response without exposing raw errors."""
    try:
        response = ResearchObservationResponse.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        raise _failure() from None
    _validate_observations(response, unit, context)
    return response


def _validate_observations(
    response: ResearchObservationResponse,
    unit: ResearchResolverObservationUnit,
    context: _ResolutionContext,
) -> None:
    """Validate dataset/evidence membership and bounded plain-text claims."""
    allowed_datasets = set(unit.dataset_ids)
    seen_datasets: set[str] = set()
    allowed_evidence = set(unit.evidence_ids)
    for observation in response.observations:
        if (
            observation.dataset_id not in allowed_datasets
            or observation.dataset_id in seen_datasets
            or not observation.evidence_ids
            or len(observation.evidence_ids)
            != len(set(observation.evidence_ids))
            or not set(observation.evidence_ids).issubset(allowed_evidence)
        ):
            raise _failure()
        seen_datasets.add(observation.dataset_id)
        for evidence_id in observation.evidence_ids:
            evidence = context.evidence_by_id[evidence_id]
            if observation.dataset_id not in evidence.dataset_ids:
                raise _failure()
        _validate_claim(
            observation.claim,
            observation.confidence,
            context.max_description_chars,
        )


def _validate_claim(
    claim: str, confidence: ResearchConfidence, max_description_chars: int
) -> None:
    """Reject blank, generic, private-coordinate, or dishonest claims."""
    normalized = " ".join(claim.split()).casefold()
    if (
        not normalized
        or normalized in _GENERIC_CLAIMS
        or _PATH_OR_ARGUMENT.search(claim) is not None
    ):
        raise _failure()
    if len(claim) > max_description_chars:
        raise _failure()
    if confidence == "low" and not _honest_ambiguity(normalized):
        raise _failure()


def _honest_ambiguity(claim: str) -> bool:
    """Require both supported facts and a bounded uncertainty statement."""
    supported = any(marker in claim for marker in _SUPPORTED_MARKERS)
    uncertain = any(marker in claim for marker in _UNCERTAINTY_MARKERS)
    return supported and uncertain


def _ensure_complete_coverage(
    completed: set[str], required: tuple[str, ...]
) -> None:
    """Reject reconciliation when any planned evidence unit did not complete."""
    if completed != set(required):
        raise _failure()


def _reconcile(
    observations: list[ResearchObservation],
    inventory: ResearchInputInventory,
    evidence_order: tuple[str, ...],
    max_description_chars: int,
) -> ResearchResolutionResponse:
    """Reduce all valid observations into one ordered result per dataset."""
    by_dataset: dict[str, list[ResearchObservation]] = {}
    for observation in observations:
        by_dataset.setdefault(observation.dataset_id, []).append(observation)
    evidence_positions = {
        evidence_id: index for index, evidence_id in enumerate(evidence_order)
    }
    datasets: list[ResolvedResearchDataset] = []
    for entry in inventory.datasets:
        current = by_dataset.get(entry.dataset_id, [])
        if not current:
            raise _failure()
        description, confidence = _reduce_claims(
            current, max_description_chars
        )
        if len(description) > max_description_chars:
            raise _failure()
        evidence_ids = _ordered_evidence_ids(current, evidence_positions)
        datasets.append(
            ResolvedResearchDataset(
                id=entry.dataset_id,
                description=description,
                confidence=confidence,
                evidence_ids=evidence_ids,
            )
        )
    try:
        return ResearchResolutionResponse(datasets=datasets)
    except ValidationError:
        raise _failure() from None


def _reduce_claims(
    observations: list[ResearchObservation], max_description_chars: int
) -> tuple[str, ResearchConfidence]:
    """Keep identical claims deterministic and make conflicts explicit."""
    unique: list[str] = []
    normalized: set[str] = set()
    for observation in observations:
        key = " ".join(observation.claim.split()).casefold()
        if key not in normalized:
            normalized.add(key)
            unique.append(observation.claim.strip())
    if len(unique) == 1:
        confidence = min(
            (
                cast(ResearchConfidence, item.confidence)
                for item in observations
            ),
            key=lambda item: _CONFIDENCE_RANK[item],
        )
        _validate_claim(unique[0], confidence, max_description_chars)
        return unique[0], confidence
    description = (
        "Supported facts: "
        + "; ".join(unique)
        + ". Missing or conflicting facts: observations disagree."
    )
    _validate_claim(description, "low", max_description_chars)
    return description, "low"


def _ordered_evidence_ids(
    observations: list[ResearchObservation], positions: dict[str, int]
) -> list[str]:
    """Return evidence IDs once in planned source order."""
    unique = {
        evidence_id
        for observation in observations
        for evidence_id in observation.evidence_ids
    }
    return sorted(unique, key=lambda evidence_id: positions[evidence_id])


def _digest_bytes(value: bytes) -> str:
    """Hash exact serialized work bytes for repository binding."""
    return hashlib.sha256(value).hexdigest()


def _failure() -> ResearchInputFailure:
    """Build the stable terminal resolver failure."""
    return research_input_failure(
        "research_input_resolution_failed",
        _SAFE_FAILURE_MESSAGE,
        http_status_hint=422,
        retryable=False,
    )


def _unavailable() -> ResearchInputFailure:
    """Build the stable non-disclosing unavailable failure."""
    return research_input_failure(
        "research_input_resolution_unavailable",
        _SAFE_UNAVAILABLE_MESSAGE,
        http_status_hint=503,
        retryable=False,
    )
