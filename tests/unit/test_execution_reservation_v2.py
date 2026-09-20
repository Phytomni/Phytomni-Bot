# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Idempotent Bot reservation before Runtime routing or business work."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Literal

import pytest
from tests.support.execution_supervisor_v2 import create_root_span

from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ExecutionEventType,
    ExecutionStatus,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    ExecutionReservationConflictError,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
    TransportNeutralResult,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def _command(slug: str = "chat", **arguments: object):

    return ExecutionCommand(
        agent_slug=slug, arguments=arguments or {"query": "safe"}
    )


def test_reservation_is_owner_scoped_idempotent_and_fingerprint_bound(
    tmp_path: Path,
) -> None:
    """Verify reservation is owner scoped idempotent and fingerprint bound."""

    db_path = tmp_path / "reservations.db"
    ids = iter(("run-1", "run-bob"))
    roots = iter(("span-root-1", "span-root-bob"))
    repository = SQLiteExecutionReservationRepository(
        str(db_path),
        run_id_factory=lambda: next(ids),
        root_span_id_factory=lambda: next(roots),
    )

    first = repository.reserve(
        owner="alice",
        execution_id="turn-same",
        fingerprint_version=1,
        fingerprint="a" * 64,
        command=_command(query="rice"),
    )
    replay = repository.reserve(
        owner="alice",
        execution_id="turn-same",
        fingerprint_version=1,
        fingerprint="a" * 64,
        command=_command(query="rice"),
    )
    foreign = repository.reserve(
        owner="bob",
        execution_id="turn-same",
        fingerprint_version=1,
        fingerprint="b" * 64,
        command=_command(query="rice"),
    )

    assert replay == first
    assert first.run_id == "run-1"
    assert first.root_span_id == "span-root-1"
    assert first.status.value == "admitted"
    assert first.driver == "resumable_graph"
    assert foreign.owner == "bob"
    assert foreign.run_id == "run-bob"
    for changed in (
        {
            "fingerprint_version": 1,
            "fingerprint": "c" * 64,
            "command": _command(query="rice"),
        },
        {
            "fingerprint_version": 2,
            "fingerprint": "a" * 64,
            "command": _command(query="rice"),
        },
        {
            "fingerprint_version": 1,
            "fingerprint": "a" * 64,
            "command": _command(query="changed"),
        },
        {
            "fingerprint_version": 1,
            "fingerprint": "a" * 64,
            "command": _command("knowledge", query="rice"),
        },
    ):
        with pytest.raises(ExecutionReservationConflictError):
            repository.reserve(
                owner="alice", execution_id="turn-same", **changed
            )


def test_concurrent_retry_creates_one_binding_and_one_run(
    tmp_path: Path,
) -> None:
    """Verify concurrent retry creates one binding and one run."""

    db_path = tmp_path / "concurrent.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))

    def reserve(_index: int):
        return repository.reserve(
            owner="alice",
            execution_id="turn-concurrent",
            fingerprint_version=1,
            fingerprint="d" * 64,
            command=_command(query="same"),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(reserve, range(16)))

    assert len({record.run_id for record in records}) == 1
    assert len({record.root_span_id for record in records}) == 1
    with sqlite_transaction(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM runs WHERE user_id = 'alice' "
                "AND execution_id = 'turn-concurrent'"
            ).fetchone()[0]
            == 1
        )


def test_private_expert_router_rebinds_once_without_new_public_execution(
    tmp_path: Path,
) -> None:
    """Verify private expert router rebinds once without new public
    execution."""

    db_path = tmp_path / "expert-router.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))
    admitted = repository.reserve(
        owner="alice",
        execution_id="turn-expert",
        fingerprint_version=2,
        fingerprint="r" * 64,
        command=_command(EXPERT_ROUTER_AGENT_SLUG, query="rice"),
    )
    selected = _command("knowledge", user_query="rice", locale="en-US")
    bound = repository.bind_routed_agent(
        owner="alice", execution_id="turn-expert", command=selected
    )
    replay = repository.bind_routed_agent(
        owner="alice", execution_id="turn-expert", command=selected
    )

    assert admitted.agent_slug == EXPERT_ROUTER_AGENT_SLUG
    assert bound == replay
    assert bound.agent_slug == "knowledge"
    assert bound.run_id == admitted.run_id
    assert bound.root_span_id == admitted.root_span_id
    with pytest.raises(ExecutionReservationConflictError):
        repository.bind_routed_agent(
            owner="alice",
            execution_id="turn-expert",
            command=_command("chat", user_query="rice"),
        )
    with sqlite_transaction(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM runs WHERE execution_id = 'turn-expert'"
            ).fetchone()[0]
            == 1
        )


def test_reservation_stores_only_command_hash_not_raw_arguments(
    tmp_path: Path,
) -> None:
    """Verify reservation stores only command hash not raw arguments."""

    db_path = tmp_path / "private-command.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))
    repository.reserve(
        owner="alice",
        execution_id="turn-private",
        fingerprint_version=1,
        fingerprint="e" * 64,
        command=_command(query="private-marker-value"),
    )

    with sqlite_transaction(db_path) as connection:
        row = connection.execute(
            "SELECT execution_command_hash, request_json FROM runs "
            "WHERE execution_id = 'turn-private'"
        ).fetchone()
    assert len(row[0]) == 64
    assert "private-marker-value" not in str(row)


def test_context_stage_projects_v1_metadata_to_the_v2_subset(
    tmp_path: Path,
) -> None:
    """Verify context stage projects V1 metadata to the V2 subset."""

    repository = SQLiteExecutionReservationRepository(
        str(tmp_path / "context-stage.db")
    )
    repository.reserve(
        owner="alice",
        execution_id="turn-context-stage",
        fingerprint_version=2,
        fingerprint="s" * 64,
        command=_command("expert-router", user_query="rice"),
    )

    assert repository.record_context_stage(
        owner="alice",
        execution_id="turn-context-stage",
        stage={
            "schema_version": 1,
            "turn_id": "38",
            "selected_agent_id": "DataAgent",
            "route_source": "router",
            "route_reason_code": "ROUTER_SELECTED",
            "base_business_context_version": 0,
            "proposed_business_context_version": 1,
            "last_applied_ledger_cursor": 38,
            "context_truncated": False,
            "context_rebuilt": True,
            "context_degraded": False,
        },
    )

    stored = repository.get(owner="alice", execution_id="turn-context-stage")
    assert json.loads(stored.context_stage_json or "null") == {
        "base_business_context_version": 0,
        "context_rebuilt": True,
        "context_truncated": False,
        "last_applied_ledger_cursor": 38,
        "proposed_business_context_version": 1,
        "route_reason_code": "ROUTER_SELECTED",
        "route_source": "router",
        "schema_version": 1,
        "selected_agent_id": "DataAgent",
        "turn_id": "38",
    }


def test_resume_claim_atomically_excludes_other_active_actions(
    tmp_path: Path,
) -> None:
    """Only one resume action may leave a waiting-input boundary."""

    repository = SQLiteExecutionReservationRepository(
        str(tmp_path / "resume-claim.db")
    )
    reservation = repository.reserve(
        owner="alice",
        execution_id="turn-resume-race",
        fingerprint_version=2,
        fingerprint="f" * 64,
        command=_command("review", user_query="rice"),
    )
    assert repository.record_observation(
        owner="alice",
        execution_id=reservation.execution_id,
        status=ExecutionStatus.WAITING_INPUT,
        tracking_health="healthy",
        cancellation_state="none",
        next_attempt_at=None,
    )
    first_command = ExecutionCommand(
        agent_slug="review",
        arguments={"approved": True},
        action_id="action-first",
        expected_revision=reservation.supervisor_revision,
    )
    first = repository.claim_operation(
        owner="alice",
        execution_id=reservation.execution_id,
        operation_id="action-first",
        operation="resume",
        expected_revision=reservation.supervisor_revision,
        command=first_command,
    )

    assert first.claimed is True
    claimed = repository.get(
        owner="alice", execution_id=reservation.execution_id
    )
    assert claimed.status is ExecutionStatus.WAITING_INPUT
    second_command = ExecutionCommand(
        agent_slug="review",
        arguments={"approved": True},
        action_id="action-second",
        expected_revision=claimed.supervisor_revision,
    )
    with pytest.raises(
        ExecutionReservationConflictError,
        match="resume_already_claimed",
    ):
        repository.claim_operation(
            owner="alice",
            execution_id=reservation.execution_id,
            operation_id="action-second",
            operation="resume",
            expected_revision=claimed.supervisor_revision,
            command=second_command,
        )


def test_terminal_settlement_commits_span_event_projection_and_replay(
    tmp_path: Path,
) -> None:
    """Verify terminal settlement commits span event projection and replay."""

    db_path = tmp_path / "terminal-atomic.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))
    reservation = repository.reserve(
        owner="alice",
        execution_id="turn-terminal-atomic",
        fingerprint_version=2,
        fingerprint="t" * 64,
        command=_command(query="rice"),
    )
    work = SQLiteExecutionWorkRepository(str(db_path))
    root = create_root_span(
        work,
        reservation,
        label_key="activity.agent.chat",
    )
    work.update_span_status(
        reservation.execution_id,
        reservation.root_span_id,
        owner="alice",
        status="running",
        expected_revision=root.revision,
    )
    authority = TerminalSettlementAuthority(
        owner_ref="alice",
        execution_id=reservation.execution_id,
        expected_revision=reservation.supervisor_revision,
        actor="runtime",
        issued_at=datetime.now(UTC),
    )
    outcome = DriverOutcome.succeeded(
        TransportNeutralResult(answer="authoritative result")
    )

    assert repository.settle_terminal(authority, outcome)
    assert repository.settle_terminal(authority, outcome)
    assert not repository.settle_terminal(
        authority,
        DriverOutcome.failed(code="losing_failure"),
    )

    stored = repository.get(
        owner="alice", execution_id=reservation.execution_id
    )
    assert stored.status is ExecutionStatus.SUCCEEDED
    assert (
        work.get_span(
            reservation.execution_id,
            reservation.root_span_id,
            owner="alice",
        ).status
        == "succeeded"
    )
    journal = SQLiteExecutionJournal(str(db_path))
    page = journal.list_events(reservation.execution_id, owner="alice")
    assert page is not None
    assert [event.type for event in page.items] == [
        ExecutionEventType.SPAN_SUCCEEDED,
        ExecutionEventType.EXECUTION_SUCCEEDED,
    ]
    projection = journal.get_projection(
        reservation.execution_id, owner="alice"
    )
    assert projection.terminal is not None
    assert projection.terminal.status == "succeeded"


def test_terminal_settlement_rolls_back_every_write_on_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify terminal settlement rolls back every write on append failure."""

    db_path = tmp_path / "terminal-rollback.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))
    reservation = repository.reserve(
        owner="alice",
        execution_id="turn-terminal-rollback",
        fingerprint_version=2,
        fingerprint="u" * 64,
        command=_command(query="rice"),
    )
    work = SQLiteExecutionWorkRepository(str(db_path))
    root = create_root_span(
        work,
        reservation,
        label_key="activity.agent.chat",
    )
    work.update_span_status(
        reservation.execution_id,
        reservation.root_span_id,
        owner="alice",
        status="running",
        expected_revision=root.revision,
    )

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("simulated journal crash window")

    monkeypatch.setattr(SQLiteExecutionJournal, "_append_locked", fail_append)
    with pytest.raises(sqlite3.OperationalError):
        repository.settle_terminal(
            TerminalSettlementAuthority(
                owner_ref="alice",
                execution_id=reservation.execution_id,
                expected_revision=reservation.supervisor_revision,
                actor="runtime",
                issued_at=datetime.now(UTC),
            ),
            DriverOutcome.succeeded(
                TransportNeutralResult(answer="must roll back")
            ),
        )

    stored = repository.get(
        owner="alice", execution_id=reservation.execution_id
    )
    assert stored.status is ExecutionStatus.ADMITTED
    assert (
        work.get_span(
            reservation.execution_id,
            reservation.root_span_id,
            owner="alice",
        ).status
        == "running"
    )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        reservation.execution_id, owner="alice"
    )
    assert page is not None
    assert not page.items


def test_terminal_settlement_provider_lease_expiry_publishes_nothing(
    tmp_path: Path,
) -> None:
    """Verify terminal settlement provider lease expiry publishes nothing."""

    now = datetime.now(UTC)
    db_path = tmp_path / "terminal-expired-lease.db"
    repository = SQLiteExecutionReservationRepository(
        str(db_path),
        clock=lambda: now,
        expected_provider_join_lease_token="join-lease",
    )
    reservation = repository.reserve(
        owner="alice",
        execution_id="turn-expired-lease",
        fingerprint_version=2,
        fingerprint="v" * 64,
        command=_command(query="rice"),
    )
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_provider_join_lease_owner = ?, "
            "execution_provider_join_lease_expires_at = ? "
            "WHERE execution_id = ?",
            (
                "join-lease",
                (now - timedelta(seconds=1)).isoformat(),
                reservation.execution_id,
            ),
        )
        connection.commit()

    assert not repository.settle_terminal(
        TerminalSettlementAuthority(
            owner_ref="alice",
            execution_id=reservation.execution_id,
            expected_revision=reservation.supervisor_revision,
            actor="runtime",
            issued_at=now,
        ),
        DriverOutcome.succeeded(TransportNeutralResult(answer="late")),
    )
    assert (
        repository.get(
            owner="alice", execution_id=reservation.execution_id
        ).status
        is ExecutionStatus.ADMITTED
    )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        reservation.execution_id, owner="alice"
    )
    assert page is not None
    assert not page.items


def test_concurrent_opposite_terminal_outcomes_publish_one_decision(
    tmp_path: Path,
) -> None:
    """Verify concurrent opposite terminal outcomes publish one decision."""

    db_path = tmp_path / "terminal-opposite-race.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))
    reservation = repository.reserve(
        owner="alice",
        execution_id="turn-opposite-race",
        fingerprint_version=2,
        fingerprint="w" * 64,
        command=_command(query="rice"),
    )
    authority = TerminalSettlementAuthority(
        owner_ref="alice",
        execution_id=reservation.execution_id,
        expected_revision=reservation.supervisor_revision,
        actor="supervisor",
        issued_at=datetime.now(UTC),
    )
    outcomes = (
        DriverOutcome.succeeded(TransportNeutralResult(answer="winner")),
        DriverOutcome.failed(code="competing_failure"),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        settled = list(
            executor.map(
                lambda outcome: repository.settle_terminal(authority, outcome),
                outcomes,
            )
        )

    assert sorted(settled) == [False, True]
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        reservation.execution_id, owner="alice"
    )
    assert page is not None
    terminal_types = {
        ExecutionEventType.EXECUTION_SUCCEEDED,
        ExecutionEventType.EXECUTION_FAILED,
    }
    assert (
        len([event for event in page.items if event.type in terminal_types])
        == 1
    )


@dataclass(frozen=True)
class _TerminalAttempt:
    """One actor's proposed terminal outcome in a settlement race."""

    source: str
    status: str
    actor: Literal["runtime", "supervisor"]


@pytest.mark.parametrize(
    ("left", "right"),
    (
        (
            _TerminalAttempt("dispatcher_rejection", "failed", "supervisor"),
            _TerminalAttempt("runtime_success", "succeeded", "runtime"),
        ),
        (
            _TerminalAttempt("runtime_failure", "failed", "runtime"),
            _TerminalAttempt("supervisor_timeout", "timed_out", "supervisor"),
        ),
        (
            _TerminalAttempt("cancellation", "cancelled", "runtime"),
            _TerminalAttempt(
                "provider_reconciliation", "succeeded", "supervisor"
            ),
        ),
    ),
)
def test_terminal_source_races_publish_only_the_winner_fact(
    tmp_path: Path,
    left: _TerminalAttempt,
    right: _TerminalAttempt,
) -> None:
    """Every terminal source loses cleanly at the canonical transaction."""

    def outcome(status: str, source: str) -> DriverOutcome:
        if status == "succeeded":
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer=f"{source} winner")
            )
        if status == "failed":
            return DriverOutcome.failed(code=f"{source}_failed")
        return DriverOutcome(status=ExecutionStatus(status))

    db_path = tmp_path / f"terminal-source-race-{left.source}.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))
    reservation = repository.reserve(
        owner="alice",
        execution_id=f"turn-{left.source}-{right.source}",
        fingerprint_version=2,
        fingerprint="r" * 64,
        command=_command(query="rice"),
    )
    barrier = Barrier(2)

    def settle(
        source: str,
        status: str,
        actor: Literal["runtime", "supervisor"],
    ) -> bool:
        barrier.wait(timeout=5)
        return repository.settle_terminal(
            TerminalSettlementAuthority(
                owner_ref="alice",
                execution_id=reservation.execution_id,
                expected_revision=reservation.supervisor_revision,
                actor=actor,
                issued_at=datetime.now(UTC),
            ),
            outcome(status, source),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        left_result = executor.submit(
            settle, left.source, left.status, left.actor
        )
        right_result = executor.submit(
            settle, right.source, right.status, right.actor
        )
        assert sorted(
            (left_result.result(timeout=5), right_result.result(timeout=5))
        ) == [False, True]

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        reservation.execution_id, owner="alice"
    )
    assert page is not None
    terminal_types = {
        ExecutionEventType.EXECUTION_SUCCEEDED,
        ExecutionEventType.EXECUTION_PARTIAL,
        ExecutionEventType.EXECUTION_FAILED,
        ExecutionEventType.EXECUTION_CANCELLED,
        ExecutionEventType.EXECUTION_TIMED_OUT,
    }
    terminal_events = [
        event for event in page.items if event.type in terminal_types
    ]
    assert len(terminal_events) == 1
    assert (
        terminal_events[0].status
        == repository.get(
            owner="alice", execution_id=reservation.execution_id
        ).status.value
    )


def test_terminal_settlement_waits_for_sqlite_writer_then_commits_once(
    tmp_path: Path,
) -> None:
    """Verify terminal settlement waits for SQLite writer then commits once."""

    db_path = tmp_path / "terminal-contention.db"
    repository = SQLiteExecutionReservationRepository(str(db_path))
    reservation = repository.reserve(
        owner="alice",
        execution_id="turn-contention",
        fingerprint_version=2,
        fingerprint="x" * 64,
        command=_command(query="rice"),
    )
    authority = TerminalSettlementAuthority(
        owner_ref="alice",
        execution_id=reservation.execution_id,
        expected_revision=reservation.supervisor_revision,
        actor="runtime",
        issued_at=datetime.now(UTC),
    )
    blocker = sqlite3.connect(db_path, timeout=10)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                repository.settle_terminal,
                authority,
                DriverOutcome.succeeded(
                    TransportNeutralResult(answer="after contention")
                ),
            )
            blocker.commit()
            assert future.result(timeout=5)
    finally:
        blocker.close()

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        reservation.execution_id, owner="alice"
    )
    assert page is not None
    assert len(page.items) == 1
