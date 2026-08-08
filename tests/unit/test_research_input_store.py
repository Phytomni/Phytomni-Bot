# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable, non-plaintext state contracts for Research coordination."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.research_input_store import (
    RESEARCH_WORK_LEASE,
    ResearchInputStore,
    ResearchWorkUnitRecord,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec

pytestmark = pytest.mark.unit


def _work_unit() -> ResearchWorkUnitRecord:
    """Return the fixed pending unit used by the durable-store tests."""
    return ResearchWorkUnitRecord(
        unit_id="unit-1",
        run_id="run-1",
        kind="resolve",
        state="pending",
        input_digest="input-digest",
        policy_digest="policy-digest",
        lease_owner=None,
        lease_expires_at=None,
        attempt=0,
        revision=0,
    )


def _store(tmp_path: Path) -> tuple[ResearchInputStore, str]:
    """Create a coordinator store over a registry-owned SQLite database."""
    database = str(tmp_path / "research.db")
    registry = RunRegistry(database)
    registry.create_run(
        RunSpec(
            run_id="run-1", user_id="owner", agent="research", origin="api"
        )
    )
    return ResearchInputStore(database), database


def test_claim_and_heartbeat_are_revision_guarded(tmp_path: Path) -> None:
    """Only the lease owner for the current revision can renew a work item."""
    store, _database = _store(tmp_path)
    store.add_work_unit(_work_unit())
    now = datetime(2026, 8, 8, tzinfo=UTC)

    claimed = store.claim_work("unit-1", "worker-a", now, RESEARCH_WORK_LEASE)

    assert claimed is not None
    assert claimed.state == "leased"
    assert (
        store.heartbeat_work("unit-1", "worker-b", claimed.revision, now)
        is None
    )
    renewed = store.heartbeat_work("unit-1", "worker-a", claimed.revision, now)
    assert renewed is not None
    assert renewed.revision == claimed.revision + 1


def test_late_completion_does_not_overwrite_a_reclaimed_lease(
    tmp_path: Path,
) -> None:
    """A completion for a prior revision has zero-row CAS semantics."""
    store, _database = _store(tmp_path)
    store.add_work_unit(_work_unit())
    now = datetime(2026, 8, 8, tzinfo=UTC)
    first = store.claim_work("unit-1", "worker-a", now, timedelta(seconds=1))
    assert first is not None
    assert store.complete_work(first, "retryable_failed", now)
    second = store.claim_work(
        "unit-1", "worker-b", now + timedelta(seconds=2), RESEARCH_WORK_LEASE
    )
    assert second is not None

    assert not store.complete_work(first, "succeeded", now)
    assert store.complete_work(second, "succeeded", now)


def test_purge_run_deletes_private_children_but_not_another_run(
    tmp_path: Path,
) -> None:
    """The private-child purge leaves unrelated registry rows intact."""
    store, database = _store(tmp_path)
    other = RunRegistry(database)
    other.create_run(
        RunSpec(
            run_id="run-2", user_id="owner", agent="research", origin="api"
        )
    )
    store.add_work_unit(_work_unit())

    store.purge_run("run-1")

    assert other.get_run("run-2", owner="owner") is not None
    assert other.get_run("run-1", owner="owner") is None


def test_private_resolution_schema_contains_only_digest_metadata(
    tmp_path: Path,
) -> None:
    """Coordinator storage has no source-body or original-query column."""
    _store_instance, database = _store(tmp_path)
    connection = sqlite3.connect(database)
    try:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(research_input_resolutions)"
            ).fetchall()
        }
    finally:
        connection.close()

    assert columns == {
        "run_id",
        "effective_query_digest",
        "source_map_digest",
        "candidate_digest",
        "snapshot_digest",
        "evidence_digest",
        "work_digest",
        "created_at",
    }


def test_registry_adds_public_coordinator_columns_without_rewriting_result(
    tmp_path: Path,
) -> None:
    """Legacy result bytes survive the additive coordinator migration."""
    database = str(tmp_path / "legacy.db")
    result_json = '{"answer":"already persisted"}'
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                agent TEXT NOT NULL, origin TEXT NOT NULL,
                status TEXT NOT NULL, result_json TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                expires_at TEXT
            )
            """)
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "alice",
                "research",
                "api",
                "running",
                result_json,
                None,
                "t",
                "t",
                None,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    registry = RunRegistry(database)
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT result_json, stage, failure_json, revision FROM runs "
            "WHERE run_id = 'legacy'"
        ).fetchone()
    finally:
        connection.close()

    assert registry.get_run("legacy", owner="alice") is not None
    assert row == (result_json, None, None, 0)
