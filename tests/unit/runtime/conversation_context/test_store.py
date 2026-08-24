# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for durable, Bot-owned conversation context staging."""

from __future__ import annotations

import inspect
import json
import logging
import sqlite3
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID

import pytest

from mcp_server_phytomni.agents.review.conversation import _candidate_thread_id
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context.review_support import (
    _ReviewClaimLookupRequest,
    _ReviewMarkerWriteRequest,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ContextVersionConflictError,
    ConversationContextStore,
    ConversationTombstonedError,
    ReviewMutationLock,
    StagedTurn,
    StagedTurnConflictError,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_connection

pytestmark = pytest.mark.unit


@pytest.fixture(name="store")
def conversation_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> ConversationContextStore:
    """Create a store through the configured task-database path."""
    db_path = tmp_path / "server_tasks.db"
    monkeypatch.setenv("API_TASKS_DB_PATH", str(db_path))
    monkeypatch.setenv("API_RUN_TTL_OK_HOURS", "2")
    return ConversationContextStore()


def _staged(
    **options: Any,
) -> StagedTurn:
    """Return one valid, intentionally unordered terminal proposal."""
    operation = options.get("operation", "append")
    base_context_version = options.get("base_context_version", 0)
    selected_agent_id = options.get("selected_agent_id", "ChatAgent")
    route_source = options.get("route_source", "instant_lock")
    result = options.get("result")
    delta = options.get("delta")
    ledger_version = options.get("ledger_version", "a" * 64)
    stage_metadata = options.get("stage_metadata")
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


def test_review_classmethod_seams_preserve_subclass_dispatch() -> None:
    """Review classmethod seams retain private subclass overrides."""
    stable_thread_id = "ctx-" + "a" * 64
    marker = {
        "version": 1,
        "operation": "new_review",
        "stable_thread_id": stable_thread_id,
        "turn_id": "turn-1",
        "report_revision": 0,
    }

    class OverrideStore(ConversationContextStore):
        """Override marker interpretation for subclass dispatch coverage."""

        @staticmethod
        def _marker_operation(_marker: Mapping[str, Any]) -> str:
            return "custom"

        @staticmethod
        def _marker_candidate_is_bounded(
            _marker: Mapping[str, Any], operation: str
        ) -> bool:
            return operation == "custom"

        @staticmethod
        def _stable_marker_matches_key(_stable: str, _key: str) -> bool:
            return True

    assert getattr(OverrideStore, "_bounded_marker_fields")(
        marker, "turn-1"
    ) == (
        "custom",
        stable_thread_id,
    )
    assert getattr(OverrideStore, "_marker_is_bounded")(
        marker, key="not-a-uuid", turn_id="turn-1"
    )


def test_review_marker_write_preserves_subclass_dispatch() -> None:
    """Review marker writes use a subclass's private encoder override."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE conversation_turns ("
        "delta_json TEXT, updated_at TEXT, conversation_key TEXT, turn_id TEXT"
        ")"
    )
    connection.execute(
        "INSERT INTO conversation_turns VALUES (?, ?, ?, ?)",
        ("before", "before", "key", "turn"),
    )

    class OverrideStore(ConversationContextStore):
        """Override marker encoding for subclass dispatch coverage."""

        @staticmethod
        def _with_review_record(
            _decoded: dict[str, Any], _marker: Mapping[str, Any]
        ) -> str:
            return "encoded-by-subclass"

    request = _ReviewMarkerWriteRequest(
        connection=connection,
        key="key",
        turn_id="turn",
        decoded={},
        marker={},
        now="now",
    )
    assert getattr(OverrideStore, "_write_review_marker")(request)
    assert connection.execute(
        "SELECT delta_json, updated_at FROM conversation_turns"
    ).fetchone() == ("encoded-by-subclass", "now")


def test_review_claim_row_failure_preserves_instance_dispatch() -> None:
    """Review claim preconditions use an instance's private state override."""

    class OverrideStore(ConversationContextStore):
        """Override claim state for instance dispatch coverage."""

        @staticmethod
        def _review_context_state(
            _connection: sqlite3.Connection, _key: str
        ) -> tuple[int, str]:
            return (0, "tombstoned")

    request = _ReviewClaimLookupRequest(
        connection=sqlite3.connect(":memory:"),
        key="key",
        row=("staged", "ledger", 0, "delta"),
        expected_ledger_version=None,
        expected_base_context_version=None,
    )
    instance = object.__new__(OverrideStore)
    failure = getattr(instance, "_claim_row_failure")(request)
    assert failure is not None
    assert failure.status == "conflict"


def test_review_reservation_preflight_preserves_subclass_dispatch() -> None:
    """Reservation preflight uses an instance's private override."""

    class OverrideStore(ConversationContextStore):
        """Reject reservation preflight before touching storage."""

        @staticmethod
        def _review_reservation_inputs_valid(
            _claim_token: object, _fence_token: object
        ) -> bool:
            return False

    instance = object.__new__(OverrideStore)
    result = instance.reserve_review_settlement(
        "key", "turn", claim_token="token", fence_token=1
    )
    assert result.status == "invalid"


def test_commit_apply_seam_signature_is_preserved() -> None:
    """The extracted commit seam keeps its historical bound signature."""
    signature = inspect.signature(
        getattr(ConversationContextStore, "_apply_staged_turn_locked")
    )
    assert tuple(signature.parameters) == ("self", "request")
    assert signature.parameters["self"].annotation is inspect.Parameter.empty


def test_claim_expiry_keeps_static_base_clock_dispatch() -> None:
    """Claim expiry keeps the store's original static clock semantics."""

    class OverrideStore(ConversationContextStore):
        """Keep claim expiry independent of overridable clock helpers."""

        @staticmethod
        def _claim_datetime(_value: datetime | str | None) -> datetime:
            raise AssertionError("static claim expiry must ignore overrides")

    now = datetime.now(UTC)
    assert getattr(OverrideStore, "_claim_is_expired")(
        (now - timedelta(minutes=1)).isoformat(),
        clock=now,
        stale_after=timedelta(seconds=1),
    )


def test_review_lock_public_identity_is_preserved(
    request: pytest.FixtureRequest,
) -> None:
    """The extracted lock keeps the store's public class identity."""
    context_store = request.getfixturevalue("store")
    assert isinstance(context_store, ConversationContextStore)
    lock = context_store.acquire_review_mutation_lock(timeout=0)
    try:
        assert isinstance(lock, ReviewMutationLock)
        assert ReviewMutationLock.__module__.endswith(
            "conversation_context.store"
        )
    finally:
        lock.release()


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
        cleanup_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(conversation_review_checkpoint_cleanup)"
            )
        }

    assert duplicate.db_path == store.db_path
    assert {"conversation_contexts", "conversation_turns"} <= tables
    assert "idx_conversation_turns_expires_at" in indices
    assert "eligible_at" in cleanup_columns


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
        "mcp_server_phytomni.runtime.conversation_context.store."
        "sqlite_connection",
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


def test_reopen_turn_resets_committed_append_for_replace(
    store: ConversationContextStore,
) -> None:
    """Replace/rebuild must clear the prior proposal on the same turn id."""
    store.begin_turn("conversation-1", "1", "append", 0)
    store.stage_turn("conversation-1", "1", _staged())
    store.commit_staged_turn(
        "conversation-1",
        "1",
        expected_ledger_version="a" * 64,
        ledger_version="a" * 64,
    )

    reopened = store.reopen_turn("conversation-1", "1", "replace", 1)

    assert reopened.operation == "replace"
    assert reopened.base_context_version == 1
    assert reopened.state == "in_progress"
    assert reopened.result is None
    assert reopened.ledger_version is None


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
    """Duplicate reads retain bounded service metadata."""
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
    with store.write() as connection:
        connection.execute(
            "UPDATE conversation_turns SET delta_json = ? "
            "WHERE conversation_key = ? AND turn_id = ?",
            (legacy_delta, "conversation-1", "1"),
        )

    legacy = store.begin_turn("conversation-1", "1", "append", 0).turn

    assert legacy.state == "staged"
    assert legacy.stage_metadata is None


def _review_metadata(*, turn_id: str = "1") -> dict[str, object]:
    """Build the private marker used by the store CAS tests."""
    stable = "ctx-" + "a" * 64
    return {
        "version": 1,
        "operation": "new_review",
        "stable_thread_id": stable,
        "candidate_thread_id": _candidate_thread_id(stable, turn_id),
        "turn_id": turn_id,
        "report_revision": 0,
        "settlement_state": "pending",
    }


def test_review_candidate_registration_is_bounded_and_idempotent(
    store: ConversationContextStore,
) -> None:
    """A candidate is registered once, before any staged turn exists."""
    key = "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"
    turn_id = "pre-stage-1"
    stable = agent_thread_id(UUID(key), "ReviewAgent")
    candidate = _candidate_thread_id(stable, turn_id)

    assert store.register_review_candidate(
        key, turn_id, "new_review", stable, candidate
    )
    assert store.register_review_candidate(
        key, turn_id, "new_review", stable, candidate
    )
    assert store.list_checkpoint_cleanup_candidates() == ()

    with sqlite3.connect(store.db_path) as connection:
        row = connection.execute(
            "SELECT staged_at, eligible_at, tombstone_pending "
            "FROM conversation_review_checkpoint_cleanup "
            "WHERE conversation_key = ? AND candidate_thread_id = ?",
            (key, candidate),
        ).fetchone()
    assert row == (None, None, 0)

    for operation in ("follow_up", "local_revision"):
        operation_turn_id = f"{operation}-1"
        operation_candidate = _candidate_thread_id(stable, operation_turn_id)
        assert not store.register_review_candidate(
            key,
            operation_turn_id,
            operation,
            stable,
            operation_candidate,
        )


def test_review_candidate_registration_fails_closed_after_tombstone(
    store: ConversationContextStore,
) -> None:
    """A deleted conversation cannot acquire a new Review candidate."""
    key = "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"
    turn_id = "pre-stage-tombstoned"
    stable = agent_thread_id(UUID(key), "ReviewAgent")
    candidate = _candidate_thread_id(stable, turn_id)
    store.tombstone(key)

    assert not store.register_review_candidate(
        key, turn_id, "new_review", stable, candidate
    )
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(1) FROM conversation_review_checkpoint_cleanup "
            "WHERE conversation_key = ?",
            (key,),
        ).fetchone() == (0,)


def test_review_settlement_claim_is_durable_across_store_instances(
    store: ConversationContextStore,
) -> None:
    """Two workers cannot both claim one Review settlement marker."""
    key = "conversation-1"
    store.begin_turn(key, "1", "append", 0)
    store.stage_turn(
        key,
        "1",
        _staged(stage_metadata={"_review_settlement": _review_metadata()}),
    )
    duplicate = ConversationContextStore(store.db_path)

    first = store.claim_review_settlement(key, "1")
    second = duplicate.claim_review_settlement(key, "1")

    assert sorted((first.status, second.status)) == ["claimed", "settling"]
    token = first.claim_token or second.claim_token
    assert token is not None
    assert store.finalize_review_settlement(
        key, "1", claim_token=token, state="promoted", report_revision=1
    )
    assert store.finalize_review_settlement(
        key, "1", claim_token=token, state="promoted", report_revision=1
    )
    assert duplicate.claim_review_settlement(key, "1").status == "promoted"
    assert not duplicate.finalize_review_settlement(
        key, "1", claim_token="wrong", state="rejected"
    )


def test_review_settlement_reject_race_has_one_terminal_winner(
    store: ConversationContextStore,
) -> None:
    """A rejected marker cannot later be promoted by another worker."""
    key = "conversation-1"
    store.begin_turn(key, "1", "append", 0)
    store.stage_turn(
        key,
        "1",
        _staged(stage_metadata={"_review_settlement": _review_metadata()}),
    )
    duplicate = ConversationContextStore(store.db_path)

    claim = store.claim_review_settlement(key, "1")
    assert claim.claim_token is not None
    assert duplicate.claim_review_settlement(key, "1").status == "settling"
    assert store.finalize_review_settlement(
        key, "1", claim_token=claim.claim_token, state="rejected"
    )
    assert duplicate.claim_review_settlement(key, "1").status == "rejected"
    assert not duplicate.finalize_review_settlement(
        key,
        "1",
        claim_token=claim.claim_token,
        state="promoted",
        report_revision=1,
    )


def test_review_settlement_claim_reclaims_expired_worker_with_new_fence(
    store: ConversationContextStore,
) -> None:
    """An expired claim is recoverable while its old fence is rejected."""
    key = "conversation-1"
    start = datetime(2026, 7, 28, tzinfo=UTC)
    store.begin_turn(key, "1", "append", 0)
    store.stage_turn(
        key,
        "1",
        _staged(stage_metadata={"_review_settlement": _review_metadata()}),
    )

    first = store.claim_review_settlement(key, "1", now=start)
    active = store.claim_review_settlement(
        key, "1", now=start + timedelta(minutes=4, seconds=59)
    )
    expired = store.claim_review_settlement(
        key, "1", now=start + timedelta(minutes=5, seconds=1)
    )

    assert first.status == "claimed"
    assert active.status == "settling"
    assert expired.status == "claimed"
    assert first.claim_token is not None
    assert first.fence_token is not None
    assert expired.claim_token is not None
    assert expired.fence_token is not None
    assert expired.fence_token > first.fence_token
    old_reservation = store.reserve_review_settlement(
        key,
        "1",
        claim_token=first.claim_token,
        fence_token=first.fence_token,
    )
    assert old_reservation.status == "conflict"
    assert not store.finalize_review_settlement(
        key,
        "1",
        claim_token=first.claim_token,
        fence_token=first.fence_token,
        state="promoted",
        report_revision=1,
    )
    assert store.finalize_review_settlement(
        key,
        "1",
        claim_token=expired.claim_token,
        fence_token=expired.fence_token,
        state="failed",
    )


def test_review_settlement_claim_preflights_staged_ledger_version(
    store: ConversationContextStore,
) -> None:
    """A stale settlement cannot claim or mutate the Review marker."""
    key = "conversation-1"
    store.begin_turn(key, "1", "append", 0)
    store.stage_turn(
        key,
        "1",
        _staged(stage_metadata={"_review_settlement": _review_metadata()}),
    )

    stale = store.claim_review_settlement(
        key, "1", expected_ledger_version="b" * 64
    )

    assert stale.status == "conflict"
    pending = store.load_turn(key, "1")
    assert pending is not None
    assert pending.ledger_version == "a" * 64
    assert pending.stage_metadata is not None
    assert pending.stage_metadata["_review_settlement"][
        "settlement_state"
    ] == ("pending")
    claim = store.claim_review_settlement(
        key, "1", expected_ledger_version="a" * 64
    )
    assert claim.status == "claimed"


def test_review_settlement_claim_accepts_the_current_active_context_version(
    store: ConversationContextStore,
) -> None:
    """A valid follow-up can claim against an already committed context."""
    key = "conversation-1"
    store.begin_turn(key, "base", "append", 0)
    store.stage_turn(key, "base", _staged())
    store.commit_staged_turn(key, "base", "a" * 64, "a" * 64)
    store.begin_turn(key, "1", "append", 1)
    store.stage_turn(
        key,
        "1",
        _staged(
            base_context_version=1,
            stage_metadata={"_review_settlement": _review_metadata()},
        ),
    )

    claim = store.claim_review_settlement(
        key, "1", expected_ledger_version="a" * 64
    )

    assert claim.status == "claimed"


def test_tombstone_fences_and_retains_review_candidate_threads(
    store: ConversationContextStore,
) -> None:
    """Tombstoning records candidates before deleting staged rows."""
    key = "conversation-1"
    stable = "ctx-" + "a" * 64
    candidate = _candidate_thread_id(stable, "1")
    metadata = _review_metadata()
    metadata.update(
        {
            "stable_thread_id": stable,
            "candidate_thread_id": candidate,
        }
    )
    store.begin_turn(key, "1", "append", 0)
    store.stage_turn(
        key,
        "1",
        _staged(stage_metadata={"_review_settlement": metadata}),
    )
    claim = store.claim_review_settlement(key, "1")
    assert claim.claim_token is not None

    candidates = store.tombstone(key)

    assert candidates == (candidate,)
    assert store.load_turn(key, "1") is None
    assert not store.is_review_settlement_claim_active(
        key, "1", claim_token=claim.claim_token
    )
    store.complete_checkpoint_cleanup(key)
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(1) FROM conversation_review_checkpoint_cleanup "
            "WHERE conversation_key = ?",
            (key,),
        ).fetchone() == (0,)


def test_review_settlement_malformed_marker_is_failed_and_not_retryable(
    store: ConversationContextStore,
) -> None:
    """Unknown marker state fails closed and cannot be claimed afterward."""
    key = "conversation-1"
    store.begin_turn(key, "1", "append", 0)
    metadata = _review_metadata()
    metadata["settlement_state"] = "unknown"
    store.stage_turn(
        key,
        "1",
        _staged(stage_metadata={"_review_settlement": metadata}),
    )

    assert store.claim_review_settlement(key, "1").status == "invalid"
    assert store.mark_review_settlement_failed(key, "1")
    assert store.claim_review_settlement(key, "1").status == "failed"


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

    proposal = (
        _staged(selected_agent_id=value)
        if field == "selected_agent_id"
        else _staged(route_source=value)
    )
    with pytest.raises(StagedTurnConflictError):
        store.stage_turn("conversation-1", "1", proposal)


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
            "SELECT COUNT(*) FROM conversation_turns "
            "WHERE conversation_key = ?",
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
            "SELECT COUNT(*) FROM conversation_turns "
            "WHERE conversation_key = ?",
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


def test_expired_review_turn_retains_candidate_for_later_tombstone(
    store: ConversationContextStore,
) -> None:
    """Retention never removes the only durable candidate cleanup identity."""
    key = "conversation-1"
    stable = "ctx-" + "b" * 64
    candidate = _candidate_thread_id(stable, "1")
    metadata = _review_metadata()
    metadata.update(
        {
            "stable_thread_id": stable,
            "candidate_thread_id": candidate,
        }
    )
    store.begin_turn(key, "1", "append", 0)
    store.stage_turn(
        key,
        "1",
        _staged(stage_metadata={"_review_settlement": metadata}),
    )
    with sqlite3.connect(store.db_path) as connection:
        expires_at = connection.execute(
            "SELECT expires_at FROM conversation_turns "
            "WHERE conversation_key = ? AND turn_id = ?",
            (key, "1"),
        ).fetchone()[0]

    assert store.purge_expired_staged(datetime.fromisoformat(expires_at)) == 1
    assert store.load_turn(key, "1") is None
    assert store.list_checkpoint_cleanup_candidates() == ((key, candidate),)
    assert store.tombstone(key) == (candidate,)


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
