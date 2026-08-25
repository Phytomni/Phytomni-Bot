# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite provider conformance for the execution-keyed V2 journal."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest


def _seed_execution(db_path: Path, owner: str, execution_id: str) -> None:
    from mcp_server_phytomni.runtime.run_registry import RunRegistry
    from mcp_server_phytomni.runtime.run_registry_models import (
        RunRequestInfo,
        local_run_spec,
    )

    registry = RunRegistry(str(db_path))
    registry.create_run(
        local_run_spec(f"run-{execution_id}", owner, "chat"),
        request_info=RunRequestInfo(execution_id=execution_id),
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_id = ? WHERE run_id = ?",
            (execution_id, f"run-{execution_id}"),
        )
        connection.commit()


def _intent(index: int, *, idempotency_key: str | None = None):
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        parse_execution_event_intent_v2,
    )

    return parse_execution_event_intent_v2(
        {
            "type": "span.started",
            "status": "running",
            "source": "graph",
            "span_id": f"span-{index}",
            "parent_span_id": "span-root",
            "work_unit_id": f"work-{index}",
            "attempt": 1,
            "summary": {
                "key": "activity.phase.started",
                "text": f"Phase {index} started",
            },
            "public_payload": {"phase": f"phase-{index}"},
            "idempotency_key": idempotency_key,
        }
    )


def _typed_intent(
    event_type: str,
    status: str,
    payload: dict[str, object],
    *,
    idempotency_key: str,
    target: dict[str, str] | None = None,
    work_unit_id: str | None = None,
    attempt: int = 1,
):
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        parse_execution_event_intent_v2,
    )

    return parse_execution_event_intent_v2(
        {
            "type": event_type,
            "status": status,
            "source": "runtime",
            "span_id": "span-root",
            "work_unit_id": work_unit_id,
            "attempt": attempt,
            "summary": {"key": "activity.test", "text": event_type},
            "public_payload": payload,
            "target": target,
            "idempotency_key": idempotency_key,
        }
    )


def test_append_allocates_sequence_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = tmp_path / "journal.db"
    _seed_execution(db_path, "alice", "turn-1")
    ids = iter(("evt-1", "evt-should-not-be-used"))
    journal = SQLiteExecutionJournal(
        str(db_path),
        event_id_factory=lambda: next(ids),
        clock=lambda: "2026-08-19T00:00:00Z",
    )

    first = journal.append(
        "turn-1",
        owner="alice",
        intent=_intent(1, idempotency_key="source:phase-1:start"),
    )
    replay = journal.append(
        "turn-1",
        owner="alice",
        intent=_intent(1, idempotency_key="source:phase-1:start"),
    )

    assert first == replay
    assert first.seq == 1
    assert first.event_id == "evt-1"
    page = journal.list_events("turn-1", owner="alice")
    assert page is not None
    assert page.items == (first,)
    assert page.next_after_seq == 1
    assert page.has_more is False
    assert journal.get_event("turn-1", "evt-1", owner="alice") == first


def test_concurrent_append_allocates_unique_commit_order(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = tmp_path / "concurrent.db"
    _seed_execution(db_path, "alice", "turn-concurrent")
    journal = SQLiteExecutionJournal(str(db_path))

    def append(index: int) -> int:
        return journal.append(
            "turn-concurrent",
            owner="alice",
            intent=_intent(index, idempotency_key=f"source:{index}"),
        ).seq

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append, range(1, 33)))

    assert sorted(sequences) == list(range(1, 33))
    first = journal.list_events(
        "turn-concurrent", owner="alice", after_seq=0, limit=7
    )
    assert first is not None
    assert [event.seq for event in first.items] == list(range(1, 8))
    assert first.next_after_seq == 7
    assert first.has_more is True


def test_pages_and_owner_checks_fail_closed(tmp_path: Path) -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        ExecutionEventLimitError,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        ExecutionJournalNotFoundError,
        SQLiteExecutionJournal,
    )

    db_path = tmp_path / "owners.db"
    _seed_execution(db_path, "alice", "turn-owned")
    journal = SQLiteExecutionJournal(str(db_path))
    journal.append("turn-owned", owner="alice", intent=_intent(1))

    with pytest.raises(ExecutionJournalNotFoundError):
        journal.list_events("turn-owned", owner="mallory")
    with pytest.raises(ExecutionJournalNotFoundError):
        journal.append("turn-missing", owner="alice", intent=_intent(2))
    with pytest.raises(ExecutionEventLimitError):
        journal.list_events("turn-owned", owner="alice", limit=201)


def test_progress_coalescing_retention_and_sequence_gaps(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_EVENT_LIMITS,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = tmp_path / "retention.db"
    _seed_execution(db_path, "alice", "turn-retention")
    moments = iter(
        [
            "2026-08-19T00:00:00.000Z",
            "2026-08-19T00:00:01.000Z",
            "2026-08-19T00:00:01.100Z",
            "2026-08-19T00:00:02.000Z",
            "2026-08-19T00:00:03.000Z",
            "2026-08-19T00:00:04.000Z",
            "2026-08-19T00:00:05.000Z",
        ]
    )
    limits = replace(DEFAULT_EXECUTION_EVENT_LIMITS, max_events_per_run=5)
    journal = SQLiteExecutionJournal(
        str(db_path), limits=limits, clock=lambda: next(moments)
    )
    admitted = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "execution.admitted", "admitted", {}, idempotency_key="admitted"
        ),
    )
    progress = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "span.progress",
            "running",
            {"phase": "search", "completed": 1, "total": 3},
            idempotency_key="progress-1",
        ),
    )
    coalesced = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "span.progress",
            "running",
            {"phase": "search", "completed": 2, "total": 3},
            idempotency_key="progress-2",
        ),
    )
    assert coalesced == progress
    coalesced_replay = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "span.progress",
            "running",
            {"phase": "search", "completed": 2, "total": 3},
            idempotency_key="progress-2",
        ),
    )
    assert coalesced_replay == progress
    later_progress = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "span.progress",
            "running",
            {"phase": "search", "completed": 3, "total": 3},
            idempotency_key="progress-3",
        ),
    )
    message = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "message.completed",
            "succeeded",
            {
                "message_id": "assistant-turn-retention",
                "source_message_id": "assistant-turn-retention",
                "output_revision": 1,
                "offset": 4,
                "base_offset": 0,
                "total_length": 4,
                "chunk_index": 0,
                "chunk_count": 1,
                "content_sha256": hashlib.sha256(b"done").hexdigest(),
                "text": "done",
            },
            idempotency_key="message",
        ),
    )
    artifact = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "artifact.published",
            "succeeded",
            {"name": "result.csv", "media_type": "text/csv", "size_bytes": 4},
            target={"kind": "artifact", "id": "artifact-result"},
            idempotency_key="artifact",
        ),
    )
    terminal = journal.append(
        "turn-retention",
        owner="alice",
        intent=_typed_intent(
            "execution.succeeded", "succeeded", {}, idempotency_key="terminal"
        ),
    )

    page = journal.list_events("turn-retention", owner="alice", limit=20)
    assert page is not None
    assert [event.event_id for event in page.items] == [
        admitted.event_id,
        later_progress.event_id,
        message.event_id,
        artifact.event_id,
        terminal.event_id,
    ]
    assert [
        (gap.first_missing_seq, gap.last_missing_seq) for gap in page.gaps
    ] == [(2, 2)]
    projection = journal.get_projection("turn-retention", owner="alice")
    assert projection.status.value == "succeeded"
    assert projection.latest_seq == terminal.seq
    assert projection.output_revision == 1
    assert [result.name for result in projection.results] == ["result.csv"]


def test_tombstone_purges_only_v2_execution_children(tmp_path: Path) -> None:
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        ExecutionJournalNotFoundError,
        SQLiteExecutionJournal,
    )

    db_path = tmp_path / "tombstone.db"
    _seed_execution(db_path, "alice", "turn-delete")
    journal = SQLiteExecutionJournal(str(db_path))
    journal.append("turn-delete", owner="alice", intent=_intent(1))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS run_events (run_id TEXT, seq INTEGER)"
        )
        connection.execute(
            "INSERT INTO run_events VALUES ('run-turn-delete', 1)"
        )
        connection.commit()

    journal.tombstone_execution("turn-delete", owner="alice")

    with pytest.raises(ExecutionJournalNotFoundError):
        journal.list_events("turn-delete", owner="alice")
    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_events_v2"
            ).fetchone()[0]
            == 0
        )


def test_failed_append_rolls_back_and_corrupt_projection_rebuilds(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = tmp_path / "crash.db"
    _seed_execution(db_path, "alice", "turn-crash")

    def fail_event_id() -> str:
        raise RuntimeError("simulated crash before commit")

    crashing = SQLiteExecutionJournal(
        str(db_path), event_id_factory=fail_event_id
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        crashing.append("turn-crash", owner="alice", intent=_intent(1))
    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_events_v2"
            ).fetchone()[0]
            == 0
        )

    journal = SQLiteExecutionJournal(str(db_path))
    first = journal.append("turn-crash", owner="alice", intent=_intent(1))
    assert first.seq == 1
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE execution_projection_v2 SET projection_json = 'not-json' "
            "WHERE owner_ref = 'alice' AND execution_id = 'turn-crash'"
        )
        connection.commit()

    rebuilt = journal.get_projection("turn-crash", owner="alice")
    assert rebuilt.latest_seq == 1
    assert rebuilt.active_span_ids == ("span-1",)
    second = journal.append("turn-crash", owner="alice", intent=_intent(2))
    assert second.seq == 2


def test_retry_attempts_and_partial_join_remain_inspectable(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = tmp_path / "partial.db"
    _seed_execution(db_path, "alice", "turn-partial")
    journal = SQLiteExecutionJournal(str(db_path))
    first_attempt = journal.append(
        "turn-partial",
        owner="alice",
        intent=_typed_intent(
            "work_unit.failed",
            "failed",
            {
                "code": "provider_timeout",
                "retryable": True,
                "work_unit_id": "work-a",
            },
            work_unit_id="work-a",
            attempt=1,
            idempotency_key="work-a:1:failed",
        ),
    )
    second_attempt = journal.append(
        "turn-partial",
        owner="alice",
        intent=_typed_intent(
            "work_unit.succeeded",
            "succeeded",
            {"operation_key": "provider.search", "source_revision": 2},
            work_unit_id="work-a",
            attempt=2,
            idempotency_key="work-a:2:succeeded",
        ),
    )
    journal.append(
        "turn-partial",
        owner="alice",
        intent=_typed_intent(
            "artifact.published",
            "succeeded",
            {"name": "partial.csv", "media_type": "text/csv", "size_bytes": 7},
            target={"kind": "artifact", "id": "artifact-partial"},
            idempotency_key="artifact-partial",
        ),
    )
    journal.append(
        "turn-partial",
        owner="alice",
        intent=_typed_intent(
            "execution.partial",
            "partial",
            {
                "code": "best_effort_join",
                "retryable": False,
                "work_unit_id": "work-b",
            },
            work_unit_id="work-b",
            idempotency_key="partial-terminal",
        ),
    )

    assert (first_attempt.attempt, second_attempt.attempt) == (1, 2)
    projection = journal.get_projection("turn-partial", owner="alice")
    assert projection.status.value == "partial"
    assert projection.failed_work_unit_ids == ("work-a", "work-b")
    assert [warning.code for warning in projection.warnings] == [
        "provider_timeout",
        "best_effort_join",
    ]
    assert [result.name for result in projection.results] == ["partial.csv"]
