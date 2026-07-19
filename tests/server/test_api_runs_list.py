# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``GET /v1/runs`` owner-scoped listing + lazy purge.

Covers owner-only visibility (foreign rows hidden), the status / agent
/ origin equality filters, limit / offset paging, and the lazy purge:
a listing call deletes expired rows before reading so the registry
stays bounded under listing-heavy workloads.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from tests.agents.shared.deep_genome_fixtures import (
    assert_report_metadata,
    attach_formatted_result,
    seed_partial_deep_genome_run,
    seed_terminal_deep_genome_run,
)
from tests.support.chat_fakes import install_chat_handler

from mcp_server_phytomni import server
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.server


async def test_list_deep_genome_projects_intermediate_snapshot(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The list path returns the current local DeepGenome report snapshot."""
    run_id = seed_partial_deep_genome_run(tasks_db_path)

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    row = next(
        item for item in response.json()["data"] if item["run_id"] == run_id
    )
    assert row["status"] == "running"
    assert row["result"]["intermediate_report"].startswith("#")
    assert row["result"]["final_report"] is None
    assert row["result"]["report_revision"] == 3
    assert row["answer"] == row["result"]["intermediate_report"]
    assert "task_results" not in row["result"]
    assert "live_status" not in row["result"]
    assert "raw" not in row["result"]
    assert "formatted" not in row["result"]


async def test_list_deep_genome_adds_report_metadata_to_formatted_result(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The list projection uses the same report metadata adapter as status."""
    run_id = seed_partial_deep_genome_run(tasks_db_path)
    attach_formatted_result(tasks_db_path, run_id)

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    row = next(
        item for item in response.json()["data"] if item["run_id"] == run_id
    )
    result = row["result"]
    assert_report_metadata(result, stage="intermediate", revision=3)


async def test_list_deep_genome_debug_preserves_private_snapshot_fields(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Debug list reads retain the registry's private result fields."""
    run_id = seed_partial_deep_genome_run(tasks_db_path)

    response = await api_client.get(
        "/v1/runs",
        params={"debug": "true"},
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    row = next(
        item for item in response.json()["data"] if item["run_id"] == run_id
    )
    assert "task_results" in row["result"]
    assert "live_status" in row["result"]
    assert "artifacts" in row["result"]


async def test_list_deep_genome_projects_degraded_final_snapshot(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A final report remains visible when optional analyses failed."""
    run_id = seed_terminal_deep_genome_run(
        tasks_db_path,
        owner="u1",
        all_failed=False,
    )

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    row = next(
        item for item in response.json()["data"] if item["run_id"] == run_id
    )
    assert row["status"] == "succeeded"
    assert row["result"]["final_report"] == "# Final report\n"
    assert row["result"]["report_completeness"] == "partial"
    assert row["result"]["degraded"] is True
    assert_report_metadata(row["result"], stage="final")
    assert row["answer"] == row["result"]["final_report"]


async def test_list_deep_genome_preserves_all_failed_intermediate_snapshot(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """All-analysis failure stays failed while preserving intermediate text."""
    run_id = seed_terminal_deep_genome_run(
        tasks_db_path,
        owner="u1",
        all_failed=True,
    )

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    row = next(
        item for item in response.json()["data"] if item["run_id"] == run_id
    )
    assert row["status"] == "failed"
    assert row["result"]["final_report"] is None
    assert row["result"]["intermediate_report"].startswith("#")
    assert row["result"]["degraded"] is True
    assert_report_metadata(row["result"], stage="intermediate")
    assert row["answer"] == row["result"]["intermediate_report"]


async def test_list_deep_genome_hides_foreign_snapshot(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Foreign DeepGenome snapshots are not looked up or returned."""
    run_id = seed_partial_deep_genome_run(tasks_db_path, owner="u2")

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert run_id not in {row["run_id"] for row in response.json()["data"]}


async def test_list_deep_genome_does_not_probe_remote_children(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot listing never calls the remote task reconciler."""
    seed_partial_deep_genome_run(tasks_db_path)
    remote_status_mock = AsyncMock()
    monkeypatch.setattr(
        run_registry_module,
        "reconcile_task",
        remote_status_mock,
    )

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    remote_status_mock.assert_not_awaited()


def _seed(registry: RunRegistry, **kwargs: str) -> str:
    """Create one terminal run with the given spec fields.

    Pass ``dialogue_id`` in ``**kwargs`` to populate the runs row's
    ``dialogue_id`` column via ``RunRequestInfo``; omitted callers
    keep the legacy NULL behaviour.
    """
    spec = RunSpec(
        run_id=kwargs["run_id"],
        user_id=kwargs["user_id"],
        agent=kwargs["agent"],
        origin=kwargs["origin"],
    )
    dialogue_id = kwargs.get("dialogue_id")
    request_info = (
        RunRequestInfo(dialogue_id=dialogue_id)
        if dialogue_id is not None
        else None
    )
    registry.create_run(
        spec,
        outcome=RunOutcome(status="succeeded", result={"ok": True}),
        request_info=request_info,
    )
    return spec.run_id


async def test_list_runs_returns_only_owner_rows(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Foreign-owner runs are invisible to the authenticated caller."""
    registry = RunRegistry(tasks_db_path)
    _seed(
        registry,
        run_id="run-mine-1",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    _seed(
        registry,
        run_id="run-foreign-1",
        user_id="other",
        agent="chat",
        origin="local",
    )

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    ids = {row["run_id"] for row in body["data"]}
    assert ids == {"run-mine-1"}


async def test_list_runs_filters_by_agent_and_origin(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The agent / origin equality filters compose conjunctively."""
    registry = RunRegistry(tasks_db_path)
    _seed(
        registry,
        run_id="run-1",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    _seed(
        registry,
        run_id="run-2",
        user_id="u1",
        agent="analyst",
        origin="remote",
    )
    _seed(
        registry,
        run_id="run-3",
        user_id="u1",
        agent="chat",
        origin="remote",
    )

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        params={"agent": "chat", "origin": "remote"},
    )
    assert response.status_code == 200
    ids = {row["run_id"] for row in response.json()["data"]}
    assert ids == {"run-3"}


async def test_list_runs_paginates(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """``limit`` / ``offset`` slice the newest-first listing."""
    registry = RunRegistry(tasks_db_path)
    for i in range(3):
        _seed(
            registry,
            run_id=f"run-{i}",
            user_id="u1",
            agent="chat",
            origin="local",
        )

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        params={"limit": 1, "offset": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1


async def test_list_runs_lazy_purges_expired(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    tmp_path: Path,
) -> None:
    """An expired row is purged before listing returns the live set."""
    _ = tmp_path
    registry = RunRegistry(tasks_db_path)
    _seed(
        registry,
        run_id="run-live",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    _seed(
        registry,
        run_id="run-stale",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    with sqlite3.connect(tasks_db_path) as conn:
        conn.execute(
            "UPDATE runs SET expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", "run-stale"),
        )
        conn.commit()

    response = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    ids = {row["run_id"] for row in response.json()["data"]}
    assert ids == {"run-live"}
    assert RunRegistry(tasks_db_path).get_run("run-stale", owner="u1") is None


async def _expire_run(db: str, run_id: str) -> None:
    """Mark one run as already expired so the next purge sweeps it."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE runs SET expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", run_id),
        )
        conn.commit()


async def test_chat_completions_purges_expired(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A chat completions write trips the lazy purge for expired rows."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a stub completion result."""
        _ = args
        return {"answer": "ok"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )
    registry = RunRegistry(tasks_db_path)
    _seed(
        registry,
        run_id="run-stale",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    await _expire_run(tasks_db_path, "run-stale")

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "model": "phyto-chat",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 200
    assert RunRegistry(tasks_db_path).get_run("run-stale", owner="u1") is None


async def test_agent_run_purges_expired(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A native agent run write trips the lazy purge for expired rows."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a stub sync agent result."""
        _ = args
        return {"answer": "ok"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )
    registry = RunRegistry(tasks_db_path)
    _seed(
        registry,
        run_id="run-stale",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    await _expire_run(tasks_db_path, "run-stale")

    response = await api_client.post(
        "/v1/agents/chat/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "purge-trigger",
                "obs_file_list": [],
            }
        },
    )
    assert response.status_code == 200
    purged = RunRegistry(tasks_db_path).get_run("run-stale", owner="u1")
    assert purged is None


async def test_list_runs_rejects_user_id_without_service_token(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user key cannot use ``?user_id=`` to escape owner scope."""
    del tasks_db_path
    monkeypatch.delenv("API_SERVICE_TOKEN", raising=False)

    response = await api_client.get(
        "/v1/runs?user_id=other-user",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 403
    assert "service token" in response.json()["error"]["message"].lower()


async def test_list_runs_delegated_query_with_service_token(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user key + valid service token returns the target user's runs."""
    monkeypatch.setenv("API_SERVICE_TOKEN", "svc-token-xyz")
    registry = RunRegistry(tasks_db_path)
    _seed(
        registry,
        run_id="run-other-1",
        user_id="alice@example.com",
        agent="chat",
        origin="local",
    )
    _seed(
        registry,
        run_id="run-mine-1",
        user_id="u1",
        agent="chat",
        origin="local",
    )

    response = await api_client.get(
        "/v1/runs?user_id=alice@example.com",
        headers={
            "Authorization": f"Bearer {issued_api_key}",
            "X-Service-Token": "svc-token-xyz",
        },
    )

    assert response.status_code == 200
    body = response.json()
    ids = {row["run_id"] for row in body["data"]}
    assert ids == {"run-other-1"}


async def test_list_runs_created_after_filter(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The created_after query parameter trims older rows."""
    registry = RunRegistry(tasks_db_path)
    _seed(
        registry,
        run_id="run-old",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    _seed(
        registry,
        run_id="run-new",
        user_id="u1",
        agent="chat",
        origin="local",
    )
    with sqlite3.connect(tasks_db_path) as conn:
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", "run-old"),
                ("2026-06-01T00:00:00+00:00", "run-new"),
            ],
        )
        conn.commit()

    response = await api_client.get(
        "/v1/runs?created_after=2026-03-01T00:00:00%2B00:00",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    ids = [row["run_id"] for row in response.json()["data"]]
    assert ids == ["run-new"]


async def test_list_runs_dialogue_id_server_side_filter(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """``dialogue_id`` filter runs server-side before ``limit``.

    Seeds 15 owner rows — 10 non-matching dialogues and 5 with the
    target dialogue id — then queries with ``?dialogue_id=&limit=10``.
    A correct server-side ``WHERE`` predicate trims to exactly the
    five matches; a missing predicate would return the first 10
    owner rows regardless of dialogue id, masking AF-004 regressions.
    """
    registry = RunRegistry(tasks_db_path)
    for index in range(10):
        _seed(
            registry,
            run_id=f"run-noise-{index}",
            user_id="u1",
            agent="chat",
            origin="local",
            dialogue_id=f"dlg-noise-{index}",
        )
    for index in range(5):
        _seed(
            registry,
            run_id=f"run-target-{index}",
            user_id="u1",
            agent="chat",
            origin="local",
            dialogue_id="dlg-target",
        )

    response = await api_client.get(
        "/v1/runs?dialogue_id=dlg-target&limit=10",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 5
    assert {row["run_id"] for row in rows} == {
        f"run-target-{index}" for index in range(5)
    }
    for row in rows:
        assert row["dialogue_id"] == "dlg-target"


async def test_list_runs_response_row_shape(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Response rows expose dialogue/query/tool/model/answer fields."""
    del tasks_db_path

    install_chat_handler(
        monkeypatch,
        {},
        content="C3 photosynthesis fixes CO2 in the Calvin cycle.",
    )

    chat_response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "model": "phyto-chat",
            "messages": [{"role": "user", "content": "Explain C3."}],
            "dialogue_id": "dlg-resp",
        },
    )
    assert chat_response.status_code == 200

    listing = await api_client.get(
        "/v1/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert listing.status_code == 200
    rows = listing.json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["dialogue_id"] == "dlg-resp"
    assert row["tool_name"] == "ChatAgent"
    assert row["model"] == "phyto-chat"
    assert row["query"] is not None
    assert "Explain C3" in row["query"]
    assert "Calvin cycle" in row["answer"]
