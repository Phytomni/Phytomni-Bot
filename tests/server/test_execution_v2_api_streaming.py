# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Execution V2 streaming, trace, and compatibility tests."""

from __future__ import annotations

import sqlite3
from typing import cast

import httpx
import pytest
from starlette.requests import Request
from tests.support.execution_v2_api import (
    append_execution_succeeded,
    execution_succeeded_sleep_callback,
    execution_user_headers,
    execution_v2_admission,
    execution_v2_headers,
    seed_execution_v2,
)

from mcp_server_phytomni.api.routes import executions_v2 as execution_v2_routes
from mcp_server_phytomni.runtime.execution_content_stream_v2 import (
    execution_content_stream_for_db,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    parse_execution_event_intent_v2,
)
from mcp_server_phytomni.runtime.execution_trace_target_v2 import (
    trace_target_for_operation,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

pytestmark = pytest.mark.server


async def test_v2_stream_carries_resumable_content_and_sequence_free_heartbeat(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient content and keepalive traffic never consume durable seq."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, published = seed_execution_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-live"
    )
    execution_content_stream_for_db(tasks_db_path).publish(
        owner="u1",
        execution_id=record.execution_id,
        output_revision=1,
        offset=5,
        delta="hello",
    )
    journal = SQLiteExecutionJournal(tasks_db_path)

    monkeypatch.setattr(
        execution_v2_routes,
        "EXECUTION_V2_HEARTBEAT_POLL_TICKS",
        1,
    )
    monkeypatch.setattr(
        execution_v2_routes.asyncio,
        "sleep",
        execution_succeeded_sleep_callback(journal, record, owner="u1"),
    )
    response = await api_client.get(
        "/v2/executions/turn-v2-live/events/stream"
        f"?after_seq={published.seq}&after_revision=1&after_offset=0",
        headers=headers,
    )

    assert response.status_code == 200
    assert "event: execution_content\n" in response.text
    assert '"output_revision":1' in response.text
    assert '"offset":5' in response.text
    assert '"delta":"hello"' in response.text
    assert ": heartbeat\n\n" in response.text
    assert "id: heartbeat" not in response.text


async def test_v2_stream_before_dispatch_flushes_admitted_snapshot(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed admission is streamable before a worker dispatches it."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    admitted = await api_client.post(
        "/v2/executions",
        headers=headers,
        json=execution_v2_admission(
            execution_id="turn-stream-before-dispatch"
        ),
    )

    async def disconnected(_request: Request) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    response = await api_client.get(
        "/v2/executions/turn-stream-before-dispatch/events/stream",
        headers=headers,
    )

    assert admitted.status_code == 202
    assert response.status_code == 200
    assert response.text.startswith("event: execution_snapshot\n")
    assert '"status":"admitted"' in response.text


async def test_v2_reconcile_snapshot_is_degraded_without_fabricated_events(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify V2 reconcile snapshot is degraded without fabricated events."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    execution_id = "turn-reconcile-snapshot"
    admitted = await api_client.post(
        "/v2/executions",
        headers=headers,
        json=execution_v2_admission(execution_id=execution_id),
    )
    with sqlite_transaction(tasks_db_path) as connection:
        connection.execute(
            "UPDATE execution_commands_v2 SET state = 'reconcile', "
            "classification = 'reconcile', reconcile_attempt = 2, "
            "last_error_code = 'reconcile_ambiguous' "
            "WHERE execution_id = ?",
            (execution_id,),
        )
        connection.commit()
    snapshot = await api_client.get(
        f"/v2/executions/{execution_id}", headers=headers
    )

    async def disconnected(_request: Request) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    stream = await api_client.get(
        f"/v2/executions/{execution_id}/events/stream", headers=headers
    )

    assert admitted.status_code == 202
    assert snapshot.status_code == 200
    assert snapshot.json()["latest_seq"] == 0
    assert snapshot.json()["tracking_health"] == "degraded"
    assert snapshot.json()["warnings"] == [
        {"code": "execution_reconciliation_pending", "work_unit_id": None}
    ]
    assert stream.status_code == 200
    assert '"latest_seq":0' in stream.text
    assert '"tracking_health":"degraded"' in stream.text
    assert '"code":"execution_reconciliation_pending"' in stream.text
    assert "event: execution_event" not in stream.text


async def test_v2_stream_reconnect_deduplicates_event_and_content_cursors(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconnect resumes strictly after both durable and content cursors."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, started, published = seed_execution_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-reconnect"
    )
    stream = execution_content_stream_for_db(tasks_db_path)
    stream.publish(
        owner="u1",
        execution_id=record.execution_id,
        output_revision=1,
        offset=5,
        delta="first",
    )
    stream.publish(
        owner="u1",
        execution_id=record.execution_id,
        output_revision=1,
        offset=11,
        delta="second",
    )
    terminal = append_execution_succeeded(
        SQLiteExecutionJournal(tasks_db_path),
        record,
        owner="u1",
        summary_key="done",
        summary_text="Done",
    )
    response = await api_client.get(
        "/v2/executions/turn-v2-reconnect/events/stream"
        "?after_revision=1&after_offset=5",
        headers={**headers, "Last-Event-ID": str(published.seq)},
    )

    assert f"id: {started.seq}\n" not in response.text
    assert f"id: {published.seq}\n" not in response.text
    assert f"id: {terminal.seq}\n" in response.text
    assert '"delta":"first"' not in response.text
    assert '"delta":"second"' in response.text


async def test_v2_stream_reports_storage_failure_as_degraded_control(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A journal follow failure is explicit and does not fabricate progress."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    seed_execution_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-storage-fail",
    )

    def unavailable(*_args: object, **_kwargs: object):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(SQLiteExecutionJournal, "list_events", unavailable)
    response = await api_client.get(
        "/v2/executions/turn-v2-storage-fail/events/stream",
        headers=headers,
    )

    assert response.status_code == 200
    assert "event: execution_tracking\n" in response.text
    assert '"tracking_health":"degraded"' in response.text
    assert "database unavailable" not in response.text


async def test_v2_target_rejects_unknown_kind_and_unsafe_identifier(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typed target lookup never accepts arbitrary kinds or path values."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    seed_execution_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-safe-target",
    )

    unknown_kind = await api_client.get(
        "/v2/executions/turn-v2-safe-target/targets/url/artifact-report",
        headers=headers,
    )
    unsafe_id = await api_client.get(
        "/v2/executions/turn-v2-safe-target/targets/artifact/%2E%2E%3Asecret",
        headers=headers,
    )

    assert unknown_kind.status_code == 422
    assert unsafe_id.status_code == 422


async def test_v2_trace_target_resolves_bounded_canonical_public_feed(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace detail is owner scoped, paged, ordered, and identity safe."""

    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, _published = seed_execution_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-trace-target",
        agent_slug="network",
    )
    journal = SQLiteExecutionJournal(tasks_db_path)
    target = trace_target_for_operation(
        agent_slug="network",
        operation_key="remote.analysis",
        execution_id=record.execution_id,
        work_unit_id="private-analysis-work",
    )
    assert target is not None
    for index, fact in enumerate(
        cast(
            tuple[dict[str, object], ...],
            (
                {
                    "type": "work_unit.registered",
                    "status": "queued",
                    "span_id": record.root_span_id,
                    "parent_span_id": None,
                    "work_unit_id": "private-analysis-work",
                    "summary": {
                        "key": "remote.analysis",
                        "text": "Run analysis",
                    },
                    "public_payload": {"operation_key": "remote.analysis"},
                    "target": target,
                },
                {
                    "type": "span.created",
                    "status": "pending",
                    "span_id": "private-analysis-span",
                    "parent_span_id": record.root_span_id,
                    "work_unit_id": "private-analysis-work",
                    "summary": {
                        "key": "remote.analysis",
                        "text": "Run analysis",
                    },
                    "public_payload": {"phase": "remote.analysis"},
                },
                {
                    "type": "work_unit.registered",
                    "status": "queued",
                    "span_id": "private-child-span",
                    "parent_span_id": "private-analysis-span",
                    "work_unit_id": "private-child-work",
                    "summary": {
                        "key": "execution.trace.geneNetwork.prepareInputs",
                        "text": "Prepare analysis inputs",
                    },
                    "public_payload": {
                        "operation_key": "gene_network.prepare_inputs"
                    },
                },
                {
                    "type": "reasoning.summary",
                    "status": "running",
                    "span_id": "private-analysis-span",
                    "parent_span_id": record.root_span_id,
                    "work_unit_id": "private-analysis-work",
                    "summary": {
                        "key": "gene_network.target_validated",
                        "text": "Reasoning summary",
                    },
                    "public_payload": {
                        "text": (
                            "Validated the trait target and species for "
                            "network analysis."
                        )
                    },
                },
                {
                    "type": "work_unit.succeeded",
                    "status": "succeeded",
                    "span_id": "private-child-span",
                    "parent_span_id": "private-analysis-span",
                    "work_unit_id": "private-child-work",
                    "summary": {
                        "key": "execution.trace.geneNetwork.prepareInputs",
                        "text": "Prepare analysis inputs completed",
                    },
                    "public_payload": {
                        "operation_key": "gene_network.prepare_inputs"
                    },
                },
            ),
        )
    ):
        journal.append(
            record.execution_id,
            owner="u1",
            intent=parse_execution_event_intent_v2(
                {
                    **fact,
                    "source": "provider",
                    "attempt": 1,
                    "idempotency_key": f"trace-fixture:{index}",
                }
            ),
        )

    first = await api_client.get(
        f"/v2/executions/{record.execution_id}/targets/trace/{target['id']}"
        "?after_seq=0&limit=2",
        headers=headers,
    )
    assert first.status_code == 200
    body = first.json()
    assert body["schema_version"] == 1
    assert body["target"] == target
    assert body["operation"]["operation_key"] == "remote.analysis"
    assert "work_unit_id" not in body["operation"]
    assert [item["kind"] for item in body["items"]] == [
        "phase",
        "reasoning_summary",
    ]
    assert body["has_more"] is True

    second = await api_client.get(
        f"/v2/executions/{record.execution_id}/targets/trace/{target['id']}"
        f"?after_seq={body['next_after_seq']}&limit=2",
        headers=headers,
    )
    assert [item["status"] for item in second.json()["items"]] == ["succeeded"]
    assert second.json()["has_more"] is False

    assert "private-analysis-work" not in str(body) + str(second.json())
    assert "private-analysis-span" not in str(body) + str(second.json())
    assert "private-child-work" not in str(body) + str(second.json())
    not_found = (
        await api_client.get(
            f"/v2/executions/{record.execution_id}/targets/trace/"
            f"{target['id']}",
            headers={**headers, "X-Phyto-Owner": "other-user"},
        ),
        await api_client.get(
            f"/v2/executions/{record.execution_id}/targets/trace/"
            "trc_A1b2C3d4E5f6G7h8",
            headers=headers,
        ),
    )
    assert all(response.status_code == 404 for response in not_found)


async def test_v2_contract_reports_version_gap_degraded_and_terminal_conflicts(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery and conflict states are explicit finite contracts."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, _published = seed_execution_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-controls"
    )
    journal = SQLiteExecutionJournal(tasks_db_path)
    journal.append(
        record.execution_id,
        owner="u1",
        intent=parse_execution_event_intent_v2(
            {
                "type": "tracking.degraded",
                "status": "running",
                "source": "runtime",
                "span_id": record.root_span_id,
                "summary": {
                    "key": "tracking.degraded",
                    "text": "Tracking is degraded",
                },
                "public_payload": {
                    "health": "degraded",
                    "code": "journal_unavailable",
                },
            }
        ),
    )
    terminal = append_execution_succeeded(
        journal,
        record,
        owner="u1",
    )
    with sqlite_transaction(tasks_db_path) as connection:
        connection.execute(
            "DELETE FROM execution_events_v2 WHERE owner_ref = ? "
            "AND execution_id = ? AND seq = 1",
            ("u1", record.execution_id),
        )
        connection.commit()

    unsupported_responses = (
        await api_client.post(
            "/v2/executions",
            headers=headers,
            json=execution_v2_admission(
                schema_version=3,
                execution_id="turn-v3",
            ),
        ),
        await api_client.get(
            f"/v2/executions/{record.execution_id}",
            headers={**headers, "X-Phyto-Execution-Schema": "3"},
        ),
    )
    page = await api_client.get(
        f"/v2/executions/{record.execution_id}/events?after_seq=0",
        headers=headers,
    )
    snapshot = await api_client.get(
        f"/v2/executions/{record.execution_id}", headers=headers
    )
    terminal_cancel = await api_client.post(
        f"/v2/executions/{record.execution_id}/cancel",
        headers=headers,
        json={
            "request_id": "cancel-terminal",
            "expected_revision": record.supervisor_revision,
            "reason": "user_requested",
        },
    )

    for response in unsupported_responses:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == (
            "execution_contract_unsupported"
        )
    assert page.status_code == 200
    assert page.json()["gaps"] == [
        {"first_missing_seq": 1, "last_missing_seq": 1}
    ]
    assert snapshot.json()["tracking_health"] == "degraded"
    assert snapshot.json()["terminal"]["event_id"] == terminal.event_id
    assert terminal_cancel.status_code == 409
    assert terminal_cancel.json()["error"]["code"] == (
        "execution_terminal_conflict"
    )


async def test_v1_run_history_is_a_read_only_projection_of_v2(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """New executions never need a second V1 event writer for compatibility."""
    record, started, published = seed_execution_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-v1-view"
    )
    response = await api_client.get(
        f"/v1/runs/{record.run_id}/events?after_seq=0&limit=10",
        headers=execution_user_headers(issued_api_key),
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["seq"] for item in items] == [started.seq, published.seq]
    assert [item["kind"] for item in items] == [
        "run.started",
        "artifact.published",
    ]
    assert all(item["schema_version"] == 1 for item in items)
    assert all(item["run_id"] == record.run_id for item in items)
    with sqlite_transaction(tasks_db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id = ?",
            (record.run_id,),
        ).fetchone() == (0,)
