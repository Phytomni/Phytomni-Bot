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

import httpx
import pytest

from mcp_server_phytomni import server
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


def _seed(registry: RunRegistry, **kwargs: str) -> str:
    """Create one terminal run with the given spec fields."""
    spec = RunSpec(
        run_id=kwargs["run_id"],
        user_id=kwargs["user_id"],
        agent=kwargs["agent"],
        origin=kwargs["origin"],
    )
    registry.create_run(spec, status="succeeded", result={"ok": True})
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
        json={"arguments": {"user_query": "hi", "obs_file_list": []}},
    )
    assert response.status_code == 200
    assert RunRegistry(tasks_db_path).get_run("run-stale", owner="u1") is None
