# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Independent semantic, provider, and stream liveness clocks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path


def test_three_liveness_clocks_advance_independently_without_regression() -> (
    None
):
    from mcp_server_phytomni.runtime.execution_liveness_v2 import (
        ExecutionLivenessClocks,
    )

    clocks = ExecutionLivenessClocks().observe_execution_fact(
        "2026-08-22T04:00:05Z"
    )
    provider = clocks.observe_provider_contact(
        "2026-08-22T04:00:15Z",
        semantic_changed=False,
    )
    assert provider.last_execution_fact_at == "2026-08-22T04:00:05Z"
    assert provider.last_provider_contact_at == "2026-08-22T04:00:15Z"
    assert provider.last_stream_contact_at is None

    heartbeat = provider.observe_stream_contact("2026-08-22T04:00:20Z")
    assert heartbeat.last_execution_fact_at == "2026-08-22T04:00:05Z"
    assert heartbeat.last_provider_contact_at == "2026-08-22T04:00:15Z"
    assert heartbeat.last_stream_contact_at == "2026-08-22T04:00:20Z"

    regressive = heartbeat.observe_stream_contact("2026-08-22T04:00:10Z")
    assert regressive == heartbeat


def test_liveness_payload_requires_no_fabricated_percentage() -> None:
    import pytest

    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        ExecutionJournalValidationError,
        parse_execution_event_intent_v2,
    )

    intent = parse_execution_event_intent_v2(
        {
            "type": "work_unit.progress",
            "status": "running",
            "source": "runtime",
            "span_id": "span-operation-1",
            "work_unit_id": "work-operation-1",
            "attempt": 1,
            "summary": {
                "key": "knowledge.search.liveness",
                "text": "Search knowledge is active",
            },
            "public_payload": {
                "phase": "knowledge.search",
                "observation": "liveness",
                "elapsed_ms": 30000,
            },
        }
    )
    assert intent.public_payload.model_dump(exclude_none=True) == {
        "phase": "knowledge.search",
        "observation": "liveness",
        "elapsed_ms": 30000,
    }

    with pytest.raises(ExecutionJournalValidationError):
        parse_execution_event_intent_v2(
            {
                **intent.model_dump(mode="json"),
                "public_payload": {
                    "phase": "knowledge.search",
                    "percent": 50,
                },
            }
        )


def test_quiet_operation_liveness_is_coalesced_at_the_shared_window(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )
    from mcp_server_phytomni.runtime.operation_instrumentation_v2 import (
        instrument_operation_invocation,
        record_current_operation_liveness,
    )

    now = datetime(2026, 8, 22, 4, 0, tzinfo=UTC)

    def clock() -> datetime:
        return now

    def journal_clock() -> str:
        return now.isoformat()

    db_path = str(tmp_path / "quiet-liveness.db")
    journal = SQLiteExecutionJournal(db_path, clock=journal_clock)
    work = SQLiteExecutionWorkRepository(db_path, clock=clock)

    async def start_handler(context, command, services):
        del context, command, services

        async def quiet_call() -> str:
            nonlocal now
            assert record_current_operation_liveness() is True
            now += timedelta(milliseconds=29_999)
            assert record_current_operation_liveness() is False
            now += timedelta(milliseconds=1)
            assert record_current_operation_liveness() is True
            return "private result"

        assert (
            await instrument_operation_invocation(
                "knowledge.search",
                quiet_call,
            )
            == "private result"
        )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(
            db_path, clock=clock
        ),
        journal=journal,
        work=work,
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
        clock=clock,
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-quiet-liveness",
            fingerprint_version=1,
            fingerprint="a" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge", arguments={"query": "rice"}
            ),
            transport="test",
        )
    )
    assert outcome.status.value == "succeeded"

    page = journal.list_events("turn-quiet-liveness", owner="alice", limit=100)
    assert page is not None
    liveness = [
        event
        for event in page.items
        if event.type.value == "work_unit.progress"
        and event.work_unit_id is not None
    ]
    assert len(liveness) == 2
    assert all(
        event.public_payload.model_dump(exclude_none=True)["observation"]
        == "liveness"
        for event in liveness
    )
    assert all(
        "completed" not in event.public_payload.model_dump(exclude_none=True)
        and "total" not in event.public_payload.model_dump(exclude_none=True)
        for event in liveness
    )


def test_sse_heartbeat_is_transport_only_and_never_appended() -> None:
    route = (
        Path(__file__).parents[2]
        / "src"
        / "mcp_server_phytomni"
        / "api"
        / "routes"
        / "executions_v2.py"
    ).read_text(encoding="utf-8")

    heartbeat_start = route.index('yield ": heartbeat\\n\\n"') - 180
    heartbeat_block = route[slice(heartbeat_start, None)]
    heartbeat_block = heartbeat_block[:400]
    assert "journal.append" not in heartbeat_block
    assert "store.append" not in heartbeat_block
