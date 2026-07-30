# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert request validation and context guard tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from tests.server.test_query_route import (
    _conversation_envelope,
    _post_query_route,
)

import mcp_server_phytomni.api.app as api_app
from mcp_server_phytomni.agents.expert import ToolSelection
from mcp_server_phytomni.api.schemas import ExpertQueryRequest
from mcp_server_phytomni.config.defaults import ApiConfig

pytestmark = pytest.mark.server


def test_expert_query_request_accepts_ordered_autonomous_constraints() -> None:
    """An autonomous Expert request retains its ordered canonical tools."""
    request = ExpertQueryRequest(
        user_query="Compare drought candidates",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
    )

    assert request.allowed_tools == ["KnowledgeAgent", "ChatAgent"]
    assert request.forced_tool is None


def test_expert_query_request_accepts_member_forced_tool() -> None:
    """A forced tool is valid when it belongs to the caller allowlist."""
    request = ExpertQueryRequest(
        user_query="Compare drought candidates",
        allowed_tools=["KnowledgeAgent", "ChatAgent"],
        forced_tool="ChatAgent",
    )

    assert request.forced_tool == "ChatAgent"


def test_expert_activation_stays_outside_bot_config() -> None:
    """Expert activation remains owned by the Web gateway boundary."""
    fields = vars(ApiConfig).get("model_fields", {})
    assert "EXPERT_ENABLED" not in fields


@pytest.mark.parametrize(
    ("patch", "expected_fragment"),
    [
        ({}, "allowed_tools"),
        ({"allowed_tools": []}, "allowed_tools"),
        (
            {"allowed_tools": ["ChatAgent", "ChatAgent"]},
            "allowed_tools must contain unique canonical tool names",
        ),
        (
            {"allowed_tools": [" ChatAgent"]},
            "allowed_tools contains an unknown canonical tool",
        ),
        (
            {"allowed_tools": ["MissingAgent"]},
            "allowed_tools contains an unknown canonical tool",
        ),
        (
            {
                "allowed_tools": ["ChatAgent"],
                "forced_tool": "DataAgent",
            },
            "forced_tool must be a member of allowed_tools",
        ),
    ],
)
async def test_route_rejects_invalid_tool_constraints(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    patch: dict[str, object],
    expected_fragment: str,
) -> None:
    """Expert requests reject invalid tool allowlist constraints."""
    payload: dict[str, object] = {
        "user_query": "Compare drought candidates",
        "history": [],
        "obs_file_list": [],
        "dialogue_id": "dialogue-1",
    }
    payload.update(patch)

    response = await _post_query_route(api_client, issued_api_key, payload)

    assert response.status_code == 422
    with pytest.raises(ValidationError, match=expected_fragment):
        ExpertQueryRequest.model_validate(payload)


async def test_route_rejects_more_than_ten_allowed_tools(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """Expert requests bound the allowlist independently of uniqueness."""
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "Compare drought candidates",
            "allowed_tools": [f"Tool{index}" for index in range(11)],
        },
    )

    assert response.status_code == 422
    with pytest.raises(ValidationError, match="allowed_tools"):
        ExpertQueryRequest.model_validate(
            {
                "user_query": "Compare drought candidates",
                "allowed_tools": [f"Tool{index}" for index in range(11)],
            }
        )


async def test_context_expert_is_disabled_before_routing_or_invocation(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled V1 Expert envelope returns 404 without dispatching."""
    routed = False
    invoked = False

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        nonlocal routed
        routed = True
        raise AssertionError("disabled V1 Expert must not route")

    async def forbidden_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        nonlocal invoked
        invoked = True
        raise AssertionError("disabled V1 Expert must not invoke an agent")

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)
    payload = {
        "user_query": "legacy query is ignored by V1 dispatch",
        "allowed_tools": ["KnowledgeAgent"],
        "conversation": _conversation_envelope(
            requested_agent_id="KnowledgeAgent",
            allowed_agent_ids=["KnowledgeAgent"],
        ),
    }
    response = await _post_query_route(api_client, issued_api_key, payload)

    assert response.status_code == 404
    assert not routed
    assert not invoked


async def test_context_expert_explicit_selection_stages_without_router(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit V1 Expert invoker receives bounded native history."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit Expert selection must not route")

    captured: dict[str, Any] = {}

    async def fake_invoke(
        *,
        agent: str,
        arguments: dict[str, Any],
        conversation_messages: tuple[dict[str, str], ...] = (),
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        captured["agent"] = agent
        captured["arguments"] = arguments
        captured["conversation_messages"] = conversation_messages
        return (
            {
                "id": "context-data",
                "object": "agent.run",
                "agent": agent,
                "status": "succeeded",
                "task_ids": [],
                "result": {"formatted": {"answer": "drought result"}},
            },
            200,
        )

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    envelope = _conversation_envelope(
        requested_agent_id="DataAgent", allowed_agent_ids=["DataAgent"]
    )
    envelope["turn_id"] = "3"
    envelope["request_id"] = "request-3"
    envelope["ledger_cursor"] = 3
    envelope["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Compare drought candidates by yield.",
        },
        {
            "turn_id": "2",
            "role": "assistant",
            "content": "Yield is one comparison criterion.",
            "summary": "Yield is one comparison criterion.",
        },
        {
            "turn_id": "3",
            "role": "user",
            "content": "Compare drought candidates",
        },
    ]

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["DataAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    stage = response.json()["conversation_context"]
    assert stage["selected_agent_id"] == "DataAgent"
    assert stage["route_source"] == "explicit_selection"
    duplicate = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["DataAgent"],
            "conversation": envelope,
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == response.json()
    assert captured == {
        "agent": "data",
        "arguments": {
            "user_query": "Compare drought candidates",
            "locale": "en-US",
        },
        "conversation_messages": (
            {
                "role": "user",
                "content": "Compare drought candidates by yield.",
            },
            {
                "role": "assistant",
                "content": "Yield is one comparison criterion.",
            },
        ),
    }
