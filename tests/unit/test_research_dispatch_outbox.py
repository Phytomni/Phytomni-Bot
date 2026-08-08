# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Transactional and crash-safe Research child outbox tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from mcp_server_phytomni.agents.research import (
    dispatch_outbox,
    dispatch_outbox_storage,
)
from mcp_server_phytomni.agents.research.dispatch_outbox import (
    ResearchDispatchOutbox,
    ResearchDispatchRecord,
    persist_plan_and_outbox,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from mcp_server_phytomni.agents.research.planning import (
    ResearchChildPlan,
    ResearchPlan,
)
from mcp_server_phytomni.agents.research.recovery import (
    ResearchRecoveryService,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec

pytestmark = pytest.mark.unit


def _digest(value: object) -> str:
    """Return the fixture's canonical identity digest."""
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _store(tmp_path: Path) -> ResearchInputStore:
    """Create a running Research parent and private store."""
    database = str(tmp_path / "research.db")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="run-1", user_id="owner", agent="research", origin="api"
        )
    )
    store = ResearchInputStore(database)
    assert store.persist_resolution(
        "run-1",
        original_query_digest="q" * 64,
        original_query_length=5,
        effective_query="query",
        source_map={},
        parsed_candidates=[],
        managed_snapshot=[],
        evidence_digest="e" * 64,
        work_digest="w" * 64,
    )
    return store


def _prepared() -> PreparedResearchInput:
    """Return a bounded private projection used by the outbox tests."""
    return PreparedResearchInput(
        effective_query="query",
        obs_file_list=(),
        data_list=MappingProxyType({"bucket/data.tsv": "a dataset"}),
        inventory_digest="i" * 64,
        evidence_digest="e" * 64,
        execution_fingerprint="x" * 64,
        authority_ids=("authority-1",),
    )


def _plan(count: int = 2) -> ResearchPlan:
    """Build a deterministic fixture plan with one row per child."""
    children = tuple(
        ResearchChildPlan(
            ordinal=index,
            task_name=f"research_goal_{index}",
            goal_description=f"goal-{index}",
            context="",
            data_list=MappingProxyType({"bucket/data.tsv": "a dataset"}),
            output_dir=f"research/run-1/children/part-{index + 1:03d}",
            thread_id=f"thread-{index}-run-1",
            interop_mode="off",
            interop_targets=(),
            dispatch_fingerprint=_digest(("dispatch", index)),
        )
        for index in range(count)
    )
    return ResearchPlan(
        goals=(),
        children=children,
        digest=_digest(
            {
                "children": [child.dispatch_fingerprint for child in children],
                "run_id": "run-1",
            }
        ),
    )


def test_atomic_plan_projection_and_outbox_commit(tmp_path: Path) -> None:
    """One commit exposes the final projection and every deterministic row."""
    store = _store(tmp_path)
    records = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan())

    assert tuple(record.child_ordinal for record in records) == (0, 1)
    with sqlite3.connect(store.db_path) as connection:
        resolution = connection.execute(
            "SELECT final_projection_json, plan_digest, status, last_stage "
            "FROM research_input_resolutions WHERE run_id = 'run-1'"
        ).fetchone()
        outbox = connection.execute(
            "SELECT state, child_ordinal, dispatch_fingerprint "
            "FROM research_dispatch_outbox ORDER BY child_ordinal"
        ).fetchall()
        work = connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE kind = 'dispatch'"
        ).fetchone()
        stage = connection.execute(
            "SELECT stage FROM runs WHERE run_id = 'run-1'"
        ).fetchone()

    assert json.loads(resolution[0])["effective_query"] == "query"
    assert resolution[1] == _plan().digest
    assert resolution[2:] == ("planning", "planning")
    assert [row[0] for row in outbox] == ["pending", "pending"]
    assert [row[1] for row in outbox] == [0, 1]
    assert [row[2] for row in outbox] == [
        child.dispatch_fingerprint for child in _plan().children
    ]
    assert work == (2,)
    assert stage == ("planning",)


def test_plan_write_failure_rolls_back_every_private_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected outbox insert error cannot leave a partial enqueue."""
    store = _store(tmp_path)

    def fail_on_outbox(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.OperationalError("injected outbox failure")

    monkeypatch.setattr(
        dispatch_outbox_storage, "insert_plan_row", fail_on_outbox
    )
    with pytest.raises(sqlite3.OperationalError):
        persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan())

    with sqlite3.connect(store.db_path) as connection:
        resolution = connection.execute(
            "SELECT final_projection_json, plan_digest, status FROM "
            "research_input_resolutions WHERE run_id = 'run-1'"
        ).fetchone()
        outbox = connection.execute(
            "SELECT COUNT(*) FROM research_dispatch_outbox "
            "WHERE run_id = 'run-1'"
        ).fetchone()
        work = connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE kind = 'dispatch'"
        ).fetchone()

    assert resolution == (None, None, "pending")
    assert outbox == (0,)
    assert work == (0,)


def test_claim_persists_new_lease_and_heartbeat_rejects_expiry(
    tmp_path: Path,
) -> None:
    """A claim owns a fresh lease and cannot renew it after expiry."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    now = datetime(2026, 8, 9, tzinfo=UTC)
    claimed = ResearchDispatchOutbox(store).claim(
        record.dispatch_id, "worker-a", now=now
    )
    assert claimed is not None
    assert claimed.state == "leased"
    assert claimed.lease_expires_at == now + timedelta(seconds=60)
    assert (
        dispatch_outbox.ResearchDispatchOutbox(store).heartbeat(
            record.dispatch_id,
            "worker-a",
            claimed.revision,
            now=now + timedelta(seconds=61),
        )
        is None
    )


@pytest.mark.asyncio
async def test_recovery_reclaims_expired_leased_outbox(
    tmp_path: Path,
) -> None:
    """A crash after claim is recovered without stranding the child."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    now = datetime(2026, 8, 9, tzinfo=UTC)
    claimed = ResearchDispatchOutbox(store).claim(
        record.dispatch_id,
        "dead-worker",
        now=now - timedelta(minutes=2),
    )
    assert claimed is not None
    calls: list[str] = []

    async def submit(_row: ResearchDispatchRecord) -> object:
        calls.append("submit")
        return {"task_id": "task-recovered"}

    outcomes = await dispatch_outbox.recover_dispatch_outbox(
        ResearchDispatchOutbox(store, submit=submit), now, 1, "recovery"
    )
    assert outcomes == ("accepted",)
    assert calls == ["submit"]


@pytest.mark.asyncio
async def test_first_dispatch_queries_remote_and_ignores_failed_local_task(
    tmp_path: Path,
) -> None:
    """A dead local fingerprint row cannot short-circuit identity lookup."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "INSERT INTO tasks(task_id,status,analysis_id,output_dir,run_id,"
            "input_fingerprint) VALUES(?,?,?,?,?,?)",
            (
                "dead-task",
                "failed",
                "",
                "",
                "run-1",
                record.dispatch_fingerprint,
            ),
        )
        connection.commit()
    calls: list[str] = []

    async def remote_query(_row: ResearchDispatchRecord) -> object:
        calls.append("query")
        return {"task_id": "remote-existing"}

    async def submit(_row: ResearchDispatchRecord) -> object:
        calls.append("submit")
        return {"task_id": "unexpected"}

    disposition = await ResearchDispatchOutbox(
        store, remote_query=remote_query, submit=submit
    ).dispatch_once(record.dispatch_id, "worker-a")
    assert disposition.state == "accepted"
    assert disposition.remote_task_id == "remote-existing"
    assert calls == ["query"]


@pytest.mark.asyncio
async def test_authority_verifier_receives_durable_child_bindings(
    tmp_path: Path,
) -> None:
    """Fresh authority verification sees payload, grants, and snapshot."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    observed: list[ResearchDispatchRecord] = []

    async def verify(row: ResearchDispatchRecord) -> bool:
        observed.append(row)
        return bool(row.payload and row.grant_ids and row.snapshot_digest)

    async def submit(_row: ResearchDispatchRecord) -> object:
        return {"task_id": "task-authority"}

    disposition = await ResearchDispatchOutbox(
        store, submit=submit, authority_verifier=verify
    ).dispatch_once(record.dispatch_id, "worker-a")
    assert disposition.state == "accepted"
    assert (
        observed[0].payload["dispatch_fingerprint"]
        == record.dispatch_fingerprint
    )
    assert observed[0].grant_ids == ("authority-1",)


def test_plan_replacement_removes_stale_child_rows(tmp_path: Path) -> None:
    """A changed deterministic plan cannot retain unrequested children."""
    store = _store(tmp_path)
    persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(2))
    records = persist_plan_and_outbox(store, "run-1", 1, _prepared(), _plan(1))
    assert tuple(record.child_ordinal for record in records) == (0,)
    with sqlite3.connect(store.db_path) as connection:
        rows = connection.execute(
            "SELECT child_ordinal FROM research_dispatch_outbox "
            "WHERE run_id='run-1' ORDER BY child_ordinal"
        ).fetchall()
        work = connection.execute(
            "SELECT unit_id FROM research_work_units WHERE run_id='run-1' "
            "AND kind='dispatch' ORDER BY unit_id"
        ).fetchall()
    assert rows == [(0,)]
    assert len(work) == 1


@pytest.mark.asyncio
async def test_acceptance_requires_parent_revision_and_durable_attachment(
    tmp_path: Path,
) -> None:
    """Parent revision CAS blocks stale acceptance and stores attachment."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET revision=revision+1 WHERE run_id='run-1'"
        )
        connection.commit()

    async def submit(_row: ResearchDispatchRecord) -> object:
        return {"task_id": "task-stale"}

    stale = await ResearchDispatchOutbox(store, submit=submit).dispatch_once(
        record.dispatch_id, "worker-a"
    )
    assert stale.state == "ambiguous"
    assert store is not None

    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    fresh_store = _store(fresh_dir)
    fresh = persist_plan_and_outbox(
        fresh_store, "run-1", 0, _prepared(), _plan(1)
    )[0]

    def fail_attach(_row: ResearchDispatchRecord, _task_id: str) -> None:
        raise RuntimeError("attachment failed")

    accepted = await ResearchDispatchOutbox(
        fresh_store, submit=submit, attach_task=fail_attach
    ).dispatch_once(fresh.dispatch_id, "worker-a")
    assert accepted.state == "accepted"
    with sqlite3.connect(fresh_store.db_path) as connection:
        task = connection.execute(
            "SELECT run_id,input_fingerprint,status FROM tasks "
            "WHERE task_id='task-stale'"
        ).fetchone()
    assert task == ("run-1", fresh.dispatch_fingerprint, "submitted")


@pytest.mark.asyncio
async def test_unknown_acceptance_never_blind_resubmits(
    tmp_path: Path,
) -> None:
    """A sent row without a status seam becomes ambiguous once."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    calls: list[str] = []

    async def submit(_row: ResearchDispatchRecord) -> object:
        calls.append("submit")
        raise RuntimeError("crash after remote acceptance")

    outbox = ResearchDispatchOutbox(store, submit=submit)
    first = await outbox.dispatch_once(record.dispatch_id, "worker-a")
    second = await outbox.reconcile_once(record.dispatch_id, "worker-b")

    assert first.state == "ambiguous"
    assert second.state == "ambiguous"
    assert first.failure_code == "research_run_tracking_failed"
    assert second.failure_code == "research_run_tracking_failed"
    assert calls == ["submit"]
    assert outbox.load(record.dispatch_id).state == "ambiguous"


@pytest.mark.asyncio
async def test_remote_task_id_is_cas_accepted_and_local_replay_is_reused(
    tmp_path: Path,
) -> None:
    """A confirmed task ID is persisted once and replay prevents submit."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    calls: list[str] = []

    async def submit(_row: ResearchDispatchRecord) -> object:
        calls.append("submit")
        return {"task_id": "task-1"}

    outbox = ResearchDispatchOutbox(store, submit=submit)
    accepted = await outbox.dispatch_once(record.dispatch_id, "worker-a")
    replay = await outbox.dispatch_once(record.dispatch_id, "worker-b")

    assert accepted.state == "accepted"
    assert accepted.remote_task_id == "task-1"
    assert replay.state == "accepted"
    assert replay.remote_task_id == "task-1"
    assert calls == ["submit"]


@pytest.mark.asyncio
async def test_pending_dispatch_is_cancelled_when_parent_is_cancelled(
    tmp_path: Path,
) -> None:
    """Cancellation prevents a pending child crossing the send boundary."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE research_input_resolutions SET cancel_requested = 1 "
            "WHERE run_id = 'run-1'"
        )
        connection.commit()
    calls: list[str] = []

    async def submit(_row: ResearchDispatchRecord) -> object:
        calls.append("submit")
        return {"task_id": "never"}

    disposition = await ResearchDispatchOutbox(
        store, submit=submit
    ).dispatch_once(record.dispatch_id, "worker-a")

    assert disposition.state == "cancelled"
    assert not calls


@pytest.mark.asyncio
async def test_recovery_processes_pending_outbox_without_resolver_provider(
    tmp_path: Path,
) -> None:
    """Recovery sends dispatch rows through outbox identity only."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    calls: list[str] = []

    async def submit(_row: ResearchDispatchRecord) -> object:
        calls.append("submit")
        return {"task_id": "task-recovery"}

    class _Provider:
        async def invoke(self, work_input: object, policy: object) -> object:
            """Reject accidental dispatch-provider invocation."""
            del work_input, policy
            raise AssertionError("dispatch row entered resolver provider")

        async def query(self, request_identity: str) -> object | None:
            """Reject accidental resolver status lookup."""
            del request_identity
            return None

    recovery = ResearchRecoveryService(
        store,
        _Provider(),
        dispatch_submit=submit,
        batch_size=1,
        lease_owner="recovery-worker",
    )

    summary = await recovery.recover_once(datetime.now().astimezone())

    assert summary.reconciled == 1
    assert calls == ["submit"]
    assert recovery.outbox is not None
    assert recovery.outbox.load(record.dispatch_id).state == "accepted"
