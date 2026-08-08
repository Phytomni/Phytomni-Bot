# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Lease-safe execution and bounded recovery for Research work units.
The store is deliberately used as a short transaction boundary.  In
particular, provider preparation, invocation, and status queries happen
after the claim or state transition has committed; a provider call is never
made while an SQLite transaction is held open.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

from ...runtime.research_input_store import (
    RESEARCH_HEARTBEAT_INTERVAL,
    RESEARCH_RECOVERY_BATCH_SIZE,
    ResearchInputStore,
    ResearchWorkUnitRecord,
    _to_record,
)
from ...runtime.sqlite import sqlite_transaction
from . import recovery_support as _recovery_support
from .dispatch_outbox import recover_dispatch_outbox
from .input_contracts import ResearchErrorCode
from .recovery_support import (
    ContextSubdivider,
    ResearchContextLengthRejected,
    ResearchContextLengthRejectedError,
    ResearchWorkBinding,
    build_context_subdivider,
    recover_registered_request,
    recover_registered_startup,
    register_recovery_service,
    selected_output_binding,
)

_execute_resolver_work = _recovery_support.execute_resolver_work
_bindings_match = _recovery_support.bindings_match

__all__ = [
    "ResearchRecoveryService",
    "ResearchContextLengthRejected",
    "ResearchPreAcceptanceRejected",
    "ResearchPreAcceptanceRejectedError",
    "ResearchWorkBinding",
    "ResearchWorkExecutor",
    "ResearchWorkProvider",
    "ResearchRecoverySummary",
    "WorkDisposition",
    "build_context_subdivider",
    "recover_registered_request",
    "recover_registered_startup",
]
WorkDispositionState = Literal[
    "reclaimed", "reconciled", "reused", "ambiguous", "terminal_failed"
]


@dataclass(frozen=True, slots=True)
class WorkDisposition:
    """Safe, content-free outcome for one durable work-unit operation."""

    unit_id: str
    state: WorkDispositionState
    failure_code: ResearchErrorCode | None


@dataclass(frozen=True, slots=True)
class ResearchRecoverySummary:
    """Bounded count-only result of one recovery scan."""

    reclaimed: int
    reconciled: int
    reused: int
    ambiguous: int
    terminal_failed: int


class ResearchWorkProvider(Protocol):
    """External provider boundary for one opaque work-unit request."""

    async def invoke(self, work_input: object, policy: object) -> object:
        """Submit one already-prepared request to the provider."""
        raise NotImplementedError

    async def query(self, request_identity: str) -> object | None:
        """Query the original provider request identity, when supported."""
        raise NotImplementedError


class ResearchPreAcceptanceRejectedError(RuntimeError):
    """Provider proof that a marked request was rejected before acceptance."""


ResearchPreAcceptanceRejected = ResearchPreAcceptanceRejectedError
WorkInputFactory = Callable[[ResearchWorkUnitRecord], object]
PolicyFactory = Callable[[ResearchWorkUnitRecord], object]
ResultValidator = Callable[[object, ResearchWorkUnitRecord], object]
Clock = Callable[[], datetime]
_RESEARCH_FAILED: ResearchErrorCode = "research_input_resolution_failed"
_RESEARCH_UNAVAILABLE: ResearchErrorCode = (
    "research_input_resolution_" "unavailable"
)
_RESEARCH_CANCEL_CONFLICT: ResearchErrorCode = "research_cancel_conflict"
_TERMINAL_PARENT_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_SUCCESS_QUERY_STATUSES = {"complete", "completed", "success", "succeeded"}
_RECOVERY_FAILURES: tuple[type[Exception], ...] = (Exception,)


_WORK_SELECT = "SELECT * FROM research_work_units WHERE unit_id = ?"
_PARENT_LIVE = (
    "EXISTS (SELECT 1 FROM runs WHERE runs.run_id = "
    "research_work_units.run_id AND runs.status NOT IN "
    "('succeeded', 'failed', 'cancelled')) AND NOT EXISTS (SELECT 1 FROM "
    "research_input_resolutions WHERE research_input_resolutions.run_id = "
    "research_work_units.run_id AND COALESCE(cancel_requested, 0) <> 0)"
)


def _get_work_unit(
    store: ResearchInputStore, unit_id: str
) -> ResearchWorkUnitRecord | None:
    """Read one work row in a short transaction owned by recovery."""
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(_WORK_SELECT, (unit_id,)).fetchone()
    return None if row is None else _to_record(row)


def _list_recovery_candidates(
    store: ResearchInputStore, now: datetime, limit: int
) -> list[ResearchWorkUnitRecord]:
    """Read a bounded set of expired/pending rows without holding a lease."""
    if limit <= 0:
        return []
    now_iso = now.astimezone(UTC).isoformat()
    query = (
        "SELECT * FROM research_work_units WHERE kind <> 'dispatch' AND ("
        "state IN ('pending', 'retryable_failed') OR (state IN "
        "('leased', 'sent') AND lease_expires_at IS NOT NULL AND "
        "lease_expires_at <= ?)) "
        "ORDER BY updated_at, unit_id LIMIT ?"
    )
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, (now_iso, limit)).fetchall()
    return [_to_record(row) for row in rows]


def _claim_sent_for_recovery(
    store: ResearchInputStore,
    unit_id: str,
    lease_owner: str,
    now: datetime,
) -> ResearchWorkUnitRecord | None:
    """Claim one expired sent row for status reconciliation."""
    now_iso = now.astimezone(UTC).isoformat()
    expires = (now + timedelta(seconds=60)).astimezone(UTC).isoformat()
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(_WORK_SELECT, (unit_id,)).fetchone()
        if (
            row is None
            or row["state"] != "sent"
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] > now_iso
        ):
            return None
        cursor = connection.execute(
            "UPDATE research_work_units SET lease_owner = ?, "
            "lease_expires_at = ?, updated_at = ?, "
            "revision = revision + 1 WHERE unit_id = ? AND revision = ? "
            "AND state = 'sent' AND " + _PARENT_LIVE,
            (lease_owner, expires, now_iso, unit_id, row["revision"]),
        )
        if cursor.rowcount != 1:
            return None
        claimed = connection.execute(_WORK_SELECT, (unit_id,)).fetchone()
    return None if claimed is None else _to_record(claimed)


def _parent_status(store: ResearchInputStore, run_id: str) -> str | None:
    """Read a parent run status in a short transaction."""
    with sqlite_transaction(store.db_path) as connection:
        row = connection.execute(
            "SELECT CASE WHEN EXISTS ("
            "SELECT 1 FROM research_input_resolutions "
            "WHERE run_id = ? AND COALESCE(cancel_requested, 0) <> 0) "
            "THEN 'cancelled' ELSE status END FROM runs WHERE run_id = ?",
            (run_id, run_id),
        ).fetchone()
    return None if row is None else cast(str, row[0])


def _load_validated_output(
    store: ResearchInputStore,
    unit_id: str,
    binding_or_input: ResearchWorkBinding | str,
    *args: object,
    **options: object,
) -> dict[str, Any] | None:
    """Read a succeeded output only when every supplied binding matches."""
    if isinstance(binding_or_input, ResearchWorkBinding):
        binding = binding_or_input
    else:
        policy_value = args[0] if args else options.get("policy_digest")
        execution_fingerprint = options.get(
            "execution_fingerprint", args[1] if len(args) > 1 else None
        )
        evidence_digest = options.get(
            "evidence_digest", args[2] if len(args) > 2 else None
        )
        if not isinstance(execution_fingerprint, str) or not isinstance(
            evidence_digest, str
        ):
            return None
        binding = ResearchWorkBinding(
            binding_or_input,
            cast(str, policy_value),
            execution_fingerprint,
            evidence_digest,
        )
    return _recovery_support.load_output_for_binding(store, unit_id, binding)


@dataclass(frozen=True, slots=True)
class _ExecutorOptions:
    """Injected execution hooks kept together for the executor."""

    now: Clock
    result_validator: ResultValidator | None = None
    work_input: WorkInputFactory | None = None
    policy: PolicyFactory | None = None
    heartbeat_interval: timedelta = RESEARCH_HEARTBEAT_INTERVAL
    context_subdivider: ContextSubdivider | None = None

    @classmethod
    def from_kwargs(cls, values: Mapping[str, object]) -> _ExecutorOptions:
        """Build options while retaining the historical keyword surface."""
        allowed = {
            "now",
            "result_validator",
            "work_input",
            "policy",
            "heartbeat_interval",
            "context_subdivider",
        }
        unknown = set(values).difference(allowed)
        if unknown:
            name = sorted(unknown)[0]
            raise TypeError(f"unexpected executor option: {name}")
        now_value = values.get("now")
        clock = _utc_now if now_value is None else cast(Clock, now_value)
        return cls(
            now=clock,
            result_validator=cast(
                ResultValidator | None, values.get("result_validator")
            ),
            work_input=cast(WorkInputFactory | None, values.get("work_input")),
            policy=cast(PolicyFactory | None, values.get("policy")),
            heartbeat_interval=cast(
                timedelta,
                values.get("heartbeat_interval", RESEARCH_HEARTBEAT_INTERVAL),
            ),
            context_subdivider=cast(
                ContextSubdivider | None, values.get("context_subdivider")
            ),
        )


@dataclass(frozen=True, slots=True)
class _CompletionOptions:
    """CAS completion values kept together for the safe completion seam."""

    state: str
    failure_code: ResearchErrorCode
    now: datetime
    failure_retryable: bool = False
    lease_owner: str | None = None
    revision: int | None = None


@dataclass(frozen=True, slots=True)
class _RecoveryOptions:
    """Injected recovery hooks kept together for the recovery service."""

    now: Clock
    result_validator: ResultValidator | None = None
    batch_size: int = RESEARCH_RECOVERY_BATCH_SIZE
    lease_owner: str | None = None

    @classmethod
    def from_kwargs(cls, values: Mapping[str, object]) -> _RecoveryOptions:
        """Build options while retaining the historical keyword surface."""
        allowed = {
            "now",
            "result_validator",
            "batch_size",
            "lease_owner",
        }
        unknown = set(values).difference(allowed)
        if unknown:
            name = sorted(unknown)[0]
            raise TypeError(f"unexpected recovery option: {name}")
        now_value = values.get("now")
        return cls(
            now=_utc_now if now_value is None else cast(Clock, now_value),
            result_validator=cast(
                ResultValidator | None, values.get("result_validator")
            ),
            batch_size=cast(
                int,
                values.get("batch_size", RESEARCH_RECOVERY_BATCH_SIZE),
            ),
            lease_owner=cast(str | None, values.get("lease_owner")),
        )


class _WorkContractError(ValueError):
    """Private sentinel for an invalid local/provider result contract."""


async def _maybe_await(value: object) -> object:
    """Resolve sync or async injected hooks without widening their surface."""
    if inspect.isawaitable(value):
        return await cast(Awaitable[object], value)
    return value


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for default lease operations."""
    return datetime.now(UTC)


def _normalise_output(value: object) -> dict[str, Any]:
    """Require a JSON-serialisable mapping before private persistence."""
    if not isinstance(value, Mapping):
        raise _WorkContractError("provider result must be a mapping")
    output = dict(value)
    try:
        json.dumps(output, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError, OverflowError) as error:
        raise _WorkContractError(
            "provider result is not serialisable"
        ) from error
    return output


def _request_identity(record: ResearchWorkUnitRecord) -> str:
    """Derive one stable provider identity from immutable work bindings."""
    if record.provider_request_digest:
        return record.provider_request_digest
    binding = (
        "research-work-request-v1",
        record.unit_id,
        record.run_id,
        record.kind,
        record.input_digest,
        record.policy_digest,
        record.execution_fingerprint,
        record.evidence_digest,
    )
    encoded = json.dumps(
        binding, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_terminal(status: str | None) -> bool:
    """Return whether a parent status bars any further child work."""
    return status in _TERMINAL_PARENT_STATUSES


def _query_payload(value: object) -> dict[str, Any] | None:
    """Project a provider status response to a bounded result mapping.
    Providers may return the result directly or wrap it in a successful
    status envelope.  Non-success statuses remain unresolved and therefore
    must be classified as ambiguous by the caller.
    """
    if not isinstance(value, Mapping):
        return None
    status = value.get("status")
    if status is not None:
        if not isinstance(status, str) or status.casefold() not in (
            _SUCCESS_QUERY_STATUSES
        ):
            return None
        nested = value.get("result")
        if nested is not None:
            value = nested
    try:
        return _normalise_output(value)
    except _WorkContractError:
        return None


class ResearchWorkExecutor:
    """Execute one Research unit across durable lease boundaries."""

    def __init__(
        self,
        store: ResearchInputStore,
        provider: ResearchWorkProvider,
        *,
        options: _ExecutorOptions | None = None,
        **option_kwargs: object,
    ) -> None:
        if options is not None and option_kwargs:
            raise TypeError("options cannot be combined with option keywords")
        resolved = options or _ExecutorOptions.from_kwargs(option_kwargs)
        self.store = store
        self.provider = provider
        self.options = resolved

    @property
    def heartbeat_enabled(self) -> bool:
        """Return whether this executor will renew its lease."""
        return self.options.heartbeat_interval.total_seconds() > 0

    def load_validated_output(
        self,
        unit_id: str,
        expected_binding: ResearchWorkBinding | None = None,
    ) -> dict[str, Any] | None:
        """Read one output through the executor's complete durable binding."""
        binding = selected_output_binding(
            _get_work_unit(self.store, unit_id), expected_binding
        )
        if binding is None:
            return None
        return _recovery_support.load_output_for_binding(
            self.store, unit_id, binding
        )

    async def execute(
        self,
        unit_id: str,
        lease_owner: str,
        *,
        expected_binding: ResearchWorkBinding | None = None,
    ) -> WorkDisposition:
        """Claim, mark sent, invoke, validate, and CAS-settle one unit."""
        claimed = self._claim_or_reuse(unit_id, lease_owner, expected_binding)
        if isinstance(claimed, WorkDisposition):
            return claimed
        return await self._execute_claimed(
            claimed, lease_owner, expected_binding
        )

    def _claim_or_reuse(
        self,
        unit_id: str,
        lease_owner: str,
        expected_binding: ResearchWorkBinding | None = None,
    ) -> ResearchWorkUnitRecord | WorkDisposition:
        """Return a reusable row, a new claim, or a safe early disposition."""
        if not unit_id or not lease_owner:
            return WorkDisposition(
                unit_id, "terminal_failed", _RESEARCH_FAILED
            )
        record = _get_work_unit(self.store, unit_id)
        if record is None:
            return WorkDisposition(
                unit_id, "terminal_failed", _RESEARCH_FAILED
            )
        if self._has_reusable_output(record, expected_binding):
            return WorkDisposition(unit_id, "reused", None)
        if _is_terminal(_parent_status(self.store, record.run_id)):
            return WorkDisposition(
                unit_id, "terminal_failed", _RESEARCH_CANCEL_CONFLICT
            )
        claimed = self.store.claim_work(
            unit_id, lease_owner, self.options.now()
        )
        if claimed is not None:
            return claimed
        return self._after_claim_failure(unit_id, expected_binding)

    def _after_claim_failure(
        self,
        unit_id: str,
        expected_binding: ResearchWorkBinding | None = None,
    ) -> WorkDisposition:
        """Classify a failed claim using the newest durable row."""
        latest = _get_work_unit(self.store, unit_id)
        if (
            latest is not None
            and expected_binding is not None
            and not _bindings_match(latest, expected_binding)
        ):
            return WorkDisposition(unit_id, "ambiguous", _RESEARCH_UNAVAILABLE)
        if latest is not None and self._has_reusable_output(
            latest, expected_binding
        ):
            return WorkDisposition(unit_id, "reused", None)
        if latest is not None and _is_terminal(
            _parent_status(self.store, latest.run_id)
        ):
            return WorkDisposition(
                unit_id, "terminal_failed", _RESEARCH_CANCEL_CONFLICT
            )
        return WorkDisposition(unit_id, "ambiguous", _RESEARCH_UNAVAILABLE)

    async def _execute_claimed(
        self,
        claimed: ResearchWorkUnitRecord,
        lease_owner: str,
        expected_binding: ResearchWorkBinding | None = None,
    ) -> WorkDisposition:
        """Run preparation and provider I/O under one heartbeat lifecycle."""
        lease = {"revision": claimed.revision, "lost": False}
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(
                claimed.unit_id,
                lease_owner,
                lease,
                stop_heartbeat,
            )
        )
        try:
            prepared = await self._prepare_and_mark_sent(
                claimed, lease_owner, lease, expected_binding
            )
            if isinstance(prepared, WorkDisposition):
                return prepared
            return await self._invoke_and_settle(
                claimed, lease_owner, lease, prepared
            )
        finally:
            stop_heartbeat.set()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _prepare_and_mark_sent(
        self,
        claimed: ResearchWorkUnitRecord,
        lease_owner: str,
        lease: dict[str, int | bool],
        expected_binding: ResearchWorkBinding | None = None,
    ) -> tuple[object, object] | WorkDisposition:
        """Build provider inputs and durably mark the request as sent."""
        try:
            work_input = (
                await _maybe_await(self.options.work_input(claimed))
                if self.options.work_input is not None
                else claimed
            )
            policy = (
                await _maybe_await(self.options.policy(claimed))
                if self.options.policy is not None
                else claimed
            )
        except _RECOVERY_FAILURES:
            self._complete_safely(
                claimed,
                _CompletionOptions(
                    state="terminal_failed",
                    failure_code=_RESEARCH_FAILED,
                    now=self.options.now(),
                ),
            )
            return WorkDisposition(
                claimed.unit_id, "terminal_failed", _RESEARCH_FAILED
            )
        request_identity = (
            expected_binding.provider_request_digest
            if expected_binding is not None
            and expected_binding.provider_request_digest
            else _request_identity(claimed)
        )
        sent_revision = self.store.mark_sent(
            claimed.unit_id,
            lease_owner,
            int(lease["revision"]),
            provider_request_digest=request_identity,
            provider_idempotency_digest=request_identity,
            now=self.options.now(),
        )
        if not isinstance(sent_revision, int) or isinstance(
            sent_revision, bool
        ):
            return self._lost_lease_disposition(
                claimed.unit_id, claimed.run_id
            )
        lease["revision"] = sent_revision
        return work_input, policy

    async def _invoke_and_settle(
        self,
        claimed: ResearchWorkUnitRecord,
        lease_owner: str,
        lease: dict[str, int | bool],
        prepared: tuple[object, object],
    ) -> WorkDisposition:
        """Invoke, validate, and CAS-settle one durably marked request."""
        work_input, policy = prepared
        # Provider I/O occurs only after all preceding store calls commit.
        try:
            raw = await _maybe_await(self.provider.invoke(work_input, policy))
        except ResearchContextLengthRejected as error:
            return self._settle_context_rejection(
                claimed, lease_owner, int(lease["revision"]), error
            )
        except ResearchPreAcceptanceRejected:
            return self._settle_provider_rejection(
                claimed, lease_owner, int(lease["revision"])
            )
        except _RECOVERY_FAILURES:
            # The call may have been accepted.  Leave it sent so recovery can
            # query the same identity; never resubmit automatically.
            return WorkDisposition(
                claimed.unit_id, "ambiguous", _RESEARCH_UNAVAILABLE
            )
        try:
            validated = (
                await _maybe_await(self.options.result_validator(raw, claimed))
                if self.options.result_validator is not None
                else raw
            )
            output = _normalise_output(validated)
        except _RECOVERY_FAILURES:
            return self._settle_invalid_result(
                claimed, lease_owner, int(lease["revision"])
            )
        settled = self.store.settle_validated(
            claimed.unit_id,
            lease_owner,
            int(lease["revision"]),
            output,
            now=self.options.now(),
        )
        if settled:
            return WorkDisposition(claimed.unit_id, "reconciled", None)
        return self._lost_lease_disposition(claimed.unit_id, claimed.run_id)

    def _settle_context_rejection(
        self,
        record: ResearchWorkUnitRecord,
        owner: str,
        revision: int,
        rejection: ResearchContextLengthRejectedError,
    ) -> WorkDisposition:
        """Replace a verified context rejection with children atomically."""
        if not rejection.verified or self.options.context_subdivider is None:
            return WorkDisposition(
                record.unit_id, "ambiguous", _RESEARCH_UNAVAILABLE
            )
        try:
            children = tuple(self.options.context_subdivider(record))
            replaced = self.store.replace_work_unit_with_children(
                replace(record, lease_owner=owner, revision=revision),
                children,
                self.options.now(),
            )
        except _RECOVERY_FAILURES:
            replaced = False
        if replaced:
            return WorkDisposition(
                record.unit_id, "reclaimed", _RESEARCH_UNAVAILABLE
            )
        return self._lost_lease_disposition(record.unit_id, record.run_id)

    def _settle_provider_rejection(
        self, record: ResearchWorkUnitRecord, owner: str, revision: int
    ) -> WorkDisposition:
        """Persist an explicit pre-acceptance rejection as retryable."""
        settled = self._complete_safely(
            record,
            _CompletionOptions(
                state="retryable_failed",
                failure_code=_RESEARCH_UNAVAILABLE,
                now=self.options.now(),
                failure_retryable=True,
                lease_owner=owner,
                revision=revision,
            ),
        )
        if not settled:
            return self._lost_lease_disposition(record.unit_id, record.run_id)
        return WorkDisposition(
            record.unit_id, "reclaimed", _RESEARCH_UNAVAILABLE
        )

    def _settle_invalid_result(
        self, record: ResearchWorkUnitRecord, owner: str, revision: int
    ) -> WorkDisposition:
        """Persist invalid provider content as terminal, never retryable."""
        settled = self._complete_safely(
            record,
            _CompletionOptions(
                state="terminal_failed",
                failure_code=_RESEARCH_FAILED,
                now=self.options.now(),
                lease_owner=owner,
                revision=revision,
            ),
        )
        if not settled:
            return self._lost_lease_disposition(record.unit_id, record.run_id)
        return WorkDisposition(
            record.unit_id, "terminal_failed", _RESEARCH_FAILED
        )

    def _has_reusable_output(
        self,
        record: ResearchWorkUnitRecord,
        expected_binding: ResearchWorkBinding | None = None,
    ) -> bool:
        """Check a cached result against every required row binding."""
        binding = selected_output_binding(record, expected_binding)
        if binding is None:
            return False
        try:
            output = _load_validated_output(
                self.store,
                record.unit_id,
                binding,
            )
        except _RECOVERY_FAILURES:
            return False
        return output is not None

    def _complete_safely(
        self,
        record: ResearchWorkUnitRecord,
        options: _CompletionOptions,
    ) -> bool:
        """Best-effort CAS completion that never leaks storage details."""
        current = record
        if options.lease_owner is not None or options.revision is not None:
            current = replace(
                record,
                lease_owner=(
                    record.lease_owner
                    if options.lease_owner is None
                    else options.lease_owner
                ),
                revision=(
                    record.revision
                    if options.revision is None
                    else options.revision
                ),
            )
        try:
            return self.store.complete_work(
                current,
                options.state,
                options.now,
                failure_code=options.failure_code,
                failure_retryable=options.failure_retryable,
            )
        except _RECOVERY_FAILURES:
            return False

    def _lost_lease_disposition(
        self, unit_id: str, run_id: str
    ) -> WorkDisposition:
        """Classify a failed CAS without exposing a race or SQLite error."""
        if _is_terminal(_parent_status(self.store, run_id)):
            return WorkDisposition(
                unit_id, "terminal_failed", _RESEARCH_CANCEL_CONFLICT
            )
        return WorkDisposition(unit_id, "ambiguous", _RESEARCH_UNAVAILABLE)

    async def _heartbeat_loop(
        self,
        unit_id: str,
        lease_owner: str,
        lease: dict[str, int | bool],
        stop: asyncio.Event,
    ) -> None:
        """Renew a lease independently of provider/extraction I/O."""
        interval = self.options.heartbeat_interval.total_seconds()
        if not self.heartbeat_enabled:
            return
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            if stop.is_set() or bool(lease["lost"]):
                return
            try:
                refreshed = self.store.heartbeat_work(
                    unit_id,
                    lease_owner,
                    int(lease["revision"]),
                    self.options.now(),
                )
            except _RECOVERY_FAILURES:
                lease["lost"] = True
                return
            if refreshed is None:
                lease["lost"] = True
                return
            lease["revision"] = refreshed.revision


class ResearchRecoveryService:
    """Reclaim bounded expired work and reconcile sent calls safely."""

    def __init__(
        self,
        store: ResearchInputStore,
        provider: ResearchWorkProvider,
        *,
        options: _RecoveryOptions | None = None,
        outbox: Any | None = None,
        **option_kwargs: object,
    ) -> None:
        if options is not None and option_kwargs:
            raise TypeError("options cannot be combined with option keywords")
        resolved = options or _RecoveryOptions.from_kwargs(option_kwargs)
        self.store = store
        self.provider = provider
        self.now = resolved.now
        self.result_validator = resolved.result_validator
        self.batch_size = max(0, int(resolved.batch_size))
        self.lease_owner = (
            resolved.lease_owner or f"research-recovery-{uuid.uuid4().hex}"
        )
        self.outbox = outbox
        register_recovery_service(self)

    @property
    def recovery_limit(self) -> int:
        """Return the bounded number of rows processed by one scan."""
        return min(self.batch_size, RESEARCH_RECOVERY_BATCH_SIZE)

    async def recover_startup(self) -> ResearchRecoverySummary:
        """Run one bounded recovery scan using the injected service clock."""
        return await self.recover_once(self.now())

    async def recover_request(
        self, now: datetime | None = None
    ) -> ResearchRecoverySummary:
        """Run one bounded request-triggered recovery scan."""
        return await self.recover_once(now or self.now())

    async def recover_once(self, now: datetime) -> ResearchRecoverySummary:
        """Reclaim only safe leases and reconcile queryable sent calls."""
        counts = {
            "reclaimed": 0,
            "reconciled": 0,
            "reused": 0,
            "ambiguous": 0,
            "terminal_failed": 0,
        }
        try:
            candidates = _list_recovery_candidates(
                self.store, now, self.recovery_limit
            )
        except _RECOVERY_FAILURES:
            return ResearchRecoverySummary(**counts)
        for candidate in candidates:
            try:
                outcome = await self._recover_candidate(candidate, now)
            except _RECOVERY_FAILURES:
                outcome = self._candidate_error_outcome(candidate)
            if outcome is not None:
                counts[outcome] += 1
        if self.outbox is not None:
            try:
                outcomes = await recover_dispatch_outbox(
                    self.outbox,
                    now,
                    self.recovery_limit,
                    self.lease_owner,
                )
            except _RECOVERY_FAILURES:
                outcomes = ()
            counts["reconciled"] += outcomes.count("accepted")
            counts["reconciled"] += outcomes.count("reconciled")
            counts["ambiguous"] += outcomes.count("ambiguous")
            counts["terminal_failed"] += outcomes.count("cancelled")
        return ResearchRecoverySummary(**counts)

    async def _recover_candidate(
        self, candidate: ResearchWorkUnitRecord, now: datetime
    ) -> WorkDispositionState | None:
        """Recover one candidate and return its count-only disposition."""
        if _is_terminal(_parent_status(self.store, candidate.run_id)):
            return "terminal_failed"
        if candidate.state in {"pending", "retryable_failed", "leased"}:
            return self._reclaim_candidate(candidate, now)
        if candidate.state != "sent":
            return None
        return await self._reconcile_sent_candidate(candidate, now)

    def _candidate_error_outcome(
        self, candidate: ResearchWorkUnitRecord
    ) -> WorkDispositionState:
        """Map an isolated candidate error to a content-free count."""
        try:
            if _is_terminal(_parent_status(self.store, candidate.run_id)):
                return "terminal_failed"
        except _RECOVERY_FAILURES:
            pass
        return "ambiguous"

    def _reclaim_candidate(
        self, candidate: ResearchWorkUnitRecord, now: datetime
    ) -> WorkDispositionState | None:
        """Reclaim expired local work without making provider calls."""
        claimed = self.store.claim_work(
            candidate.unit_id, self.lease_owner, now
        )
        if claimed is not None:
            return "reclaimed"
        return self._terminal_outcome(candidate.run_id)

    async def _reconcile_sent_candidate(
        self, candidate: ResearchWorkUnitRecord, now: datetime
    ) -> WorkDispositionState | None:
        """Query and settle one sent request, or close it as ambiguous."""
        claimed = _claim_sent_for_recovery(
            self.store, candidate.unit_id, self.lease_owner, now
        )
        if claimed is None:
            return self._terminal_outcome(candidate.run_id)
        result = await self._query_and_validate(claimed)
        if result is not None:
            settled = self.store.settle_validated(
                claimed.unit_id,
                self.lease_owner,
                claimed.revision,
                result,
                now=now,
            )
            if settled:
                return "reconciled"
            terminal = self._terminal_outcome(candidate.run_id)
            if terminal is not None:
                return terminal
        ambiguous = self._mark_ambiguous(claimed, now)
        if ambiguous:
            return "ambiguous"
        return self._terminal_outcome(candidate.run_id)

    def _terminal_outcome(self, run_id: str) -> WorkDispositionState | None:
        """Return the terminal conflict outcome when the parent is terminal."""
        if _is_terminal(_parent_status(self.store, run_id)):
            return "terminal_failed"
        return None

    async def _query_and_validate(
        self, record: ResearchWorkUnitRecord
    ) -> dict[str, Any] | None:
        """Query the persisted identity, never invoke a replacement call."""
        request_identity = record.provider_request_digest
        query = getattr(self.provider, "query", None)
        if not request_identity or not callable(query):
            return None
        try:
            queried = await _maybe_await(query(request_identity))
        except _RECOVERY_FAILURES:
            return None
        payload = _query_payload(queried)
        if payload is None:
            return None
        if self.result_validator is None:
            return payload
        try:
            validated = await _maybe_await(
                self.result_validator(payload, record)
            )
            return _normalise_output(validated)
        except _RECOVERY_FAILURES:
            return None

    def _mark_ambiguous(
        self, record: ResearchWorkUnitRecord, now: datetime
    ) -> bool:
        """Close an unknown provider outcome without retrying it."""
        try:
            return self.store.complete_work(
                record,
                "ambiguous",
                now,
                failure_code=_RESEARCH_UNAVAILABLE,
                failure_retryable=False,
            )
        except _RECOVERY_FAILURES:
            return False
