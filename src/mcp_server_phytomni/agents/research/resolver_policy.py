# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Versioned, lossless request planning for Research resolution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from .document_evidence import ExtractedResearchEvidence, ResearchEvidenceUnit
from .input_contracts import SourceSpan, TokenEstimator
from .input_inventory import ResearchInputInventory

__all__ = [
    "ResearchResolverObservationUnit",
    "ResearchResolverPolicy",
    "ResearchResolverPolicyError",
    "ResearchResolverWorkPlan",
    "canonical_json_bytes",
    "plan_resolver_work",
    "subdivide_verified_context_rejection",
]

_POLICY_FIELDS = (
    "schema_version",
    "model_id",
    "context_token_limit",
    "output_token_reserve",
    "prompt_token_overhead",
    "schema_token_overhead",
    "safety_margin_tokens",
    "max_serialized_request_bytes",
    "max_description_chars",
    "overlap_chars",
    "provider_identity",
    "provider_idempotency_supported",
    "provider_status_query_supported",
)


class ResearchResolverPolicyError(ValueError):
    """A safe local planning failure that never contains evidence text."""


@dataclass(frozen=True, slots=True)
class ResearchResolverPolicy:
    """All semantic controls that affect deterministic resolver requests."""

    schema_version: int
    model_id: str
    context_token_limit: int
    output_token_reserve: int
    prompt_token_overhead: int
    schema_token_overhead: int
    safety_margin_tokens: int
    max_serialized_request_bytes: int
    max_description_chars: int
    overlap_chars: int
    provider_identity: str
    provider_idempotency_supported: bool
    provider_status_query_supported: bool

    def fingerprint(self) -> str:
        """Hash every policy semantic field in stable versioned order."""
        values = [(field, getattr(self, field)) for field in _POLICY_FIELDS]
        return _sha256(
            canonical_json_bytes(["research-resolver-policy", values])
        )


@dataclass(frozen=True, slots=True)
class ResearchResolverObservationUnit:
    """One opaque, exactly budgeted resolver-provider request."""

    unit_id: str
    evidence_ids: tuple[str, ...]
    dataset_ids: tuple[str, ...]
    serialized_request: bytes
    serialized_bytes: int
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class ResearchResolverWorkPlan:
    """Immutable all-evidence request plan bound to one policy fingerprint."""

    observation_units: tuple[ResearchResolverObservationUnit, ...]
    required_evidence_ids: tuple[str, ...]
    policy_fingerprint: str
    digest: str


@dataclass(frozen=True, slots=True)
class _Fragment:
    """One source-preserving evidence fragment before request serialization."""

    evidence_id: str
    dataset_ids: tuple[str, ...]
    source_kind: str
    source_ordinal: int
    source_span: SourceSpan | None
    text: str
    overlap_chars: int = 0


def canonical_json_bytes(value: object) -> bytes:
    """Return canonical UTF-8 JSON used for both hashing and byte budgets."""
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def plan_resolver_work(
    evidence: ExtractedResearchEvidence,
    inventory: ResearchInputInventory,
    policy: ResearchResolverPolicy,
    estimator: TokenEstimator,
) -> ResearchResolverWorkPlan:
    """Cover every evidence item in requests satisfying both hard budgets."""
    _validate_policy(policy)
    required = tuple(unit.evidence_id for unit in evidence.units)
    if len(required) != len(set(required)):
        raise ResearchResolverPolicyError("Research evidence IDs are invalid.")
    fingerprint = policy.fingerprint()
    units: list[ResearchResolverObservationUnit] = []
    for evidence_unit in evidence.units:
        fragments = _fit_evidence_unit(
            evidence_unit,
            policy,
            estimator,
            fingerprint,
            inventory.digest,
            tuple(entry.dataset_id for entry in inventory.entries),
        )
        units.extend(
            _observation(
                fragment,
                f"resolver_{len(units) + 1:03d}",
                policy,
                estimator,
                fingerprint,
                inventory.digest,
                tuple(entry.dataset_id for entry in inventory.entries),
                estimated_tokens=tokens,
            )
            for fragment, tokens in fragments
        )
    return _work_plan(tuple(units), required, fingerprint)


def subdivide_verified_context_rejection(
    plan: ResearchResolverWorkPlan,
    unit_id: str,
    policy: ResearchResolverPolicy,
    estimator: TokenEstimator,
    *,
    verified: bool,
) -> ResearchResolverWorkPlan:
    """Replace a verified limit rejection with smaller children."""
    if not verified:
        raise ResearchResolverPolicyError(
            "Provider context limit was not verified."
        )
    _validate_policy(policy)
    if plan.policy_fingerprint != policy.fingerprint():
        raise ResearchResolverPolicyError(
            "Resolver policy changed before retry."
        )
    parent = next(
        (unit for unit in plan.observation_units if unit.unit_id == unit_id),
        None,
    )
    if parent is None:
        raise ResearchResolverPolicyError("Resolver work unit is unavailable.")
    fragments = _fragments_from_request(parent.serialized_request)
    inventory_digest, group_members = _request_context(
        parent.serialized_request
    )
    if len(fragments) != 1 or len(fragments[0].text) < 2:
        raise ResearchResolverPolicyError(
            "Resolver work unit cannot be subdivided."
        )
    left, right = _split_fragment(fragments[0], policy.overlap_chars)
    children = (
        _observation(
            left,
            f"{unit_id}.1",
            policy,
            estimator,
            plan.policy_fingerprint,
            inventory_digest,
            group_members,
        ),
        _observation(
            right,
            f"{unit_id}.2",
            policy,
            estimator,
            plan.policy_fingerprint,
            inventory_digest,
            group_members,
        ),
    )
    if any(
        child.serialized_bytes >= parent.serialized_bytes for child in children
    ):
        raise ResearchResolverPolicyError(
            "Resolver work unit cannot be reduced."
        )
    replacement = tuple(
        child
        for unit in plan.observation_units
        for child in (children if unit == parent else (unit,))
    )
    return _work_plan(
        replacement, plan.required_evidence_ids, plan.policy_fingerprint
    )


def _validate_policy(policy: ResearchResolverPolicy) -> None:
    """Reject impossible local budgets before observing any evidence text."""
    integer_values = (
        policy.schema_version,
        policy.context_token_limit,
        policy.output_token_reserve,
        policy.prompt_token_overhead,
        policy.schema_token_overhead,
        policy.safety_margin_tokens,
        policy.max_serialized_request_bytes,
        policy.max_description_chars,
        policy.overlap_chars,
    )
    if (
        not policy.model_id
        or not policy.provider_identity
        or any(value < 0 for value in integer_values)
        or policy.schema_version < 1
        or policy.max_serialized_request_bytes < 1
        or _token_budget(policy) < 1
    ):
        raise ResearchResolverPolicyError(
            "Research resolver policy is invalid."
        )


def _token_budget(policy: ResearchResolverPolicy) -> int:
    """Return the request token capacity after all deterministic reserves."""
    return policy.context_token_limit - sum(
        (
            policy.output_token_reserve,
            policy.prompt_token_overhead,
            policy.schema_token_overhead,
            policy.safety_margin_tokens,
        )
    )


def _fit_evidence_unit(
    evidence: ResearchEvidenceUnit,
    policy: ResearchResolverPolicy,
    estimator: TokenEstimator,
    fingerprint: str,
    inventory_digest: str,
    group_members: tuple[str, ...],
) -> tuple[tuple[_Fragment, int], ...]:
    """Split one unit while retaining every text suffix and source boundary."""
    pending = [_fragment_from_evidence(evidence)]
    fitted: list[tuple[_Fragment, int]] = []
    while pending:
        fragment = pending.pop(0)
        serialized = _serialized_payload(
            fragment,
            policy,
            fingerprint,
            inventory_digest,
            group_members,
        )
        tokens = estimator.estimate(serialized)
        if (
            len(serialized) <= policy.max_serialized_request_bytes
            and tokens <= _token_budget(policy)
            and len(fragment.text) <= policy.max_description_chars
        ):
            fitted.append((fragment, tokens))
            continue
        if len(fragment.text) < 2:
            raise ResearchResolverPolicyError(
                "Research evidence exceeds request budget."
            )
        left, right = _split_fragment(fragment, policy.overlap_chars)
        pending[0:0] = [left, right]
    return tuple(fitted)


def _fragment_from_evidence(evidence: ResearchEvidenceUnit) -> _Fragment:
    """Retain only opaque source identity, membership, and transient text."""
    return _Fragment(
        evidence_id=evidence.evidence_id,
        dataset_ids=evidence.dataset_ids,
        source_kind=evidence.source_kind,
        source_ordinal=evidence.source_ordinal,
        source_span=evidence.source_span,
        text=evidence.text,
    )


def _observation(
    fragment: _Fragment,
    unit_id: str,
    policy: ResearchResolverPolicy,
    estimator: TokenEstimator,
    fingerprint: str,
    inventory_digest: str,
    group_members: tuple[str, ...],
    *,
    estimated_tokens: int | None = None,
) -> ResearchResolverObservationUnit:
    """Build one already-validated immutable provider observation unit."""
    serialized = _serialized_payload(
        fragment,
        policy,
        fingerprint,
        inventory_digest,
        group_members,
    )
    tokens = (
        estimator.estimate(serialized)
        if estimated_tokens is None
        else estimated_tokens
    )
    if len(
        serialized
    ) > policy.max_serialized_request_bytes or tokens > _token_budget(policy):
        raise ResearchResolverPolicyError(
            "Research evidence exceeds request budget."
        )
    return ResearchResolverObservationUnit(
        unit_id=unit_id,
        evidence_ids=(fragment.evidence_id,),
        dataset_ids=fragment.dataset_ids,
        serialized_request=serialized,
        serialized_bytes=len(serialized),
        estimated_tokens=tokens,
    )


def _serialized_payload(
    fragment: _Fragment,
    policy: ResearchResolverPolicy,
    fingerprint: str,
    inventory_digest: str,
    group_members: tuple[str, ...],
) -> bytes:
    """Serialize the provider-safe source fragment and policy binding."""
    return canonical_json_bytes(
        {
            "evidence": [
                {
                    "dataset_ids": fragment.dataset_ids,
                    "evidence_id": fragment.evidence_id,
                    "overlap_chars": fragment.overlap_chars,
                    "source_kind": fragment.source_kind,
                    "source_ordinal": fragment.source_ordinal,
                    "source_span": _span(fragment.source_span),
                    "text": fragment.text,
                }
            ],
            "model_id": policy.model_id,
            "inventory_digest": inventory_digest,
            "group_members": group_members,
            "schema_version": policy.schema_version,
        }
    )


def _span(value: SourceSpan | None) -> dict[str, object] | None:
    """Return source coordinates without serializing original-query text."""
    if value is None:
        return None
    return {"end": value.end, "grammar": value.grammar, "start": value.start}


def _split_fragment(
    fragment: _Fragment, overlap: int
) -> tuple[_Fragment, _Fragment]:
    """Split at paragraph or word boundaries without dropping a suffix."""
    cut = _split_index(fragment.text)
    if cut < 1 or cut >= len(fragment.text):
        raise ResearchResolverPolicyError(
            "Research evidence cannot be partitioned."
        )
    actual_overlap = min(max(overlap, 0), cut - 1)
    return (
        replace(
            fragment,
            text=fragment.text[:cut],
            overlap_chars=fragment.overlap_chars,
        ),
        replace(
            fragment,
            text=fragment.text[cut - actual_overlap :],  # noqa: E203
            overlap_chars=actual_overlap,
        ),
    )


def _split_index(text: str) -> int:
    """Prefer a recorded paragraph or word boundary nearest the midpoint."""
    midpoint = len(text) // 2
    candidates = [
        index + marker_length
        for marker, marker_length in (("\n\n", 2), (" ", 1), ("\n", 1))
        for index in _all_indices(text, marker)
        if 0 < index + marker_length < len(text)
    ]
    if candidates:
        return min(candidates, key=lambda index: abs(index - midpoint))
    return midpoint


def _all_indices(text: str, marker: str) -> tuple[int, ...]:
    """Return every non-overlapping marker position in source order."""
    positions: list[int] = []
    start = 0
    while (index := text.find(marker, start)) >= 0:
        positions.append(index)
        start = index + len(marker)
    return tuple(positions)


def _fragments_from_request(serialized: bytes) -> tuple[_Fragment, ...]:
    """Recover private fragment data from canonical request bytes."""
    try:
        raw = json.loads(serialized)
        evidence = raw["evidence"]
        if not isinstance(evidence, list):
            raise TypeError
        return tuple(_fragment_from_payload(item) for item in evidence)
    except (KeyError, TypeError, ValueError) as error:
        raise ResearchResolverPolicyError(
            "Resolver work unit is invalid."
        ) from error


def _request_context(serialized: bytes) -> tuple[str, tuple[str, ...]]:
    """Read the opaque inventory binding from a self-produced request."""
    try:
        raw = json.loads(serialized)
        digest = raw["inventory_digest"]
        members = raw["group_members"]
        if not isinstance(digest, str) or not isinstance(members, list):
            raise TypeError
        if any(not isinstance(member, str) for member in members):
            raise TypeError
        return digest, tuple(members)
    except (KeyError, TypeError, ValueError) as error:
        raise ResearchResolverPolicyError(
            "Resolver work unit is invalid."
        ) from error


def _fragment_from_payload(value: Any) -> _Fragment:
    """Validate one self-produced payload without exposing malformed text."""
    span_value = value.get("source_span")
    span = None if span_value is None else SourceSpan(**span_value)
    return _Fragment(
        evidence_id=value["evidence_id"],
        dataset_ids=tuple(value["dataset_ids"]),
        source_kind=value["source_kind"],
        source_ordinal=value["source_ordinal"],
        source_span=span,
        text=value["text"],
        overlap_chars=value["overlap_chars"],
    )


def _work_plan(
    units: tuple[ResearchResolverObservationUnit, ...],
    required: tuple[str, ...],
    fingerprint: str,
) -> ResearchResolverWorkPlan:
    """Hash immutable request identities and required all-evidence coverage."""
    digest = _sha256(
        canonical_json_bytes(
            {
                "policy_fingerprint": fingerprint,
                "required_evidence_ids": required,
                "units": [
                    {
                        "evidence_ids": unit.evidence_ids,
                        "request_digest": _sha256(unit.serialized_request),
                        "unit_id": unit.unit_id,
                    }
                    for unit in units
                ],
            }
        )
    )
    return ResearchResolverWorkPlan(units, required, fingerprint, digest)


def _sha256(value: bytes) -> str:
    """Return a stable digest for canonical request or policy bytes."""
    return hashlib.sha256(value).hexdigest()
