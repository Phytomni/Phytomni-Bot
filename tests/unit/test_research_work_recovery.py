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
from fastapi import FastAPI

from mcp_server_phytomni.agents.research.recovery import (
    ResearchContextLengthRejected,
    ResearchPreAcceptanceRejected,
    ResearchRecoveryService,
    ResearchWorkExecutor,
    WorkDisposition,
    _get_work_unit,
    _load_validated_output,
    build_context_subdivider,
)
from mcp_server_phytomni.agents.research.resolver_policy import (
    ResearchResolverObservationUnit,
    ResearchResolverPolicy,
    ResearchResolverWorkPlan,
    canonical_json_bytes,
)
from mcp_server_phytomni.api import app_support
from mcp_server_phytomni.runtime.research_input_store import (
    ResearchInputStore,
    ResearchWorkUnitRecord,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec

pytestmark = pytest.mark.unit


_NOW = datetime(2026, 8, 8, tzinfo=UTC)


class _ContextEstimator:
    """Deterministic byte estimator for the subdivision fixture."""

    def estimate(self, serialized_request: bytes) -> int:
        """Estimate the fixture request by its serialized byte length."""
        return len(serialized_request)

    @property
    def contract_name(self) -> str:
        """Identify the deterministic test estimator contract."""
        return "research_token_estimator"


def _context_estimator() -> _ContextEstimator:
    return _ContextEstimator()


def _context_policy() -> ResearchResolverPolicy:
    return ResearchResolverPolicy(
        schema_version=1,
        model_id="test-model",
        context_token_limit=2048,
        output_token_reserve=1,
        prompt_token_overhead=1,
        schema_token_overhead=1,
        safety_margin_tokens=1,
        max_serialized_request_bytes=1024,
        max_description_chars=200,
        overlap_chars=0,
        provider_identity="test-provider",
        provider_idempotency_supported=False,
        provider_status_query_supported=False,
    )


def _context_plan() -> ResearchResolverWorkPlan:
    policy = _context_policy()
    payload = {
        "evidence": [
            {
                "dataset_ids": ["dataset-1"],
                "evidence_id": "evidence-1",
                "overlap_chars": 0,
                "source_kind": "query",
                "source_ordinal": 0,
                "source_span": None,
                "text": "left and right",
            }
        ],
        "model_id": policy.model_id,
        "policy_fingerprint": policy.fingerprint(),
        "inventory_digest": "inventory-1",
        "group_members": ["dataset-1"],
        "schema_version": policy.schema_version,
    }
    serialized = canonical_json_bytes(payload)
    unit = ResearchResolverObservationUnit(
        "unit-1",
        ("evidence-1",),
        ("dataset-1",),
        serialized,
        len(serialized),
        len(serialized),
    )
    return ResearchResolverWorkPlan(
        (unit,), ("evidence-1",), policy.fingerprint(), "plan-1"
    )


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


class _BlockingProvider(_Provider):
    """Provider that keeps its network boundary open until released."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        self.started.set()
        await self.release.wait()
        return {"observations": []}


class _ContextRejectedProvider(_Provider):
    """Provider fixture that proves a context rejection was verified."""

    async def invoke(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        raise ResearchContextLengthRejected(verified=True)


class _UnverifiedContextProvider(_ContextRejectedProvider):
    """Provider fixture that does not prove the rejection boundary."""

    async def invoke(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        raise ResearchContextLengthRejected(verified=False)


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
        "execution-1",
        "evidence-1",
    ) == {"observations": []}
    assert (
        _load_validated_output(store, "unit-1", "input-1", "policy-1") is None
    )
    assert store.load_validated_output(
        "unit-1",
        "input-1",
        "policy-1",
        "execution-1",
        "evidence-1",
    ) == {"observations": []}
    assert (
        store.load_validated_output(
            "unit-1",
            "input-1",
            "policy-1",
            "changed",
            "evidence-1",
        )
        is None
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
async def test_heartbeat_refreshes_revision_during_blocking_provider(
    tmp_path: Path,
) -> None:
    """A blocking provider does not stop independent lease heartbeats."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    provider = _BlockingProvider()
    executor = ResearchWorkExecutor(
        store,
        provider,
        now=lambda: _NOW,
        heartbeat_interval=timedelta(milliseconds=5),
        work_input=lambda record: record,
        policy=lambda record: record,
    )
    task = asyncio.create_task(executor.execute("unit-1", "worker-a"))
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    await asyncio.sleep(0.03)
    live = _get_work_unit(store, "unit-1")
    assert live is not None
    assert live.revision >= 3
    provider.release.set()
    assert (await task).state == "reconciled"


@pytest.mark.asyncio
async def test_verified_context_rejection_replaces_parent_with_children(
    tmp_path: Path,
) -> None:
    """Verified context errors close a unit and enqueue smaller children."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    plan = _context_plan()
    executor = ResearchWorkExecutor(
        store,
        _ContextRejectedProvider(),
        now=lambda: _NOW,
        work_input=lambda record: record,
        policy=lambda record: record,
        context_subdivider=build_context_subdivider(
            plan, _context_policy(), _context_estimator()
        ),
    )
    disposition = await executor.execute("unit-1", "worker-a")
    assert disposition.state == "reclaimed"
    assert disposition.failure_code == "research_input_resolution_unavailable"
    with sqlite3.connect(store.db_path) as connection:
        rows = connection.execute(
            "SELECT unit_id, state FROM research_work_units ORDER BY unit_id"
        ).fetchall()
    assert rows[0] == ("unit-1", "cancelled")
    assert [unit_id for unit_id, _state in rows[1:]] == [
        "unit-1.1",
        "unit-1.2",
    ]
    assert all(state == "pending" for _unit_id, state in rows[1:])


@pytest.mark.asyncio
async def test_unverified_context_rejection_stays_ambiguous(
    tmp_path: Path,
) -> None:
    """An unverified limit error cannot trigger subdivision or retry."""
    store = _store(tmp_path)
    store.add_work_unit(_record())
    executor = ResearchWorkExecutor(
        store,
        _UnverifiedContextProvider(),
        now=lambda: _NOW,
        work_input=lambda record: record,
        policy=lambda record: record,
        context_subdivider=build_context_subdivider(
            _context_plan(), _context_policy(), _context_estimator()
        ),
    )
    disposition = await executor.execute("unit-1", "worker-a")
    assert disposition.state == "ambiguous"
    with sqlite3.connect(store.db_path) as connection:
        rows = connection.execute(
            "SELECT unit_id, state FROM research_work_units ORDER BY unit_id"
        ).fetchall()
    assert rows == [("unit-1", "sent")]


@pytest.mark.asyncio
async def test_http_lifespan_runs_registered_recovery_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API lifespan exposes one bounded startup recovery call."""
    calls: list[str] = []
    monkeypatch.setattr(
        app_support, "validate_citation_database", lambda: None
    )
    monkeypatch.setattr(app_support, "init_shared_client", lambda: None)
    monkeypatch.setattr(
        app_support,
        "aclose_shared_client",
        lambda: _completed_awaitable(calls, "close-client"),
    )
    monkeypatch.setattr(
        app_support,
        "aclose_gauss_pool",
        lambda: _completed_awaitable(calls, "close-gauss"),
    )
    monkeypatch.setattr(
        app_support,
        "recover_registered_startup",
        lambda: _completed_awaitable(calls, "recover"),
    )
    lifespan = getattr(app_support, "_http_lifespan")
    async with lifespan(FastAPI()):
        assert calls == ["recover"]
    assert calls == ["recover", "close-client", "close-gauss"]


def _completed_awaitable(calls: list[str], value: str):
    """Return a tiny awaitable for lifecycle monkeypatches."""

    async def finish() -> None:
        calls.append(value)

    return finish()


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
