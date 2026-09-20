# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Behavior tests for the durable SQLite execution-event store."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.execution_event_store import (
    ExecutionEventRunNotFoundError,
    ExecutionEventStore,
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.execution_events import (
    ExecutionEventIntent,
    parse_execution_event_intent,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_models import RunSpec


def _intent(
    kind: str = "run.started",
    *,
    idempotency_key: str | None = None,
) -> ExecutionEventIntent:
    payload: dict[str, object] = {}
    status = "running"
    if kind == "decision.note":
        payload = {"text": "Selected the bounded path."}
    if kind == "run.succeeded":
        status = "succeeded"
    return parse_execution_event_intent(
        {
            "kind": kind,
            "status": status,
            "summary": {
                "key": f"activity.{kind}",
                "text": kind,
            },
            "payload": payload,
            **(
                {"idempotency_key": idempotency_key}
                if idempotency_key is not None
                else {}
            ),
        }
    )


def _ids() -> Iterator[str]:
    yield from ("evt-1", "evt-2", "evt-3", "evt-4")


def _store(tmp_path: Path):

    db_path = tmp_path / "runs.db"
    registry = RunRegistry(str(db_path))
    registry.create_run(RunSpec("run-1", "alice", "chat", "local"))
    registry.create_run(RunSpec("run-2", "bob", "chat", "local"))
    event_ids = _ids()
    store = SQLiteExecutionEventStore(
        str(db_path),
        event_id_factory=lambda: next(event_ids),
        clock=lambda: "2026-08-18T00:00:00Z",
    )
    return store


def test_store_implements_protocol_and_allocates_ordered_identity(
    tmp_path: Path,
) -> None:
    """Verify store implements protocol and allocates ordered identity."""

    store = _store(tmp_path)
    assert isinstance(store, ExecutionEventStore)

    first = store.append("run-1", owner="alice", intent=_intent())
    second = store.append(
        "run-1", owner="alice", intent=_intent("decision.note")
    )

    assert (first.seq, first.event_id) == (1, "evt-1")
    assert (second.seq, second.event_id) == (2, "evt-2")
    assert first.occurred_at == "2026-08-18T00:00:00Z"


def test_idempotent_append_returns_the_committed_event(tmp_path: Path) -> None:
    """Verify idempotent append returns the committed event."""
    store = _store(tmp_path)
    intent = _intent(idempotency_key="run:start")

    first = store.append("run-1", owner="alice", intent=intent)
    retried = store.append("run-1", owner="alice", intent=intent)

    assert retried == first
    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert page.items == (first,)


def test_owner_scoped_reads_hide_foreign_and_unknown_runs(
    tmp_path: Path,
) -> None:
    """Verify owner scoped reads hide foreign and unknown runs."""

    store = _store(tmp_path)
    event = store.append("run-1", owner="alice", intent=_intent())

    assert store.list_events("run-1", owner="mallory") is None
    assert store.list_events("missing", owner="alice") is None
    assert store.get_event("run-1", event.event_id, owner="mallory") is None
    assert store.get_event("missing", event.event_id, owner="alice") is None
    with pytest.raises(ExecutionEventRunNotFoundError):
        store.append("run-1", owner="mallory", intent=_intent())
    with pytest.raises(ExecutionEventRunNotFoundError):
        store.append("missing", owner="alice", intent=_intent())


def test_event_pages_are_bounded_and_resume_after_sequence(
    tmp_path: Path,
) -> None:
    """Verify event pages are bounded and resume after sequence."""
    store = _store(tmp_path)
    events = tuple(
        store.append("run-1", owner="alice", intent=_intent(kind))
        for kind in ("run.started", "decision.note", "run.succeeded")
    )

    first = store.list_events("run-1", owner="alice", limit=1)
    assert first is not None
    assert first.items == events[:1]
    assert first.next_after_seq == 1
    assert first.has_more is True

    second = store.list_events(
        "run-1", owner="alice", after_seq=first.next_after_seq, limit=2
    )
    assert second is not None
    assert second.items == events[1:]
    assert second.next_after_seq == 3
    assert second.has_more is False


def test_event_detail_is_owner_scoped_and_round_trips_typed_payload(
    tmp_path: Path,
) -> None:
    """Verify event detail is owner scoped and round trips typed payload."""
    store = _store(tmp_path)
    appended = store.append(
        "run-1", owner="alice", intent=_intent("decision.note")
    )

    loaded = store.get_event("run-1", appended.event_id, owner="alice")

    assert loaded == appended
    assert loaded is not None
    assert loaded.to_public_dict()["payload"] == {
        "text": "Selected the bounded path."
    }
