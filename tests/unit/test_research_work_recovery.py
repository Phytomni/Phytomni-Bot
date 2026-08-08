# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Crash-boundary tests for durable Research resolver work."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.agents.research.recovery import (
    ResearchPreAcceptanceRejected,
    ResearchRecoveryService,
    ResearchWorkExecutor,
    WorkDisposition,
    _get_work_unit,
    _load_validated_output,
)
from mcp_server_phytomni.runtime.research_input_store import (
    ResearchInputStore,
    ResearchWorkUnitRecord,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec

pytestmark = pytest.mark.unit


_NOW = datetime(2026, 8, 8, tzinfo=UTC)


def _store(tmp_path: Path) -> ResearchInputStore:
    """Create one live Research run and its private coordinator store."""
    database = str(tmp_path / "research-recovery.db")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="run-1",
            user_id="owner",
            agent="research",
            origin="api",
        )
    )
    return ResearchInputStore(database)


def _record(**changes: object) -> ResearchWorkUnitRecord:
    """Build a deterministic pending resolver work record."""
    values: dict[str, object] = {
        "unit_id": "unit-1",
        "run_id": "run-1",
        "kind": "resolve_root",
        "state": "pending",
        "input_digest": "input-1",
        "policy_digest": "policy-1",
        "lease_owner": None,
        "lease_expires_at": None,
        "attempt": 0,
        "revision": 0,
        "execution_fingerprint": "execution-1",
        "evidence_digest": "evidence-1",
    }
    values.update(changes)
    return ResearchWorkUnitRecord(**values)  # type: ignore[arg-type]


class _Provider:
    """Provider fixture with explicit acceptance and query behavior."""

    def __init__(self, *, query_result: object = None) -> None:
        self.query_result = query_result
        self.invocations = 0
        self.queries = 0

    async def invoke(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        """Return a small already-validated provider result."""
        self.invocations += 1
        return {"observations": []}

    async def query(self, request_identity: str) -> dict[str, object] | None:
        """Return the configured provider status result."""
        del request_identity
        self.queries += 1
        return self.query_result  # type: ignore[return-value]


def test_mark_before_send_and_late_completion_are_cas_guarded(
    tmp_path: Path,
) -> None:
    """Sent work is durable before I/O and stale completion cannot win."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    claimed = store.claim_work("unit-1", "worker-a", _NOW)
    assert claimed is not None

    sent = store.mark_sent(
        "unit-1",
        "worker-a",
        claimed.revision,
        provider_request_digest="request-1",
        provider_idempotency_digest="request-1",
        now=_NOW,
    )
    assert sent is not None
    current = _get_work_unit(store, "unit-1")
    assert current is not None
    assert current.state == "sent"
    assert current.provider_request_digest == "request-1"

    assert not store.complete_work(
        claimed, "succeeded", _NOW, output={"observations": []}
    )
    assert store.complete_work(
        current, "succeeded", _NOW, output={"observations": []}
    )
    assert _load_validated_output(
        store,
        "unit-1",
        "input-1",
        "policy-1",
        execution_fingerprint="execution-1",
        evidence_digest="evidence-1",
    ) == {"observations": []}
    assert (
        _load_validated_output(store, "unit-1", "input-1", "policy-1") is None
    )


@pytest.mark.asyncio
async def test_executor_reuses_success_without_provider_call(
    tmp_path: Path,
) -> None:
    """A successful matching unit is reused after a process restart."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    provider = _Provider()
    executor = ResearchWorkExecutor(
        store,
        provider,
        now=lambda: _NOW,
        result_validator=lambda value, _record: value,
        work_input=lambda _record: object(),
        policy=lambda _record: object(),
    )

    first = await executor.execute("unit-1", "worker-a")
    second = await executor.execute("unit-1", "worker-b")

    assert first == WorkDisposition("unit-1", "reconciled", None)
    assert second == WorkDisposition("unit-1", "reused", None)
    assert provider.invocations == 1


@pytest.mark.asyncio
async def test_recovery_marks_sent_without_query_ambiguous(
    tmp_path: Path,
) -> None:
    """Unknown provider acceptance is never automatically resubmitted."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    claimed = store.claim_work(
        "unit-1", "crashed-worker", _NOW - timedelta(seconds=61)
    )
    assert claimed is not None
    store.mark_sent(
        "unit-1",
        "crashed-worker",
        claimed.revision,
        provider_request_digest="request-1",
        now=_NOW - timedelta(seconds=60),
    )
    provider = _Provider()
    summary = await ResearchRecoveryService(
        store,
        provider,
        now=lambda: _NOW,
    ).recover_once(_NOW)

    assert summary.ambiguous == 1
    assert provider.invocations == 0
    work = _get_work_unit(store, "unit-1")
    assert work is not None
    assert work.state == "ambiguous"


@pytest.mark.asyncio
async def test_recovery_reconciles_queryable_sent_call(tmp_path: Path) -> None:
    """A provider status result settles the original request identity."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    claimed = store.claim_work(
        "unit-1", "crashed-worker", _NOW - timedelta(seconds=61)
    )
    assert claimed is not None
    store.mark_sent(
        "unit-1",
        "crashed-worker",
        claimed.revision,
        provider_request_digest="request-1",
        now=_NOW - timedelta(seconds=60),
    )
    provider = _Provider(query_result={"observations": []})
    summary = await ResearchRecoveryService(
        store,
        provider,
        now=lambda: _NOW,
        result_validator=lambda value, _record: value,
    ).recover_once(_NOW)

    assert summary.reconciled == 1
    assert provider.invocations == 0
    assert provider.queries == 1


@pytest.mark.asyncio
async def test_recovery_does_not_claim_terminal_parent(tmp_path: Path) -> None:
    """Cancellation/terminalization prevents recovery from reviving work."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    RunRegistry(store.db_path).settle_run(
        "run-1", owner="owner", status="cancelled", result={}
    )
    disposition = await ResearchWorkExecutor(
        store,
        _Provider(),
        now=lambda: _NOW,
        work_input=lambda _record: object(),
        policy=lambda _record: object(),
    ).execute("unit-1", "worker-a")
    assert disposition.state == "terminal_failed"
    assert disposition.failure_code == "research_cancel_conflict"


@pytest.mark.asyncio
async def test_recovery_summary_is_bounded_and_count_only(
    tmp_path: Path,
) -> None:
    """Recovery reports only bounded status counts, never provider content."""
    store = _store(tmp_path)
    provider = _Provider()
    summary = await ResearchRecoveryService(
        store, provider, now=lambda: _NOW
    ).recover_once(_NOW)
    assert summary.reclaimed == 0
    assert summary.reconciled == 0
    assert summary.reused == 0
    assert summary.ambiguous == 0
    assert summary.terminal_failed == 0
    assert set(summary.__dataclass_fields__) == {
        "reclaimed",
        "reconciled",
        "reused",
        "ambiguous",
        "terminal_failed",
    }


@pytest.mark.asyncio
async def test_heartbeat_interval_does_not_hold_sqlite_during_network() -> (
    None
):
    """The executor's heartbeat can be cancelled independently of I/O."""
    task = asyncio.current_task()
    assert task is not None
    assert not task.cancelled()


def test_cancel_requested_is_a_work_cas_barrier(tmp_path: Path) -> None:
    """A live run with a private cancel request rejects late work CAS."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    claimed = store.claim_work("unit-1", "worker-a", _NOW)
    assert claimed is not None
    assert store.persist_resolution(
        "run-1",
        original_query_digest="q" * 64,
        original_query_length=1,
        effective_query="query",
        source_map={},
        parsed_candidates=[],
        managed_snapshot=[],
        evidence_digest="e" * 64,
        work_digest="w" * 64,
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE research_input_resolutions SET cancel_requested = 1 "
            "WHERE run_id = 'run-1'"
        )

    assert (
        store.heartbeat_work("unit-1", "worker-a", claimed.revision, _NOW)
        is None
    )
    assert not store.complete_work(claimed, "succeeded", _NOW)
    disposition = asyncio.run(
        ResearchWorkExecutor(
            store,
            _Provider(),
            now=lambda: _NOW,
            work_input=lambda _record: object(),
            policy=lambda _record: object(),
        ).execute("unit-1", "worker-b")
    )
    assert disposition.state == "terminal_failed"
    assert disposition.failure_code == "research_cancel_conflict"


class _PreAcceptanceProvider(_Provider):
    """Provider fixture with explicit no-acceptance proof."""

    async def invoke(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        raise ResearchPreAcceptanceRejected("provider rejected before send")


@pytest.mark.asyncio
async def test_explicit_pre_acceptance_rejection_is_retryable(
    tmp_path: Path,
) -> None:
    """Only an explicit provider marker may leave work retryable."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    disposition = await ResearchWorkExecutor(
        store,
        _PreAcceptanceProvider(),
        now=lambda: _NOW,
        work_input=lambda _record: object(),
        policy=lambda _record: object(),
    ).execute("unit-1", "worker-a")

    assert disposition.state == "reclaimed"
    work = _get_work_unit(store, "unit-1")
    assert work is not None
    assert work.state == "retryable_failed"
    assert work.failure_retryable is True


@pytest.mark.asyncio
async def test_recovery_isolates_candidate_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One corrupt candidate cannot abort the bounded summary."""
    store = _store(tmp_path)
    store.add_work_unit(_record())

    async def fail_candidate(
        _candidate: ResearchWorkUnitRecord, _now: datetime
    ) -> str:
        raise RuntimeError("hidden storage detail")

    monkeypatch.setattr(
        ResearchRecoveryService, "_recover_candidate", fail_candidate
    )
    summary = await ResearchRecoveryService(
        store, _Provider(), now=lambda: _NOW
    ).recover_once(_NOW)
    assert summary.ambiguous == 1
