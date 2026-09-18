# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline multi-connection scenarios for durable Research input handling."""

from __future__ import annotations

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import MappingProxyType
from typing import Any

import pytest
import tests.conftest as test_config
from tests.support.research_fakes import local_server_pytest_generate_tests
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.agents.research.dispatch_outbox import (
    persist_plan_and_outbox,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from mcp_server_phytomni.agents.research.planning import (
    ResearchChildPlan,
    ResearchPlan,
)
from mcp_server_phytomni.runtime.research_input_store import (
    ResearchAdmissionReservation,
    ResearchInputStore,
    ResearchWorkUnitRecord,
    cancel_research_run,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


_original_layer_marker = getattr(test_config, "_layer_marker_for_item")


pytest_generate_tests = local_server_pytest_generate_tests(
    _original_layer_marker, __file__, test_config
)


def _store(tmp_path: Path) -> tuple[ResearchInputStore, str]:
    """Create a private store backed by one temporary SQLite file."""
    database = str(tmp_path / "research-scenarios.sqlite")
    RunRegistry(database)
    return ResearchInputStore(database), database


def _reserve(
    store: ResearchInputStore,
    run_id: str,
    identity: str,
    *,
    alias: str | None = None,
    fingerprint: str = "f" * 64,
) -> ResearchAdmissionReservation | None:
    """Reserve a minimal valid root admission through the public store API."""
    return store.reserve_admission(
        run_id=run_id,
        owner="owner-1",
        identity_digest=identity,
        identity_kind="conversation",
        header_alias_digest=alias,
        client_fingerprint=fingerprint,
        original_query_digest="q" * 64,
        original_query_length=8,
        effective_query="question",
        source_map={},
        candidates=(),
        managed_snapshot=(),
        locale="en-US",
        root_input_digest="q" * 64,
    )


def _work(run_id: str = "run-work") -> ResearchWorkUnitRecord:
    """Return one resolver work row with all durable reuse bindings."""
    return ResearchWorkUnitRecord(
        unit_id=f"{run_id}:resolve",
        run_id=run_id,
        kind="resolve",
        state="pending",
        input_digest="input-1",
        policy_digest="policy-1",
        lease_owner=None,
        lease_expires_at=None,
        attempt=0,
        revision=0,
        execution_fingerprint="execution-1",
        evidence_digest="evidence-1",
    )


def _attach_alias_after_process_restart(database: str, result: Any) -> None:
    """Attach one replay alias from a fresh Python process."""
    reservation = _reserve(
        ResearchInputStore(database),
        "run-child-process",
        "identity-process",
        alias="alias-process",
    )
    result.put(
        (reservation is not None and reservation.replay,)
        + ((reservation.run_id if reservation is not None else None),)
    )


def test_twenty_connections_admit_one_owner_and_replay_the_same_run(
    tmp_path: Path,
) -> None:
    """Twenty independent SQLite clients elect one owner for one key."""
    _initial, database = _store(tmp_path)
    barrier = Barrier(20)

    def reserve(index: int) -> ResearchAdmissionReservation | None:
        store = ResearchInputStore(database)
        barrier.wait()
        return _reserve(store, f"run-{index}", "identity-1")

    with ThreadPoolExecutor(max_workers=20) as executor:
        reservations = list(executor.map(reserve, range(20)))

    resolved = [item for item in reservations if item is not None]
    assert len(resolved) == 20
    owners = [item for item in resolved if not item.replay]
    replays = [item for item in resolved if item.replay]
    assert len(owners) == 1
    assert len(replays) == 19
    assert {item.run_id for item in resolved} == {owners[0].run_id}


def test_restart_attaches_one_alias_and_rejects_an_alias_conflict(
    tmp_path: Path,
) -> None:
    """A restarted process attaches an alias only to its original run."""
    first, database = _store(tmp_path)
    owner = _reserve(first, "run-original", "identity-a")
    assert owner is not None
    assert not owner.replay

    restarted = ResearchInputStore(database)
    replay = _reserve(
        restarted, "run-replay", "identity-a", alias="alias-digest"
    )
    conflict = _reserve(
        restarted, "run-conflict", "identity-b", alias="alias-digest"
    )

    assert replay is not None and replay.replay
    assert replay.run_id == "run-original"
    assert conflict is None


def test_process_restart_preserves_admission_identity_and_alias(
    tmp_path: Path,
) -> None:
    """A new interpreter reopens SQLite and attaches the original alias."""
    store, database = _store(tmp_path)
    first = _reserve(store, "run-process", "identity-process")
    assert first is not None and not first.replay
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(
        target=_attach_alias_after_process_restart,
        args=(database, result),
    )

    process.start()
    process.join(timeout=15)

    assert process.exitcode == 0
    assert result.get(timeout=3) == (True, "run-process")
    assert (
        _reserve(
            ResearchInputStore(database),
            "run-process-conflict",
            "other-identity",
            alias="alias-process",
        )
        is None
    )


def test_lease_reclaim_discards_late_completion_and_reuses_success(
    tmp_path: Path,
) -> None:
    """A stale worker loses after restart while matching output reuses."""
    store, database = _store(tmp_path)
    _reserve(store, "run-work", "identity-work")
    store.add_work_unit(_work())
    now = datetime(2026, 8, 9, tzinfo=UTC)
    first = store.claim_work(
        "run-work:resolve", "worker-a", now, timedelta(seconds=1)
    )
    assert first is not None

    restarted = ResearchInputStore(database)
    second = restarted.claim_work(
        "run-work:resolve", "worker-b", now + timedelta(seconds=2)
    )
    assert second is not None
    assert not restarted.complete_work(
        first, "succeeded", now, output={"answer": "late"}
    )
    sent_revision = restarted.mark_sent(
        second.unit_id,
        "worker-b",
        second.revision,
        provider_request_digest="provider-request",
        provider_idempotency_digest="provider-request",
        now=now + timedelta(seconds=2),
    )
    assert sent_revision is not None
    current = restarted.claim_work(
        second.unit_id, "worker-c", now + timedelta(seconds=3)
    )
    assert current is None

    with closed_sqlite_connection(database) as connection:
        row = connection.execute(
            "SELECT revision FROM research_work_units WHERE unit_id = ?",
            (second.unit_id,),
        ).fetchone()
    assert row is not None
    sent = replace(second, revision=row[0], state="sent")
    assert restarted.complete_work(
        sent,
        "succeeded",
        now + timedelta(seconds=3),
        output={"answer": "ok"},
    )
    assert restarted.load_validated_output(
        second.unit_id,
        "input-1",
        "policy-1",
        "execution-1",
        "evidence-1",
    ) == {"answer": "ok"}


def test_cancellation_blocks_late_callback_and_purge_removes_old_run(
    tmp_path: Path,
) -> None:
    """Cancellation wins its race and the old run remains unreadable."""
    store, database = _store(tmp_path)
    reservation = _reserve(store, "run-cancel", "identity-cancel")
    assert reservation is not None
    outcome = cancel_research_run(store, "run-cancel", "owner-1", 0)

    restarted = ResearchInputStore(database)
    assert (
        restarted.claim_work(
            "run-cancel:resolve_root", "late-worker", datetime.now(UTC)
        )
        is None
    )
    assert outcome.status == "cancelled"
    restarted.purge_run("run-cancel")

    with closed_sqlite_connection(database) as connection:
        old_run = connection.execute(
            "SELECT run_id FROM runs WHERE run_id = 'run-cancel'"
        ).fetchone()
        old_resolution = connection.execute(
            "SELECT run_id FROM research_input_resolutions "
            "WHERE run_id = 'run-cancel'"
        ).fetchone()
    assert old_run is None
    assert old_resolution is None


def test_restart_keeps_one_child_plan_and_one_outbox_row(
    tmp_path: Path,
) -> None:
    """A committed child plan remains one dispatch after restart."""
    store, database = _store(tmp_path)
    reservation = _reserve(store, "run-outbox", "identity-outbox")
    assert reservation is not None
    prepared = PreparedResearchInput(
        effective_query="question",
        obs_file_list=(),
        data_list=MappingProxyType({"bucket/data.tsv": "dataset"}),
        inventory_digest="a" * 64,
        evidence_digest="e" * 64,
        execution_fingerprint="b" * 64,
        authority_ids=(),
    )
    child = ResearchChildPlan(
        ordinal=0,
        task_name="research_goal_0",
        goal_description="goal",
        context="",
        data_list=prepared.data_list,
        output_dir="research/run-outbox/children/part-001",
        thread_id="thread-0-run-outbox",
        interop_mode="off",
        interop_targets=(),
        dispatch_fingerprint="d" * 64,
    )
    plan = ResearchPlan(goals=(), children=(child,), digest="c" * 64)

    records = persist_plan_and_outbox(store, "run-outbox", 0, prepared, plan)
    restarted = ResearchInputStore(database)

    assert len(records) == 1
    with closed_sqlite_connection(database) as connection:
        rows = connection.execute(
            "SELECT outbox_id, state FROM research_dispatch_outbox "
            "WHERE run_id = 'run-outbox'"
        ).fetchall()
    assert rows == [(records[0].dispatch_id, "pending")]
    assert restarted.load_resolution("run-outbox") is not None
