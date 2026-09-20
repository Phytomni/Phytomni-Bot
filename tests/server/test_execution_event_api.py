# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Owner-scoped durable execution event HTTP API tests."""

from __future__ import annotations

import httpx
import pytest
from tests.support.execution_v2_api import (
    execution_user_headers,
    one_shot_sleep_callback,
)

from mcp_server_phytomni.api.routes import runs as run_routes
from mcp_server_phytomni.runtime.execution_event_sink import event_intent
from mcp_server_phytomni.runtime.execution_event_store import (
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
    RunRequestInfo,
)
from mcp_server_phytomni.runtime.run_registry_models import RunSpec
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

pytestmark = pytest.mark.server


def _seed(tasks_db_path: str, run_id: str, owner: str):
    registry = RunRegistry(tasks_db_path)
    registry.create_run(RunSpec(run_id, owner, "chat", "api"))
    store = SQLiteExecutionEventStore(tasks_db_path)
    started = store.append(
        run_id,
        owner=owner,
        intent=event_intent("run.started", status="running"),
    )
    completed = store.append(
        run_id,
        owner=owner,
        intent=event_intent("run.succeeded", status="succeeded"),
    )
    return started, completed


async def test_event_page_detail_and_projection_are_resumable(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify event page detail and projection are resumable."""
    started, completed = _seed(tasks_db_path, "run-events-1", "u1")
    headers = execution_user_headers(issued_api_key)

    first = await api_client.get(
        "/v1/runs/run-events-1/events?limit=1",
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["items"] == [started.to_public_dict()]
    assert first.json()["next_after_seq"] == 1
    assert first.json()["has_more"] is True

    resumed = await api_client.get(
        "/v1/runs/run-events-1/events?after_seq=1&limit=1",
        headers=headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["items"] == [completed.to_public_dict()]

    detail = await api_client.get(
        f"/v1/runs/run-events-1/events/{completed.event_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json() == completed.to_public_dict()

    projection = await api_client.get(
        "/v1/runs/run-events-1/event-projection",
        headers=headers,
    )
    assert projection.status_code == 200
    assert projection.json()["status"] == "succeeded"
    assert projection.json()["latest_seq"] == 2


async def test_event_page_resolves_public_execution_identity(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify event page resolves public execution identity."""
    execution_id = "turn-550e8400-e29b-41d4-a716-446655440000"
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec("run-execution-1", "u1", "chat", "api"),
        request_info=RunRequestInfo(execution_id=execution_id),
        result={},
    )
    page = SQLiteExecutionEventStore(tasks_db_path).list_events(
        "run-execution-1", owner="u1"
    )
    assert page is not None
    event = page.items[0]

    response = await api_client.get(
        f"/v1/executions/{execution_id}/events",
        headers=execution_user_headers(issued_api_key),
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-execution-1"
    assert response.json()["execution_id"] == execution_id
    assert response.json()["items"] == [event.to_public_dict()]


async def test_execution_identity_resolves_projection_detail_and_stream(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify execution identity resolves projection detail and stream."""
    execution_id = "turn-550e8400-e29b-41d4-a716-446655440001"
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec("run-execution-2", "u1", "chat", "api"),
        request_info=RunRequestInfo(execution_id=execution_id),
        result={},
    )
    store = SQLiteExecutionEventStore(tasks_db_path)
    initial_page = store.list_events("run-execution-2", owner="u1")
    assert initial_page is not None
    started = initial_page.items[0]
    completed = store.append(
        "run-execution-2",
        owner="u1",
        intent=event_intent("run.succeeded", status="succeeded"),
    )
    headers = execution_user_headers(issued_api_key)

    projection = await api_client.get(
        f"/v1/executions/{execution_id}/event-projection",
        headers=headers,
    )
    detail = await api_client.get(
        f"/v1/executions/{execution_id}/events/{completed.event_id}",
        headers=headers,
    )
    stream = await api_client.get(
        f"/v1/executions/{execution_id}/events/stream?after_seq=1",
        headers=headers,
    )

    assert projection.status_code == 200
    assert projection.json()["run_id"] == "run-execution-2"
    assert projection.json()["latest_seq"] == 2
    assert detail.status_code == 200
    assert detail.json() == completed.to_public_dict()
    assert stream.status_code == 200
    assert started.event_id not in stream.text
    assert completed.event_id in stream.text


async def test_execution_stream_waits_for_delayed_execution_registration(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify execution stream waits for delayed execution registration."""
    execution_id = "turn-delayed-execution-registration"
    registry = RunRegistry(tasks_db_path)
    registered = False

    async def register_during_wait(_seconds: float) -> None:
        nonlocal registered
        if registered:
            return
        registered = True
        registry.reserve_run(
            RunSpec("run-delayed-execution", "u1", "chat", "api"),
            request_info=RunRequestInfo(execution_id=execution_id),
            result={},
        )
        assert registry.settle_run(
            "run-delayed-execution",
            owner="u1",
            status="succeeded",
            result={"formatted": {"answer": "done"}},
            expected_revision=0,
        )

    monkeypatch.setattr(run_routes.asyncio, "sleep", register_during_wait)

    response = await api_client.get(
        f"/v1/executions/{execution_id}/events/stream",
        headers=execution_user_headers(issued_api_key),
    )

    assert registered is True
    assert response.status_code == 200
    assert "event: execution_event" in response.text
    assert '"kind":"run.succeeded"' in response.text


async def test_unknown_and_foreign_execution_ids_share_not_found(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify unknown and foreign execution IDs share not found."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec("run-execution-foreign", "other-user", "chat", "api"),
        request_info=RunRequestInfo(execution_id="turn-foreign-execution"),
        result={},
    )
    headers = execution_user_headers(issued_api_key)

    foreign = await api_client.get(
        "/v1/executions/turn-foreign-execution/events",
        headers=headers,
    )
    unknown = await api_client.get(
        "/v1/executions/turn-missing-execution/events",
        headers=headers,
    )

    assert foreign.status_code == unknown.status_code == 404
    for response in (foreign, unknown):
        assert response.json()["error"]["code"] == "not_found"
        assert response.json()["error"]["message"] == "resource not found"


async def test_unknown_and_foreign_event_runs_have_identical_not_found(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify unknown and foreign event runs have identical not found."""
    _seed(tasks_db_path, "run-events-foreign", "other-user")
    headers = execution_user_headers(issued_api_key)

    foreign = await api_client.get(
        "/v1/runs/run-events-foreign/events",
        headers=headers,
    )
    unknown = await api_client.get(
        "/v1/runs/run-events-missing/events",
        headers=headers,
    )
    assert foreign.status_code == unknown.status_code == 404
    for response in (foreign, unknown):
        assert response.json()["error"]["code"] == "not_found"
        assert response.json()["error"]["message"] == "resource not found"


async def test_event_page_query_limits_are_enforced_by_openapi_validation(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """Verify event page query limits are enforced by OpenAPI validation."""
    headers = execution_user_headers(issued_api_key)
    response = await api_client.get(
        "/v1/runs/anything/events?limit=201",
        headers=headers,
    )
    assert response.status_code == 422


async def test_event_stream_drains_terminal_history_and_resumes_from_header(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify event stream drains terminal history and resumes from header."""
    _started, completed = _seed(tasks_db_path, "run-stream-1", "u1")
    headers = execution_user_headers(issued_api_key, last_event_id="1")

    response = await api_client.get(
        "/v1/runs/run-stream-1/events/stream",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" not in response.text
    assert "id: 2" in response.text
    assert "event: execution_event" in response.text
    assert completed.event_id in response.text


async def test_event_stream_query_cursor_overrides_stale_resume_header(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify event stream query cursor overrides stale resume header."""
    started, completed = _seed(tasks_db_path, "run-stream-cursor", "u1")
    response = await api_client.get(
        "/v1/runs/run-stream-cursor/events/stream?after_seq=1",
        headers=execution_user_headers(issued_api_key, last_event_id="0"),
    )

    assert response.status_code == 200
    assert started.event_id not in response.text
    assert response.text.count(completed.event_id) == 1


async def test_terminal_settlement_is_last_committed_stream_fact(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify terminal settlement is last committed stream fact."""
    registry = RunRegistry(tasks_db_path)
    registry.reserve_run(
        RunSpec("run-terminal-order", "u1", "chat", "api"),
        request_info=RunRequestInfo(),
        result={},
    )
    assert registry.settle_run(
        "run-terminal-order",
        owner="u1",
        status="succeeded",
        result={"formatted": {"answer": "done"}},
        expected_revision=0,
    )

    response = await api_client.get(
        "/v1/runs/run-terminal-order/events/stream",
        headers=execution_user_headers(issued_api_key),
    )

    assert response.status_code == 200
    kinds = [
        line.split('"kind":"', 1)[1].split('"', 1)[0]
        for line in response.text.splitlines()
        if line.startswith("data:") and '"kind":"' in line
    ]
    assert kinds == ["run.started", "run.succeeded"]


async def test_event_stream_reports_pruned_sequence_gap(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Verify event stream reports pruned sequence gap."""
    _seed(tasks_db_path, "run-stream-gap", "u1")
    with sqlite_transaction(tasks_db_path) as connection:
        connection.execute(
            "DELETE FROM run_events WHERE run_id = ? AND seq = 1",
            ("run-stream-gap",),
        )
        connection.commit()

    response = await api_client.get(
        "/v1/runs/run-stream-gap/events/stream",
        headers=execution_user_headers(issued_api_key),
    )

    assert response.status_code == 200
    assert "event: execution_gap" in response.text
    assert '"next_available_seq":2' in response.text
    assert '"reason":"history_pruned_or_gap"' in response.text


async def test_event_stream_sends_heartbeat_while_a_run_is_idle(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify event stream sends heartbeat while a run is idle."""
    run_id = "run-stream-idle"
    RunRegistry(tasks_db_path).create_run(RunSpec(run_id, "u1", "chat", "api"))
    store = SQLiteExecutionEventStore(tasks_db_path)
    monkeypatch.setattr(run_routes, "EXECUTION_EVENT_HEARTBEAT_POLL_TICKS", 1)

    def settle() -> None:
        store.append(
            run_id,
            owner="u1",
            intent=event_intent("run.succeeded", status="succeeded"),
        )

    monkeypatch.setattr(
        run_routes.asyncio,
        "sleep",
        one_shot_sleep_callback(settle),
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}/events/stream",
        headers=execution_user_headers(issued_api_key),
    )

    assert response.status_code == 200
    assert ": heartbeat\n\n" in response.text
    assert "event: execution_event" in response.text
