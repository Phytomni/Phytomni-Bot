# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Validate opaque observations and reconcile grounded descriptions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
)

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
_MAX_ID_CHARS = 256
_MAX_CLAIM_CHARS = 4096
_MAX_EVIDENCE_IDS = 256
_MAX_OBSERVATIONS = 256
_SUCCESS_STATUSES = frozenset(
    {"complete", "completed", "success", "succeeded"}
)
_EXTERNAL_FAILURES: tuple[type[Exception], ...] = (Exception,)
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

    model_config = ConfigDict(extra="forbid", strict=True)

    dataset_id: StrictStr = Field(min_length=1, max_length=_MAX_ID_CHARS)
    claim: StrictStr = Field(min_length=1, max_length=_MAX_CLAIM_CHARS)
    confidence: ResearchConfidence
    evidence_ids: list[StrictStr] = Field(
        min_length=1, max_length=_MAX_EVIDENCE_IDS
    )

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(cls, value: list[str]) -> list[str]:
        """Reject duplicate evidence references at the DTO boundary."""
        if len(value) != len(set(value)):
            raise ValueError("evidence IDs must be unique")
        return value


class ResearchObservationResponse(BaseModel):
    """Strict response envelope returned by one resolver work unit."""

    model_config = ConfigDict(extra="forbid", strict=True)

    observations: list[ResearchObservation] = Field(
        max_length=_MAX_OBSERVATIONS
    )


class ResolvedResearchDataset(BaseModel):
    """One final grounded description before the native join removes IDs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr = Field(min_length=1, max_length=_MAX_ID_CHARS)
    description: StrictStr = Field(min_length=1, max_length=_MAX_CLAIM_CHARS)
    confidence: ResearchConfidence
    evidence_ids: list[StrictStr] = Field(
        min_length=1, max_length=_MAX_EVIDENCE_IDS
    )

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(cls, value: list[str]) -> list[str]:
        """Keep the final evidence projection deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("evidence IDs must be unique")
        return value


class ResearchResolutionResponse(BaseModel):
    """Strict ordered final output for every inventory dataset."""

    model_config = ConfigDict(extra="forbid", strict=True)

    datasets: list[ResolvedResearchDataset] = Field(
        max_length=_MAX_OBSERVATIONS
    )


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
    """Resolve evidence into ordered, grounded dataset descriptions."""

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
        input_digest = _request_input_digest(request, unit)
        policy_digest = context.policy_digest
        try:
            cached = self.repository.load_validated_output(
                unit.unit_id, input_digest, policy_digest
            )
        except _EXTERNAL_FAILURES:
            raise _unavailable() from None
        if cached is not None:
            return _validated_response(cached, unit, context)

        try:
            revision = self.repository.mark_sent(unit.unit_id, lease_owner, 0)
        except _EXTERNAL_FAILURES:
            raise _unavailable() from None
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise _unavailable()

        request_identity = _request_identity(unit, input_digest, policy_digest)
        try:
            raw = await self.provider.invoke(unit, request.policy)
        except _EXTERNAL_FAILURES:
            raw = await self._query_after_exception(
                unit, request.policy, request_identity
            )
        response = _validated_response(raw, unit, context)
        try:
            settled = self.repository.settle_validated(
                unit.unit_id,
                lease_owner,
                revision,
                response.model_dump(mode="json"),
            )
        except _EXTERNAL_FAILURES:
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
        """Use declared status/idempotency support without a new identity."""
        if policy.provider_status_query_supported:
            try:
                queried = await self.provider.query(request_identity)
            except _EXTERNAL_FAILURES:
                raise _unavailable() from None
            payload = _status_payload(queried)
            if payload is None:
                raise _unavailable()
            return payload
        if policy.provider_idempotency_supported:
            try:
                # The exact unit carries the same provider identity.  A retry
                # is safe because policy explicitly declares it idempotent.
                return await self.provider.invoke(unit, policy)
            except _EXTERNAL_FAILURES:
                raise _unavailable() from None
        raise _unavailable()


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
    _validate_units(
        plan,
        evidence_by_id,
        inventory_ids,
        request.inventory,
        request.policy,
    )
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
    inventory: ResearchInputInventory,
    policy: ResearchResolverPolicy,
) -> None:
    """Ensure every planned unit is bounded and covers known evidence only."""
    if not plan.observation_units:
        raise _ResolutionContractError
    seen_units: set[str] = set()
    covered: set[str] = set()
    valid_inventory = set(inventory_ids)
    private_values = _private_inventory_values(inventory)
    for unit in plan.observation_units:
        if _invalid_unit(
            unit, seen_units, valid_inventory, private_values, policy
        ):
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
    private_values: frozenset[str],
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
        or not _opaque_serialized_request(
            unit.serialized_request,
            private_values,
            set(unit.evidence_ids),
            valid_inventory,
        )
    )
    return invalid_identity or invalid_membership or invalid_budget


def _private_inventory_values(
    inventory: ResearchInputInventory,
) -> frozenset[str]:
    """Return private inventory values that must not reach a provider."""
    values: set[str] = set()
    for entry in inventory.entries:
        values.update(
            value
            for value in (
                entry.exact_reference,
                entry.comparison_digest,
                entry.safe_basename,
                entry.authority_id,
                entry.snapshot.snapshot_digest,
                entry.snapshot.etag,
                entry.snapshot.version_id,
            )
            if value
        )
    return frozenset(values)


def _opaque_serialized_request(
    serialized: bytes,
    private_values: frozenset[str],
    allowed_evidence: set[str],
    valid_inventory: set[str],
) -> bool:
    """Accept only canonical JSON without private coordinates or fields."""
    try:
        decoded = serialized.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if canonical_json_bytes(payload) != serialized:
        return False
    return not _contains_private_payload(
        payload, private_values
    ) and _valid_payload_membership(payload, allowed_evidence, valid_inventory)


def _contains_private_payload(
    value: object, private_values: frozenset[str]
) -> bool:
    """Find private fields, coordinates, and inventory values recursively."""
    allowed_keys = frozenset(
        {
            "dataset_ids",
            "end",
            "evidence",
            "evidence_id",
            "evidence_ids",
            "grammar",
            "group_members",
            "inventory_digest",
            "model_id",
            "overlap_chars",
            "policy_fingerprint",
            "schema_version",
            "source_kind",
            "source_ordinal",
            "source_span",
            "start",
            "text",
        }
    )
    forbidden_keys = frozenset(
        {
            "asset_id",
            "authority_id",
            "bucket",
            "comparison_digest",
            "data_list",
            "exact_reference",
            "file_path",
            "object_key",
            "obs_file_list",
            "path",
            "safe_basename",
            "url",
        }
    )
    private = False
    if isinstance(value, dict):
        private = any(
            str(key).casefold() not in allowed_keys for key in value
        ) or any(str(key).casefold() in forbidden_keys for key in value)
        if not private:
            private = any(
                _contains_private_payload(item, private_values)
                for item in value.values()
            )
    elif isinstance(value, list):
        private = any(
            _contains_private_payload(item, private_values) for item in value
        )
    elif isinstance(value, str):
        if value in private_values:
            private = True
        else:
            private = (
                re.search(
                    r"(?:obs|s3|gs|file)://|(?:^|[\s=:])/(?:[A-Za-z0-9._-]+/)+"
                    r"|\b[A-Za-z]:[\\/]",
                    value,
                    re.IGNORECASE,
                )
                is not None
            )
    return private


def _valid_payload_membership(
    value: object,
    allowed_evidence: set[str],
    valid_inventory: set[str],
) -> bool:
    """Ensure serialized opaque IDs stay within this plan's memberships."""
    valid = True
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"dataset_ids", "group_members"} and (
                not isinstance(nested, list)
                or any(
                    not isinstance(item, str) or item not in valid_inventory
                    for item in nested
                )
            ):
                valid = False
            if key in {"evidence_ids", "evidence_id"}:
                candidates = nested if isinstance(nested, list) else [nested]
                if valid and any(
                    not isinstance(item, str) or item not in allowed_evidence
                    for item in candidates
                ):
                    valid = False
            if valid and not _valid_payload_membership(
                nested, allowed_evidence, valid_inventory
            ):
                valid = False
            if not valid:
                break
    elif isinstance(value, list):
        valid = all(
            _valid_payload_membership(item, allowed_evidence, valid_inventory)
            for item in value
        )
    return valid


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


def _request_input_digest(
    request: ResearchResolutionRequest,
    unit: ResearchResolverObservationUnit,
) -> str:
    """Bind cache identity to the complete immutable request context."""
    inventory = request.inventory
    inventory_identity = [
        {
            "authority_digest": _optional_digest(entry.authority_id),
            "comparison_digest": entry.comparison_digest,
            "dataset_id": entry.dataset_id,
            "exact_reference_digest": _digest_bytes(
                entry.exact_reference.encode("utf-8")
            ),
            "lane": entry.lane,
            "lane_ordinal": entry.lane_ordinal,
            "purpose": entry.purpose,
            "safe_basename_digest": _digest_bytes(
                entry.safe_basename.encode("utf-8")
            ),
            "size_bytes": entry.size_bytes,
            "snapshot": {
                "etag": _optional_digest(entry.snapshot.etag),
                "last_modified": _optional_digest(
                    entry.snapshot.last_modified
                ),
                "snapshot_digest": entry.snapshot.snapshot_digest,
                "state_version": entry.snapshot.state_version,
                "version_id": _optional_digest(entry.snapshot.version_id),
            },
        }
        for entry in inventory.entries
    ]
    evidence_identity = [
        {
            "content_digest": item.content_digest,
            "dataset_ids": item.dataset_ids,
            "evidence_id": item.evidence_id,
            "source_kind": item.source_kind,
            "source_ordinal": item.source_ordinal,
        }
        for item in request.evidence.units
    ]
    value = {
        "coverage_digest": request.evidence.coverage_digest,
        "evidence": evidence_identity,
        "inventory_digest": inventory.digest,
        "inventory_entries": inventory_identity,
        "plan_digest": request.work_plan.digest,
        "unit": {
            "dataset_ids": unit.dataset_ids,
            "evidence_ids": unit.evidence_ids,
            "request_digest": _digest_bytes(unit.serialized_request),
            "unit_id": unit.unit_id,
        },
    }
    return _digest_bytes(canonical_json_bytes(value))


def _optional_digest(value: str | None) -> str | None:
    """Hash optional metadata before it enters a persistent identity."""
    if value is None:
        return None
    return _digest_bytes(value.encode("utf-8"))


def _request_identity(
    unit: ResearchResolverObservationUnit,
    input_digest: str,
    policy_digest: str,
) -> str:
    """Build one opaque provider idempotency/status identity."""
    return _digest_bytes(
        canonical_json_bytes(
            {
                "input_digest": input_digest,
                "policy_digest": policy_digest,
                "request_digest": _digest_bytes(unit.serialized_request),
                "unit_id": unit.unit_id,
            }
        )
    )


def _status_payload(raw: object) -> dict[str, Any] | None:
    """Project a successful status response to the strict DTO envelope."""
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if status is None:
        return raw
    if (
        not isinstance(status, str)
        or status.casefold() not in _SUCCESS_STATUSES
    ):
        return None
    if isinstance(raw.get("observations"), list):
        return {"observations": raw["observations"]}
    for key in ("result", "response", "output", "payload"):
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            return candidate
    return None


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
    """Reject reconciliation when a planned evidence unit did not complete."""
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
        confidences: list[ResearchConfidence] = [
            _as_confidence(item.confidence) for item in observations
        ]
        reduced_confidence: ResearchConfidence = min(
            confidences, key=_confidence_rank
        )
        _validate_claim(unique[0], reduced_confidence, max_description_chars)
        return unique[0], reduced_confidence
    description = (
        "Supported facts: "
        + "; ".join(unique)
        + ". Missing or conflicting facts: observations disagree."
    )
    confidence: ResearchConfidence = "low"
    _validate_claim(description, confidence, max_description_chars)
    return description, confidence


def _as_confidence(value: str) -> ResearchConfidence:
    """Narrow a Pydantic confidence value for static type checkers."""
    if value == "high":
        return "high"
    if value == "medium":
        return "medium"
    if value == "low":
        return "low"
    raise _ResolutionContractError


def _confidence_rank(value: ResearchConfidence) -> int:
    """Return the deterministic conservative confidence rank."""
    return _CONFIDENCE_RANK[value]


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
