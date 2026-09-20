# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Execution Runtime control, cancellation, and recovery tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support.execution_runtime_v2 import build_execution_runtime_stack
from tests.unit.test_execution_runtime_v2 import _command

from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ExecutionStatus,
    TrackingHealth,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationConflictError,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TransportNeutralResult,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction


def test_running_result_publishes_snapshot_without_terminal_completion(
    tmp_path: Path,
) -> None:
    """Verify running result publishes snapshot without terminal completion."""

    @dataclass(eq=False)
    class Driver:
        """Driver returning a running snapshot without completion."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            return DriverOutcome.running(
                result=TransportNeutralResult(answer="bounded draft")
            )

    runtime, _reservations, journal, _work = build_execution_runtime_stack(
        tmp_path / "snapshot.db", Driver()
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-snapshot",
            fingerprint_version=1,
            fingerprint="2" * 64,
            command=_command(),
            transport="http",
        )
    )

    assert outcome.status.value == "running"
    page = journal.list_events("turn-snapshot", owner="alice", limit=20)
    assert page is not None
    types = [event.type.value for event in page.items]
    assert types.count("message.snapshot") == 1
    assert "message.completed" not in types


def test_concurrent_start_dispatches_driver_once(tmp_path: Path) -> None:
    """Verify concurrent start dispatches driver once."""

    @dataclass(eq=False)
    class Driver:
        """Blocking driver used to prove dispatch occurs once."""

        def __init__(self) -> None:
            self.calls = 0
            self.release = asyncio.Event()

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            self.calls += 1
            await self.release.wait()
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    async def scenario() -> tuple[DriverOutcome, DriverOutcome, int]:
        driver = Driver()
        runtime, _reservations, _journal, _work = (
            build_execution_runtime_stack(
                tmp_path / "concurrent-runtime.db", driver
            )
        )

        async def start() -> DriverOutcome:
            return await runtime.start(
                owner="alice",
                execution_id="turn-concurrent-runtime",
                fingerprint_version=1,
                fingerprint="c" * 64,
                command=_command(),
                transport="http",
            )

        first_task = asyncio.create_task(start())
        await asyncio.sleep(0)
        second = await start()
        driver.release.set()
        first = await first_task
        return first, second, driver.calls

    first, second, calls = asyncio.run(scenario())
    assert calls == 1
    assert first.status.value == "succeeded"
    assert second.status.value in {"dispatching", "running"}


def test_resume_action_is_revision_checked_and_idempotently_replayed(
    tmp_path: Path,
) -> None:
    """Verify resume action is revision checked and idempotently replayed."""

    @dataclass(eq=False)
    class Driver:
        """Driver recording idempotent resume calls and revisions."""

        def __init__(self) -> None:
            self.operations: list[str] = []

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del context, command, services
            self.operations.append(operation.value)
            if operation.value == "start":
                return DriverOutcome(status=ExecutionStatus.WAITING_INPUT)
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="resumed")
            )

    driver = Driver()
    runtime, reservations, journal, _work = build_execution_runtime_stack(
        tmp_path / "resume.db", driver, "resumable_graph"
    )
    started = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-resume",
            fingerprint_version=1,
            fingerprint="d" * 64,
            command=_command("review"),
            transport="http",
        )
    )
    assert started.status.value == "waiting_input"
    revision = reservations.get(
        owner="alice", execution_id="turn-resume"
    ).supervisor_revision

    with pytest.raises(
        ExecutionReservationConflictError, match="stale_revision"
    ):
        asyncio.run(
            runtime.resume(
                owner="alice",
                execution_id="turn-resume",
                command=ExecutionCommand(
                    agent_slug="review",
                    arguments={"answer": "private-stale-input-marker"},
                    action_id="action-stale",
                    expected_revision=revision - 1,
                ),
                transport="http",
            )
        )
    action = ExecutionCommand(
        agent_slug="review",
        arguments={"answer": "continue"},
        action_id="action-resume-1",
        expected_revision=revision,
    )
    assert (
        asyncio.run(
            runtime.resume(
                owner="alice",
                execution_id="turn-resume",
                command=action,
                transport="http",
            )
        ).status.value
        == "succeeded"
    )
    assert (
        asyncio.run(
            runtime.resume(
                owner="alice",
                execution_id="turn-resume",
                command=action,
                transport="mcp",
            )
        ).status.value
        == "succeeded"
    )

    assert driver.operations == ["start", "resume"]
    page = journal.list_events("turn-resume", owner="alice", limit=30)
    assert page is not None
    event_types = [event.type.value for event in page.items]
    assert event_types.count("execution.resumed") == 1
    assert event_types.count("execution.succeeded") == 1
    assert event_types.count("input.action_claimed") == 1
    assert event_types.count("input.resolved") == 1
    assert event_types.count("input.action_rejected") == 2
    rejected = [
        event.public_payload.model_dump(mode="json")
        for event in page.items
        if event.type.value == "input.action_rejected"
    ]
    assert {payload["outcome"] for payload in rejected} == {
        "duplicate_action",
        "stale_revision",
    }
    encoded = str([event.to_public_dict() for event in page.items])
    assert "continue" not in encoded
    assert "private-stale-input-marker" not in encoded


def test_cancel_best_effort_remains_nonterminal_and_is_explicit(
    tmp_path: Path,
) -> None:
    """Verify cancel best effort remains nonterminal and is explicit."""

    @dataclass(eq=False)
    class Driver:
        """Driver reporting best-effort cancellation as nonterminal."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del context, command, services
            if operation.value == "cancel":
                return DriverOutcome.running(
                    cancellation_outcome="best_effort"
                )
            return DriverOutcome.running()

    runtime, reservations, journal, _work = build_execution_runtime_stack(
        tmp_path / "cancel.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-cancel",
            fingerprint_version=1,
            fingerprint="e" * 64,
            command=_command(),
            transport="http",
        )
    )
    revision = reservations.get(
        owner="alice", execution_id="turn-cancel"
    ).supervisor_revision
    outcome = asyncio.run(
        runtime.cancel(
            owner="alice",
            execution_id="turn-cancel",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={"reason": "user_requested"},
                action_id="cancel-1",
                expected_revision=revision,
            ),
            transport="http",
        )
    )

    record = reservations.get(owner="alice", execution_id="turn-cancel")
    assert outcome.status.value == "running"
    assert outcome.cancellation_outcome == "best_effort"
    assert record.cancellation_state == "best_effort"
    assert record.status.value == "running"
    page = journal.list_events("turn-cancel", owner="alice", limit=30)
    assert page is not None
    payloads = [
        event.public_payload.model_dump(mode="json")
        for event in page.items
        if event.type.value == "execution.cancellation_requested"
    ]
    assert {payload["outcome"] for payload in payloads} == {
        "requested",
        "best_effort",
    }


def test_best_effort_cancel_preserves_late_start_terminal(
    tmp_path: Path,
) -> None:
    """Verify best effort cancel preserves late start terminal."""

    entered = asyncio.Event()
    release = asyncio.Event()

    @dataclass(eq=False)
    class Driver:
        """Driver completing after a best-effort cancel request."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del context, command, services
            if operation.value == "cancel":
                return DriverOutcome.running(
                    cancellation_outcome="best_effort"
                )
            entered.set()
            await release.wait()
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="late provider answer")
            )

    runtime, reservations, journal, _work = build_execution_runtime_stack(
        tmp_path / "cancel-late-terminal.db", Driver()
    )

    async def scenario() -> None:
        start = asyncio.create_task(
            runtime.start(
                owner="alice",
                execution_id="turn-cancel-late-terminal",
                fingerprint_version=1,
                fingerprint="1" * 64,
                command=_command(),
                transport="http",
            )
        )
        await entered.wait()
        revision = reservations.get(
            owner="alice", execution_id="turn-cancel-late-terminal"
        ).supervisor_revision
        cancellation = await runtime.cancel(
            owner="alice",
            execution_id="turn-cancel-late-terminal",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={"reason": "user_requested"},
                action_id="cancel-late-terminal",
                expected_revision=revision,
            ),
            transport="http",
        )
        assert cancellation.cancellation_outcome == "best_effort"
        release.set()
        terminal = await start
        assert terminal.status.value == "succeeded"

    asyncio.run(scenario())

    record = reservations.get(
        owner="alice", execution_id="turn-cancel-late-terminal"
    )
    projection = journal.get_projection(
        "turn-cancel-late-terminal", owner="alice"
    )
    assert record.status.value == "succeeded"
    assert record.cancellation_state == "best_effort"
    assert projection.terminal is not None
    assert projection.terminal.status == "succeeded"


def test_confirmed_cancellation_is_terminal_and_persisted(
    tmp_path: Path,
) -> None:
    """Verify confirmed cancellation is terminal and persisted."""

    @dataclass(eq=False)
    class Driver:
        """Driver confirming cancellation as a terminal outcome."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del context, command, services
            if operation.value == "cancel":
                return DriverOutcome(
                    status=ExecutionStatus.CANCELLED,
                    cancellation_outcome="confirmed",
                )
            return DriverOutcome.running()

    runtime, reservations, _journal, _work = build_execution_runtime_stack(
        tmp_path / "cancel-confirmed.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-cancel-confirmed",
            fingerprint_version=1,
            fingerprint="f" * 64,
            command=_command(),
            transport="http",
        )
    )
    revision = reservations.get(
        owner="alice", execution_id="turn-cancel-confirmed"
    ).supervisor_revision
    outcome = asyncio.run(
        runtime.cancel(
            owner="alice",
            execution_id="turn-cancel-confirmed",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={"reason": "user_requested"},
                action_id="cancel-confirmed-1",
                expected_revision=revision,
            ),
            transport="http",
        )
    )
    record = reservations.get(
        owner="alice", execution_id="turn-cancel-confirmed"
    )
    assert outcome.status.value == "cancelled"
    assert record.status.value == "cancelled"
    assert record.cancellation_state == "confirmed"


def test_reconcile_schedules_retry_and_tracks_degradation_recovery(
    tmp_path: Path,
) -> None:
    """Verify reconcile schedules retry and tracks degradation recovery."""

    @dataclass(eq=False)
    class Driver:
        """Driver failing transiently before succeeding on retry."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del context, command, services
            if operation.value == "reconcile":
                return DriverOutcome.running(
                    retry_after_ms=250,
                    tracking_health=TrackingHealth.DEGRADED,
                )
            if operation.value == "recover":
                return DriverOutcome.running()
            return DriverOutcome.running()

    runtime, reservations, journal, _work = build_execution_runtime_stack(
        tmp_path / "retry.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-retry",
            fingerprint_version=1,
            fingerprint="f" * 64,
            command=_command(),
            transport="http",
        )
    )
    revision = reservations.get(
        owner="alice", execution_id="turn-retry"
    ).supervisor_revision
    retry = asyncio.run(
        runtime.reconcile(
            owner="alice",
            execution_id="turn-retry",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={},
                action_id="reconcile-1",
                expected_revision=revision,
            ),
        )
    )
    record = reservations.get(owner="alice", execution_id="turn-retry")
    assert retry.retry_after_ms == 250
    assert record.next_attempt_at is not None
    assert record.tracking_health == "degraded"

    recovered = asyncio.run(
        runtime.recover(
            owner="alice",
            execution_id="turn-retry",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={},
                action_id="recover-1",
                expected_revision=record.supervisor_revision,
            ),
        )
    )
    assert recovered.status.value == "running"
    assert (
        reservations.get(
            owner="alice", execution_id="turn-retry"
        ).tracking_health
        == "healthy"
    )
    page = journal.list_events("turn-retry", owner="alice", limit=40)
    assert page is not None
    types = [event.type.value for event in page.items]
    assert "span.retry_scheduled" in types
    assert "tracking.degraded" in types
    assert "tracking.recovered" in types


def test_recovery_enforces_deadline_and_partial_is_terminal(
    tmp_path: Path,
) -> None:
    """Verify recovery enforces deadline and partial is terminal."""

    @dataclass(eq=False)
    class Driver:
        """Driver exposing deadline and partial-result recovery."""

        def __init__(self) -> None:
            self.recover_calls = 0

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del context, command, services
            if operation.value == "recover":
                self.recover_calls += 1
            return DriverOutcome.running()

    driver = Driver()
    db_path = tmp_path / "deadline.db"
    runtime, reservations, journal, _work = build_execution_runtime_stack(
        db_path, driver
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-deadline",
            fingerprint_version=1,
            fingerprint="1" * 64,
            command=_command(),
            transport="http",
        )
    )
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_deadline_at = ? WHERE execution_id = ?",
            (past, "turn-deadline"),
        )
        connection.commit()
    record = reservations.get(owner="alice", execution_id="turn-deadline")
    outcome = asyncio.run(
        runtime.recover(
            owner="alice",
            execution_id="turn-deadline",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={},
                action_id="recover-deadline",
                expected_revision=record.supervisor_revision,
            ),
        )
    )

    assert outcome.status.value == "timed_out"
    assert driver.recover_calls == 0
    assert (
        reservations.get(
            owner="alice", execution_id="turn-deadline"
        ).status.value
        == "timed_out"
    )
    page = journal.list_events("turn-deadline", owner="alice", limit=30)
    assert page is not None
    assert [event.type.value for event in page.items][-2:] == [
        "span.timed_out",
        "execution.timed_out",
    ]


def test_partial_driver_outcome_settles_root_and_execution(
    tmp_path: Path,
) -> None:
    """Verify partial driver outcome settles root and execution."""

    @dataclass(eq=False)
    class Driver:
        """Driver returning a partial terminal outcome."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            return DriverOutcome(status=ExecutionStatus.PARTIAL)

    runtime, reservations, journal, work = build_execution_runtime_stack(
        tmp_path / "partial.db", Driver()
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-partial",
            fingerprint_version=1,
            fingerprint="2" * 64,
            command=_command(),
            transport="http",
        )
    )

    record = reservations.get(owner="alice", execution_id="turn-partial")
    assert outcome.status.value == "partial"
    assert record.status.value == "partial"
    assert (
        work.get_span(
            "turn-partial", record.root_span_id, owner="alice"
        ).status.value
        == "partial"
    )
    page = journal.list_events("turn-partial", owner="alice", limit=20)
    assert page is not None
    assert [event.type.value for event in page.items][-2:] == [
        "span.partial",
        "execution.partial",
    ]
