# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable, non-plaintext state contracts for Research coordination."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime import research_input_store
from mcp_server_phytomni.runtime.research_input_store import (
    RESEARCH_WORK_LEASE,
    ResearchInputStore,
    ResearchWorkUnitRecord,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

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
    assert isinstance(claimed.lease_expires_at, datetime)
    assert claimed.lease_expires_at == now + RESEARCH_WORK_LEASE
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

    assert {
        "schema_version",
        "original_query_digest",
        "original_query_length",
        "effective_query",
        "source_map_json",
        "candidates_json",
        "managed_snapshot_json",
        "evidence_digest",
        "work_digest",
    } <= columns


def test_resolution_persistence_keeps_private_state_out_of_public_json(
    tmp_path: Path,
) -> None:
    """Only bounded private resolution state stores the effective query."""
    store, database = _store(tmp_path)

    store.persist_resolution(
        "run-1",
        original_query_digest="q" * 64,
        original_query_length=11,
        effective_query="private effective query",
        source_map={"version": 1, "spans": []},
        parsed_candidates=[{"dataset_id": "dataset-1"}],
        managed_snapshot=[{"asset_id": "asset-1", "revision": "v1"}],
        evidence_digest="e" * 64,
        work_digest="w" * 64,
    )

    connection = sqlite3.connect(database)
    try:
        private = connection.execute(
            "SELECT effective_query, source_map_json, candidates_json, "
            "managed_snapshot_json, original_query_digest, "
            "original_query_length FROM research_input_resolutions "
            "WHERE run_id = 'run-1'"
        ).fetchone()
        public = connection.execute(
            "SELECT result_json, query, request_json FROM runs "
            "WHERE run_id = 'run-1'"
        ).fetchone()
    finally:
        connection.close()

    assert private == (
        "private effective query",
        '{"spans":[],"version":1}',
        '[{"dataset_id":"dataset-1"}]',
        '[{"asset_id":"asset-1","revision":"v1"}]',
        "q" * 64,
        11,
    )
    assert public == (None, None, None)


@pytest.mark.parametrize("status", ("succeeded", "failed", "cancelled"))
def test_terminal_parent_rejects_work_and_resolution_mutations(
    tmp_path: Path, status: str
) -> None:
    """Terminal runs cannot receive new or CAS-updated private state."""
    store, database = _store(tmp_path)
    assert store.persist_resolution(
        "run-1",
        original_query_digest="q" * 64,
        original_query_length=1,
        effective_query="before-terminal",
        source_map={"version": 1},
        parsed_candidates=[],
        managed_snapshot=[],
        evidence_digest="e" * 64,
        work_digest="w" * 64,
    )
    registry = RunRegistry(database)
    assert registry.settle_run(
        "run-1", owner="owner", status=status, result={}
    )

    store.add_work_unit(_work_unit())
    assert not store.persist_resolution(
        "run-1",
        expected_revision=0,
        original_query_digest="q" * 64,
        original_query_length=2,
        effective_query="after-terminal",
        source_map={"version": 2},
        parsed_candidates=[],
        managed_snapshot=[],
        evidence_digest="e" * 64,
        work_digest="w" * 64,
    )

    with sqlite3.connect(database) as connection:
        work_count = connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE run_id = 'run-1'"
        ).fetchone()
        effective_query = connection.execute(
            "SELECT effective_query FROM research_input_resolutions "
            "WHERE run_id = 'run-1'"
        ).fetchone()

    assert work_count == (0,)
    assert effective_query == ("before-terminal",)


def test_successful_work_identity_includes_kind(tmp_path: Path) -> None:
    """Different deterministic work kinds may reuse an input digest safely."""
    store, _database = _store(tmp_path)
    store.add_work_unit(_work_unit())
    store.add_work_unit(
        ResearchWorkUnitRecord(
            unit_id="unit-2",
            run_id="run-1",
            kind="reconcile",
            state="pending",
            input_digest="input-digest",
            policy_digest="policy-digest",
            lease_owner=None,
            lease_expires_at=None,
            attempt=0,
            revision=0,
        )
    )
    now = datetime(2026, 8, 8, tzinfo=UTC)
    first = store.claim_work("unit-1", "worker-a", now)
    second = store.claim_work("unit-2", "worker-a", now)

    assert first is not None
    assert second is not None
    assert store.complete_work(first, "succeeded", now)
    assert store.complete_work(second, "succeeded", now)


def test_additive_private_schema_is_versioned_and_repeatable(
    tmp_path: Path,
) -> None:
    """Repeated initialization leaves the version and legacy values stable."""
    store, database = _store(tmp_path)
    connection = sqlite3.connect(database)
    try:
        before = connection.execute(
            "SELECT result_json FROM runs WHERE run_id = 'run-1'"
        ).fetchone()
    finally:
        connection.close()

    store.initialize()

    connection = sqlite3.connect(database)
    try:
        versions = connection.execute(
            "SELECT version FROM research_input_schema_version WHERE id = 1"
        ).fetchone()
        after = connection.execute(
            "SELECT result_json FROM runs WHERE run_id = 'run-1'"
        ).fetchone()
        tables = {
            "research_idempotency_bindings",
            "research_input_resolutions",
            "research_work_units",
            "research_dispatch_outbox",
        }
        columns = {
            row[1]
            for table in tables
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    finally:
        connection.close()

    assert versions == (1,)
    assert after == before
    assert "schema_version" in columns


def test_existing_private_rows_survive_additive_migration(
    tmp_path: Path,
) -> None:
    """Legacy private rows keep their bytes while safe columns are added."""
    database = str(tmp_path / "legacy-private.db")
    registry = RunRegistry(database)
    registry.create_run(
        RunSpec(
            run_id="run-1", user_id="owner", agent="research", origin="api"
        )
    )
    with sqlite3.connect(database) as connection:
        legacy_tables = {
            "research_idempotency_bindings": (
                "run_id TEXT",
                "idempotency_digest TEXT",
                "request_digest TEXT",
                "created_at TEXT",
            ),
            "research_input_resolutions": (
                "run_id TEXT",
                "effective_query_digest TEXT",
                "source_map_digest TEXT",
                "candidate_digest TEXT",
                "snapshot_digest TEXT",
                "evidence_digest TEXT",
                "work_digest TEXT",
                "created_at TEXT",
            ),
            "research_work_units": (
                "unit_id TEXT",
                "run_id TEXT",
                "kind TEXT",
                "state TEXT",
                "input_digest TEXT",
                "policy_digest TEXT",
                "lease_owner TEXT",
                "lease_expires_at TEXT",
                "attempt INTEGER",
                "revision INTEGER",
            ),
            "research_dispatch_outbox": (
                "outbox_id TEXT",
                "run_id TEXT",
                "unit_id TEXT",
                "payload_digest TEXT",
                "state TEXT",
                "attempt INTEGER",
                "revision INTEGER",
                "created_at TEXT",
            ),
        }
        for table, columns in legacy_tables.items():
            connection.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
        connection.execute(
            "INSERT INTO research_idempotency_bindings VALUES "
            "('run-1', 'identity', 'request', 'created')"
        )
        connection.execute(
            "INSERT INTO research_input_resolutions VALUES "
            "('run-1', 'effective', 'source', 'candidate', 'snapshot', "
            "'evidence', 'work', 'created')"
        )
        connection.execute(
            "INSERT INTO research_work_units VALUES "
            "('unit-1', 'run-1', 'resolve', 'pending', 'input', 'policy', "
            "NULL, NULL, 0, 0)"
        )
        connection.execute(
            "INSERT INTO research_dispatch_outbox VALUES "
            "('outbox-1', 'run-1', 'unit-1', 'payload', 'pending', 0, 0, "
            "'created')"
        )

    ResearchInputStore(database)

    with sqlite3.connect(database) as connection:
        binding = connection.execute(
            "SELECT idempotency_digest, request_digest FROM "
            "research_idempotency_bindings"
        ).fetchone()
        resolution = connection.execute(
            "SELECT effective_query_digest, source_map_digest, "
            "candidate_digest, "
            "snapshot_digest, evidence_digest, work_digest "
            "FROM research_input_resolutions"
        ).fetchone()
        work = connection.execute(
            "SELECT unit_id, input_digest, policy_digest "
            "FROM research_work_units"
        ).fetchone()
        outbox = connection.execute(
            "SELECT outbox_id, payload_digest FROM research_dispatch_outbox"
        ).fetchone()
        versioned = {
            table: "schema_version"
            in {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for table in (
                "research_idempotency_bindings",
                "research_input_resolutions",
                "research_work_units",
                "research_dispatch_outbox",
            )
        }

    assert binding == ("identity", "request")
    assert resolution == (
        "effective",
        "source",
        "candidate",
        "snapshot",
        "evidence",
        "work",
    )
    assert work == ("unit-1", "input", "policy")
    assert outbox == ("outbox-1", "payload")
    assert all(versioned.values())


def test_partial_private_table_gets_safe_digest_defaults(
    tmp_path: Path,
) -> None:
    """A partial legacy table is completed without rewriting its row."""
    database = str(tmp_path / "partial-private.db")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="run-1", user_id="owner", agent="research", origin="api"
        )
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE research_input_resolutions ("
            "run_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO research_input_resolutions(run_id) VALUES ('run-1')"
        )

    ResearchInputStore(database)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT run_id, effective_query_digest, evidence_digest, "
            "schema_version FROM research_input_resolutions"
        ).fetchone()

    assert row == ("run-1", "", "", 1)


def test_private_migration_failure_rolls_back_all_new_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected DDL failure leaves no half-migrated private schema."""
    database = str(tmp_path / "failed-migration.db")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="run-1", user_id="owner", agent="research", origin="api"
        )
    )

    def fail(_connection: sqlite3.Connection) -> None:
        """Inject a failure after tables and version metadata are staged."""
        raise sqlite3.OperationalError("injected migration failure")

    monkeypatch.setattr(research_input_store, "_ensure_indexes", fail)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        ResearchInputStore(database)

    with sqlite3.connect(database) as connection:
        private_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND "
            "name LIKE 'research_%'"
        ).fetchall()

    assert private_tables == []


def test_registry_expiry_purges_research_children_and_grants(
    tmp_path: Path,
) -> None:
    """Registry expiry removes only the stale coordinator subtree."""
    store, database = _store(tmp_path)
    registry = RunRegistry(database)
    registry.create_run(
        RunSpec(
            run_id="run-2", user_id="owner", agent="research", origin="api"
        )
    )
    manager = TaskManager(database)
    for run_id, task_id in (("run-1", "task-stale"), ("run-2", "task-keep")):
        manager.record(
            Submission(
                task_id=task_id,
                status="succeeded",
                output_dir="/out",
                run_context=RunContext(run_id=run_id, user_id="owner"),
            )
        )
    store.add_work_unit(_work_unit())
    store.add_work_unit(
        ResearchWorkUnitRecord(
            unit_id="unit-2",
            run_id="run-2",
            kind="resolve",
            state="pending",
            input_digest="input-2",
            policy_digest="policy",
            lease_owner=None,
            lease_expires_at=None,
            attempt=0,
            revision=0,
        )
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE research_object_grants ("
            "grant_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO research_object_grants VALUES (?, ?)",
            (("grant-stale", "run-1"), ("grant-keep", "run-2")),
        )
        connection.execute(
            "UPDATE runs SET expires_at = '2000-01-01T00:00:00+00:00' "
            "WHERE run_id = 'run-1'"
        )

    assert registry.purge_expired() == 1
    with sqlite3.connect(database) as connection:
        stale = connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE run_id = 'run-1'"
        ).fetchone()
        grants = connection.execute(
            "SELECT COUNT(*) FROM research_object_grants "
            "WHERE parent_run_id = 'run-1'"
        ).fetchone()
        keep_task = connection.execute(
            "SELECT task_id FROM tasks WHERE run_id = 'run-2'"
        ).fetchone()

    assert stale == (0,)
    assert grants == (0,)
    assert keep_task == ("task-keep",)


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
