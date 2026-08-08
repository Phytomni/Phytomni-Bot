# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Typed projections used by the private Research input store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class _ResearchWorkUnitIdentity:
    """Identity portion of a private coordinator work record."""

    unit_id: str
    run_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class _ResearchWorkUnitLease(_ResearchWorkUnitIdentity):
    """Lease and digest fields for a private work unit."""

    state: str
    input_digest: str
    policy_digest: str
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempt: int
    revision: int


@dataclass(frozen=True, slots=True)
class _ResearchWorkUnitProvider(_ResearchWorkUnitLease):
    """Provider identity fields for a private work unit."""

    provider_request_digest: str | None = None
    provider_idempotency_digest: str | None = None
    execution_fingerprint: str | None = None
    evidence_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchWorkUnitRecord(_ResearchWorkUnitProvider):
    """Lease-safe private work-unit projection."""

    output: dict[str, Any] | None = None
    failure_code: str | None = None
    failure_retryable: bool | None = None
    sent_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResearchAdmissionReservation:
    """Atomic admission result before a post-commit worker launch."""

    run_id: str
    replay: bool
    status: str


__all__ = ["ResearchAdmissionReservation", "ResearchWorkUnitRecord"]
