# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Retention, volume, coalescing, and parent-run cleanup tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.execution_event_limits import (
    DEFAULT_EXECUTION_EVENT_LIMITS,
    ExecutionEventLimitError,
)
from mcp_server_phytomni.runtime.execution_events import (
    ExecutionEventIntent,
    parse_execution_event_intent,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_models import RunSpec


def _intent(
    kind: str,
    *,
    payload: Mapping[str, object] | None = None,
    status: str = "running",
) -> ExecutionEventIntent:
    return parse_execution_event_intent(
        {
            "kind": kind,
            "status": status,
            "summary": {"key": f"activity.{kind}", "text": kind},
            "payload": dict(payload or {}),
        }
    )


def _clock(values: tuple[str, ...]) -> Iterator[str]:
    yield from values


def _store(
    tmp_path: Path,
    *,
    max_events: int = 10_000,
    timestamps: tuple[str, ...] = ("2026-08-18T00:00:00Z",) * 20,
):
    from mcp_server_phytomni.runtime.execution_event_store import (
        SQLiteExecutionEventStore,
    )

    db_path = tmp_path / "retention.db"
    registry = RunRegistry(str(db_path))
    registry.create_run(RunSpec("run-1", "alice", "chat", "local"))
    times = _clock(timestamps)
    ids = iter(f"evt-{index}" for index in range(1, 30))
    store = SQLiteExecutionEventStore(
        str(db_path),
        event_id_factory=lambda: next(ids),
        clock=lambda: next(times),
        limits=replace(
            DEFAULT_EXECUTION_EVENT_LIMITS,
            max_events_per_run=max_events,
        ),
    )
    return db_path, registry, store


def test_redundant_same_phase_progress_is_coalesced_before_persistence(
    tmp_path: Path,
) -> None:
    _db_path, _registry, store = _store(
        tmp_path,
        timestamps=(
            "2026-08-18T00:00:00.000Z",
            "2026-08-18T00:00:00.100Z",
        ),
    )
    payload = {"phase": "analysis", "completed": 1, "total": 10}
    first = store.append(
        "run-1",
        owner="alice",
        intent=_intent("phase.progress", payload=payload),
    )
    second = store.append(
        "run-1",
        owner="alice",
        intent=_intent(
            "phase.progress",
            payload={**payload, "completed": 2},
        ),
    )

    assert second == first
    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert page.items == (first,)


def test_critical_fact_prunes_oldest_progress_and_keeps_sequence_monotonic(
    tmp_path: Path,
) -> None:
    _db_path, _registry, store = _store(
        tmp_path,
        max_events=2,
        timestamps=(
            "2026-08-18T00:00:00Z",
            "2026-08-18T00:00:01Z",
            "2026-08-18T00:00:02Z",
        ),
    )
    started = store.append(
        "run-1", owner="alice", intent=_intent("run.started")
    )
    progress = store.append(
        "run-1",
        owner="alice",
        intent=_intent(
            "phase.progress",
            payload={"phase": "analysis", "completed": 1, "total": 2},
        ),
    )
    todo = store.append(
        "run-1",
        owner="alice",
        intent=_intent(
            "todo.snapshot",
            payload={
                "items": [
                    {
                        "id": "finish",
                        "label_key": "todo.finish",
                        "status": "pending",
                    }
                ]
            },
        ),
    )

    assert (started.seq, progress.seq, todo.seq) == (1, 2, 3)
    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert [event.seq for event in page.items] == [1, 3]
    projection = store.get_projection("run-1", owner="alice")
    assert projection is not None
    assert projection.latest_seq == 3
    assert [item.id for item in projection.todos] == ["finish"]


def test_volume_limit_never_discards_non_droppable_facts(
    tmp_path: Path,
) -> None:
    _db_path, _registry, store = _store(tmp_path, max_events=2)
    store.append("run-1", owner="alice", intent=_intent("run.started"))
    store.append(
        "run-1",
        owner="alice",
        intent=_intent("todo.snapshot", payload={"items": []}),
    )

    with pytest.raises(
        ExecutionEventLimitError, match="event_volume_exceeded"
    ):
        store.append(
            "run-1",
            owner="alice",
            intent=_intent("run.succeeded", status="succeeded"),
        )

    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert [event.kind for event in page.items] == [
        "run.started",
        "todo.snapshot",
    ]


def test_parent_run_expiry_purges_event_ledger_and_projection(
    tmp_path: Path,
) -> None:
    db_path, registry, store = _store(tmp_path)
    store.append("run-1", owner="alice", intent=_intent("run.started"))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runs SET expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", "run-1"),
        )
        connection.commit()

    assert registry.purge_expired() == 1
    with sqlite3.connect(db_path) as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id = 'run-1'"
        ).fetchone()[0]
        projection_count = connection.execute(
            "SELECT COUNT(*) FROM run_event_projection WHERE run_id = 'run-1'"
        ).fetchone()[0]
    assert (event_count, projection_count) == (0, 0)
