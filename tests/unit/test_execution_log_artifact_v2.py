# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Execution logs contain only bounded, already-public operation facts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path


def test_execution_event_and_log_rollback_flags_are_independent(
    monkeypatch,
) -> None:
    from mcp_server_phytomni.runtime.execution_event_flags import (
        execution_event_production_enabled,
        execution_log_artifact_enabled,
    )

    monkeypatch.setenv("PHYTOMNI_EXECUTION_EVENTS_ENABLED", "false")
    monkeypatch.setenv("PHYTOMNI_EXECUTION_LOG_ENABLED", "true")
    assert execution_event_production_enabled() is False
    assert execution_log_artifact_enabled() is True

    monkeypatch.setenv("PHYTOMNI_EXECUTION_EVENTS_ENABLED", "true")
    monkeypatch.setenv("PHYTOMNI_EXECUTION_LOG_ENABLED", "false")
    assert execution_event_production_enabled() is True
    assert execution_log_artifact_enabled() is False


def test_terminal_runtime_publishes_owner_scoped_execution_log(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_log_artifact_v2 import (
        SQLiteExecutionLogArtifactStore,
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
    from mcp_server_phytomni.runtime.execution_target_store_v2 import (
        SQLiteExecutionTargetStore,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )
    from mcp_server_phytomni.runtime.operation_instrumentation_v2 import (
        instrument_operation_invocation,
    )

    db_path = str(tmp_path / "execution-log.db")
    journal = SQLiteExecutionJournal(db_path)
    log_store = SQLiteExecutionLogArtifactStore(db_path)
    target_store = SQLiteExecutionTargetStore(db_path)
    private_answer = "private answer must not enter the execution log"

    async def start_handler(context, command, services):
        del context, command, services
        result = await instrument_operation_invocation(
            "knowledge.search",
            lambda: asyncio.sleep(0, result=["private evidence"]),
            detail={"repository_count": 2},
            detail_from_result=lambda value: {"result_count": len(value)},
        )
        assert result == ["private evidence"]
        return DriverOutcome.succeeded(
            TransportNeutralResult(answer=private_answer)
        )

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=journal,
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
        target_store=target_store,
        execution_log_store=log_store,
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-execution-log",
            fingerprint_version=1,
            fingerprint="9" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge", arguments={"query": "rice"}
            ),
            transport="test",
        )
    )
    assert outcome.status.value == "succeeded"

    projection = journal.get_projection("turn-execution-log", owner="alice")
    log_target = next(
        target for target in projection.targets if target.id.startswith("log-")
    )
    binding = target_store.get(
        owner="alice",
        execution_id="turn-execution-log",
        kind="artifact",
        target_id=log_target.id,
    )
    assert binding is not None
    assert binding.role == "execution_log"
    assert binding.media_type == "application/json"
    stored = log_store.get(
        owner="alice",
        execution_id="turn-execution-log",
        target_id=log_target.id,
    )
    assert stored is not None
    document = json.loads(stored.decode("utf-8"))
    assert document["schema_version"] == 1
    assert document["execution_id"] == "turn-execution-log"
    assert document["records"]
    assert {record["operation_key"] for record in document["records"]} == {
        "knowledge.search"
    }
    public_log = stored.decode("utf-8")
    assert private_answer not in public_log
    assert "private evidence" not in public_log
    assert (
        log_store.get(
            owner="mallory",
            execution_id="turn-execution-log",
            target_id=log_target.id,
        )
        is None
    )


def test_execution_log_builder_is_bounded_and_ignores_non_operation_facts() -> (
    None
):
    from mcp_server_phytomni.runtime.execution_event_limits import (
        DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS,
    )
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        parse_execution_event_v2,
    )
    from mcp_server_phytomni.runtime.execution_log_artifact_v2 import (
        build_execution_log_document,
    )

    def event(seq: int, event_type: str, payload: dict[str, object]):
        return parse_execution_event_v2(
            {
                "schema_version": 2,
                "event_id": f"evt-{seq}",
                "execution_id": "turn-bounded-log",
                "seq": seq,
                "type": event_type,
                "status": "running",
                "occurred_at": f"2026-08-22T04:00:{seq:02d}Z",
                "source": "runtime",
                "span_id": "span-root",
                "parent_span_id": None,
                "work_unit_id": (
                    "work-search"
                    if event_type.startswith("work_unit.")
                    else None
                ),
                "attempt": 1,
                "summary": {"key": "safe.fact", "text": "Safe fact"},
                "public_payload": payload,
                "target": None,
                "idempotency_key": f"fact:{seq}",
            }
        )

    facts = [
        event(
            1,
            "work_unit.registered",
            {"operation_key": "knowledge.search"},
        ),
        event(2, "execution.started", {}),
    ]
    payload = build_execution_log_document("turn-bounded-log", facts)
    document = json.loads(payload)

    DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.validate_execution_log_size(
        len(payload)
    )
    assert len(document["records"]) == 1
    assert document["records"][0]["operation_key"] == "knowledge.search"


def test_public_operation_records_and_log_reject_private_adversarial_values(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_drivers_v2 import (
        LocalGraphDriver,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_log_artifact_v2 import (
        SQLiteExecutionLogArtifactStore,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOperation,
        ExecutionCommand,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
    )
    from mcp_server_phytomni.runtime.execution_target_store_v2 import (
        SQLiteExecutionTargetStore,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )
    from mcp_server_phytomni.runtime.operation_instrumentation_v2 import (
        instrument_operation_invocation,
    )

    sentinels = {
        "prompt": "PROMPT_SECRET_91",
        "source_passage": "SOURCE_PASSAGE_SECRET_92",
        "sql": "SELECT_SQL_SECRET_93_FROM_PRIVATE",
        "tool_arguments": "TOOL_ARGUMENT_SECRET_94",
        "tool_results": "TOOL_RESULT_SECRET_95",
        "path": "/private/PATH_SECRET_96/data.fa",
        "url": "https://private.invalid/URL_SECRET_97",
        "credentials": "Bearer CREDENTIAL_SECRET_98",
        "provider_body": "PROVIDER_BODY_SECRET_99",
        "exception": "EXCEPTION_SECRET_100",
    }
    db_path = str(tmp_path / "adversarial-log.db")
    journal = SQLiteExecutionJournal(db_path)
    log_store = SQLiteExecutionLogArtifactStore(db_path)
    target_store = SQLiteExecutionTargetStore(db_path)

    async def start_handler(context, command, services):
        del context, command, services

        async def private_provider_call():
            return {
                "passage": sentinels["source_passage"],
                "tool_result": sentinels["tool_results"],
                "provider_body": sentinels["provider_body"],
            }

        await instrument_operation_invocation(
            "remote.reconcile",
            private_provider_call,
            detail={
                "provider_state": "running",
                "path": sentinels["path"],
                "url": sentinels["url"],
                "credential": sentinels["credentials"],
            },
            detail_from_result=lambda _value: {
                "result_count": 1,
                "provider_body": sentinels["provider_body"],
            },
        )

        async def failing_query():
            assert sentinels["tool_arguments"]
            raise RuntimeError(sentinels["exception"])

        await instrument_operation_invocation(
            "data.query",
            failing_query,
            detail={
                "result_count": 0,
                "sql": sentinels["sql"],
                "arguments": sentinels["tool_arguments"],
            },
        )
        raise AssertionError("unreachable")

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=journal,
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
        target_store=target_store,
        execution_log_store=log_store,
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-adversarial-log",
            fingerprint_version=1,
            fingerprint="8" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={
                    "prompt": sentinels["prompt"],
                    "sql": sentinels["sql"],
                    "path": sentinels["path"],
                    "url": sentinels["url"],
                    "credentials": sentinels["credentials"],
                    "tool_arguments": sentinels["tool_arguments"],
                },
            ),
            transport="test",
        )
    )
    assert outcome.status.value == "failed"

    page = journal.list_events(
        "turn-adversarial-log", owner="alice", limit=200
    )
    assert page is not None
    public_records = json.dumps(
        [event.to_public_dict() for event in page.items], sort_keys=True
    )
    projection = journal.get_projection("turn-adversarial-log", owner="alice")
    log_target = next(
        target for target in projection.targets if target.id.startswith("log-")
    )
    public_log = log_store.get(
        owner="alice",
        execution_id="turn-adversarial-log",
        target_id=log_target.id,
    )
    assert public_log is not None
    combined = public_records + public_log.decode("utf-8")
    for private_value in sentinels.values():
        assert private_value not in combined
