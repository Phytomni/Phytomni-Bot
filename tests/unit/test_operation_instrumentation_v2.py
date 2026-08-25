# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Safe shared operation instrumentation for retrieval and other seams."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any

import pytest


def test_safe_operation_boundary_publishes_only_presenter_fields(
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
    )

    db_path = str(tmp_path / "safe-operation.db")
    journal = SQLiteExecutionJournal(db_path)

    async def start_handler(context, command, services):
        del context, command, services

        async def retrieve_call() -> dict[str, Any]:
            return {
                "doc_list": [
                    {
                        "content": "private scientific evidence",
                        "url": "https://private.invalid/evidence",
                    }
                ],
                "query": "private retrieval query",
            }

        result = await instrument_operation_invocation(
            "knowledge.search",
            retrieve_call,
            detail={
                "repository_count": 2,
                "query": "private retrieval query",
            },
            detail_from_result=lambda value: {
                "result_count": len(value["doc_list"]),
                "source_passage": value["doc_list"][0]["content"],
            },
        )
        assert len(result["doc_list"]) == 1
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=journal,
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={
            "local_graph": LocalGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-knowledge-operation",
            fingerprint_version=1,
            fingerprint="9" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge", arguments={"query": "rice"}
            ),
            transport="test",
        )
    )
    assert outcome.status.value == "succeeded"

    page = journal.list_events(
        "turn-knowledge-operation", owner="alice", limit=50
    )
    assert page is not None
    operation_events = [
        event
        for event in page.items
        if event.work_unit_id is not None
        and event.summary.key.startswith("knowledge.search.")
    ]
    succeeded = next(
        event
        for event in operation_events
        if event.type.value == "work_unit.succeeded"
    )
    assert succeeded.public_payload.model_dump(exclude_none=True)[
        "detail"
    ] == {
        "repository_count": 2,
        "result_count": 1,
    }
    public_text = str([event.to_public_dict() for event in operation_events])
    assert "private retrieval query" not in public_text
    assert "private scientific evidence" not in public_text
    assert "private.invalid" not in public_text


@pytest.mark.asyncio
async def test_knowledge_retrieve_uses_safe_operation_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_server_phytomni.agents.knowledge import retrieval

    captured: dict[str, Any] = {}

    async def fake_cached(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "doc_list": [{"content": "private source passage"}],
            "total": 1,
            "outcome": "complete",
            "failures": [],
        }

    async def fake_instrument(
        operation_key: str,
        call: Any,
        *,
        detail: dict[str, Any],
        detail_from_result: Any,
    ) -> dict[str, Any]:
        result = await call()
        captured.update(
            operation_key=operation_key,
            detail=detail,
            result_detail=detail_from_result(result),
        )
        return result

    monkeypatch.setattr(retrieval, "_retrieve_cached", fake_cached)
    monkeypatch.setattr(
        retrieval,
        "instrument_operation_invocation",
        fake_instrument,
        raising=False,
    )

    result = await retrieval.retrieve(
        "private retrieval query",
        repo_id="repo-private-name",
        extra_repo_ids=["repo-second-private-name"],
    )

    assert result["total"] == 1
    assert captured == {
        "operation_key": "knowledge.search",
        "detail": {"repository_count": 2},
        "result_detail": {"result_count": 1},
    }


@pytest.mark.asyncio
async def test_data_request_uses_safe_operation_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nl2sql = importlib.import_module("mcp_server_phytomni.agents.data.nl2sql")

    captured: dict[str, Any] = {}

    async def fake_cached(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "data": [["private row value"], ["another private value"]],
            "sql": "select private_column from private_table",
        }

    async def fake_instrument(
        operation_key: str,
        call: Any,
        *,
        detail_from_result: Any,
    ) -> dict[str, Any]:
        result = await call()
        captured.update(
            operation_key=operation_key,
            result_detail=detail_from_result(result),
        )
        return result

    monkeypatch.setattr(nl2sql, "_execute_nl2sql_cached", fake_cached)
    monkeypatch.setattr(
        nl2sql,
        "instrument_operation_invocation",
        fake_instrument,
        raising=False,
    )
    request = nl2sql.Nl2SqlRequest.from_kwargs(
        "private natural-language database question",
        {},
    )

    result = await nl2sql.execute_nl2sql_request(request)

    assert len(result["data"]) == 2
    assert captured == {
        "operation_key": "data.query",
        "result_detail": {"result_count": 2},
    }


def test_remote_and_artifact_boundaries_use_registered_operations() -> None:
    source_root = Path(__file__).parents[2] / "src" / "mcp_server_phytomni"
    analyst = (source_root / "agents" / "analyst" / "graph.py").read_text(
        encoding="utf-8"
    )
    provider = (
        source_root / "runtime" / "provider_instrumentation_v2.py"
    ).read_text(encoding="utf-8")
    archive = (source_root / "runtime" / "result_archive.py").read_text(
        encoding="utf-8"
    )

    assert 'operation_key="remote.analysis"' in analyst
    assert 'label_key="remote.submit"' in provider
    assert 'summary_key=f"remote.reconcile.' in provider
    assert (
        'instrument_operation_invocation(\n        "artifact.package"'
        in archive
    )
