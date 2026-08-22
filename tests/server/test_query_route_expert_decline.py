# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unforced Expert decline falls back to ChatAgent when chat is allowed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from tests.server.test_query_route import (
    RunRegistry,
    _post_query_route,
    _router_completion,
    api_app,
    httpx,
    pytest,
)
from tests.support.expert_router_fakes import patch_expert_router

from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.mcp.formatting.agui import run_finished, run_started

pytestmark = pytest.mark.server


async def test_route_strict_decline_dispatches_chat_when_allowed(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """An unforced decline becomes a ChatAgent dispatch when chat is allowed.

    The real router raises ``ExpertRoutingDeclinedError`` when the provider
    returns no tool call. That is plain chat, not a contract fault, so the
    route dispatches ChatAgent with the original query and records a run.
    """
    captured: dict[str, Any] = {}
    started: list[dict[str, Any]] = []

    async def prepared_stream(
        selected_tool: str,
        selected_arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
        **_kwargs: Any,
    ) -> AsyncIterator[Any]:
        started.append(
            {"tool": selected_tool, "arguments": selected_arguments}
        )
        captured["user_query"] = selected_arguments["user_query"]
        yield run_started(run_id, dialogue_id)
        yield run_finished(run_id)

    monkeypatch.setattr(api_app, "prepare_tool_stream", prepared_stream)
    patch_expert_router(
        monkeypatch, expert_router, _router_completion(empty_choices=True)
    )

    query = "what is photosynthesis"
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {"user_query": query, "allowed_tools": ["ChatAgent", "DataAgent"]},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["agent"] == "chat"
    assert body["object"] == "agent.run"
    assert captured["user_query"] == query
    assert started[0]["tool"] == "ChatAgent"
    record = RunRegistry(tasks_db_path).get_run(body["run_id"], owner="u1")
    assert record is not None
    assert record.spec.agent == "chat"


async def test_route_strict_decline_without_chat_returns_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A decline stays a sanitized 502 when ChatAgent is not allowed."""
    captured: dict[str, Any] = {}
    patch_expert_router(
        monkeypatch, expert_router, _router_completion(empty_choices=True)
    )

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "allowed_tools": ["KnowledgeAgent", "DataAgent"],
            "user_query": "explain chlorophyll",
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == ("routing_contract_violation")
    assert response.json()["error"]["stage"] == "routing"
    assert response.json()["error"]["retryable"] is False
    assert not captured
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")
