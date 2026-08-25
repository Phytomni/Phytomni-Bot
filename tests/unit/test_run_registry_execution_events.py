# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Run-registry execution event integration tests."""

from __future__ import annotations

from mcp_server_phytomni.runtime.execution_event_store import (
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)


def test_terminal_on_creation_run_persists_started_and_terminal_events(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PHYTOMNI_EXECUTION_EVENTS_ENABLED", "true")
    db_path = str(tmp_path / "tasks.sqlite")
    registry = RunRegistry(db_path)

    registry.create_run(
        RunSpec("run-sync-1", "alice", "brief_gene", "http"),
        outcome=RunOutcome(status="succeeded", result={"answer": "done"}),
    )

    page = SQLiteExecutionEventStore(db_path).list_events(
        "run-sync-1",
        owner="alice",
        after_seq=0,
        limit=20,
    )
    assert page is not None
    assert [(item.kind, item.status) for item in page.items] == [
        ("run.started", "running"),
        ("run.succeeded", "succeeded"),
    ]
    projection = SQLiteExecutionEventStore(db_path).get_projection(
        "run-sync-1", owner="alice"
    )
    assert projection is not None
    assert projection.latest_seq == 2
    assert projection.status == "succeeded"
    assert projection.terminal is not None


def test_run_registry_resolves_owner_scoped_external_execution_identity(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "tasks.sqlite")
    registry = RunRegistry(db_path)
    execution_id = "turn-550e8400-e29b-41d4-a716-446655440000"

    registry.reserve_run(
        RunSpec("run-root-1", "alice", "brief_gene", "http"),
        request_info=RunRequestInfo(execution_id=execution_id),
        result={},
    )

    owned = registry.get_run_by_execution_id(execution_id, owner="alice")
    assert owned is not None
    assert owned.spec.run_id == "run-root-1"
    assert owned.request_info.execution_id == execution_id
    assert registry.get_run_by_execution_id(execution_id, owner="bob") is None


def test_execution_identity_and_terminal_ledger_survive_process_restart(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PHYTOMNI_EXECUTION_EVENTS_ENABLED", "true")
    db_path = str(tmp_path / "tasks.sqlite")
    execution_id = "turn-restart-550e8400-e29b-41d4-a716-446655440000"
    registry = RunRegistry(db_path)
    registry.reserve_run(
        RunSpec("run-restart-1", "alice", "brief_gene", "http"),
        request_info=RunRequestInfo(execution_id=execution_id),
        result={},
    )
    assert registry.settle_run(
        "run-restart-1",
        owner="alice",
        status="succeeded",
        result={"answer": "durable"},
        expected_revision=0,
    )

    restarted_registry = RunRegistry(db_path)
    restarted_store = SQLiteExecutionEventStore(db_path)
    rebound = restarted_registry.get_run_by_execution_id(
        execution_id,
        owner="alice",
    )
    page = restarted_store.list_events(
        "run-restart-1",
        owner="alice",
        after_seq=0,
        limit=20,
    )

    assert rebound is not None
    assert rebound.spec.run_id == "run-restart-1"
    assert rebound.status == "succeeded"
    assert rebound.result == {"answer": "durable"}
    assert page is not None
    assert [(item.kind, item.status) for item in page.items] == [
        ("run.started", "running"),
        ("run.succeeded", "succeeded"),
    ]
