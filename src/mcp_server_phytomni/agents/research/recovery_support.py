# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Small public recovery adapters kept outside the execution module."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class ResearchWorkBinding:
    """Expected resolver identity for one durable work-unit row."""

    input_digest: str
    policy_digest: str
    execution_fingerprint: str
    evidence_digest: str
    provider_request_digest: str | None = None


def bindings_match(
    record: ResearchWorkUnitRecord | None,
    expected: ResearchWorkBinding,
) -> bool:
    """Compare current resolver bindings with the durable work row."""
    if record is None:
        return False
    if (
        record.input_digest != expected.input_digest
        or record.policy_digest != expected.policy_digest
        or record.execution_fingerprint != expected.execution_fingerprint
        or record.evidence_digest != expected.evidence_digest
    ):
        return False
    return (
        expected.provider_request_digest is None
        or record.provider_request_digest == expected.provider_request_digest
    )


def selected_output_binding(
    record: ResearchWorkUnitRecord | None,
    expected: ResearchWorkBinding | None = None,
) -> ResearchWorkBinding | None:
    """Return the expected binding only when the durable row agrees."""
    if record is None or (
        expected is not None and not bindings_match(record, expected)
    ):
        return None
    binding = expected or ResearchWorkBinding(
        record.input_digest,
        record.policy_digest,
        record.execution_fingerprint or "",
        record.evidence_digest or "",
        record.provider_request_digest,
    )
    if not binding.execution_fingerprint or not binding.evidence_digest:
        return None
    return binding


async def execute_resolver_work(
    executor: Any,
    repository: Any,
    unit_id: str,
    binding: ResearchWorkBinding,
    lease_owner: str,
) -> tuple[str, dict[str, Any] | None]:
    """Project a durable disposition without provider re-invocation."""
    try:
        disposition = await executor.execute(
            unit_id, lease_owner, expected_binding=binding
        )
        state = getattr(disposition, "state", None)
        if state == "terminal_failed":
            code = getattr(disposition, "failure_code", None)
            return (
                ("failed", None)
                if code == "research_input_resolution_failed"
                else ("unavailable", None)
            )
        if state not in {"reconciled", "reused"}:
            return "unavailable", None
        loader = getattr(executor, "load_validated_output", None)
        if callable(loader):
            cached = await _maybe_await(loader(unit_id, binding))
        else:
            cached = load_output_for_binding(repository, unit_id, binding)
    except _recovery_failures:
        return "unavailable", None
    return (
        ("ok", cached) if isinstance(cached, dict) else ("unavailable", None)
    )


def load_output_for_binding(
    repository: Any, unit_id: str, binding: ResearchWorkBinding
) -> Any:
    """Read one output through the exact four durable resolver bindings."""
    return repository.load_validated_output(
        unit_id,
        binding.input_digest,
        binding.policy_digest,
        binding.execution_fingerprint,
        binding.evidence_digest,
    )


async def _maybe_await(value: Any) -> Any:
    """Await a callback result when the injected seam is asynchronous."""
    return await value if inspect.isawaitable(value) else value


class ResearchContextLengthRejectedError(RuntimeError):
    """Provider proof that a request exceeded its context limit."""

    def __init__(self, *, verified: bool) -> None:
        super().__init__("research context limit")
        self.verified = verified


ResearchContextLengthRejected = ResearchContextLengthRejectedError
_recovery_failures: tuple[type[Exception], ...] = (Exception,)
_SERVICES: list[Any] = []


@dataclass(frozen=True, slots=True)
class ResearchGrantRevocation:
    """Opaque durable grant-revocation request for one cancelled parent."""

    parent_run_id: str
    execution_fingerprint: str
    grant_ids: tuple[str, ...]


GrantRevoke = Callable[[ResearchGrantRevocation], object]
_ACTIVE_RESEARCH_TASKS: dict[str, set[asyncio.Task[Any]]] = {}
_GRANT_REVOKERS: dict[str, GrantRevoke | None] = {}


def register_grant_revoke(store: Any, callback: GrantRevoke | None) -> None:
    """Bind one process-local revoke callback."""
    key = getattr(store, "db_path", "")
    if key:
        _GRANT_REVOKERS[key] = callback


def register_research_task(
    run_id: str, task: asyncio.Task[Any] | None = None
) -> None:
    """Track one in-process coordinator task behind a durable run id."""
    current = task or asyncio.current_task()
    if current is not None and run_id:
        _ACTIVE_RESEARCH_TASKS.setdefault(run_id, set()).add(current)


def unregister_research_task(
    run_id: str, task: asyncio.Task[Any] | None = None
) -> None:
    """Remove one completed coordinator task from the process-local index."""
    current = task or asyncio.current_task()
    tasks = _ACTIVE_RESEARCH_TASKS.get(run_id)
    if tasks is None or current is None:
        return
    tasks.discard(current)
    if not tasks:
        _ACTIVE_RESEARCH_TASKS.pop(run_id, None)


def cancel_registered_research_tasks(run_id: str) -> int:
    """Cancel owned in-process work after the durable CAS has committed."""
    current = asyncio.current_task()
    cancelled = 0
    for task in tuple(_ACTIVE_RESEARCH_TASKS.get(run_id, ())):
        if task is not current and not task.done() and task.cancel():
            cancelled += 1
    return cancelled


async def revoke_registered_research_run(
    run_id: str, now: datetime | None = None
) -> None:
    """Run post-commit revoke hooks for one cancelled Research parent."""
    for service in tuple(_SERVICES):
        await recover_pending_grants(service, now or datetime.now(), run_id)


async def recover_pending_grants(
    service: Any, now: datetime, run_id: str | None = None
) -> None:
    """Retry bounded grant revocation without reopening a cancelled parent."""
    callback = _GRANT_REVOKERS.get(getattr(service.store, "db_path", ""))
    if callback is None:
        return
    try:
        pending = service.store.pending_grant_revocations(
            service.recovery_limit
        )
    except _recovery_failures:
        return
    for parent, fingerprint, grant_ids in pending:
        if run_id is not None and parent != run_id:
            continue
        request = ResearchGrantRevocation(parent, fingerprint, grant_ids)
        try:
            result = callback(request)
            if inspect.isawaitable(result):
                result = await result
            if result is not False:
                service.store.mark_grant_revoked(
                    parent, fingerprint, grant_ids, now
                )
        except _recovery_failures:
            continue


def register_recovery_service(service: Any) -> None:
    """Register one process-local service for bounded lifecycle passes."""
    if service not in _SERVICES:
        _SERVICES.append(service)


async def recover_registered_startup() -> None:
    """Run one bounded startup pass for registered services."""
    for service in tuple(_SERVICES):
        try:
            await service.recover_startup()
        except _recovery_failures:
            continue


async def recover_registered_request(now: datetime | None = None) -> None:
    """Run one bounded request pass for registered services."""
    for service in tuple(_SERVICES):
        try:
            await service.recover_request(now)
        except _recovery_failures:
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
