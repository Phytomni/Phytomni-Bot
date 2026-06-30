# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the autonomous Expert routing route ``POST /v1/query/route``.

The routing LLM is always mocked (``select_agent_tool`` patched), so the
suite stays offline. Covers the resolved-slug + formatted envelope (HR-1 /
HR-2), the remote running/task_ids shape (HR-3), the chat fallback, the
obs-injection gate, auth, and the forced_tool / unknown-tool / invalid-arg
error paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

import mcp_server_phytomni.api.app as api_app
from mcp_server_phytomni import server
from mcp_server_phytomni.agents.expert import ToolSelection
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


def _patch_select(
    monkeypatch: pytest.MonkeyPatch, selection: ToolSelection | None
) -> None:
    """Patch the in-process router to return a fixed selection."""

    async def fake_select(
        user_query: str, history: Any = ()
    ) -> ToolSelection | None:
        _ = (user_query, history)
        return selection

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)


def _auth(key: str) -> dict[str, str]:
    """Return the bearer auth header for a key."""
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(name="scoped_key_without_agents")
def _scoped_key_without_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Return a valid key whose scopes do not include ``agents``."""
    db = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    return ApiKeyStore(db).create(user_id="u9", scopes=["files"]).api_key


async def test_route_sync_agent_returns_resolved_slug(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A routed sync agent returns its resolved slug + formatted envelope.

    Locks HR-1 (``agent`` is the resolved slug, never ``"expert"``) and
    HR-2 (the ``result.formatted`` block ships, with the ``references``
    key present for the cited KnowledgeAgent). Also confirms the verbatim
    obs attachment reaches the obs-capable knowledge tool.
    """
    captured: dict[str, Any] = {}

    async def fake(args: Any) -> dict[str, Any]:
        captured["args"] = args
        return {"answer": "rice answer", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.KNOWLEDGE_AGENT.value,
        fake,
    )
    _patch_select(
        monkeypatch,
        ToolSelection("KnowledgeAgent", {"user_query": "rice drought"}),
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "rice drought", "obs_file_list": ["/obs/x.pdf"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "agent.run"
    assert body["agent"] == "knowledge"
    assert body["status"] == "succeeded"
    assert body["task_ids"] == []
    formatted = body["result"]["formatted"]
    assert "answer" in formatted
    assert "references" in formatted
    # The verbatim attachment reached the obs-capable tool.
    assert captured["args"].obs_file_list == ["/obs/x.pdf"]
    assert captured["args"].user_query == "rice drought"

    record = RunRegistry(tasks_db_path).list_runs(owner="u1")[0]
    assert record.spec.agent == "knowledge"
    assert record.spec.origin == "local"


async def test_route_remote_agent_returns_running_task_ids(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A routed remote agent returns 202 + running + task_ids (HR-3)."""

    async def fake(args: Any) -> dict[str, Any]:
        _ = args
        return {"task_id": "T-A", "output_dir": "/obs/a"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )
    _patch_select(
        monkeypatch,
        ToolSelection(
            "AnalystAgent",
            {
                "goal_description": "assemble",
                "data_list": {},
                "obs_file_list": [],
            },
        ),
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "assemble a genome"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == "analyst"
    assert body["status"] == "running"
    assert set(body["task_ids"]) == {"T-A"}
    assert body["id"]

    record = RunRegistry(tasks_db_path).list_runs(owner="u1")[0]
    assert record.spec.agent == "analyst"
    assert record.spec.origin == "remote"


async def test_route_no_tool_falls_back_to_chat(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """When the router selects no tool the query falls back to chat."""

    async def fake(args: Any) -> dict[str, Any]:
        _ = args
        return {"answer": "ok", "doc_list": []}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )
    _patch_select(monkeypatch, None)

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "hello there"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "chat"
    assert body["status"] == "succeeded"

    record = RunRegistry(tasks_db_path).list_runs(owner="u1")[0]
    assert record.spec.agent == "chat"


async def test_route_injects_obs_only_for_obs_capable_tool(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """obs_file_list is injected for knowledge but not for data.

    Pins the ``tool_accepts_obs`` gate: only chat / knowledge / review
    receive the attachment. Patches ``_invoke_agent_run`` to capture the
    exact arguments the route assembled, independent of model extra-field
    behavior.
    """
    captured: dict[str, dict[str, Any]] = {}

    async def fake_invoke(
        *, agent: str, arguments: dict[str, Any], **_kwargs: Any
    ) -> tuple[dict[str, Any], int]:
        captured[agent] = arguments
        return (
            {
                "id": "r1",
                "object": "agent.run",
                "agent": agent,
                "status": "succeeded",
                "task_ids": [],
                "result": {"formatted": {}},
            },
            200,
        )

    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)

    _patch_select(
        monkeypatch, ToolSelection("KnowledgeAgent", {"user_query": "q"})
    )
    await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "q", "obs_file_list": ["/obs/x.pdf"]},
    )
    assert captured["knowledge"]["obs_file_list"] == ["/obs/x.pdf"]

    _patch_select(monkeypatch, ToolSelection("DataAgent", {"user_query": "q"}))
    await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "q", "obs_file_list": ["/obs/x.pdf"]},
    )
    assert "obs_file_list" not in captured["data"]


async def test_route_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """An unauthenticated caller sees the unified 401."""
    response = await api_client.post(
        "/v1/query/route", json={"user_query": "hi"}
    )
    assert response.status_code == 401


async def test_route_insufficient_scope_returns_403(
    api_client: httpx.AsyncClient,
    scoped_key_without_agents: str,
) -> None:
    """A valid key lacking the ``agents`` scope is rejected with 403."""
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(scoped_key_without_agents),
        json={"user_query": "hi"},
    )
    assert response.status_code == 403


async def test_route_forced_tool_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """``forced_tool`` is accepted but unsupported in v1 -> 400."""
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "hi", "forced_tool": "ChatAgent"},
    )
    assert response.status_code == 400


async def test_route_unknown_tool_returns_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool outside the agent set (e.g. GetTaskStatus) -> 502."""
    _patch_select(
        monkeypatch, ToolSelection("GetTaskStatus", {"task_id": "T-1"})
    )
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "status?"},
    )
    assert response.status_code == 502


async def test_route_invalid_arguments_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM-extracted arguments that fail the agent schema -> 400.

    The KnowledgeAgent schema requires ``user_query``; an empty argument
    object makes ``invoke_tool_enveloped`` raise ``McpError`` with
    ``INVALID_PARAMS``, which the route maps to 400 rather than letting it
    fall through to the generic 500 handler.
    """
    _patch_select(monkeypatch, ToolSelection("KnowledgeAgent", {}))
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "rice"},
    )
    assert response.status_code == 400
