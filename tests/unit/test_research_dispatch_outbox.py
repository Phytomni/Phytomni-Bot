# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Transactional and crash-safe Research child outbox tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from mcp_server_phytomni.agents.research import dispatch_outbox
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
        dispatch_outbox, "_insert_dispatch_row", fail_on_outbox
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

    outbox = ResearchDispatchOutbox(store, submit=submit)
    recovery = ResearchRecoveryService(
        store,
        _Provider(),
        outbox=outbox,
        batch_size=1,
        lease_owner="recovery-worker",
    )

    summary = await recovery.recover_once(datetime.now().astimezone())

    assert summary.reconciled == 1
    assert calls == ["submit"]
    assert outbox.load(record.dispatch_id).state == "accepted"
