# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Focused public-contract tests for Research child dispatch."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime

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
    ResearchPlanCommitError,
    _canonical_json,
    _enqueue_payload,
    persist_plan_and_outbox,
    recover_dispatch_outbox,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore

pytestmark = pytest.mark.unit


def _corrupt_payload(store: ResearchInputStore, dispatch_id: str) -> None:
    """Inject malformed JSON through the durable test database seam."""
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE research_dispatch_outbox SET payload_json=? "
            "WHERE outbox_id=?",
            ("{not-json", dispatch_id),
        )
        connection.commit()


def test_outbox_enqueue_rejects_unbounded_or_malformed_values() -> None:
    """The private enqueue boundary rejects malformed durable projections."""
    bad_values: tuple[dict[str, object], ...] = (
        {},
        {
            "final_projection": {},
            "plan_digest": "digest",
            "children": "not-a-sequence",
        },
        {
            "final_projection": {},
            "plan_digest": "digest",
            "children": (),
        },
        {
            "final_projection": {"x": "y"},
            "plan_digest": "digest",
            "children": ({"child": "x"},),
            "execution_fingerprint": object(),
        },
    )
    for values in bad_values:
        with pytest.raises(ResearchPlanCommitError):
            _enqueue_payload(values, 0)


def test_outbox_canonical_json_rejects_non_serialisable_values() -> None:
    """Private serialization never leaks an encoder implementation error."""
    with pytest.raises(ResearchPlanCommitError):
        _canonical_json({"private": object()})


@pytest.mark.asyncio
async def test_outbox_recovery_limit_zero_is_side_effect_free(
    tmp_path,
) -> None:
    """A disabled recovery budget performs no database work."""
    store = _store(tmp_path)
    outbox = ResearchDispatchOutbox(store)
    assert (
        await recover_dispatch_outbox(
            outbox, datetime(2026, 8, 8), 0, "worker"
        )
        == ()
    )


@pytest.mark.asyncio
async def test_outbox_acceptance_schedules_async_attachment(tmp_path) -> None:
    """Async attachment callbacks are scheduled after durable acceptance."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    attached: list[str] = []

    async def attach(_record: ResearchDispatchRecord, task_id: str) -> None:
        attached.append(task_id)

    async def submit(_record: ResearchDispatchRecord) -> object:
        return {"task_id": "task-async"}

    outcome = await ResearchDispatchOutbox(
        store,
        submit=submit,
        authority_verifier=_authority_verifier,
        attach_task=attach,
    ).dispatch_once(record.dispatch_id, "worker")
    await asyncio.sleep(0)
    assert outcome.state == "accepted"
    assert attached == ["task-async"]


@pytest.mark.asyncio
async def test_outbox_missing_dispatch_is_terminal_without_provider_call(
    tmp_path,
) -> None:
    """An unknown durable dispatch ID never reaches verify or submit ports."""
    store = _store(tmp_path)
    submitted: list[object] = []

    async def submit(_record: ResearchDispatchRecord) -> object:
        submitted.append(_record)
        return {"task_id": "must-not-submit"}

    disposition = await ResearchDispatchOutbox(
        store, submit=submit, authority_verifier=_authority_verifier
    ).dispatch_once("missing-dispatch", "worker")

    assert disposition.state == "ambiguous"
    assert not submitted


@pytest.mark.asyncio
async def test_submit_failure_reconciles_a_queryable_remote_task(
    tmp_path,
) -> None:
    """A post-send transport failure accepts only a verified remote replay."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]

    async def submit(_row: ResearchDispatchRecord) -> object:
        raise RuntimeError("transport lost after submission")

    async def remote_query(_row: ResearchDispatchRecord) -> object:
        return {"task_id": "remote-recovered"}

    outcome = await ResearchDispatchOutbox(
        store,
        submit=submit,
        remote_query=remote_query,
        authority_verifier=_authority_verifier,
    ).dispatch_once(record.dispatch_id, "worker")

    assert outcome.state == "accepted"
    assert outcome.remote_task_id == "remote-recovered"


@pytest.mark.asyncio
async def test_corrupt_outbox_payload_never_reaches_submit(
    tmp_path,
) -> None:
    """A malformed durable payload is unavailable to both load and dispatch."""
    store = _store(tmp_path)
    record = persist_plan_and_outbox(store, "run-1", 0, _prepared(), _plan(1))[
        0
    ]
    _corrupt_payload(store, record.dispatch_id)
    submitted: list[object] = []

    async def submit(_record: ResearchDispatchRecord) -> object:
        submitted.append(_record)
        return {"task_id": "must-not-submit"}

    outbox = ResearchDispatchOutbox(
        store, submit=submit, authority_verifier=_authority_verifier
    )
    with pytest.raises(KeyError):
        outbox.load(record.dispatch_id)
    assert (
        await outbox.dispatch_once(record.dispatch_id, "worker")
    ).state == "ambiguous"
    assert not submitted
