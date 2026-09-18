# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Memory-class Research outbox relaunch tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest
from tests.unit.test_research_dispatch_outbox import (
    _authority_verifier,
    _plan,
    _prepared,
    _store,
)

from mcp_server_phytomni.agents.research.dispatch_outbox import (
    ResearchDispatchOutbox,
    ResearchDispatchRecord,
    persist_plan_and_outbox,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

pytestmark = pytest.mark.unit


def _counting_submit() -> tuple[
    list[ResearchDispatchRecord],
    Any,
]:
    """Return a submit port that records each child and mints unique ids."""
    submitted: list[ResearchDispatchRecord] = []

    async def submit(row: ResearchDispatchRecord) -> object:
        submitted.append(row)
        return {"task_id": f"ei-{len(submitted)}"}

    return submitted, submit


def _memory_failure() -> tuple[dict[str, str], dict[str, object]]:
    """Return a live FAILED body plus an OOM task log."""
    return (
        {"status": "FAILED", "message": "MemoryError"},
        {"logs": [{"content": "OOM killed the worker"}], "text": "OOM"},
    )


async def _accepted_children(tmp_path: Path, count: int = 2) -> tuple[
    ResearchDispatchOutbox,
    tuple[ResearchDispatchRecord, ...],
    list[ResearchDispatchRecord],
    list[Any],
]:
    """Persist, submit, and accept ``count`` children on one outbox."""
    store = _store(tmp_path)
    records = persist_plan_and_outbox(
        store, "run-1", 0, _prepared(), _plan(count)
    )
    submitted, submit = _counting_submit()
    outbox = ResearchDispatchOutbox(
        store,
        submit=submit,
        authority_verifier=_authority_verifier,
    )
    dispatched = [
        await outbox.dispatch_once(item.dispatch_id, "worker")
        for item in records
    ]
    return outbox, records, submitted, dispatched


@pytest.mark.asyncio
async def test_memory_relaunch_bumps_small_child_to_medium(
    tmp_path: Path,
) -> None:
    """A memory-class FAILED child resubmits once on medium."""
    outbox, records, submitted, dispatched = await _accepted_children(tmp_path)
    first, second = dispatched
    loaded = outbox.load(records[0].dispatch_id)
    assert loaded.payload["compute_resource"] == "small"
    assert first.remote_task_id == "ei-1"
    sibling_before = outbox.load(records[1].dispatch_id)

    status_payload, log_payload = _memory_failure()
    relaunched = await outbox.relaunch_memory_exhausted(
        records[0].dispatch_id,
        status_payload,
        log_payload,
    )

    bumped = outbox.load(records[0].dispatch_id)
    sibling = outbox.load(records[1].dispatch_id)
    assert relaunched.state in {"accepted", "reconciled"}
    assert relaunched.state != "ambiguous"
    assert relaunched.remote_task_id == "ei-3"
    assert bumped.dispatch_fingerprint == loaded.dispatch_fingerprint
    assert bumped.payload["compute_resource"] == "medium"
    assert bumped.payload["compute_resource_generation"] == 1
    assert bumped.payload["data_list"] == loaded.payload["data_list"]
    assert bumped.remote_task_id != first.remote_task_id
    assert submitted[2].dispatch_id == records[0].dispatch_id
    assert submitted[2].payload["compute_resource"] == "medium"
    assert sibling.remote_task_id == sibling_before.remote_task_id
    assert sibling.payload["compute_resource"] == "small"
    assert sibling.revision == sibling_before.revision
    assert second.remote_task_id == "ei-2"
    assert [row.dispatch_id for row in submitted] == [
        records[0].dispatch_id,
        records[1].dispatch_id,
        records[0].dispatch_id,
    ]


@pytest.mark.asyncio
async def test_memory_relaunch_stops_after_large_generation(
    tmp_path: Path,
) -> None:
    """A third memory failure on large must not submit a fourth job."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    submitted, submit = _counting_submit()
    outbox = ResearchDispatchOutbox(
        store,
        submit=submit,
        authority_verifier=_authority_verifier,
    )
    await outbox.dispatch_once(record.dispatch_id, "worker")
    status_payload, log_payload = _memory_failure()
    await outbox.relaunch_memory_exhausted(
        record.dispatch_id, status_payload, log_payload
    )
    await outbox.relaunch_memory_exhausted(
        record.dispatch_id, status_payload, log_payload
    )
    large = outbox.load(record.dispatch_id)
    assert large.payload["compute_resource"] == "large"
    assert large.payload["compute_resource_generation"] == 2
    assert len(submitted) == 3

    stopped = await outbox.relaunch_memory_exhausted(
        record.dispatch_id, status_payload, log_payload
    )
    assert len(submitted) == 3
    assert stopped.remote_task_id == large.remote_task_id
    assert outbox.load(record.dispatch_id).payload["compute_resource"] == (
        "large"
    )


@pytest.mark.asyncio
async def test_non_memory_failure_does_not_relaunch(
    tmp_path: Path,
) -> None:
    """A ValueError FAILED child keeps the original remote task."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    submitted, submit = _counting_submit()
    outbox = ResearchDispatchOutbox(
        store,
        submit=submit,
        authority_verifier=_authority_verifier,
    )
    accepted = await outbox.dispatch_once(record.dispatch_id, "worker")

    skipped = await outbox.relaunch_memory_exhausted(
        record.dispatch_id,
        {
            "status": "FAILED",
            "message": "ValueError missing column",
        },
        {"logs": [{"content": "ValueError missing column"}]},
    )

    assert len(submitted) == 1
    assert skipped.remote_task_id == accepted.remote_task_id
    loaded = outbox.load(record.dispatch_id)
    assert loaded.payload["compute_resource"] == "small"
    assert loaded.remote_task_id == "ei-1"


@pytest.mark.asyncio
async def test_memory_relaunch_missing_child_does_not_submit(
    tmp_path: Path,
) -> None:
    """A missing dispatch row remains ambiguous without a provider call."""
    outbox, records, submitted, _ = await _accepted_children(tmp_path, 1)
    before = outbox.load(records[0].dispatch_id)
    status_payload, log_payload = _memory_failure()

    result = await outbox.relaunch_memory_exhausted(
        "missing-dispatch", status_payload, log_payload
    )

    assert result.dispatch_id == "missing-dispatch"
    assert result.state == "ambiguous"
    assert result.remote_task_id is None
    assert result.failure_code == "research_run_tracking_failed"
    assert len(submitted) == 1
    assert outbox.load(records[0].dispatch_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unbound", "raised", "missing_id"])
async def test_memory_relaunch_submission_failure_preserves_child(
    tmp_path: Path,
    failure: Literal["unbound", "raised", "missing_id"],
) -> None:
    """An unsuccessful submit must not persist a tier or remote id change."""
    original, records, submitted, dispatched = await _accepted_children(
        tmp_path, 1
    )
    before = original.load(records[0].dispatch_id)
    attempts: list[ResearchDispatchRecord] = []

    async def submit(row: ResearchDispatchRecord) -> object:
        attempts.append(row)
        if failure == "raised":
            raise RuntimeError("provider unavailable")
        return {}

    outbox = ResearchDispatchOutbox(
        original.store, submit=None if failure == "unbound" else submit
    )
    status_payload, log_payload = _memory_failure()

    result = await outbox.relaunch_memory_exhausted(
        before.dispatch_id, status_payload, log_payload
    )

    assert result == dispatched[0]
    assert outbox.load(before.dispatch_id) == before
    assert len(submitted) == 1
    if failure == "unbound":
        assert not attempts
    else:
        assert len(attempts) == 1
        assert attempts[0].dispatch_id == before.dispatch_id
        assert attempts[0].payload == {
            **before.payload,
            "compute_resource": "medium",
            "compute_resource_generation": 1,
        }


@pytest.mark.asyncio
async def test_memory_relaunch_cancelled_parent_does_not_submit(
    tmp_path: Path,
) -> None:
    """A cancelled parent blocks resubmission of an accepted child."""
    outbox, records, submitted, _ = await _accepted_children(tmp_path, 1)
    before = outbox.load(records[0].dispatch_id)
    with sqlite_transaction(outbox.store.db_path) as connection:
        connection.execute(
            "UPDATE research_input_resolutions SET cancel_requested = 1 "
            "WHERE run_id = ?",
            (before.run_id,),
        )
    status_payload, log_payload = _memory_failure()

    result = await outbox.relaunch_memory_exhausted(
        before.dispatch_id, status_payload, log_payload
    )

    assert result.state == "cancelled"
    assert result.remote_task_id is None
    assert result.failure_code == "research_input_resolution_unavailable"
    assert len(submitted) == 1
    assert outbox.load(before.dispatch_id) == before


@pytest.mark.asyncio
async def test_memory_relaunch_accepts_synchronous_submit_port(
    tmp_path: Path,
) -> None:
    """A synchronous submit result persists the same bounded tier bump."""
    original, records, _, _ = await _accepted_children(tmp_path, 1)
    before = original.load(records[0].dispatch_id)
    attempts: list[ResearchDispatchRecord] = []

    def submit(row: ResearchDispatchRecord) -> object:
        attempts.append(row)
        return {"task_id": "ei-sync"}

    outbox = ResearchDispatchOutbox(original.store, submit=submit)
    status_payload, log_payload = _memory_failure()

    result = await outbox.relaunch_memory_exhausted(
        before.dispatch_id, status_payload, log_payload
    )

    assert result.state == "accepted"
    assert result.remote_task_id == "ei-sync"
    assert result.failure_code is None
    assert len(attempts) == 1
    bumped = outbox.load(before.dispatch_id)
    assert bumped.remote_task_id == result.remote_task_id
    assert bumped.dispatch_fingerprint == before.dispatch_fingerprint
    assert (
        bumped.payload
        == attempts[0].payload
        == {
            **before.payload,
            "compute_resource": "medium",
            "compute_resource_generation": 1,
        }
    )
