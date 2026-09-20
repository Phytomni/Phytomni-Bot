# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Projection folding and cache-rebuild tests for execution events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from tests.support.execution_event_fixtures import todo_snapshot_intent

from mcp_server_phytomni.runtime.execution_event_projection import (
    fold_execution_events,
)
from mcp_server_phytomni.runtime.execution_event_store import (
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.execution_events import (
    ExecutionEventIntent,
    ExecutionEventV1,
    parse_execution_event_intent,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_models import RunSpec
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def _intent(
    kind: str,
    *,
    status: str = "running",
    payload: Mapping[str, object] | None = None,
    target: Mapping[str, object] | None = None,
) -> ExecutionEventIntent:
    raw: dict[str, object] = {
        "kind": kind,
        "status": status,
        "summary": {"key": f"activity.{kind}", "text": kind},
        "payload": dict(payload or {}),
    }
    if target is not None:
        raw["target"] = dict(target)
    return parse_execution_event_intent(raw)


def _events() -> tuple[ExecutionEventV1, ...]:
    intents = (
        _intent("run.started"),
        _intent(
            "phase.started",
            payload={"phase": "retrieval", "label_key": "phase.retrieval"},
        ),
        _intent(
            "todo.snapshot",
            payload={
                "items": [
                    {
                        "id": "prepare",
                        "label_key": "todo.prepare",
                        "status": "completed",
                    },
                    {
                        "id": "analyze",
                        "label_key": "todo.analyze",
                        "status": "in_progress",
                    },
                ]
            },
        ),
        _intent(
            "input.required",
            status="waiting",
            payload={"surface_id": "surface-1", "widget": "confirm"},
        ),
        _intent(
            "input.resolved",
            payload={"surface_id": "surface-1", "outcome": "accepted"},
        ),
        _intent(
            "artifact.published",
            status="succeeded",
            payload={
                "name": "result.csv",
                "media_type": "text/csv",
                "size_bytes": 128,
            },
            target={"kind": "artifact", "id": "artifact-1"},
        ),
        _intent("run.succeeded", status="succeeded"),
    )
    return tuple(
        intent.materialize(
            run_id="run-1",
            seq=index,
            event_id=f"evt-{index}",
            occurred_at=f"2026-08-18T00:00:0{index}Z",
        )
        for index, intent in enumerate(intents, start=1)
    )


def test_fold_reconstructs_lifecycle_todo_results_and_terminal_state() -> None:
    """Verify fold reconstructs lifecycle todo results and terminal state."""

    projection = fold_execution_events("run-1", _events())

    assert projection.latest_seq == 7
    assert projection.status == "succeeded"
    assert projection.phase == "retrieval"
    assert [item.id for item in projection.todos] == ["prepare", "analyze"]
    assert projection.todos[1].status == "in_progress"
    assert projection.input_required is None
    assert len(projection.results) == 1
    assert projection.results[0].target.id == "artifact-1"
    assert projection.terminal is not None
    assert projection.terminal.event_id == "evt-7"


def _store(tmp_path: Path):

    db_path = tmp_path / "projection.db"
    registry = RunRegistry(str(db_path))
    registry.create_run(RunSpec("run-1", "alice", "chat", "local"))
    ids = iter(f"evt-{index}" for index in range(1, 20))
    store = SQLiteExecutionEventStore(
        str(db_path),
        event_id_factory=lambda: next(ids),
        clock=lambda: "2026-08-18T00:00:00Z",
    )
    return db_path, store


def test_store_append_updates_the_replaceable_projection(
    tmp_path: Path,
) -> None:
    """Verify store append updates the replaceable projection."""
    _db_path, store = _store(tmp_path)
    store.append("run-1", owner="alice", intent=_intent("run.started"))
    store.append(
        "run-1",
        owner="alice",
        intent=todo_snapshot_intent(
            "one",
            "todo.one",
            "in_progress",
        ),
    )

    projection = store.get_projection("run-1", owner="alice")

    assert projection is not None
    assert projection.latest_seq == 2
    assert [item.id for item in projection.todos] == ["one"]
    assert store.get_projection("run-1", owner="mallory") is None


def test_missing_or_corrupt_projection_is_rebuilt_from_ledger(
    tmp_path: Path,
) -> None:
    """Verify missing or corrupt projection is rebuilt from ledger."""
    db_path, store = _store(tmp_path)
    for event in _events()[:3]:
        intent = parse_execution_event_intent(
            {
                key: value
                for key, value in event.to_public_dict().items()
                if key
                in {
                    "kind",
                    "status",
                    "summary",
                    "payload",
                    "target",
                    "task_id",
                    "parent_event_id",
                    "idempotency_key",
                }
            }
        )
        store.append("run-1", owner="alice", intent=intent)
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE run_event_projection SET projection_json = ?, "
            "latest_seq = ? "
            "WHERE run_id = ?",
            ("{not-json", 99, "run-1"),
        )
        connection.commit()

    rebuilt = store.get_projection("run-1", owner="alice")

    assert rebuilt is not None
    assert rebuilt.latest_seq == 3
    assert [item.id for item in rebuilt.todos] == ["prepare", "analyze"]
    with sqlite_transaction(db_path) as connection:
        cached = connection.execute(
            "SELECT latest_seq, projection_json FROM run_event_projection "
            "WHERE run_id = 'run-1'"
        ).fetchone()
    assert cached is not None
    assert cached[0] == 3
    assert json.loads(cached[1])["latest_seq"] == 3


def test_later_todo_snapshot_replaces_earlier_items_atomically(
    tmp_path: Path,
) -> None:
    """Verify later todo snapshot replaces earlier items atomically."""
    _db_path, store = _store(tmp_path)
    for item_id in ("old", "new"):
        store.append(
            "run-1",
            owner="alice",
            intent=_intent(
                "todo.snapshot",
                payload={
                    "items": [
                        {
                            "id": item_id,
                            "label_key": f"todo.{item_id}",
                            "status": "pending",
                        }
                    ]
                },
            ),
        )

    projection = store.get_projection("run-1", owner="alice")
    assert projection is not None
    assert [item.id for item in projection.todos] == ["new"]
