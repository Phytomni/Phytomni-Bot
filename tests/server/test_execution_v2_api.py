# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Service admission and owner-scoped execution V2 HTTP contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

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
from mcp_server_phytomni.runtime.execution_log_artifact_v2 import (
    SQLiteExecutionLogArtifactStore,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    ExecutionCommand,
)
from mcp_server_phytomni.runtime.execution_target_store_v2 import (
    ExecutionTargetBindingV2,
    SQLiteExecutionTargetStore,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SpanSpec,
    SQLiteExecutionWorkRepository,
    WorkUnitSpec,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

pytestmark = pytest.mark.server


def _admission(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 2,
        "owner_ref": "u1",
        "execution_id": "turn-v2-admission",
        "fingerprint_version": 1,
        "fingerprint": "a" * 64,
        "agent_slug": "chat",
        "arguments": {"query": "rice"},
    }
    payload.update(overrides)
    return payload


async def test_service_admission_is_durable_idempotent_and_non_executing(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admission commits identity and private command before returning 202."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    headers = {"X-Service-Token": "execution-service-token"}

    first = await api_client.post(
        "/v2/executions",
        headers=headers,
        json=_admission(),
    )
    replay = await api_client.post(
        "/v2/executions",
        headers=headers,
        json=_admission(),
    )

    assert first.status_code == replay.status_code == 202
    assert first.json() == {
        "schema_version": 2,
        "execution_id": "turn-v2-admission",
        "run_id": first.json()["run_id"],
        "agent_slug": "chat",
        "status": "admitted",
        "event_cursor": 0,
        "supervisor_revision": 0,
        "idempotent_replay": False,
    }
    assert replay.json() == {**first.json(), "idempotent_replay": True}
    with sqlite_transaction(tasks_db_path) as connection:
        run = connection.execute(
            "SELECT status, request_json FROM runs WHERE user_id = ? "
            "AND execution_id = ?",
            ("u1", "turn-v2-admission"),
        ).fetchone()
        command = connection.execute(
            "SELECT state, command_json FROM execution_commands_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            ("u1", "turn-v2-admission"),
        ).fetchone()
    assert run == ("admitted", None)
    assert command is not None
    assert command[0] == "pending"
    assert '"query":"rice"' in command[1]


async def test_service_admits_private_expert_router_without_catalog_export(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    response = await api_client.post(
        "/v2/executions",
        headers={"X-Service-Token": "execution-service-token"},
        json=_admission(
            execution_id="turn-expert-router",
            agent_slug="expert-router",
            arguments={
                "__query": "rice",
                "__allowed_tools": ["ChatAgent"],
            },
        ),
    )

    assert response.status_code == 202
    assert response.json()["agent_slug"] == "expert-router"


async def test_service_admission_rejects_changed_fingerprint_and_user_key(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same execution cannot be rebound and user credentials cannot admit."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    service_headers = {"X-Service-Token": "execution-service-token"}
    admitted = await api_client.post(
        "/v2/executions", headers=service_headers, json=_admission()
    )
    conflict = await api_client.post(
        "/v2/executions",
        headers=service_headers,
        json=_admission(fingerprint="b" * 64),
    )
    user_only = await api_client.post(
        "/v2/executions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_admission(execution_id="turn-user-denied"),
    )

    assert admitted.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["message"] == (
        "execution identity conflict"
    )
    assert user_only.status_code == 401


def _seed_v2(
    tasks_db_path: str,
    *,
    owner: str,
    execution_id: str,
    agent_slug: str = "chat",
):
    repository = SQLiteExecutionReservationRepository(tasks_db_path)
    record = repository.reserve(
        owner=owner,
        execution_id=execution_id,
        fingerprint_version=1,
        fingerprint="c" * 64,
        command=ExecutionCommand(
            agent_slug=agent_slug, arguments={"query": "rice"}
        ),
    )
    SQLiteExecutionWorkRepository(tasks_db_path).create_span(
        SpanSpec(
            owner=owner,
            execution_id=execution_id,
            span_id=record.root_span_id,
            kind="agent",
            label_key="agent.chat",
        )
    )
    journal = SQLiteExecutionJournal(tasks_db_path)
    started = journal.append(
        execution_id,
        owner=owner,
        intent=parse_execution_event_intent_v2(
            {
                "type": "execution.started",
                "status": "running",
                "source": "runtime",
                "span_id": record.root_span_id,
                "summary": {
                    "key": "execution.started",
                    "text": "Execution started",
                },
                "public_payload": {},
            }
        ),
    )
    published = journal.append(
        execution_id,
        owner=owner,
        intent=parse_execution_event_intent_v2(
            {
                "type": "artifact.published",
                "status": "succeeded",
                "source": "artifact",
                "span_id": record.root_span_id,
                "summary": {
                    "key": "artifact.published",
                    "text": "Result published",
                },
                "public_payload": {
                    "name": "report.md",
                    "media_type": "text/markdown",
                    "size_bytes": 12,
                },
                "target": {"kind": "artifact", "id": "artifact-report"},
            }
        ),
    )
    return record, started, published


async def test_v2_snapshot_page_detail_target_and_owner_isolation(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every V2 read resolves the service-asserted owner binding."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, started, published = _seed_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-read"
    )
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }

    snapshot = await api_client.get(
        "/v2/executions/turn-v2-read", headers=headers
    )
    page = await api_client.get(
        "/v2/executions/turn-v2-read/events?after_seq=1&limit=10",
        headers=headers,
    )
    detail = await api_client.get(
        f"/v2/executions/turn-v2-read/events/{published.event_id}",
        headers=headers,
    )
    target = await api_client.get(
        "/v2/executions/turn-v2-read/targets/artifact/artifact-report",
        headers=headers,
    )
    foreign = await api_client.get(
        "/v2/executions/turn-v2-read",
        headers={**headers, "X-Phyto-Owner": "other-user"},
    )
    unknown = await api_client.get(
        "/v2/executions/turn-v2-missing", headers=headers
    )

    assert snapshot.status_code == 200
    assert snapshot.json()["execution_id"] == record.execution_id
    assert snapshot.json()["run_id"] == record.run_id
    assert snapshot.json()["status"] == "running"
    assert snapshot.json()["latest_seq"] == 2
    assert page.status_code == 200
    assert page.json()["items"] == [published.to_public_dict()]
    assert page.json()["next_after_seq"] == 2
    assert detail.json() == published.to_public_dict()
    assert target.json() == {
        "schema_version": 2,
        "execution_id": "turn-v2-read",
        "target": {"kind": "artifact", "id": "artifact-report"},
        "resolution": "authorized",
        "delivery_available": False,
    }
    assert started.seq == 1
    assert foreign.status_code == unknown.status_code == 404
    for response in (foreign, unknown):
        assert response.json()["error"]["code"] == "not_found"
        assert response.json()["error"]["message"] == "resource not found"


async def test_v2_snapshot_and_operation_detail_expose_grouped_projection(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, _started, _published = _seed_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-operation-detail",
        agent_slug="knowledge",
    )
    SQLiteExecutionJournal(tasks_db_path).append(
        record.execution_id,
        owner="u1",
        intent=parse_execution_event_intent_v2(
            {
                "type": "work_unit.registered",
                "status": "queued",
                "source": "runtime",
                "span_id": record.root_span_id,
                "work_unit_id": "work-knowledge-search",
                "summary": {
                    "key": "knowledge.search.registered",
                    "text": "Search knowledge registered",
                },
                "public_payload": {
                    "operation_key": "knowledge.search",
                    "detail": {"repository_count": 2},
                },
            }
        ),
    )
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }

    snapshot = await api_client.get(
        f"/v2/executions/{record.execution_id}", headers=headers
    )
    operation = snapshot.json()["operations"][0]
    detail = await api_client.get(
        f"/v2/executions/{record.execution_id}/operations/"
        f"{operation['operation_id']}",
        headers=headers,
    )
    foreign = await api_client.get(
        f"/v2/executions/{record.execution_id}/operations/"
        f"{operation['operation_id']}",
        headers={**headers, "X-Phyto-Owner": "other"},
    )

    assert snapshot.status_code == 200
    assert operation["operation_key"] == "knowledge.search"
    assert operation["detail"] == {"repository_count": 2}
    assert snapshot.json()["execution_stage"]["stage"] == "consolidation"
    assert detail.status_code == 200
    assert detail.json() == operation
    assert foreign.status_code == 404


async def test_v2_target_delivery_is_private_owner_scoped_and_click_time(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution hides OBS refs and reauthorizes content per click."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    _seed_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-target-delivery",
    )
    SQLiteExecutionTargetStore(tasks_db_path).put(
        ExecutionTargetBindingV2(
            owner="u1",
            execution_id="turn-v2-target-delivery",
            kind="artifact",
            target_id="artifact-report",
            role="scientific_report",
            name="report.md",
            media_type="text/markdown",
            size_bytes=12,
            delivery_ref="obs://private/run/report.md",
        )
    )
    source = tmp_path / "report.md"
    source.write_bytes(b"hello report")

    calls = 0

    async def resolve(_delivery_ref: str, _server_dir: str) -> str:
        nonlocal calls
        calls += 1
        assert _delivery_ref == "obs://private/run/report.md"
        if calls > 1:
            return str(source)
        materialized = Path(_server_dir) / "report.md"
        materialized.write_bytes(source.read_bytes())
        return str(materialized)

    monkeypatch.setattr(execution_v2_routes, "download_obs_file", resolve)
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }

    target_path = (
        "/v2/executions/turn-v2-target-delivery/targets/"
        "artifact/artifact-report"
    )
    content_path = f"{target_path}/content"
    resolution = await api_client.get(target_path, headers=headers)
    content = await api_client.get(content_path, headers=headers)
    escaped = await api_client.get(content_path, headers=headers)
    foreign = await api_client.get(
        content_path,
        headers={**headers, "X-Phyto-Owner": "other"},
    )

    assert resolution.status_code == 200
    assert resolution.json() == {
        "schema_version": 2,
        "execution_id": "turn-v2-target-delivery",
        "target": {"kind": "artifact", "id": "artifact-report"},
        "resolution": "authorized",
        "delivery_available": True,
        "name": "report.md",
        "media_type": "text/markdown",
        "size_bytes": 12,
    }
    assert "obs://" not in resolution.text
    assert content.status_code == 200
    assert content.content == b"hello report"
    assert content.headers["content-type"].startswith("text/markdown")
    assert escaped.status_code == 503
    assert escaped.json()["error"]["code"] == "target_delivery_unavailable"
    assert foreign.status_code == 404


async def test_v2_execution_log_delivery_is_role_aware_and_owner_scoped(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execution logs bypass OBS but retain public-target authorization."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, _started, _published = _seed_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-execution-log",
    )
    payload = b'{"schema_version":1,"records":[]}'
    artifact = SQLiteExecutionLogArtifactStore(tasks_db_path).put(
        owner="u1",
        execution_id=record.execution_id,
        payload=payload,
    )
    SQLiteExecutionTargetStore(tasks_db_path).put(
        ExecutionTargetBindingV2(
            owner="u1",
            execution_id=record.execution_id,
            kind="artifact",
            target_id=artifact.target_id,
            role="execution_log",
            name=artifact.name,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            delivery_ref=artifact.delivery_ref,
        )
    )
    SQLiteExecutionJournal(tasks_db_path).append(
        record.execution_id,
        owner="u1",
        intent=parse_execution_event_intent_v2(
            {
                "type": "artifact.published",
                "status": "succeeded",
                "source": "artifact",
                "span_id": record.root_span_id,
                "summary": {
                    "key": "result.execution_log.published",
                    "text": "Execution log published",
                },
                "public_payload": {
                    "name": artifact.name,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                },
                "target": {"kind": "artifact", "id": artifact.target_id},
            }
        ),
    )
    path = (
        f"/v2/executions/{record.execution_id}/targets/artifact/"
        f"{artifact.target_id}/content"
    )
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }

    content = await api_client.get(path, headers=headers)
    foreign = await api_client.get(
        path,
        headers={**headers, "X-Phyto-Owner": "other"},
    )

    assert content.status_code == 200
    assert content.content == payload
    assert content.headers["content-type"].startswith("application/json")
    assert "execution-log.json" in content.headers["content-disposition"]
    assert foreign.status_code == 404


async def test_v2_action_and_cancellation_are_revision_checked(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported actions fail safely and cancellation stays explicit."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, _started, _published = _seed_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-control"
    )
    unsupported_record, _unsupported_started, _unsupported_published = (
        _seed_v2(
            tasks_db_path,
            owner="u1",
            execution_id="turn-v2-action-unsupported",
            agent_slug="knowledge",
        )
    )
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }
    unsupported = await api_client.post(
        "/v2/executions/turn-v2-action-unsupported/actions",
        headers=headers,
        json={
            "action_id": "action-1",
            "expected_revision": unsupported_record.supervisor_revision,
            "surface_id": "surface-1",
            "widget": "confirm",
            "payload": {"accepted": True},
        },
    )
    wrong_state = await api_client.post(
        "/v2/executions/turn-v2-control/actions",
        headers=headers,
        json={
            "action_id": "action-2",
            "expected_revision": record.supervisor_revision,
            "surface_id": "surface-2",
            "widget": "confirm",
            "payload": {"accepted": True},
        },
    )
    stale = await api_client.post(
        "/v2/executions/turn-v2-control/cancel",
        headers=headers,
        json={
            "request_id": "cancel-stale",
            "expected_revision": 99,
            "reason": "user_requested",
        },
    )
    cancelled = await api_client.post(
        "/v2/executions/turn-v2-control/cancel",
        headers=headers,
        json={
            "request_id": "cancel-1",
            "expected_revision": record.supervisor_revision,
            "reason": "user_requested",
        },
    )

    assert unsupported.status_code == 409
    assert unsupported.json()["error"]["code"] == "action_unsupported"
    assert wrong_state.status_code == 409
    assert wrong_state.json()["error"]["code"] == "run_state_conflict"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "execution_revision_conflict"
    assert cancelled.status_code == 202
    assert cancelled.json()["cancellation_outcome"] == "best_effort"


async def test_v2_stream_starts_with_snapshot_and_drains_terminal_backlog(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid stream is useful before clients need any internal run id."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, started, published = _seed_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-stream"
    )
    terminal = SQLiteExecutionJournal(tasks_db_path).append(
        record.execution_id,
        owner="u1",
        intent=parse_execution_event_intent_v2(
            {
                "type": "execution.succeeded",
                "status": "succeeded",
                "source": "runtime",
                "span_id": record.root_span_id,
                "summary": {
                    "key": "execution.succeeded",
                    "text": "Execution succeeded",
                },
                "public_payload": {},
            }
        ),
    )
    response = await api_client.get(
        "/v2/executions/turn-v2-stream/events/stream?after_seq=1",
        headers={
            "X-Service-Token": "execution-service-token",
            "X-Phyto-Owner": "u1",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith("event: execution_snapshot\n")
    assert f"id: {started.seq}\n" not in response.text
    assert f"id: {published.seq}\n" in response.text
    assert f"id: {terminal.seq}\n" in response.text
    assert response.text.count("event: execution_event\n") == 2


async def test_v2_snapshots_include_sequence_free_provider_contact(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, _started, _published = _seed_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-provider-contact",
    )
    work = SQLiteExecutionWorkRepository(tasks_db_path)
    unit = work.create_work_unit(
        WorkUnitSpec(
            owner="u1",
            execution_id=record.execution_id,
            work_unit_id="work-remote-analysis",
            parent_span_id=record.root_span_id,
            operation_key="remote.analysis",
            driver="provider",
        )
    )
    observed_at = "2026-08-24T08:30:00+00:00"
    assert work.observe_provider_contact(
        record.execution_id,
        unit.work_unit_id,
        owner="u1",
        observed_at=observed_at,
    )
    journal = SQLiteExecutionJournal(tasks_db_path)
    terminal = journal.append(
        record.execution_id,
        owner="u1",
        intent=parse_execution_event_intent_v2(
            {
                "type": "execution.succeeded",
                "status": "succeeded",
                "source": "runtime",
                "span_id": record.root_span_id,
                "summary": {
                    "key": "execution.succeeded",
                    "text": "Execution succeeded",
                },
                "public_payload": {},
            }
        ),
    )
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }

    snapshot = await api_client.get(
        f"/v2/executions/{record.execution_id}", headers=headers
    )
    stream = await api_client.get(
        f"/v2/executions/{record.execution_id}/events/stream"
        f"?after_seq={terminal.seq}",
        headers=headers,
    )

    assert snapshot.status_code == stream.status_code == 200
    assert snapshot.json()["latest_seq"] == terminal.seq
    assert (
        snapshot.json()["execution_stage"]["clocks"][
            "last_provider_contact_at"
        ]
        == observed_at
    )
    assert f'"last_provider_contact_at":"{observed_at}"' in stream.text
    assert "work_unit.progress" not in stream.text


async def test_v2_live_stream_refreshes_provider_clock_without_advancing_seq(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, _started, published = _seed_v2(
        tasks_db_path,
        owner="u1",
        execution_id="turn-v2-live-contact",
    )
    work = SQLiteExecutionWorkRepository(tasks_db_path)
    unit = work.create_work_unit(
        WorkUnitSpec(
            owner="u1",
            execution_id=record.execution_id,
            work_unit_id="work-live-analysis",
            parent_span_id=record.root_span_id,
            operation_key="remote.analysis",
            driver="provider",
        )
    )
    observed_at = "2026-08-24T09:00:00+00:00"
    journal = SQLiteExecutionJournal(tasks_db_path)
    sleep_calls = 0

    async def refresh_then_settle(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            assert work.observe_provider_contact(
                record.execution_id,
                unit.work_unit_id,
                owner="u1",
                observed_at=observed_at,
            )
        elif sleep_calls == 2:
            journal.append(
                record.execution_id,
                owner="u1",
                intent=parse_execution_event_intent_v2(
                    {
                        "type": "execution.succeeded",
                        "status": "succeeded",
                        "source": "runtime",
                        "span_id": record.root_span_id,
                        "summary": {
                            "key": "execution.succeeded",
                            "text": "Execution succeeded",
                        },
                        "public_payload": {},
                    }
                ),
            )

    monkeypatch.setattr(
        execution_v2_routes.asyncio, "sleep", refresh_then_settle
    )
    response = await api_client.get(
        f"/v2/executions/{record.execution_id}/events/stream"
        f"?after_seq={published.seq}",
        headers={
            "X-Service-Token": "execution-service-token",
            "X-Phyto-Owner": "u1",
        },
    )

    assert response.status_code == 200
    assert response.text.count("event: execution_snapshot\n") == 2
    assert f'"last_provider_contact_at":"{observed_at}"' in response.text
    assert "work_unit.progress" not in response.text


async def test_v2_stream_carries_resumable_content_and_sequence_free_heartbeat(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient content and keepalive traffic never consume durable seq."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, _started, published = _seed_v2(
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
    settled = False

    async def settle_after_heartbeat(_seconds: float) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        journal.append(
            record.execution_id,
            owner="u1",
            intent=parse_execution_event_intent_v2(
                {
                    "type": "execution.succeeded",
                    "status": "succeeded",
                    "source": "runtime",
                    "span_id": record.root_span_id,
                    "summary": {
                        "key": "execution.succeeded",
                        "text": "Execution succeeded",
                    },
                    "public_payload": {},
                }
            ),
        )

    monkeypatch.setattr(
        execution_v2_routes,
        "EXECUTION_V2_HEARTBEAT_POLL_TICKS",
        1,
    )
    monkeypatch.setattr(
        execution_v2_routes.asyncio,
        "sleep",
        settle_after_heartbeat,
    )
    response = await api_client.get(
        "/v2/executions/turn-v2-live/events/stream"
        f"?after_seq={published.seq}&after_revision=1&after_offset=0",
        headers={
            "X-Service-Token": "execution-service-token",
            "X-Phyto-Owner": "u1",
        },
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
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    admitted = await api_client.post(
        "/v2/executions",
        headers={"X-Service-Token": "execution-service-token"},
        json=_admission(execution_id="turn-stream-before-dispatch"),
    )

    async def disconnected(_request: Request) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    response = await api_client.get(
        "/v2/executions/turn-stream-before-dispatch/events/stream",
        headers={
            "X-Service-Token": "execution-service-token",
            "X-Phyto-Owner": "u1",
        },
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
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    execution_id = "turn-reconcile-snapshot"
    admitted = await api_client.post(
        "/v2/executions",
        headers={"X-Service-Token": "execution-service-token"},
        json=_admission(execution_id=execution_id),
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
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }
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
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, started, published = _seed_v2(
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
    terminal = SQLiteExecutionJournal(tasks_db_path).append(
        record.execution_id,
        owner="u1",
        intent=parse_execution_event_intent_v2(
            {
                "type": "execution.succeeded",
                "status": "succeeded",
                "source": "runtime",
                "span_id": record.root_span_id,
                "summary": {"key": "done", "text": "Done"},
                "public_payload": {},
            }
        ),
    )
    response = await api_client.get(
        "/v2/executions/turn-v2-reconnect/events/stream"
        "?after_revision=1&after_offset=5",
        headers={
            "X-Service-Token": "execution-service-token",
            "X-Phyto-Owner": "u1",
            "Last-Event-ID": str(published.seq),
        },
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
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    _seed_v2(tasks_db_path, owner="u1", execution_id="turn-v2-storage-fail")

    def unavailable(*_args: object, **_kwargs: object):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(SQLiteExecutionJournal, "list_events", unavailable)
    response = await api_client.get(
        "/v2/executions/turn-v2-storage-fail/events/stream",
        headers={
            "X-Service-Token": "execution-service-token",
            "X-Phyto-Owner": "u1",
        },
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
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    _seed_v2(tasks_db_path, owner="u1", execution_id="turn-v2-safe-target")
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }

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
    from mcp_server_phytomni.runtime.execution_trace_target_v2 import (
        trace_target_for_operation,
    )

    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    record, _started, _published = _seed_v2(
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
    facts = (
        {
            "type": "work_unit.registered",
            "status": "queued",
            "span_id": record.root_span_id,
            "parent_span_id": None,
            "work_unit_id": "private-analysis-work",
            "summary": {"key": "remote.analysis", "text": "Run analysis"},
            "public_payload": {"operation_key": "remote.analysis"},
            "target": target,
        },
        {
            "type": "span.created",
            "status": "pending",
            "span_id": "private-analysis-span",
            "parent_span_id": record.root_span_id,
            "work_unit_id": "private-analysis-work",
            "summary": {"key": "remote.analysis", "text": "Run analysis"},
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
            "public_payload": {"operation_key": "gene_network.prepare_inputs"},
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
            "public_payload": {"operation_key": "gene_network.prepare_inputs"},
        },
    )
    for index, fact in enumerate(facts):
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

    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }
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

    public = str(body) + str(second.json())
    assert "private-analysis-work" not in public
    assert "private-analysis-span" not in public
    assert "private-child-work" not in public
    foreign = await api_client.get(
        f"/v2/executions/{record.execution_id}/targets/trace/{target['id']}",
        headers={**headers, "X-Phyto-Owner": "other-user"},
    )
    missing_path = (
        f"/v2/executions/{record.execution_id}/targets/trace/"
        "trc_A1b2C3d4E5f6G7h8"
    )
    missing = await api_client.get(missing_path, headers=headers)
    assert foreign.status_code == missing.status_code == 404


async def test_v2_contract_reports_version_gap_degraded_and_terminal_conflicts(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery and conflict states are explicit finite contracts."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "execution-service-token")
    headers = {
        "X-Service-Token": "execution-service-token",
        "X-Phyto-Owner": "u1",
    }
    unsupported_admission = await api_client.post(
        "/v2/executions",
        headers={"X-Service-Token": "execution-service-token"},
        json=_admission(schema_version=3, execution_id="turn-v3"),
    )
    record, _started, _published = _seed_v2(
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
    terminal = journal.append(
        record.execution_id,
        owner="u1",
        intent=parse_execution_event_intent_v2(
            {
                "type": "execution.succeeded",
                "status": "succeeded",
                "source": "runtime",
                "span_id": record.root_span_id,
                "summary": {
                    "key": "execution.succeeded",
                    "text": "Execution succeeded",
                },
                "public_payload": {},
            }
        ),
    )
    with sqlite_transaction(tasks_db_path) as connection:
        connection.execute(
            "DELETE FROM execution_events_v2 WHERE owner_ref = ? "
            "AND execution_id = ? AND seq = 1",
            ("u1", record.execution_id),
        )
        connection.commit()

    unsupported_read = await api_client.get(
        f"/v2/executions/{record.execution_id}",
        headers={**headers, "X-Phyto-Execution-Schema": "3"},
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

    for response in (unsupported_admission, unsupported_read):
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
    record, started, published = _seed_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-v1-view"
    )
    response = await api_client.get(
        f"/v1/runs/{record.run_id}/events?after_seq=0&limit=10",
        headers={"Authorization": f"Bearer {issued_api_key}"},
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
