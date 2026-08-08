# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Static markers used by the strict Research description contract."""

from typing import Any, Protocol

from .input_contracts import ResearchConfidence


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
