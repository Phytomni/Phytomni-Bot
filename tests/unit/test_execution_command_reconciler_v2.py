# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dedicated reconciliation for detached execution commands."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.execution_command_dispatcher_v2 import (
    SQLiteExecutionCommandQueueV2,
    dispatch_one_execution_command,
)
from mcp_server_phytomni.runtime.execution_command_reconciler_v2 import (
    ReconcileDisposition,
    read_reconcile_evidence,
    reconcile_one_execution_command,
)
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    CanonicalReservationIdentity,
    bind_canonical_reservation_identity,
    bind_routed_reservation_identity,
    invoke_public_agent,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
)

pytestmark = pytest.mark.unit

_EXECUTION_ID = "turn-fd0d579b-c9f4-4fe7-a22c-a9e9d8f48884"
_CONVERSATION_KEY = "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6"
_TURN_ID = "60"
_FINGERPRINT = "f" * 64


def _durable_arguments() -> dict[str, object]:
    return {
        "user_query": "Os01g0177400",
        "obs_file_list": [],
        "__agent_slug": "design",
        "__query": "Os01g0177400",
        "__allowed_tools": ["DigitalDesignAgent"],
        "__forced_tool": "DigitalDesignAgent",
        "__conversation": {
            "schema_version": 1,
            "conversation_key": _CONVERSATION_KEY,
            "turn_id": _TURN_ID,
            "mode": "expert",
            "requested_agent_id": "DigitalDesignAgent",
            "allowed_agent_ids": ["DigitalDesignAgent"],
        },
    }


def _prepare_reconcile(
    db_path: str,
    *,
    failed_turn: bool = True,
    clock: datetime | None = None,
) -> SQLiteExecutionCommandQueueV2:
    now = clock or datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    arguments = _durable_arguments()
    SQLiteExecutionReservationRepository(db_path).reserve(
        owner="alice",
        execution_id=_EXECUTION_ID,
        fingerprint_version=2,
        fingerprint=_FINGERPRINT,
        command=ExecutionCommand(agent_slug="design", arguments=arguments),
        durable_command={
            "agent": "design",
            "arguments": arguments,
            "execution_id": _EXECUTION_ID,
            "owner_ref": "alice",
            "fingerprint_version": 2,
            "fingerprint": _FINGERPRINT,
        },
    )
    if failed_turn:
        context = ConversationContextStore(db_path)
        context.begin_turn(_CONVERSATION_KEY, _TURN_ID, "append", 0)
        context.mark_turn_failed(_CONVERSATION_KEY, _TURN_ID)
    queue = SQLiteExecutionCommandQueueV2(db_path, clock=lambda: now)
    claimed = queue.claim(worker_id="normal-worker")
    assert claimed is not None
    assert queue.reconcile(
        claimed,
        code="dispatch_unknown_after_boundary",
    )
    return queue


def _prepare_routed_reconcile(
    db_path: str,
    *,
    clock: datetime | None = None,
) -> SQLiteExecutionCommandQueueV2:
    now = clock or datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    arguments = _durable_arguments()
    arguments.pop("__forced_tool", None)
    conversation = arguments["__conversation"]
    assert isinstance(conversation, dict)
    conversation["requested_agent_id"] = None
    router_command = ExecutionCommand(
        agent_slug=EXPERT_ROUTER_AGENT_SLUG,
        arguments=arguments,
    )
    repository = SQLiteExecutionReservationRepository(db_path)
    repository.reserve(
        owner="alice",
        execution_id=_EXECUTION_ID,
        fingerprint_version=2,
        fingerprint=_FINGERPRINT,
        command=router_command,
        durable_command={
            "agent": EXPERT_ROUTER_AGENT_SLUG,
            "arguments": arguments,
            "execution_id": _EXECUTION_ID,
            "owner_ref": "alice",
            "fingerprint_version": 2,
            "fingerprint": _FINGERPRINT,
        },
    )
    repository.bind_routed_agent(
        owner="alice",
        execution_id=_EXECUTION_ID,
        command=ExecutionCommand(
            agent_slug="design",
            arguments={
                "user_query": "Os01g0177400",
                "obs_file_list": [],
                "resolve_gene_id": True,
            },
        ),
    )
    context = ConversationContextStore(db_path)
    context.begin_turn(_CONVERSATION_KEY, _TURN_ID, "append", 0)
    context.mark_turn_failed(_CONVERSATION_KEY, _TURN_ID)
    queue = SQLiteExecutionCommandQueueV2(db_path, clock=lambda: now)
    claimed = queue.claim(worker_id="normal-worker")
    assert claimed is not None
    assert queue.reconcile(
        claimed,
        code="dispatch_unknown_after_boundary",
    )
    return queue


def _command_row(db_path: str) -> tuple[object, ...]:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT state, reconcile_attempt, reconcile_redispatch_count, "
            "last_error_code FROM execution_commands_v2 "
            "WHERE execution_id = ?",
            (_EXECUTION_ID,),
        ).fetchone()
    assert row is not None
    return row


def test_evidence_reader_covers_authoritative_unstarted_state(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "reconcile-evidence.db")
    queue = _prepare_reconcile(db_path)
    claim = queue.claim_reconcile(worker_id="reconcile-evidence")
    assert claim is not None

    evidence = read_reconcile_evidence(db_path=db_path, claim=claim)

    assert evidence.reservation_status == "admitted"
    assert evidence.projection_terminal is False
    assert evidence.event_count == 0
    assert evidence.span_count == 0
    assert evidence.work_unit_count == 0
    assert evidence.provider_identity_count == 0
    assert evidence.result_count == 0
    assert evidence.conversation_key == _CONVERSATION_KEY
    assert evidence.turn_id == _TURN_ID
    assert evidence.conversation_turn_state == "failed"
    assert evidence.canonical_identity_matches is True
    assert evidence.provably_unstarted is True


def test_evidence_reader_recognizes_bound_routed_unstarted_state(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "routed-reconcile-evidence.db")
    queue = _prepare_routed_reconcile(db_path)
    claim = queue.claim_reconcile(worker_id="routed-evidence")
    assert claim is not None

    evidence = read_reconcile_evidence(db_path=db_path, claim=claim)

    assert evidence.canonical_identity_matches is False
    assert evidence.routed_binding_matches is True
    assert evidence.operation_count == 0
    assert evidence.runtime_started is False
    assert evidence.provably_unstarted is True


@pytest.mark.asyncio
async def test_safe_reconcile_releases_failed_turn_and_redispatches_once(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "safe-reconcile.db")
    now = datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    queue = _prepare_reconcile(db_path, clock=now)

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="reconcile-safe",
        db_path=db_path,
    )

    assert disposition is ReconcileDisposition.REDISPATCHED
    assert (
        ConversationContextStore(db_path).load_turn(
            _CONVERSATION_KEY, _TURN_ID
        )
        is None
    )
    assert _command_row(db_path)[:3] == ("retry", 1, 1)
    calls = 0

    async def invoke(
        _tool: str, arguments: dict[str, object], **kwargs: object
    ) -> None:
        fingerprint_version = kwargs["fingerprint_version"]
        assert isinstance(fingerprint_version, int)
        identity = CanonicalReservationIdentity(
            owner="alice",
            execution_id=_EXECUTION_ID,
            fingerprint_version=2,
            fingerprint=_FINGERPRINT,
            command=ExecutionCommand(agent_slug="design", arguments=arguments),
        )

        async def business_call() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"status": "succeeded"}

        with bind_canonical_reservation_identity(identity):
            await invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id=str(kwargs["execution_id"]),
                agent_slug="design",
                arguments={
                    key: value
                    for key, value in arguments.items()
                    if not key.startswith("__")
                },
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=fingerprint_version,
                fingerprint=str(kwargs["fingerprint"]),
            )

    assert await dispatch_one_execution_command(
        queue=queue,
        worker_id="normal-worker",
        db_path=db_path,
        invoke=invoke,
    )
    assert calls == 1
    assert _command_row(db_path)[:3] == ("acknowledged", 1, 1)


@pytest.mark.asyncio
async def test_routed_reconcile_redispatches_once_through_existing_binding(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "routed-safe-reconcile.db")
    now = datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    _prepare_routed_reconcile(db_path, clock=now)
    queue = SQLiteExecutionCommandQueueV2(db_path, clock=lambda: now)

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="routed-reconcile-safe",
        db_path=db_path,
    )

    assert disposition is ReconcileDisposition.REDISPATCHED
    assert (
        ConversationContextStore(db_path).load_turn(
            _CONVERSATION_KEY, _TURN_ID
        )
        is None
    )
    assert _command_row(db_path)[:3] == ("retry", 1, 1)
    provider_calls = 0

    async def invoke(
        _tool: str, arguments: dict[str, object], **kwargs: object
    ) -> None:
        fingerprint_version = kwargs["fingerprint_version"]
        assert isinstance(fingerprint_version, int)
        router_identity = CanonicalReservationIdentity(
            owner="alice",
            execution_id=_EXECUTION_ID,
            fingerprint_version=2,
            fingerprint=_FINGERPRINT,
            command=ExecutionCommand(
                agent_slug=EXPERT_ROUTER_AGENT_SLUG,
                arguments=arguments,
            ),
        )
        selected = ExecutionCommand(
            agent_slug="design",
            arguments={
                "user_query": "Os01g0177400",
                "obs_file_list": [],
                "resolve_gene_id": True,
            },
        )

        async def provider_call() -> dict[str, str]:
            nonlocal provider_calls
            provider_calls += 1
            return {"status": "succeeded"}

        with (
            bind_canonical_reservation_identity(router_identity),
            bind_routed_reservation_identity(
                db_path=db_path,
                owner="alice",
                execution_id=str(kwargs["execution_id"]),
                command=selected,
            ),
        ):
            await invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id=str(kwargs["execution_id"]),
                agent_slug="design",
                arguments={"user_query": "Os01g0177400"},
                transport="service_dispatcher",
                call=provider_call,
                fingerprint_version=fingerprint_version,
                fingerprint=str(kwargs["fingerprint"]),
            )

    assert await dispatch_one_execution_command(
        queue=queue,
        worker_id="routed-normal-worker",
        db_path=db_path,
        invoke=invoke,
    )
    assert provider_calls == 1
    assert _command_row(db_path)[:3] == ("acknowledged", 1, 1)


@pytest.mark.asyncio
async def test_routed_reconcile_operation_evidence_suppresses_redispatch(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "routed-operation-evidence.db")
    queue = _prepare_routed_reconcile(db_path)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO execution_operations_v2 (owner_ref, execution_id, "
            "operation_id, operation, expected_revision, command_hash, "
            "state, outcome_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                "alice",
                _EXECUTION_ID,
                "operation-1",
                "cancel",
                0,
                "c" * 64,
                "claimed",
                now,
                now,
            ),
        )
        connection.commit()

    claim = queue.claim_reconcile(worker_id="routed-operation-evidence")
    assert claim is not None
    evidence = read_reconcile_evidence(db_path=db_path, claim=claim)

    assert evidence.operation_count == 1
    assert evidence.runtime_started is True
    assert evidence.provably_unstarted is False


@pytest.mark.asyncio
async def test_routed_operation_evidence_is_acknowledged_without_replay(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "routed-operation-acknowledged.db")
    queue = _prepare_routed_reconcile(db_path)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO execution_operations_v2 (owner_ref, execution_id, "
            "operation_id, operation, expected_revision, command_hash, "
            "state, outcome_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                "alice",
                _EXECUTION_ID,
                "operation-existing",
                "cancel",
                0,
                "d" * 64,
                "claimed",
                now,
                now,
            ),
        )
        connection.commit()

    queue = SQLiteExecutionCommandQueueV2(
        db_path,
        clock=lambda: datetime(2026, 8, 24, 2, 15, tzinfo=UTC),
    )

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="routed-operation-acknowledged",
        db_path=db_path,
    )

    assert disposition is ReconcileDisposition.ACKNOWLEDGED
    assert _command_row(db_path)[:3] == ("acknowledged", 1, 0)
    assert (
        ConversationContextStore(db_path).load_turn(
            _CONVERSATION_KEY, _TURN_ID
        )
        is not None
    )


@pytest.mark.asyncio
async def test_reported_routed_ambiguous_row_recovers_once(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "reported-routed-ambiguous.db")
    _prepare_routed_reconcile(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE execution_commands_v2 SET reconcile_attempt = 52, "
            "last_error_code = 'reconcile_ambiguous' "
            "WHERE execution_id = ?",
            (_EXECUTION_ID,),
        )
        connection.commit()

    queue = SQLiteExecutionCommandQueueV2(
        db_path,
        clock=lambda: datetime(2026, 8, 24, 2, 15, tzinfo=UTC),
    )

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="reported-routed-ambiguous",
        db_path=db_path,
    )

    assert disposition is ReconcileDisposition.REDISPATCHED
    assert _command_row(db_path)[:3] == ("retry", 53, 1)


@pytest.mark.asyncio
async def test_routed_reconcile_never_redispatches_twice(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "routed-single-redispatch.db")
    queue = _prepare_routed_reconcile(db_path)
    assert (
        await reconcile_one_execution_command(
            queue=queue,
            worker_id="routed-first-reconcile",
            db_path=db_path,
        )
        is ReconcileDisposition.REDISPATCHED
    )
    retry_claim = queue.claim(worker_id="routed-retry-worker")
    assert retry_claim is not None
    assert queue.reconcile(
        retry_claim,
        code="dispatch_unknown_after_boundary",
    )

    second = await reconcile_one_execution_command(
        queue=queue,
        worker_id="routed-second-reconcile",
        db_path=db_path,
    )

    assert second is ReconcileDisposition.DEFERRED
    assert _command_row(db_path)[:3] == ("reconcile", 2, 1)


@pytest.mark.asyncio
async def test_expired_routed_reconcile_terminalizes_without_replay(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "expired-routed-reconcile.db")
    now = datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    queue = _prepare_routed_reconcile(db_path, clock=now)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_deadline_at = ? "
            "WHERE execution_id = ?",
            ((now - timedelta(seconds=1)).isoformat(), _EXECUTION_ID),
        )
        connection.commit()

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="expired-routed-reconcile",
        db_path=db_path,
        clock=lambda: now,
    )

    assert disposition is ReconcileDisposition.TERMINALIZED
    assert _command_row(db_path)[:3] == ("acknowledged", 1, 0)
    assert (
        SQLiteExecutionReservationRepository(db_path)
        .get(owner="alice", execution_id=_EXECUTION_ID)
        .status.value
        == "failed"
    )


@pytest.mark.asyncio
async def test_routed_recovery_changed_selection_is_finite_conflict(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "routed-recovery-conflict.db")
    queue = _prepare_routed_reconcile(db_path)
    assert (
        await reconcile_one_execution_command(
            queue=queue,
            worker_id="routed-reconcile-conflict",
            db_path=db_path,
        )
        is ReconcileDisposition.REDISPATCHED
    )
    provider_calls = 0

    async def invoke(
        _tool: str, arguments: dict[str, object], **kwargs: object
    ) -> None:
        nonlocal provider_calls
        router_identity = CanonicalReservationIdentity(
            owner="alice",
            execution_id=_EXECUTION_ID,
            fingerprint_version=2,
            fingerprint=_FINGERPRINT,
            command=ExecutionCommand(
                agent_slug=EXPERT_ROUTER_AGENT_SLUG,
                arguments=arguments,
            ),
        )
        with (
            bind_canonical_reservation_identity(router_identity),
            bind_routed_reservation_identity(
                db_path=db_path,
                owner="alice",
                execution_id=str(kwargs["execution_id"]),
                command=ExecutionCommand(
                    agent_slug="design",
                    arguments={"user_query": "changed selection"},
                ),
            ),
        ):
            provider_calls += 1

    assert await dispatch_one_execution_command(
        queue=queue,
        worker_id="routed-conflict-worker",
        db_path=db_path,
        invoke=invoke,
    )
    assert provider_calls == 0
    assert _command_row(db_path)[:3] == ("rejected", 1, 1)


@pytest.mark.asyncio
async def test_reconcile_acknowledges_existing_runtime_start_without_replay(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "existing-start.db")
    queue = _prepare_reconcile(db_path)
    repository = SQLiteExecutionReservationRepository(db_path)
    assert repository.claim_start(owner="alice", execution_id=_EXECUTION_ID)

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="reconcile-running",
        db_path=db_path,
    )

    assert disposition is ReconcileDisposition.ACKNOWLEDGED
    assert _command_row(db_path)[0] == "acknowledged"


@pytest.mark.asyncio
async def test_ambiguous_reconcile_backs_off_without_normal_replay(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "ambiguous-reconcile.db")
    queue = _prepare_reconcile(db_path, failed_turn=False)

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="reconcile-ambiguous",
        db_path=db_path,
    )

    assert disposition is ReconcileDisposition.DEFERRED
    assert _command_row(db_path)[0] == "reconcile"
    assert queue.claim(worker_id="normal-worker") is None


@pytest.mark.asyncio
async def test_reconcile_deadline_settles_one_canonical_failure(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "deadline-reconcile.db")
    now = datetime(2026, 8, 24, 2, 15, tzinfo=UTC)
    queue = _prepare_reconcile(db_path, clock=now)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_deadline_at = ? WHERE execution_id = ?",
            ((now - timedelta(seconds=1)).isoformat(), _EXECUTION_ID),
        )
        connection.commit()

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="reconcile-deadline",
        db_path=db_path,
        clock=lambda: now,
    )
    replay = await reconcile_one_execution_command(
        queue=queue,
        worker_id="reconcile-deadline-replay",
        db_path=db_path,
        clock=lambda: now,
    )

    assert disposition is ReconcileDisposition.TERMINALIZED
    assert replay is ReconcileDisposition.IDLE
    projection = SQLiteExecutionJournal(db_path).get_projection(
        _EXECUTION_ID, owner="alice"
    )
    assert projection.terminal is not None
    assert projection.terminal.status == "failed"
    assert {warning.code for warning in projection.warnings} == {
        "dispatch_unresolved"
    }
    page = SQLiteExecutionJournal(db_path).list_events(
        _EXECUTION_ID, owner="alice"
    )
    assert page is not None
    assert (
        sum(event.type.value == "execution.failed" for event in page.items)
        == 1
    )


@pytest.mark.asyncio
async def test_terminal_race_is_acknowledged_without_second_terminal_fact(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "terminal-race.db")
    queue = _prepare_reconcile(db_path)
    repository = SQLiteExecutionReservationRepository(db_path)
    record = repository.get(owner="alice", execution_id=_EXECUTION_ID)
    assert repository.settle_terminal(
        TerminalSettlementAuthority(
            owner_ref="alice",
            execution_id=_EXECUTION_ID,
            expected_revision=record.supervisor_revision,
            actor="supervisor",
            issued_at=datetime.now(UTC),
        ),
        DriverOutcome.failed(code="existing_terminal"),
    )

    disposition = await reconcile_one_execution_command(
        queue=queue,
        worker_id="reconcile-terminal",
        db_path=db_path,
    )

    assert disposition is ReconcileDisposition.ACKNOWLEDGED
    page = SQLiteExecutionJournal(db_path).list_events(
        _EXECUTION_ID, owner="alice"
    )
    assert page is not None
    assert (
        sum(event.type.value == "execution.failed" for event in page.items)
        == 1
    )


@pytest.mark.parametrize("turn_state", ["in_progress", "staged", "committed"])
def test_safe_redispatch_rejects_non_failed_context_turn(
    tmp_path: Path,
    turn_state: str,
) -> None:
    db_path = str(tmp_path / f"context-fence-{turn_state}.db")
    queue = _prepare_reconcile(db_path, failed_turn=False)
    context = ConversationContextStore(db_path)
    context.begin_turn(_CONVERSATION_KEY, _TURN_ID, "append", 0)
    if turn_state != "in_progress":
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE conversation_turns SET state = ? "
                "WHERE conversation_key = ? AND turn_id = ?",
                (turn_state, _CONVERSATION_KEY, _TURN_ID),
            )
            connection.commit()
    claim = queue.claim_reconcile(worker_id="reconcile-context-fence")
    assert claim is not None

    assert (
        queue.release_failed_turn_and_redispatch(
            claim,
            conversation_key=_CONVERSATION_KEY,
            turn_id=_TURN_ID,
        )
        is False
    )
    assert context.load_turn(_CONVERSATION_KEY, _TURN_ID) is not None
    assert _command_row(db_path)[0] == "reconcile"
