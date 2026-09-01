# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Memory-class Research outbox relaunch tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
