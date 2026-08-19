# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the canonical public lifecycle contract helpers."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from tests.support.resume_graph import build_resume_app
from tests.support.terminal_results import (
    SensitiveTerminalResultSpec,
    public_partial_warning,
    public_report_projection,
    public_scientific_table_artifact,
    sensitive_terminal_result,
)

from mcp_server_phytomni.agents.review.conversation import _candidate_thread_id
from mcp_server_phytomni.api import run_lifecycle
from mcp_server_phytomni.api.lifecycle_contract import (
    LifecycleInvariantError,
    SafeApiError,
    build_agent_run_response,
    canonicalize_agent_run_body,
    canonicalize_run_record,
    empty_agent_result,
)
from mcp_server_phytomni.runtime.checkpoint_backend import (
    build_default_checkpointer,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
    StagedTurn,
)
from mcp_server_phytomni.runtime.langgraph_runner import build_runnable_config


def test_safe_api_error_allows_exception_traceback_assignment() -> None:
    """Safe API errors remain usable during context-manager unwinding."""

    @contextmanager
    def rethrow_safe_error() -> Generator[None, None, None]:
        try:
            yield
        except SafeApiError as exc:
            raise exc

    error = SafeApiError(
        status_code=503,
        code="upstream_unavailable",
        message="upstream unavailable",
        stage="upstream",
        retryable=True,
    )

    with pytest.raises(SafeApiError) as raised, rethrow_safe_error():
        raise error

    assert raised.value is error
    assert error.__traceback__ is not None
    assert error.status_code == 503
    assert error.code == "upstream_unavailable"
    assert error.message == "upstream unavailable"
    assert error.stage == "upstream"
    assert error.retryable is True


def test_native_run_preserves_public_citation_contract() -> None:
    """Native canonicalization retains cited display and degradation data."""
    projected = canonicalize_agent_run_body(
        {
            "id": "run-cited",
            "agent": "knowledge",
            "status": "succeeded",
            "task_ids": [],
            "result": {
                "formatted": {
                    "answer": "Claim<sup>1</sup>.",
                    "metadata": {"citation_metadata_degraded": True},
                    "references": [
                        {
                            "file_id": "f1",
                            "title": "Title",
                            "formatted_citation": "Title.",
                            "doi_missing": True,
                        }
                    ],
                }
            },
        }
    )

    formatted = projected["result"]["formatted"]
    assert formatted["metadata"] == {"citation_metadata_degraded": True}
    assert formatted["references"] == [
        {
            "file_id": "f1",
            "title": "Title",
            "formatted_citation": "Title.",
            "doi_missing": True,
        }
    ]


@pytest.mark.asyncio
async def test_sync_gc_boundary_propagates_worker_cancellation() -> None:
    """Cancellation raised in the off-loop worker reaches the caller."""

    async def cancelled() -> None:
        raise asyncio.CancelledError

    boundary: Callable[[Callable[[], Coroutine[Any, Any, Any]]], Any] = (
        getattr(run_lifecycle, "_run_async_at_sync_boundary")
    )
    with pytest.raises(asyncio.CancelledError):
        boundary(cancelled)


@pytest.mark.asyncio
async def test_async_gc_wrapper_propagates_worker_cancellation() -> None:
    """The async GC wrapper re-raises cancellation from its worker."""

    def cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_lifecycle.purge_expired_runs_best_effort_async(
            purge=cancelled
        )


def _stage_lifecycle_turn(
    store: ConversationContextStore,
    key: str,
    turn_id: str,
    **options: Any,
) -> None:
    """Stage a compact row for the production retention contract test."""
    operation = options["operation"]
    selected_agent_id = options["selected_agent_id"]
    stage_metadata = options.get("stage_metadata")
    store.begin_turn(key, turn_id, operation, 0)
    store.stage_turn(
        key,
        turn_id,
        StagedTurn(
            operation=operation,
            base_context_version=0,
            selected_agent_id=selected_agent_id,
            route_source="lifecycle-test",
            result={"answer": "bounded"},
            delta={"summary": "bounded"},
            ledger_version="ledger-1",
            schema_version=1,
            ledger_cursor=0,
            observed_mode="expert",
            stage_metadata=stage_metadata or {},
        ),
    )


def _review_settlement_marker(
    stable_thread_id: str,
    candidate_thread_id: str,
    turn_id: str,
) -> dict[str, object]:
    """Build the bounded Review marker used by lifecycle fixtures."""
    marker: dict[str, object] = {
        "version": 1,
        "operation": "new_review",
    }
    marker.update(
        stable_thread_id=stable_thread_id,
        candidate_thread_id=candidate_thread_id,
        turn_id=turn_id,
        report_revision=0,
        settlement_state="pending",
    )
    return marker


def _stage_review_turn(
    store: ConversationContextStore,
    key: str,
    turn_id: str,
) -> tuple[str, str]:
    """Stage one Review turn and return its stable/candidate thread IDs."""
    stable_thread_id = agent_thread_id(UUID(key), "ReviewAgent")
    candidate_thread_id = _candidate_thread_id(stable_thread_id, turn_id)
    _stage_lifecycle_turn(
        store,
        key,
        turn_id,
        operation="new_review",
        selected_agent_id="ReviewAgent",
        stage_metadata={
            "_review_settlement": _review_settlement_marker(
                stable_thread_id, candidate_thread_id, turn_id
            )
        },
    )
    return stable_thread_id, candidate_thread_id


def _run_normal_lifecycle_gc(
    db_path: Path,
    registry_calls: list[str],
    deleted_candidates: list[str],
) -> bool:
    """Run normal GC with a recorder and return saver ownership state."""

    def registry_factory(path: str) -> SimpleNamespace:
        registry_calls.append(path)
        return SimpleNamespace(purge_expired=lambda: 0)

    async def delete_thread(thread_id: str) -> None:
        deleted_candidates.append(thread_id)

    checkpointer = SimpleNamespace(adelete_thread=delete_thread, closed=False)

    async def close() -> None:
        checkpointer.closed = True

    checkpointer.close = close
    run_lifecycle.purge_expired_runs_best_effort(
        db_path=str(db_path),
        registry_factory=registry_factory,
        checkpointer_factory=lambda: checkpointer,
    )
    return checkpointer.closed


def test_normal_lifecycle_gc_purges_staged_context_and_review_candidate(
    tmp_path: Path,
) -> None:
    """Normal run GC expires rows and deletes only the Review candidate."""
    db_path = tmp_path / "lifecycle.sqlite"
    store = ConversationContextStore(str(db_path))
    key = "00000000-0000-0000-0000-000000000011"
    review_turn_id = "review-turn"
    stable_thread_id, candidate_thread_id = _stage_review_turn(
        store, key, review_turn_id
    )
    _stage_lifecycle_turn(
        store,
        key,
        "chat-turn",
        operation="append",
        selected_agent_id="ChatAgent",
    )
    future_key = "00000000-0000-0000-0000-000000000013"
    future_turn_id = "future-review-turn"
    _future_stable_thread_id, future_candidate_thread_id = _stage_review_turn(
        store, future_key, future_turn_id
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE conversation_turns SET expires_at = ? "
            "WHERE conversation_key = ?",
            ("2020-01-01T00:00:00+00:00", key),
        )

    registry_calls: list[str] = []
    deleted_candidates: list[str] = []
    checkpointer_closed = _run_normal_lifecycle_gc(
        db_path,
        registry_calls,
        deleted_candidates,
    )

    assert registry_calls == [str(db_path)]
    assert store.load_turn(key, review_turn_id) is None
    assert store.load_turn(key, "chat-turn") is None
    assert deleted_candidates == [candidate_thread_id]
    assert stable_thread_id not in deleted_candidates
    assert not checkpointer_closed
    assert store.load_turn(future_key, future_turn_id) is not None
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT candidate_thread_id "
            "FROM conversation_review_checkpoint_cleanup "
            "WHERE conversation_key = ? AND eligible_at IS NULL",
            (future_key,),
        ).fetchone() == (future_candidate_thread_id,)

    assert store.tombstone(key) == ()
    store.complete_checkpoint_cleanup(key)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_review_checkpoint_cleanup "
            "WHERE conversation_key = ?",
            (key,),
        ).fetchone() == (0,)


def test_failed_review_candidate_cleanup_retries_on_next_lifecycle_gc(
    tmp_path: Path,
) -> None:
    """A failed candidate delete stays pending until a later GC succeeds."""
    db_path = tmp_path / "lifecycle-retry.sqlite"
    store = ConversationContextStore(str(db_path))
    key = "00000000-0000-0000-0000-000000000012"
    review_turn_id = "review-turn"
    stable_thread_id = agent_thread_id(UUID(key), "ReviewAgent")
    candidate_thread_id = _candidate_thread_id(
        stable_thread_id, review_turn_id
    )
    _stage_lifecycle_turn(
        store,
        key,
        review_turn_id,
        operation="new_review",
        selected_agent_id="ReviewAgent",
        stage_metadata={
            "_review_settlement": _review_settlement_marker(
                stable_thread_id, candidate_thread_id, review_turn_id
            )
        },
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE conversation_turns SET expires_at = ? "
            "WHERE conversation_key = ?",
            ("2020-01-01T00:00:00+00:00", key),
        )

    should_fail = True
    attempts: list[str] = []

    async def delete_thread(thread_id: str) -> None:
        attempts.append(thread_id)
        if should_fail:
            raise RuntimeError("checkpoint unavailable")

    checkpointer = SimpleNamespace(adelete_thread=delete_thread)

    def registry_factory(path: str) -> SimpleNamespace:
        del path
        return SimpleNamespace(purge_expired=lambda: 0)

    def run_purge() -> None:
        run_lifecycle.purge_expired_runs_best_effort(
            db_path=str(db_path),
            registry_factory=registry_factory,
            checkpointer_factory=lambda: checkpointer,
        )

    run_purge()

    assert attempts == [candidate_thread_id]
    assert store.list_checkpoint_cleanup_candidates() == (
        (key, candidate_thread_id),
    )

    should_fail = False
    run_purge()

    assert attempts == [candidate_thread_id, candidate_thread_id]
    assert store.list_checkpoint_cleanup_candidates() == ()


def test_registered_candidate_survives_tombstone_until_retry_gc(
    tmp_path: Path,
) -> None:
    """A pre-stage candidate remains discoverable after tombstone cleanup."""
    db_path = tmp_path / "lifecycle-pre-stage.sqlite"
    store = ConversationContextStore(str(db_path))
    key = "00000000-0000-0000-0000-000000000016"
    turn_id = "pre-stage-review"
    stable_thread_id = agent_thread_id(UUID(key), "ReviewAgent")
    candidate_thread_id = _candidate_thread_id(stable_thread_id, turn_id)

    assert store.register_review_candidate(
        key,
        turn_id,
        "new_review",
        stable_thread_id,
        candidate_thread_id,
    )
    assert store.tombstone(key) == (candidate_thread_id,)
    store.complete_checkpoint_cleanup(key)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT staged_at, tombstone_pending "
            "FROM conversation_review_checkpoint_cleanup "
            "WHERE conversation_key = ? AND candidate_thread_id = ?",
            (key, candidate_thread_id),
        ).fetchone() == (None, 1)

    created_threads = {candidate_thread_id}
    deleted_threads: list[str] = []

    async def delete_thread(thread_id: str) -> None:
        deleted_threads.append(thread_id)
        created_threads.discard(thread_id)

    def registry_factory(path: str) -> SimpleNamespace:
        del path
        return SimpleNamespace(purge_expired=lambda: 0)

    run_lifecycle.purge_expired_runs_best_effort(
        db_path=str(db_path),
        registry_factory=registry_factory,
        checkpointer_factory=lambda: SimpleNamespace(
            adelete_thread=delete_thread
        ),
    )

    assert created_threads == set()
    assert deleted_threads == [candidate_thread_id]
    assert store.list_checkpoint_cleanup_candidates() == ()


def test_lifecycle_gc_uses_the_persistent_checkpoint_backend_by_default(
    tmp_path: Path,
) -> None:
    """The production default deletes a row from the shared SQLite saver."""
    db_path = tmp_path / "lifecycle-persistent.sqlite"
    checkpoint_path = tmp_path / "checkpoints.db"
    key = "00000000-0000-0000-0000-000000000014"
    turn_id = "persistent-review-turn"
    stable_thread_id = agent_thread_id(UUID(key), "ReviewAgent")
    candidate_thread_id = _candidate_thread_id(stable_thread_id, turn_id)
    store = ConversationContextStore(str(db_path))
    _stage_lifecycle_turn(
        store,
        key,
        turn_id,
        operation="new_review",
        selected_agent_id="ReviewAgent",
        stage_metadata={
            "_review_settlement": _review_settlement_marker(
                stable_thread_id, candidate_thread_id, turn_id
            )
        },
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE conversation_turns SET expires_at = ? "
            "WHERE conversation_key = ? AND turn_id = ?",
            ("2020-01-01T00:00:00+00:00", key, turn_id),
        )

    async def seed_checkpoints() -> None:
        saver = build_default_checkpointer(str(checkpoint_path))
        try:
            app = build_resume_app(saver)
            for thread_id in (candidate_thread_id, stable_thread_id):
                await app.ainvoke(
                    {"value": thread_id},
                    config=build_runnable_config(thread_id),
                )
        finally:
            await saver.conn.close()

    asyncio.run(seed_checkpoints())

    def registry_factory(path: str) -> SimpleNamespace:
        assert path == str(db_path)
        return SimpleNamespace(purge_expired=lambda: 0)

    run_lifecycle.purge_expired_runs_best_effort(
        db_path=str(db_path), registry_factory=registry_factory
    )

    with sqlite3.connect(checkpoint_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
            (candidate_thread_id,),
        ).fetchone() == (0,)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                (stable_thread_id,),
            ).fetchone()[0]
            > 0
        )


@pytest.mark.asyncio
async def test_async_lifecycle_gc_keeps_injected_checkpointer_caller_owned(
    tmp_path: Path,
) -> None:
    """The off-loop GC boundary does not close an injected shared saver."""
    db_path = tmp_path / "lifecycle-async.sqlite"
    store = ConversationContextStore(str(db_path))
    key = "00000000-0000-0000-0000-000000000015"
    turn_id = "async-review-turn"
    stable_thread_id = agent_thread_id(UUID(key), "ReviewAgent")
    candidate_thread_id = _candidate_thread_id(stable_thread_id, turn_id)
    _stage_lifecycle_turn(
        store,
        key,
        turn_id,
        operation="new_review",
        selected_agent_id="ReviewAgent",
        stage_metadata={
            "_review_settlement": _review_settlement_marker(
                stable_thread_id, candidate_thread_id, turn_id
            )
        },
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE conversation_turns SET expires_at = ? "
            "WHERE conversation_key = ? AND turn_id = ?",
            ("2020-01-01T00:00:00+00:00", key, turn_id),
        )

    class SharedCheckpointer:
        """Track whether lifecycle GC closes a caller-owned saver."""

        closed = False

        async def adelete_thread(self, thread_id: str) -> None:
            """Delete only the candidate selected by lifecycle GC."""
            assert thread_id == candidate_thread_id

        async def close(self) -> None:
            """Record an attempted close for the ownership assertion."""
            self.closed = True

    shared = SharedCheckpointer()

    def registry_factory(path: str) -> SimpleNamespace:
        del path
        return SimpleNamespace(purge_expired=lambda: 0)

    await run_lifecycle.purge_expired_runs_best_effort_async(
        purge=lambda: run_lifecycle.purge_expired_runs_best_effort(
            db_path=str(db_path),
            registry_factory=registry_factory,
            checkpointer_factory=lambda: shared,
        )
    )

    assert not shared.closed
    assert store.list_checkpoint_cleanup_candidates() == ()


def test_context_lifecycle_purge_failure_preserves_run_gc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A context-store failure does not suppress the registry purge."""
    db_path = str(tmp_path / "lifecycle.sqlite")
    registry_calls: list[str] = []

    def registry_factory(path: str) -> SimpleNamespace:
        registry_calls.append(path)
        return SimpleNamespace(purge_expired=lambda: 0)

    def failing_context_factory(_path: str) -> SimpleNamespace:
        def fail(_now: object) -> None:
            raise sqlite3.OperationalError("context database is locked")

        return SimpleNamespace(
            acquire_review_mutation_lock=lambda: SimpleNamespace(
                release=lambda: None
            ),
            purge_expired_staged=fail,
        )

    monkeypatch.setattr(
        run_lifecycle, "ConversationContextStore", failing_context_factory
    )

    run_lifecycle.purge_expired_runs_best_effort(
        db_path=db_path, registry_factory=registry_factory
    )

    assert registry_calls == [db_path]


def test_running_without_recoverable_work_is_rejected() -> None:
    """A running response must point at recoverable work."""
    with pytest.raises(LifecycleInvariantError, match="running_without_work"):
        build_agent_run_response(
            run_id=None,
            agent="research",
            status="running",
            task_ids=(),
            result=empty_agent_result(),
            persisted=False,
            degraded_tracking=True,
        )


def test_degraded_running_preserves_real_task_ids() -> None:
    """The degraded remote path still exposes accepted upstream task ids."""
    response = build_agent_run_response(
        run_id=None,
        agent="research",
        status="running",
        task_ids=("task-upstream-1",),
        result=empty_agent_result(degraded=True),
        persisted=False,
        degraded_tracking=True,
    )
    assert response["id"] is None
    assert "run_id" not in response
    assert response["task_ids"] == ["task-upstream-1"]
    assert response["degraded_tracking"] is True


def test_succeeded_requires_persistence() -> None:
    """A 200 succeeded response cannot escape before persistence."""
    with pytest.raises(
        LifecycleInvariantError, match="succeeded_without_persistence"
    ):
        build_agent_run_response(
            run_id="run-data-1",
            agent="data",
            status="succeeded",
            task_ids=(),
            result=empty_agent_result(),
            persisted=False,
        )


def test_input_required_requires_valid_surface() -> None:
    """Paused public runs must carry a valid supported A2UI surface."""
    with pytest.raises(
        LifecycleInvariantError, match="input_required_without_surface"
    ):
        build_agent_run_response(
            run_id="run-review-1",
            agent="review",
            status="input_required",
            task_ids=(),
            result={"interrupt": {"draft": {}}},
            persisted=True,
        )


def test_persisted_running_record_requires_recoverable_identity() -> None:
    """A read projection cannot invent recovery for an anonymous run."""
    with pytest.raises(
        LifecycleInvariantError, match="routing_contract_violation"
    ):
        canonicalize_run_record(
            {
                "agent": "chat",
                "status": "running",
                "result": None,
                "task_ids": [],
            }
        )


def test_persisted_review_interrupt_uses_safe_summary_fallback() -> None:
    """Unknown review draft fields cannot be rendered into public surfaces."""
    projected = canonicalize_run_record(
        {
            "run_id": "run-review-safe-summary",
            "agent": "review",
            "status": "input_required",
            "task_ids": [],
            "result": {
                "interrupt": {
                    "draft": {
                        "provider_payload": "/srv/private/provider",
                    }
                }
            },
        }
    )

    interrupt = projected["result"]["interrupt"]
    assert interrupt["draft"]["summary"] == "Review approval required."
    assert "/srv/private/provider" not in str(interrupt)


def test_terminal_projection_preserves_submitted_a2ui_value() -> None:
    """Terminal A2UI submission state survives canonicalization."""
    projected = canonicalize_run_record(
        {
            "run_id": "run-review-submitted",
            "agent": "review",
            "status": "succeeded",
            "task_ids": [],
            "result": {
                "formatted": {"answer": "Approved final review."},
                "a2ui": {
                    "catalog_version": "v1.0",
                    "surface_id": "review-surface",
                    "widget": "confirm",
                    "props": {
                        "title": "Review approval",
                        "body": "draft review",
                        "status": "submitted",
                        "accepted": True,
                    },
                },
            },
        }
    )

    assert projected["result"]["a2ui"]["props"] == {
        "title": "Review approval",
        "body": "draft review",
        "status": "submitted",
        "accepted": True,
    }


def test_persisted_record_drops_unknown_top_level_fields() -> None:
    """Only known run history fields and canonical result data are public."""
    projected = canonicalize_run_record(
        {
            "run_id": "run-top-level-projection",
            "agent": "chat",
            "origin": "local",
            "user_id": "u1",
            "status": "failed",
            "created_at": "2026-07-25T00:00:00+00:00",
            "updated_at": "2026-07-25T00:01:00+00:00",
            "expires_at": "2026-07-26T00:00:00+00:00",
            "dialogue_id": "dialogue-1",
            "query": "public query",
            "tool_name": "PhytoChat",
            "model": "phyto-chat",
            "a2a_task_id": "task-1",
            "a2a_context_id": "context-1",
            "a2a_message_id": "message-1",
            "task_ids": [],
            "answer": "/srv/private/legacy-answer",
            "error": "provider exception: /srv/private",
            "provider_payload": {"path": "/srv/private"},
            "raw": {"trace": "private"},
            "result": {
                "formatted": {"answer": "canonical answer"},
                "execution": {},
            },
        }
    )

    assert projected["answer"] == "canonical answer"
    assert projected["error"] == "run failed"
    assert "provider_payload" not in projected
    assert "raw" not in projected
    assert "/srv/private" not in str(projected)


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [("failed", "run failed"), ("succeeded", None)],
)
def test_persisted_record_redacts_error_and_result_details(
    status: str, expected_error: str | None
) -> None:
    """Persisted terminal reads deeply project nested public fields."""
    projected = canonicalize_run_record(
        {
            "run_id": f"run-{status}",
            "agent": "chat",
            "status": status,
            "task_ids": [],
            "error": "provider exception: /srv/private",
            "result": sensitive_terminal_result(
                SensitiveTerminalResultSpec(
                    answer="public answer",
                    task_id="task-1",
                    citation=("di", "10.1/example"),
                    table=(["gene", "score"], [["AT1G01010", 0.9]]),
                    warning=("exception", "private"),
                )
            ),
        }
    )

    assert projected["result"] == {
        "formatted": {
            "answer": "public answer",
            "follow_up_questions": ["next?"],
            "references": [
                {
                    "file_id": "doc-1",
                    "title": "Public title",
                    "di": "10.1/example",
                }
            ],
            "tabular": {
                "headers": ["gene", "score"],
                "rows": [["AT1G01010", 0.9]],
            },
            "metadata": {
                "original_query": "public query",
            },
        },
        "execution": {
            "tracking": {"degraded": False},
            "warnings": [public_partial_warning()],
            "tasks": [
                {
                    "id": "task-1",
                    "accepted": True,
                    "status": "succeeded",
                }
            ],
            "artifacts": [public_scientific_table_artifact()],
            "output_dirs": ["/obs/public/result"],
            "report": public_report_projection(),
            "diagnostics": [
                {
                    "code": "upstream_partial",
                    "stage": "analysis",
                    "retryable": False,
                }
            ],
        },
    }
    if expected_error is None:
        assert "error" not in projected
    else:
        assert projected["error"] == expected_error


def test_persisted_record_projects_required_delivery() -> None:
    """Public run reads must keep the archive-delivery marker.

    HTTP Chat polls this projection. Dropping ``delivery`` makes Web treat
    a packed Analyst run as the legacy download_path path.
    """
    marked = empty_agent_result()
    marked["execution"]["delivery"] = {
        "schema_version": 1,
        "required": True,
        "status": "ready",
        "revision": 1,
        "inventory_digest": "sha256:" + ("a" * 64),
        "archive": {
            "role": "result_archive",
            "name": "analyst-results.zip",
            "media_type": "application/zip",
            "size_bytes": 1093,
            "downloadable": True,
            "report_context_eligible": False,
            "download_ref": "result-archive:sha256:" + ("a" * 64),
        },
        "error_code": None,
        "retryable": False,
    }
    marked["delivery_internal"] = {
        "inventory_ref": "private-should-not-leak",
        "attempts_claimed": 1,
        "last_error_code": None,
    }

    projected = canonicalize_run_record(
        {
            "run_id": "run-delivery",
            "agent": "analyst",
            "status": "succeeded",
            "task_ids": ["task-1"],
            "result": marked,
        }
    )

    delivery = projected["result"]["execution"]["delivery"]
    assert delivery["required"] is True
    assert delivery["status"] == "ready"
    assert delivery["archive"]["name"] == "analyst-results.zip"
    dumped = str(projected)
    assert "delivery_internal" not in dumped
    assert "private-should-not-leak" not in dumped


def test_canonical_execution_tasks_keep_kind_and_error_code() -> None:
    """GetRun must forward child kind and error_code for Web lifecycle."""
    projected = canonicalize_run_record(
        {
            "run_id": "run-kind",
            "agent": "design",
            "status": "running",
            "task_ids": ["child-accepted"],
            "result": {
                "execution": {
                    "tasks": [
                        {
                            "id": "child-accepted",
                            "accepted": True,
                            "status": "submitted",
                            "kind": "protein_structure_analysis",
                            "error_code": None,
                            "traceback": "must-not-leak",
                        },
                        {
                            "id": "child-failed",
                            "accepted": False,
                            "status": "failed",
                            "kind": "promoter_analysis",
                            "error_code": "input_rejected",
                            "error_detail": (
                                "Traceback (most recent call last)"
                            ),
                        },
                    ]
                }
            },
        }
    )

    assert projected["result"]["execution"]["tasks"] == [
        {
            "id": "child-accepted",
            "accepted": True,
            "status": "submitted",
            "kind": "protein_structure_analysis",
            "error_code": None,
        },
        {
            "id": "child-failed",
            "accepted": False,
            "status": "failed",
            "kind": "promoter_analysis",
            "error_code": "input_rejected",
        },
    ]
    dumped = str(projected)
    assert "traceback" not in dumped
    assert "error_detail" not in dumped
    assert "Traceback" not in dumped


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"persisted": True}, "missing required keyword-only argument"),
        ({"result": empty_agent_result()}, "missing required keyword-only"),
        (
            {
                "result": empty_agent_result(),
                "persisted": True,
                "unsupported": True,
            },
            "unexpected keyword argument",
        ),
    ],
)
def test_builder_rejects_missing_or_unknown_options(
    options: dict[str, Any],
    message: str,
) -> None:
    """The builder reports invalid keyword options as normal call errors."""
    with pytest.raises(TypeError, match=message):
        build_agent_run_response(
            run_id="run-options",
            agent="chat",
            status="succeeded",
            task_ids=(),
            **options,
        )
