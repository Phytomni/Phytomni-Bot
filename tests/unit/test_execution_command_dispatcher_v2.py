# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Durable execution-command dispatcher tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, ErrorData
from tests.support.execution_dispatch_fixtures import (
    invoke_dispatched_design,
    reserve_test_execution,
)

from mcp_server_phytomni.api import agent_runs
from mcp_server_phytomni.api.lifecycle_contract import SafeApiError
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.execution_command_dispatcher_v2 import (
    SQLiteExecutionCommandQueueV2,
    dispatch_one_execution_command,
    run_execution_command_dispatcher,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ExecutionEventType,
    ExecutionStatus,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
    TransportNeutralResult,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

_REPORTED_QUERY = (
    "Please help me design the protein structure based on evolution "
    "information for gene Os01g0177400."
)
_REPORTED_FINGERPRINT = "f" * 64


def test_selected_agent_reuses_outer_admission_fingerprint(tmp_path) -> None:
    """Verify selected agent reuses outer admission fingerprint."""

    db_path = str(tmp_path / "routed-identity.db")
    fingerprint = "f" * 64
    repository, _record = reserve_test_execution(
        db_path,
        "turn-routed-identity",
        fingerprint,
        ExecutionCommand(
            agent_slug=EXPERT_ROUTER_AGENT_SLUG,
            arguments={"__query": "route me"},
        ),
    )
    repository.bind_routed_agent(
        owner="alice",
        execution_id="turn-routed-identity",
        command=ExecutionCommand(
            agent_slug="design",
            arguments={"gene_id": "Os01g0177400"},
        ),
    )

    assert getattr(agent_runs, "_existing_execution_identity")(
        db_path=db_path,
        owner="alice",
        execution_id="turn-routed-identity",
    ) == (2, fingerprint)


def _reserve(db_path: str, execution_id: str = "turn-dispatcher") -> None:
    fingerprint = "a" * 64
    reserve_test_execution(
        db_path,
        execution_id,
        fingerprint,
        ExecutionCommand(
            agent_slug="chat",
            arguments={"user_query": "hello", "obs_file_list": []},
        ),
        persist_command=True,
    )


def _command_state(
    db_path: str, execution_id: str = "turn-dispatcher"
) -> tuple[str, int]:
    with sqlite_transaction(db_path) as connection:
        row = connection.execute(
            "SELECT state, attempt FROM execution_commands_v2 "
            "WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return str(row[0]), int(row[1])


@pytest.mark.asyncio
async def test_background_dispatcher_bootstraps_a_fresh_database(
    tmp_path,
) -> None:
    """A first boot must not lose its worker before the first admission."""
    db_path = str(tmp_path / "fresh.db")
    stop = asyncio.Event()

    async def invoke(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fresh database has no command to invoke")

    task = asyncio.create_task(
        run_execution_command_dispatcher(
            db_path=db_path,
            invoke=invoke,
            stop=stop,
            poll_seconds=0.01,
        )
    )
    await asyncio.sleep(0.05)
    assert task.done() is False
    stop.set()
    await task
    with sqlite_transaction(db_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='execution_commands_v2'"
        ).fetchone()
    assert table == ("execution_commands_v2",)


@pytest.mark.asyncio
async def test_dispatcher_invokes_once_and_acknowledges(tmp_path) -> None:
    """Verify dispatcher invokes once and acknowledges."""
    db_path = str(tmp_path / "tasks.db")
    _reserve(db_path)
    calls: list[tuple[str, dict[str, object], dict[str, object]]] = []

    async def invoke(
        tool: str, arguments: dict[str, object], **kwargs: object
    ) -> None:
        calls.append((tool, arguments, kwargs))

    worked = await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-a",
        db_path=db_path,
        invoke=invoke,
    )

    assert worked is True
    assert len(calls) == 1
    assert calls[0][0] == "ChatAgent"
    assert calls[0][2]["execution_id"] == "turn-dispatcher"
    assert calls[0][2]["agent_slug"] == "chat"
    assert _command_state(db_path) == ("acknowledged", 1)


@pytest.mark.asyncio
async def test_dispatcher_accepts_private_expert_router_command(
    tmp_path,
) -> None:
    """Verify dispatcher accepts private expert router command."""
    db_path = str(tmp_path / "expert.db")
    fingerprint = "e" * 64
    arguments = {
        "__query": "route me",
        "__allowed_tools": ["ChatAgent", "KnowledgeAgent"],
    }
    reserve_test_execution(
        db_path,
        "turn-expert",
        fingerprint,
        ExecutionCommand(
            agent_slug=EXPERT_ROUTER_AGENT_SLUG,
            arguments=arguments,
        ),
        persist_command=True,
    )
    calls: list[str] = []

    async def invoke(
        tool: str, _arguments: dict[str, object], **_kwargs: object
    ) -> None:
        calls.append(tool)

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-expert",
        db_path=db_path,
        invoke=invoke,
    )
    assert calls == ["ExpertRouter"]
    assert _command_state(db_path, "turn-expert") == ("acknowledged", 1)


@pytest.mark.asyncio
async def test_dispatcher_lease_allows_only_one_concurrent_claim(
    tmp_path,
) -> None:
    """Verify dispatcher lease allows only one concurrent claim."""
    db_path = str(tmp_path / "tasks.db")
    _reserve(db_path)
    queue = SQLiteExecutionCommandQueueV2(db_path)
    claims = await asyncio.gather(
        asyncio.to_thread(queue.claim, worker_id="worker-a"),
        asyncio.to_thread(queue.claim, worker_id="worker-b"),
    )
    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.asyncio
async def test_poison_command_rejects_without_invocation(
    tmp_path,
) -> None:
    """Verify poison command rejects without invocation."""
    db_path = str(tmp_path / "tasks.db")
    _reserve(db_path)
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE execution_commands_v2 SET command_json = '{}' "
            "WHERE execution_id = 'turn-dispatcher'"
        )
        connection.commit()
    invoked = False

    async def invoke(*_args: object, **_kwargs: object) -> None:
        nonlocal invoked
        invoked = True

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-a",
        db_path=db_path,
        invoke=invoke,
    )
    assert invoked is False
    assert _command_state(db_path) == ("rejected", 1)
    record = SQLiteExecutionReservationRepository(db_path).get(
        owner="alice", execution_id="turn-dispatcher"
    )
    assert record.status is ExecutionStatus.FAILED
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-dispatcher", owner="alice"
    )
    assert projection.terminal is not None
    assert projection.terminal.status == "failed"


@pytest.mark.asyncio
async def test_dispatcher_terminal_cas_loser_publishes_no_failed_fact(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent Runtime success must fence out every dispatcher fact."""
    db_path = str(tmp_path / "terminal-race.db")
    _reserve(db_path)
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE execution_commands_v2 SET command_json = '{}' "
            "WHERE execution_id = 'turn-dispatcher'"
        )
        connection.commit()

    original_settle = SQLiteExecutionReservationRepository.settle_terminal
    injected_success = False

    def settle_after_runtime_wins(
        repository: SQLiteExecutionReservationRepository,
        authority: TerminalSettlementAuthority,
        outcome: DriverOutcome,
    ) -> bool:
        nonlocal injected_success
        if outcome.status is ExecutionStatus.FAILED and not injected_success:
            injected_success = True
            current = repository.get(
                owner=authority.owner_ref,
                execution_id=authority.execution_id,
            )
            assert original_settle(
                repository,
                TerminalSettlementAuthority(
                    owner_ref=authority.owner_ref,
                    execution_id=authority.execution_id,
                    expected_revision=current.supervisor_revision,
                    actor="runtime",
                    issued_at=datetime.now(UTC),
                ),
                DriverOutcome.succeeded(
                    TransportNeutralResult(answer="runtime winner")
                ),
            )
        return original_settle(repository, authority, outcome)

    monkeypatch.setattr(
        SQLiteExecutionReservationRepository,
        "settle_terminal",
        settle_after_runtime_wins,
    )

    async def invoke(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "poison command must not invoke Agent business logic"
        )

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-terminal-race",
        db_path=db_path,
        invoke=invoke,
    )
    record = SQLiteExecutionReservationRepository(db_path).get(
        owner="alice", execution_id="turn-dispatcher"
    )
    assert record.status is ExecutionStatus.SUCCEEDED
    page = SQLiteExecutionJournal(db_path).list_events(
        "turn-dispatcher", owner="alice"
    )
    assert page is not None
    assert all(
        event.type is not ExecutionEventType.EXECUTION_FAILED
        for event in page.items
    )


@pytest.mark.asyncio
async def test_transient_failure_retries_without_leaking_exception_text(
    tmp_path,
) -> None:
    """Verify transient failure retries without leaking exception text."""
    db_path = str(tmp_path / "tasks.db")
    _reserve(db_path)

    async def invoke(*_args: object, **_kwargs: object) -> None:
        raise OSError("private credential=must-not-persist")

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-a",
        db_path=db_path,
        invoke=invoke,
    )
    assert _command_state(db_path) == ("retry", 1)
    with sqlite_transaction(db_path) as connection:
        error_code = connection.execute(
            "SELECT last_error_code FROM execution_commands_v2"
        ).fetchone()[0]
    assert error_code == "transport_connection_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_state", "expected_code"),
    [
        (
            McpError(
                ErrorData(code=INVALID_PARAMS, message="private invalid input")
            ),
            "rejected",
            "mcp_invalid_params",
        ),
        (
            SafeApiError(
                status_code=422,
                code="agent_business_validation_failed",
                message="private semantic detail",
                stage="agent",
                retryable=False,
            ),
            "rejected",
            "agent_business_validation_failed",
        ),
        (
            SafeApiError(
                status_code=409,
                code="conversation_context_turn_in_progress",
                message="private replay detail",
                stage="context",
                retryable=True,
            ),
            "reconcile",
            "conversation_context_turn_in_progress",
        ),
        (
            ConnectionError("private connection detail"),
            "retry",
            "transport_connection_failed",
        ),
        (
            RuntimeError("private unknown post-invocation detail"),
            "reconcile",
            "dispatch_unknown_after_boundary",
        ),
    ],
)
async def test_dispatch_failure_uses_finite_three_way_taxonomy(
    tmp_path,
    failure: Exception,
    expected_state: str,
    expected_code: str,
) -> None:
    """Verify dispatch failure uses finite three way taxonomy."""
    db_path = str(tmp_path / "failure-taxonomy.db")
    _reserve(db_path)

    async def invoke(*_args: object, **_kwargs: object) -> None:
        raise failure

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-taxonomy",
        db_path=db_path,
        invoke=invoke,
    )
    with sqlite_transaction(db_path) as connection:
        row = connection.execute(
            "SELECT state, last_error_code FROM execution_commands_v2 "
            "WHERE execution_id = 'turn-dispatcher'"
        ).fetchone()
    assert row == (expected_state, expected_code)


@pytest.mark.asyncio
async def test_nonretryable_safe_failure_terminalizes_without_retries(
    tmp_path,
) -> None:
    """A classified semantic rejection settles the admitted turn once."""
    db_path = str(tmp_path / "nonretryable.db")
    _reserve(db_path)

    async def invoke(*_args: object, **_kwargs: object) -> None:
        raise SafeApiError(
            status_code=422,
            code="agent_business_validation_failed",
            message="bounded failure",
            stage="agent",
            retryable=False,
        )

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-nonretryable",
        db_path=db_path,
        invoke=invoke,
    )

    assert _command_state(db_path) == ("rejected", 1)
    record = SQLiteExecutionReservationRepository(db_path).get(
        owner="alice", execution_id="turn-dispatcher"
    )
    assert record.status is ExecutionStatus.FAILED
    with sqlite_transaction(db_path) as connection:
        error_code = connection.execute(
            "SELECT last_error_code FROM execution_commands_v2"
        ).fetchone()[0]
    assert error_code == "agent_business_validation_failed"


@pytest.mark.asyncio
async def test_started_execution_acknowledged_if_projection_fails(
    tmp_path,
) -> None:
    """Verify started execution acknowledged if projection fails."""
    db_path = str(tmp_path / "started.db")
    _reserve(db_path)
    repository = SQLiteExecutionReservationRepository(db_path)

    async def invoke(*_args: object, **_kwargs: object) -> None:
        assert repository.claim_start(
            owner="alice", execution_id="turn-dispatcher"
        )
        repository.mark_running(owner="alice", execution_id="turn-dispatcher")
        raise ValueError("private response projection detail")

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-started",
        db_path=db_path,
        invoke=invoke,
    )
    assert _command_state(db_path) == ("acknowledged", 1)


@pytest.mark.asyncio
async def test_retry_exhaustion_reconciles_without_terminalizing_execution(
    tmp_path,
) -> None:
    """Ambiguous delivery exhaustion remains reconcilable and non-terminal."""
    db_path = str(tmp_path / "exhausted.db")
    _reserve(db_path)
    with sqlite_transaction(db_path) as connection:
        connection.execute(
            "UPDATE execution_commands_v2 SET attempt = 4 "
            "WHERE execution_id = 'turn-dispatcher'"
        )
        connection.commit()

    async def invoke(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private routing failure")

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-exhausted",
        db_path=db_path,
        invoke=invoke,
    )

    assert _command_state(db_path) == ("reconcile", 5)
    record = SQLiteExecutionReservationRepository(db_path).get(
        owner="alice", execution_id="turn-dispatcher"
    )
    assert record.status is ExecutionStatus.ADMITTED
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-dispatcher", owner="alice"
    )
    assert projection.terminal is None
    page = SQLiteExecutionJournal(db_path).list_events(
        "turn-dispatcher", owner="alice"
    )
    assert page is not None
    assert not page.items


@pytest.mark.asyncio
async def test_design_context_replay_enters_reconcile_without_reinvocation(
    tmp_path,
) -> None:
    """The reported DigitalDesignAgent shape keeps its first safe cause."""
    db_path = str(tmp_path / "design-context-replay.db")
    execution_id = "turn-033572cb-6874-4216-ab12-911b5304fc2d"
    fingerprint = "d" * 64
    query = (
        "Please help me design the protein structure based on evolution "
        "information for gene Os01g0177400."
    )
    reserve_test_execution(
        db_path,
        execution_id,
        fingerprint,
        ExecutionCommand(
            agent_slug="design",
            arguments={"user_query": query, "obs_file_list": []},
        ),
        persist_command=True,
    )
    calls = 0

    async def invoke(
        tool: str, arguments: dict[str, object], **_kwargs: object
    ) -> None:
        nonlocal calls
        calls += 1
        assert tool == "DigitalDesignAgent"
        assert arguments == {"user_query": query, "obs_file_list": []}
        raise SafeApiError(
            status_code=409,
            code="conversation_context_turn_in_progress",
            message="private replay detail",
            stage="context",
            retryable=True,
        )

    queue = SQLiteExecutionCommandQueueV2(db_path)
    assert await dispatch_one_execution_command(
        queue=queue,
        worker_id="worker-design",
        db_path=db_path,
        invoke=invoke,
    )
    assert not await dispatch_one_execution_command(
        queue=queue,
        worker_id="worker-design",
        db_path=db_path,
        invoke=invoke,
    )
    assert calls == 1
    with sqlite_transaction(db_path) as connection:
        row = connection.execute(
            "SELECT state, classification, boundary_state, "
            "first_error_code, last_error_code FROM execution_commands_v2 "
            "WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
    assert row == (
        "reconcile",
        "reconcile",
        "durably_claimed",
        "conversation_context_turn_in_progress",
        "conversation_context_turn_in_progress",
    )


@pytest.mark.asyncio
async def test_reported_preclaim_conflict_persists_complete_stall_signature(
    tmp_path,
) -> None:
    """Capture the exact admitted/reconcile/zero-fact failure signature."""

    db_path = str(tmp_path / "reported-preclaim-stall.db")
    execution_id = "turn-fd0d579b-c9f4-4fe7-a22c-a9e9d8f48884"
    conversation_key = "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6"
    prepared_arguments = {
        "user_query": _REPORTED_QUERY,
        "obs_file_list": [],
        "resolve_gene_id": True,
    }
    durable_arguments = {
        **prepared_arguments,
        "__query": _REPORTED_QUERY,
        "__agent_slug": "design",
        "__allowed_tools": ["DigitalDesignAgent"],
        "__forced_tool": "DigitalDesignAgent",
        "__conversation": {
            "schema_version": 1,
            "conversation_key": conversation_key,
            "turn_id": "60",
            "mode": "expert",
            "requested_agent_id": "DigitalDesignAgent",
            "allowed_agent_ids": ["DigitalDesignAgent"],
        },
    }
    repository = reserve_test_execution(
        db_path,
        execution_id,
        _REPORTED_FINGERPRINT,
        ExecutionCommand(agent_slug="design", arguments=durable_arguments),
        persist_command=True,
    )[0]
    context = ConversationContextStore(db_path)
    context.begin_turn(conversation_key, "60", "append", 0)
    context.mark_turn_failed(conversation_key, "60")
    business_calls = 0

    async def invoke(
        _tool: str, arguments: dict[str, object], **kwargs: object
    ) -> None:
        async def business_call() -> dict[str, str]:
            nonlocal business_calls
            business_calls += 1
            return {"status": "running"}

        await invoke_dispatched_design(
            db_path, arguments, kwargs, business_call
        )

    assert await dispatch_one_execution_command(
        queue=SQLiteExecutionCommandQueueV2(db_path),
        worker_id="worker-reported-stall",
        db_path=db_path,
        invoke=invoke,
    )

    assert business_calls == 0
    assert (
        repository.get(owner="alice", execution_id=execution_id).status
        is ExecutionStatus.ADMITTED
    )
    turn = context.load_turn(conversation_key, "60")
    assert turn is not None
    assert turn.state == "failed"
    with sqlite_transaction(db_path) as connection:
        command = connection.execute(
            "SELECT state, classification FROM execution_commands_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            ("alice", execution_id),
        ).fetchone()
        count_queries = {
            "execution_events_v2": (
                "SELECT COUNT(*) FROM execution_events_v2 "
                "WHERE execution_id = ?"
            ),
            "execution_spans": (
                "SELECT COUNT(*) FROM execution_spans WHERE execution_id = ?"
            ),
            "execution_work_units": (
                "SELECT COUNT(*) FROM execution_work_units "
                "WHERE execution_id = ?"
            ),
            "execution_target_bindings_v2": (
                "SELECT COUNT(*) FROM execution_target_bindings_v2 "
                "WHERE execution_id = ?"
            ),
        }
        counts = {
            table: connection.execute(query, (execution_id,)).fetchone()[0]
            for table, query in count_queries.items()
        }
    assert command == ("reconcile", "reconcile")
    assert counts == {
        "execution_events_v2": 0,
        "execution_spans": 0,
        "execution_work_units": 0,
        "execution_target_bindings_v2": 0,
    }


def test_reconcile_rows_use_a_dedicated_expiring_lease(tmp_path) -> None:
    """Verify reconcile rows use a dedicated expiring lease."""
    db_path = str(tmp_path / "reconcile-lease.db")
    now = datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    current = now
    queue = SQLiteExecutionCommandQueueV2(db_path, clock=lambda: current)
    _reserve(db_path)
    dispatch_claim = queue.claim(worker_id="normal-worker")
    assert dispatch_claim is not None
    assert queue.reconcile(
        dispatch_claim,
        code="dispatch_unknown_after_boundary",
    )

    claimed = queue.claim_reconcile(worker_id="reconcile-a", lease_seconds=30)
    assert claimed is not None
    assert claimed.reconcile_attempt == 1
    assert claimed.redispatch_count == 0
    assert queue.claim(worker_id="normal-worker") is None
    assert queue.claim_reconcile(worker_id="reconcile-b") is None

    current = now + timedelta(seconds=31)
    reclaimed = queue.claim_reconcile(worker_id="reconcile-b")
    assert reclaimed is not None
    assert reclaimed.reconcile_attempt == 2
    assert reclaimed.revision > claimed.revision


def test_reconcile_claim_can_reschedule_resolve_or_redispatch_once(
    tmp_path,
) -> None:
    """Verify reconcile claim can reschedule resolve or redispatch once."""
    db_path = str(tmp_path / "reconcile-transitions.db")
    now = datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    queue = SQLiteExecutionCommandQueueV2(db_path, clock=lambda: now)
    _reserve(db_path)
    dispatch_claim = queue.claim(worker_id="normal-worker")
    assert dispatch_claim is not None
    assert queue.reconcile(dispatch_claim, code="ambiguous")

    first = queue.claim_reconcile(worker_id="reconcile-a")
    assert first is not None
    assert queue.reschedule_reconcile(
        first, code="reconcile_ambiguous", delay_seconds=15
    )
    assert queue.claim_reconcile(worker_id="reconcile-a") is None

    later = now + timedelta(seconds=16)
    later_queue = SQLiteExecutionCommandQueueV2(db_path, clock=lambda: later)
    second = later_queue.claim_reconcile(worker_id="reconcile-b")
    assert second is not None
    assert later_queue.redispatch_reconcile(second)
    retry = later_queue.claim(worker_id="normal-worker")
    assert retry is not None
    assert retry.attempt == 2
    assert later_queue.reconcile(retry, code="still_ambiguous")

    final = later_queue.claim_reconcile(worker_id="reconcile-c")
    assert final is not None
    assert final.redispatch_count == 1
    assert later_queue.redispatch_reconcile(final) is False
    assert later_queue.resolve_reconcile(final)
    assert _command_state(db_path) == ("acknowledged", 2)
