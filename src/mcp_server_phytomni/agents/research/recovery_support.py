# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Small public recovery adapters kept outside the execution module."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from typing import Any

from ...runtime.research_input_types import ResearchWorkUnitRecord
from .input_contracts import TokenEstimator
from .resolver_policy import (
    ResearchResolverPolicy,
    ResearchResolverWorkPlan,
    subdivide_verified_context_rejection,
)

ContextSubdivider = Callable[
    [ResearchWorkUnitRecord], Sequence[ResearchWorkUnitRecord]
]


class ResearchContextLengthRejectedError(RuntimeError):
    """Provider proof that a request exceeded its context limit."""

    def __init__(self, *, verified: bool) -> None:
        super().__init__("research context limit")
        self.verified = verified


ResearchContextLengthRejected = ResearchContextLengthRejectedError
_RECOVERY_FAILURES: tuple[type[Exception], ...] = (Exception,)
_SERVICES: list[Any] = []


def register_recovery_service(service: Any) -> None:
    """Register one process-local service for bounded lifecycle passes."""
    if service not in _SERVICES:
        _SERVICES.append(service)


async def recover_registered_startup() -> None:
    """Run one bounded startup pass for registered services."""
    for service in tuple(_SERVICES):
        try:
            await service.recover_startup()
        except _RECOVERY_FAILURES:
            continue


async def recover_registered_request(now: datetime | None = None) -> None:
    """Run one bounded request pass for registered services."""
    for service in tuple(_SERVICES):
        try:
            await service.recover_request(now)
        except _RECOVERY_FAILURES:
            continue


def build_context_subdivider(
    plan: ResearchResolverWorkPlan,
    policy: ResearchResolverPolicy,
    estimator: TokenEstimator,
) -> ContextSubdivider:
    """Build deterministic pending children for one verified limit error."""

    def subdivide(
        record: ResearchWorkUnitRecord,
    ) -> tuple[ResearchWorkUnitRecord, ...]:
        child_plan = subdivide_verified_context_rejection(
            plan, record.unit_id, policy, estimator, verified=True
        )
        return tuple(
            _child_record(record, unit)
            for unit in child_plan.observation_units
            if unit.unit_id.startswith(record.unit_id + ".")
        )

    return subdivide


def _child_record(
    parent: ResearchWorkUnitRecord, unit: object
) -> ResearchWorkUnitRecord:
    """Project a policy child onto the parent's durable safety bindings."""
    return replace(
        parent,
        unit_id=getattr(unit, "unit_id"),
        state="pending",
        input_digest=sha256(getattr(unit, "serialized_request")).hexdigest(),
        lease_owner=None,
        lease_expires_at=None,
        attempt=0,
        revision=0,
        provider_request_digest=None,
        provider_idempotency_digest=None,
        output=None,
        failure_code=None,
        failure_retryable=None,
        sent_at=None,
        completed_at=None,
    )
