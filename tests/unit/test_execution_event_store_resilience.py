# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Concurrency, rollback, and deterministic replay tests for event storage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime import execution_event_store as store_module
from mcp_server_phytomni.runtime.execution_event_limits import (
    DEFAULT_EXECUTION_EVENT_LIMITS,
)
from mcp_server_phytomni.runtime.execution_event_projection import (
    fold_execution_events,
)
from mcp_server_phytomni.runtime.execution_events import (
    parse_execution_event_intent,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_models import RunSpec


def _intent(kind: str, *, idempotency_key: str | None = None):
    payload: dict[str, object] = {}
    if kind == "decision.note":
        payload = {"text": "bounded decision"}
    if kind == "phase.progress":
        payload = {"phase": "analysis", "completed": 1, "total": 2}
    return parse_execution_event_intent(
        {
            "kind": kind,
            "status": "running",
            "summary": {"key": f"activity.{kind}", "text": kind},
            "payload": payload,
            **(
                {"idempotency_key": idempotency_key}
                if idempotency_key is not None
                else {}
            ),
        }
    )


def _store(tmp_path: Path, *, max_events: int = 10_000):
    from mcp_server_phytomni.runtime.execution_event_store import (
        SQLiteExecutionEventStore,
    )

    db_path = tmp_path / "resilience.db"
    registry = RunRegistry(str(db_path))
    registry.create_run(RunSpec("run-1", "alice", "chat", "local"))
    store = SQLiteExecutionEventStore(
        str(db_path),
        limits=replace(
            DEFAULT_EXECUTION_EVENT_LIMITS,
            max_events_per_run=max_events,
            progress_coalesce_ms=0,
        ),
    )
    return store


def test_concurrent_writers_allocate_one_dense_run_local_sequence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    def append(index: int):
        return store.append(
            "run-1",
            owner="alice",
            intent=_intent(
                "decision.note", idempotency_key=f"decision:{index}"
            ),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = tuple(executor.map(append, range(24)))

    assert sorted(event.seq for event in events) == list(range(1, 25))
    assert len({event.event_id for event in events}) == 24
    projection = store.get_projection("run-1", owner="alice")
    assert projection is not None
    assert projection.latest_seq == 24


def test_projection_failure_rolls_back_ledger_and_retry_commits_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    real_apply = store_module.apply_execution_event

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("simulated_projection_failure")

    monkeypatch.setattr(store_module, "apply_execution_event", fail_projection)
    intent = _intent("decision.note", idempotency_key="stable-retry")
    with pytest.raises(RuntimeError, match="simulated_projection_failure"):
        store.append("run-1", owner="alice", intent=intent)
    empty_page = store.list_events("run-1", owner="alice")
    assert empty_page is not None
    assert empty_page.items == ()

    monkeypatch.setattr(store_module, "apply_execution_event", real_apply)
    committed = store.append("run-1", owner="alice", intent=intent)
    retried = store.append("run-1", owner="alice", intent=intent)
    assert committed == retried
    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert page.items == (committed,)


def test_pruned_ledger_replays_to_the_same_projection(tmp_path: Path) -> None:
    store = _store(tmp_path, max_events=3)
    store.append("run-1", owner="alice", intent=_intent("run.started"))
    store.append("run-1", owner="alice", intent=_intent("phase.progress"))
    store.append("run-1", owner="alice", intent=_intent("phase.progress"))
    store.append("run-1", owner="alice", intent=_intent("decision.note"))

    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert [event.seq for event in page.items] == [1, 3, 4]
    replayed = fold_execution_events("run-1", page.items)
    cached = store.get_projection("run-1", owner="alice")
    assert replayed == cached
