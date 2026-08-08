# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Static markers used by the strict Research description contract."""

import hashlib
from typing import Any, Protocol

from .input_contracts import ResearchConfidence
from .recovery_support import ResearchWorkBinding
from .resolver_policy import canonical_json_bytes


class ResearchWorkRepository(Protocol):
    """Storage boundary for validated work output and sent transitions."""

    def load_validated_output(
        self,
        unit_id: str,
        input_digest: str,
        policy_digest: str,
        *bindings: str,
    ) -> dict[str, Any] | None:
        """Load output bound to the exact request and optional digests."""
        raise NotImplementedError

    def mark_sent(
        self,
        unit_id: str,
        lease_owner: str,
        expected_revision: int,
        **options: object,
    ) -> int | None:
        """Commit the sent state immediately before external invocation."""
        raise NotImplementedError

    def settle_validated(
        self,
        unit_id: str,
        lease_owner: str,
        expected_revision: int,
        output: dict[str, Any],
        **options: object,
    ) -> bool:
        """CAS-settle one validated provider result."""
        raise NotImplementedError


SUPPORTED_MARKERS = (
    "supported facts",
    "known facts",
    "observed",
    "evidence shows",
    "present",
    "available",
)
UNCERTAINTY_MARKERS = (
    "missing",
    "conflict",
    "conflicting",
    "ambiguous",
    "uncertain",
    "not established",
    "cannot distinguish",
    "unknown",
)
CONFIDENCE_RANK: dict[ResearchConfidence, int] = {
    "high": 2,
    "medium": 1,
    "low": 0,
}
SUCCESS_STATUSES = frozenset({"complete", "completed", "success", "succeeded"})


def resolver_request_binding(
    request: Any,
    unit: Any,
    input_digest: str,
    policy_digest: str | None = None,
) -> ResearchWorkBinding:
    """Compute the complete current binding before durable execution."""
    current_policy = policy_digest or request.policy.fingerprint()
    evidence_digest = _resolver_evidence_digest(request, unit)
    execution_fingerprint = _digest(
        {
            "evidence_digest": evidence_digest,
            "input_digest": input_digest,
            "plan_digest": request.work_plan.digest,
            "policy_digest": current_policy,
            "provider_identity": request.policy.provider_identity,
            "schema_version": "research-resolver-execution-v1",
            "unit_id": unit.unit_id,
        }
    )
    provider_request_digest = _digest(
        {
            "evidence_digest": evidence_digest,
            "execution_fingerprint": execution_fingerprint,
            "input_digest": input_digest,
            "policy_digest": current_policy,
            "provider_identity": request.policy.provider_identity,
            "request_digest": _digest(unit.serialized_request),
            "schema_version": "research-resolver-provider-v1",
            "unit_id": unit.unit_id,
        }
    )
    return ResearchWorkBinding(
        input_digest=input_digest,
        policy_digest=current_policy,
        execution_fingerprint=execution_fingerprint,
        evidence_digest=evidence_digest,
        provider_request_digest=provider_request_digest,
    )


def _resolver_evidence_digest(request: Any, unit: Any) -> str:
    """Bind the unit to the exact immutable evidence members it consumes."""
    selected = tuple(
        {
            "content_digest": evidence.content_digest,
            "dataset_ids": evidence.dataset_ids,
            "evidence_id": evidence.evidence_id,
            "source_kind": evidence.source_kind,
            "source_ordinal": evidence.source_ordinal,
        }
        for evidence in request.evidence.units
        if evidence.evidence_id in unit.evidence_ids
    )
    return _digest(
        {
            "coverage_digest": request.evidence.coverage_digest,
            "evidence": selected,
            "evidence_ids": unit.evidence_ids,
            "schema_version": "research-resolver-evidence-v1",
            "unit_id": unit.unit_id,
        }
    )


def _digest(value: object) -> str:
    """Return a stable digest for a canonical resolver binding."""
    payload = (
        value if isinstance(value, bytes) else canonical_json_bytes(value)
    )
    return hashlib.sha256(payload).hexdigest()
