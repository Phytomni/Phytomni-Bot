# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Unit tests for durable, Bot-owned conversation context staging."""

from __future__ import annotations

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from mcp_server_phytomni.runtime.conversation_context.store import (
    ContextVersionConflictError,
    ConversationContextStore,
    ConversationTombstonedError,
    StagedTurn,
    StagedTurnConflictError,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_connection

pytestmark = pytest.mark.unit


@pytest.fixture
def store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> ConversationContextStore:
    """Create a store through the configured task-database path."""
    db_path = tmp_path / "server_tasks.db"
    monkeypatch.setenv("API_TASKS_DB_PATH", str(db_path))
    monkeypatch.setenv("API_RUN_TTL_OK_HOURS", "2")
    return ConversationContextStore()


def _staged(
    *,
    operation: str = "append",
    base_context_version: int = 0,
    selected_agent_id: str = "ChatAgent",
    route_source: str = "instant_lock",
    result: dict[str, object] | None = None,
    delta: dict[str, object] | None = None,
    ledger_version: str = "a" * 64,
    stage_metadata: dict[str, object] | None = None,
) -> StagedTurn:
    """Return one valid, intentionally unordered terminal proposal."""
    return StagedTurn(
        operation=operation,
        base_context_version=base_context_version,
        selected_agent_id=selected_agent_id,
        route_source=route_source,
        result=result or {"answer": "terminal"},
        delta=delta or {"summary": "bounded context"},
        ledger_version=ledger_version,
        schema_version=1,
        ledger_cursor=9,
        observed_mode="instant",
        stage_metadata=stage_metadata or {},
    )


def test_init_is_idempotent_and_adds_only_context_tables(
    store: ConversationContextStore,
) -> None:
    """The additive schema can be initialized repeatedly in one task DB."""
    duplicate = ConversationContextStore(store.db_path)

    with sqlite3.connect(store.db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indices = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert duplicate.db_path == store.db_path
    assert {"conversation_contexts", "conversation_turns"} <= tables
    assert "idx_conversation_turns_expires_at" in indices


def test_initialization_uses_shared_wal_and_busy_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The store uses the shared SQLite connection policy unchanged."""
    db_path = str(tmp_path / "tasks.db")
    observed: list[tuple[str, int]] = []

    @contextmanager
    def observing_connection(path: str):
        with sqlite_connection(path) as connection:
            observed.append(
                (
                    str(
                        connection.execute("PRAGMA journal_mode").fetchone()[0]
                    ),
                    int(
                        connection.execute("PRAGMA busy_timeout").fetchone()[0]
                    ),
                )
            )
            yield connection

    monkeypatch.setattr(
        "mcp_server_phytomni.runtime.conversation_context.store.sqlite_connection",
        observing_connection,
    )

    ConversationContextStore(db_path)

    assert observed == [("wal", 5000)]


def test_absent_context_starts_at_version_zero(
    store: ConversationContextStore,
) -> None:
    """A new conversation has no durable context and begins at version zero."""
    assert store.load_context("conversation-1") is None

    result = store.begin_turn("conversation-1", "1", "append", 0)

    assert result.created is True
    assert result.context_version == 0
    assert result.turn.state == "in_progress"


def test_commit_staged_turn_compare_and_swaps_from_zero_to_one(
    store: ConversationContextStore,
) -> None:
    """Settlement creates the first context only for the expected base."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())

    settlement = store.commit_staged_turn(
        "conversation-1",
        "1",
        expected_ledger_version="a" * 64,
        ledger_version="a" * 64,
    )
    context = settlement.context

    assert settlement.state == "committed"
    assert context.context_version == 1
    assert context.ledger_cursor == 9
    assert context.ledger_version == "a" * 64
    assert context.context == {"summary": "bounded context"}


def test_commit_updates_serialized_context_to_acknowledged_ledger_version(
    store: ConversationContextStore,
) -> None:
    """The context JSON reflects the ledger version accepted at settlement."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn(
        "conversation-1",
        "1",
        _staged(
            delta={
                "last_applied_ledger_version": "a" * 64,
                "summary": "bounded context",
            }
        ),
    )

    context = store.commit_staged_turn(
        "conversation-1", "1", "a" * 64, "b" * 64
    ).context

    assert context.ledger_version == "b" * 64
    assert context.context["last_applied_ledger_version"] == "b" * 64


def test_commit_rejects_a_stale_compare_and_swap(
    store: ConversationContextStore,
) -> None:
    """A stale turn cannot overwrite already-advanced business context."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())
    store.commit_staged_turn("conversation-1", "1", "a" * 64, "a" * 64)
    store.begin_turn("conversation-1", "2", "append", 0)
    store.stage_turn("conversation-1", "2", _staged())

    with pytest.raises(ContextVersionConflictError):
        store.commit_staged_turn("conversation-1", "2", "a" * 64, "b" * 64)


def test_concurrent_settlement_returns_one_commit_and_one_retry(
    store: ConversationContextStore,
) -> None:
    """Concurrent duplicate settlement derives a distinct atomic outcome."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())
    barrier = Barrier(2)

    def settle(contender: ConversationContextStore) -> str:
        barrier.wait()
        return contender.commit_staged_turn(
            "conversation-1", "1", "a" * 64, "a" * 64
        ).state

    duplicate = ConversationContextStore(store.db_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        states = list(executor.map(settle, (store, duplicate)))

    assert sorted(states) == ["already_applied", "committed"]
    context = store.load_context("conversation-1")
    assert context is not None
    assert context.context_version == 1


def test_duplicate_staging_returns_the_byte_equivalent_terminal_result(
    store: ConversationContextStore,
) -> None:
    """A retry reuses one staged row even when mapping key order differs."""
    store.begin_turn("conversation-1", "1", "append", 0)
    first = store.stage_turn(
        "conversation-1",
        "1",
        _staged(result={"b": 2, "a": 1}, delta={"z": 2, "a": 1}),
    )
    duplicate = store.stage_turn(
        "conversation-1",
        "1",
        _staged(result={"a": 1, "b": 2}, delta={"a": 1, "z": 2}),
    )

    assert duplicate == first
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_turns"
        ).fetchone() == (1,)


def test_staged_turn_round_trips_opaque_stage_metadata(
    store: ConversationContextStore,
) -> None:
    """Duplicate reads retain the bounded service metadata without widening it."""
    metadata = {
        "selected_agent_id": "ChatAgent",
        "context_degraded": True,
        "context_truncated": False,
    }
    store.begin_turn("conversation-1", "1", "append", 0)
    staged = store.stage_turn(
        "conversation-1", "1", _staged(stage_metadata=metadata)
    )
    duplicate = store.begin_turn("conversation-1", "1", "append", 0).turn

    assert staged.stage_metadata == metadata
    assert duplicate.stage_metadata == metadata


def test_legacy_staged_turn_without_metadata_still_deserializes(
    store: ConversationContextStore,
) -> None:
    """Rows written before stage metadata remain readable during retries."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())
    legacy_delta = json.dumps(
        {
            "__conversation_context_store__": {
                "schema_version": 1,
                "ledger_cursor": 9,
                "observed_mode": "instant",
            },
            "value": {"summary": "bounded context"},
        }
    )
    with store._write() as connection:  # noqa: SLF001 - legacy row seam
        connection.execute(
            "UPDATE conversation_turns SET delta_json = ? "
            "WHERE conversation_key = ? AND turn_id = ?",
            (legacy_delta, "conversation-1", "1"),
        )

    legacy = store.begin_turn("conversation-1", "1", "append", 0).turn

    assert legacy.state == "staged"
    assert legacy.stage_metadata is None


def test_conflicting_duplicate_staging_fails_closed(
    store: ConversationContextStore,
) -> None:
    """A changed terminal result never silently replaces a staged response."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged(result={"answer": "one"}))

    with pytest.raises(StagedTurnConflictError):
        store.stage_turn(
            "conversation-1",
            "1",
            _staged(result={"answer": "two"}),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (("selected_agent_id", "DeepGenomeAgent"), ("route_source", "fallback")),
)
def test_conflicting_duplicate_routing_proposal_fails_closed(
    store: ConversationContextStore,
    field: str,
    value: str,
) -> None:
    """A changed routing proposal never silently reuses a staged response."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())

    proposal = {field: value}
    with pytest.raises(StagedTurnConflictError):
        store.stage_turn("conversation-1", "1", _staged(**proposal))


def test_tombstone_clears_context_and_turns_then_refuses_new_work(
    store: ConversationContextStore,
) -> None:
    """Deletion prevents a later append or rebuild from reviving context."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())
    store.commit_staged_turn("conversation-1", "1", "a" * 64, "a" * 64)

    store.tombstone("conversation-1")

    context = store.load_context("conversation-1")
    assert context is not None
    assert context.state == "tombstoned"
    assert context.context == {}
    assert context.checkpoint_cleanup_state == "pending"
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_turns WHERE conversation_key = ?",
            ("conversation-1",),
        ).fetchone() == (0,)
    for operation in ("append", "rebuild"):
        with pytest.raises(ConversationTombstonedError):
            store.begin_turn("conversation-1", "2", operation, 1)


def test_repeated_tombstone_is_idempotent(
    store: ConversationContextStore,
) -> None:
    """Repeated deletion keeps the conversation tombstoned without turns."""
    store.tombstone("conversation-1")
    store.tombstone("conversation-1")

    context = store.load_context("conversation-1")
    assert context is not None
    assert context.state == "tombstoned"
    assert context.checkpoint_cleanup_state == "pending"
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_turns WHERE conversation_key = ?",
            ("conversation-1",),
        ).fetchone() == (0,)


def test_staged_rows_expire_using_the_success_ttl(
    store: ConversationContextStore,
) -> None:
    """Staged terminal results reuse the existing successful-run retention."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())
    with sqlite3.connect(store.db_path) as connection:
        expires_at = connection.execute(
            "SELECT expires_at FROM conversation_turns"
        ).fetchone()[0]
    expiry = datetime.fromisoformat(expires_at)
    assert (
        timedelta(hours=1, minutes=59)
        < expiry - datetime.now(UTC)
        < timedelta(hours=2, minutes=1)
    )

    assert store.purge_expired_staged(expiry) == 1
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_turns"
        ).fetchone() == (0,)


def test_store_logs_do_not_expose_conversation_payloads(
    store: ConversationContextStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Persistence emits no user content, summary, artifact path, or result."""
    user_query = "secret user request"
    summary = "private summary"
    artifact_path = "/private/artifacts/report.xlsx"
    raw_result = "raw terminal result"
    permission_list = "permission-list: admin,super_admin"
    secret_marker = "SECRET_MARKER_DO_NOT_LOG"
    caplog.set_level(logging.DEBUG)

    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn(
        "conversation-1",
        "1",
        _staged(
            result={
                "answer": raw_result,
                "query": user_query,
                "permissions": permission_list,
            },
            delta={
                "summary": summary,
                "artifact": artifact_path,
                "secret": secret_marker,
            },
        ),
    )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for forbidden in (
        user_query,
        summary,
        artifact_path,
        raw_result,
        permission_list,
        secret_marker,
    ):
        assert forbidden not in logged
