# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unforced Expert decline falls back to ChatAgent when chat is allowed."""

from __future__ import annotations

from tests.server.test_query_route import (
    Any,
    RunRegistry,
    _post_query_route,
    _router_completion,
    httpx,
    pytest,
)
from tests.support.chat_fakes import install_chat_handler
from tests.support.expert_router_fakes import patch_expert_router

from mcp_server_phytomni.agents.expert import router as expert_router

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
    install_chat_handler(monkeypatch, captured, content="declined to chat")
    patch_expert_router(
        monkeypatch, expert_router, _router_completion(empty_choices=True)
    )

    query = "what is photosynthesis"
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {"user_query": query, "allowed_tools": ["ChatAgent", "DataAgent"]},
    )

    assert captured["user_query"] == query
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["agent"] == "chat"
    assert body["object"] == "agent.run"
    record = RunRegistry(tasks_db_path).list_runs(owner="u1")[0]
    assert record.spec.agent == "chat"


async def test_route_strict_decline_without_chat_returns_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A decline stays a sanitized 502 when ChatAgent is not allowed."""
    captured: dict[str, Any] = {}
    install_chat_handler(monkeypatch, captured, content="declined to chat")
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
