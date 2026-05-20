# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the native ``/v1/agents`` and ``/v1/agents/{slug}/runs``.

Covers listing, auth enforcement, slug-not-found, sync-agent run with
``origin="local"`` written here, and remote-agent run with the
chokepoint-minted ``origin="remote"`` run_id read back via
``tasks.run_id``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.mcp.handlers import _records_submission
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


async def test_list_agents_returns_all_ten(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """List both sync and remote agents with their tool and origin."""
    response = await api_client.get(
        "/v1/agents",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    slugs = {row["slug"] for row in body["data"]}
    assert slugs == {
        "chat",
        "knowledge",
        "data",
        "review",
        "brief_gene",
        "analyst",
        "deep_genome",
        "research",
        "design",
        "network",
    }
    origins = {row["slug"]: row["origin"] for row in body["data"]}
    assert origins["chat"] == "local"
    assert origins["analyst"] == "remote"


async def test_list_agents_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """Unauthorized callers see the unified 401 envelope."""
    response = await api_client.get("/v1/agents")
    assert response.status_code == 401


async def test_agent_run_unknown_slug_returns_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """An unknown slug is rejected with a 404 envelope."""
    response = await api_client.post(
        "/v1/agents/mystery/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"arguments": {}},
    )
    assert response.status_code == 404


async def test_agent_run_sync_writes_local_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A sync-agent invocation returns the agent.run envelope at 200."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a stub chat completion-shaped result."""
        _ = args
        return {"answer": "ok", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )

    response = await api_client.post(
        "/v1/agents/chat/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"arguments": {"user_query": "hi", "obs_file_list": []}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "agent.run"
    assert body["agent"] == "chat"
    assert body["status"] == "succeeded"
    assert body["task_ids"] == []
    assert body["id"]
    assert body["result"] is not None

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    record = listing[0]
    assert record.spec.run_id == body["id"]
    assert record.spec.agent == "chat"
    assert record.spec.origin == "local"
    assert record.status == "succeeded"


async def test_agent_run_remote_returns_chokepoint_run_id(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A remote-agent invocation returns 202 with task_ids and run id."""

    async def fake(args: Any) -> dict[str, Any]:
        """Return a stub remote-submission result with a task_id."""
        _ = args
        return {"task_id": "task-fake-1", "output_dir": "/obs/run"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        _records_submission("analyst")(fake),
    )

    response = await api_client.post(
        "/v1/agents/analyst/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "goal_description": "test",
                "data_list": {},
                "obs_file_list": [],
            }
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["object"] == "agent.run"
    assert body["agent"] == "analyst"
    assert body["status"] == "running"
    assert body["task_ids"] == ["task-fake-1"]
    assert body["id"]
    assert body["result"]["metadata"]["task_id"] == "task-fake-1"

    listing = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(listing) == 1
    record = listing[0]
    assert record.spec.run_id == body["id"]
    assert record.spec.agent == "analyst"
    assert record.spec.origin == "remote"
    assert record.task_ids == ("task-fake-1",)
