# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Service admission and owner-scoped execution V2 HTTP contracts."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from tests.support.execution_v2_api import (
    append_artifact_published,
    append_execution_succeeded,
    execution_user_headers,
    execution_v2_admission,
    execution_v2_headers,
    seed_execution_v2,
)

from mcp_server_phytomni.api.routes import executions_v2 as execution_v2_routes
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    parse_execution_event_intent_v2,
)
from mcp_server_phytomni.runtime.execution_log_artifact_v2 import (
    SQLiteExecutionLogArtifactStore,
)
from mcp_server_phytomni.runtime.execution_runtime_v2 import (
    build_execution_log_target_binding,
)
from mcp_server_phytomni.runtime.execution_target_store_v2 import (
    ExecutionTargetBindingV2,
    SQLiteExecutionTargetStore,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
    WorkUnitSpec,
)
from mcp_server_phytomni.runtime.sqlite import sqlite_transaction

pytestmark = pytest.mark.server


async def test_service_admission_is_durable_idempotent_and_non_executing(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admission commits identity and private command before returning 202."""
    headers = execution_v2_headers(monkeypatch)

    first = await api_client.post(
        "/v2/executions",
        headers=headers,
        json=execution_v2_admission(),
    )
    replay = await api_client.post(
        "/v2/executions",
        headers=headers,
        json=execution_v2_admission(),
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
    """Verify service admits private expert router without catalog export."""
    headers = execution_v2_headers(monkeypatch)
    response = await api_client.post(
        "/v2/executions",
        headers=headers,
        json=execution_v2_admission(
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
    service_headers = execution_v2_headers(monkeypatch)
    admitted = await api_client.post(
        "/v2/executions",
        headers=service_headers,
        json=execution_v2_admission(),
    )
    conflict = await api_client.post(
        "/v2/executions",
        headers=service_headers,
        json=execution_v2_admission(fingerprint="b" * 64),
    )
    user_only = await api_client.post(
        "/v2/executions",
        headers=execution_user_headers(issued_api_key),
        json=execution_v2_admission(execution_id="turn-user-denied"),
    )

    assert admitted.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["message"] == (
        "execution identity conflict"
    )
    assert user_only.status_code == 401


async def test_v2_snapshot_page_detail_target_and_owner_isolation(
    api_client: httpx.AsyncClient,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every V2 read resolves the service-asserted owner binding."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, started, published = seed_execution_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-read"
    )

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
    """Verify V2 snapshot and operation detail expose grouped projection."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, _published = seed_execution_v2(
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
    headers = execution_v2_headers(monkeypatch, owner="u1")
    seed_execution_v2(
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
            delivery_ref="obs://private/run/report.md",
            role="scientific_report",
            name="report.md",
            media_type="text/markdown",
            size_bytes=12,
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
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, _published = seed_execution_v2(
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
        build_execution_log_target_binding(
            owner="u1",
            execution_id=record.execution_id,
            artifact=artifact,
        )
    )
    append_artifact_published(
        SQLiteExecutionJournal(tasks_db_path),
        record,
        owner="u1",
        summary=("result.execution_log.published", "Execution log published"),
        artifact=(
            artifact.name,
            artifact.media_type,
            artifact.size_bytes,
            artifact.target_id,
        ),
    )
    path = (
        f"/v2/executions/{record.execution_id}/targets/artifact/"
        f"{artifact.target_id}/content"
    )
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
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, _published = seed_execution_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-control"
    )
    unsupported_record, _unsupported_started, _unsupported_published = (
        seed_execution_v2(
            tasks_db_path,
            owner="u1",
            execution_id="turn-v2-action-unsupported",
            agent_slug="knowledge",
        )
    )
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
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, started, published = seed_execution_v2(
        tasks_db_path, owner="u1", execution_id="turn-v2-stream"
    )
    terminal = append_execution_succeeded(
        SQLiteExecutionJournal(tasks_db_path),
        record,
        owner="u1",
    )
    response = await api_client.get(
        "/v2/executions/turn-v2-stream/events/stream?after_seq=1",
        headers=headers,
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
    """Verify V2 snapshots include sequence free provider contact."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, _published = seed_execution_v2(
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
    terminal = append_execution_succeeded(
        journal,
        record,
        owner="u1",
    )

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
    """Verify V2 live stream refreshes provider clock without advancing seq."""
    headers = execution_v2_headers(monkeypatch, owner="u1")
    record, _started, published = seed_execution_v2(
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
            append_execution_succeeded(
                journal,
                record,
                owner="u1",
            )

    monkeypatch.setattr(
        execution_v2_routes.asyncio, "sleep", refresh_then_settle
    )
    response = await api_client.get(
        f"/v2/executions/{record.execution_id}/events/stream"
        f"?after_seq={published.seq}",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.text.count("event: execution_snapshot\n") == 2
    assert f'"last_provider_contact_at":"{observed_at}"' in response.text
    assert "work_unit.progress" not in response.text
