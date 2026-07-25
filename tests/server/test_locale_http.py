# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP locale ingress, persistence, and A2UI inheritance tests."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from tests.support.a2ui_contract_fakes import (
    chat_terminal_state,
    confirm_surface,
)
from tests.support.chat_fakes import (
    ChatCompletionOptions,
    chat_completion_payload,
)
from tests.support.http_fakes import install_tool_handler

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.expert import ToolSelection
from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.app_support import (
    _ErrorResponseOptions,
    error_response,
)
from mcp_server_phytomni.runtime.locale import (
    SupportedLocale,
    bind_effective_locale,
    current_effective_locale,
)
from mcp_server_phytomni.runtime.request_context import reset_request_var
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    local_run_spec,
)

pytestmark = pytest.mark.server


def _auth(api_key: str) -> dict[str, str]:
    """Return the bearer header used by the API fixtures."""
    return {"Authorization": f"Bearer {api_key}"}


def _chat_result() -> dict[str, Any]:
    """Return a minimal successful ChatAgent provider payload."""
    return chat_completion_payload(
        "chatcmpl-locale",
        "done",
        ChatCompletionOptions(follow_up_questions=[]),
    )


async def test_chat_body_locale_beats_header_and_persists(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat body locale wins over Accept-Language and reaches the run row."""
    captured: dict[str, Any] = {}

    async def fake(args: Any) -> dict[str, Any]:
        captured["locale"] = args.locale
        return _chat_result()

    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
    )
    response = await api_client.post(
        "/v1/chat/completions",
        headers={
            **_auth(issued_api_key),
            "Accept-Language": "en-US",
        },
        json={
            "model": "phyto-chat",
            "messages": [{"role": "user", "content": "中文问题"}],
            "locale": "zh-CN",
        },
    )

    assert response.status_code == 200
    assert captured["locale"] == "zh-CN"
    run_id = response.json()["run_id"]
    record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
    assert record is not None
    assert record.request_info.locale == "zh-CN"


async def test_native_body_locale_beats_header(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native agent runs inject the resolved body locale into arguments."""
    captured: dict[str, Any] = {}

    async def fake(args: Any) -> dict[str, Any]:
        captured["locale"] = args.locale
        return _chat_result()

    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
    )
    response = await api_client.post(
        "/v1/agents/chat/runs",
        headers={
            **_auth(issued_api_key),
            "Accept-Language": "en-US",
        },
        json={
            "arguments": {
                "user_query": "中文问题",
                "obs_file_list": [],
            },
            "locale": "zh-CN",
        },
    )

    assert response.status_code == 200
    assert captured["locale"] == "zh-CN"


@pytest.mark.parametrize(
    "case",
    [
        ("en-GB, zh-CN;q=0.9", "中文问题", "en-US"),
        ("fr-FR, zh-TW;q=0.9", "English question", "zh-CN"),
        ("fr-FR", "中文问题", "zh-CN"),
        (None, "English question", "en-US"),
    ],
)
async def test_locale_falls_back_from_header_to_latest_query(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str | None, str, SupportedLocale],
) -> None:
    """Header normalization precedes latest-query language inference."""
    accept_language, query, expected = case
    captured: dict[str, Any] = {}

    async def fake(args: Any) -> dict[str, Any]:
        captured["locale"] = args.locale
        return _chat_result()

    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
    )
    headers = _auth(issued_api_key)
    if accept_language is not None:
        headers["Accept-Language"] = accept_language
    response = await api_client.post(
        "/v1/agents/chat/runs",
        headers=headers,
        json={
            "arguments": {
                "user_query": query,
                "obs_file_list": [],
            }
        },
    )

    assert response.status_code == 200
    assert captured["locale"] == expected


async def test_expert_body_locale_reaches_selected_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expert routing binds body locale before selecting and dispatching."""
    captured: dict[str, Any] = {}

    async def fake_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        return ToolSelection("ChatAgent", {"user_query": "route me"})

    async def fake(args: Any) -> dict[str, Any]:
        captured["locale"] = args.locale
        return _chat_result()

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)
    install_tool_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value, fake
    )
    response = await api_client.post(
        "/v1/query/route",
        headers={
            **_auth(issued_api_key),
            "Accept-Language": "en-US",
        },
        json={
            "user_query": "route me",
            "allowed_tools": ["ChatAgent"],
            "locale": "zh-CN",
        },
    )

    assert response.status_code == 200
    assert captured["locale"] == "zh-CN"


async def test_unsupported_body_locale_is_422(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """An unsupported body locale uses the stable validation error code."""
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "route me",
            "allowed_tools": ["ChatAgent"],
            "locale": "fr-FR",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_locale"


def test_error_response_localizes_fixed_codes_but_preserves_dynamic_text() -> (
    None
):
    """Safe fixed errors translate without rewriting identifiers."""
    token = bind_effective_locale("zh-CN")
    try:
        fixed = error_response(
            400,
            "invalid request",
            options=_ErrorResponseOptions(code="invalid_request"),
        )
        dynamic = error_response(404, "run not found: run-locale-123")
    finally:
        reset_request_var(token)

    assert json.loads(bytes(fixed.body))["error"]["message"] == "请求无效。"
    assert (
        json.loads(bytes(dynamic.body))["error"]["message"]
        == "run not found: run-locale-123"
    )


def _seed_a2ui_run(
    tasks_db_path: str,
    *,
    run_id: str,
    locale: SupportedLocale | None,
    query: str,
) -> None:
    """Create a paused Chat run with current or legacy locale metadata."""
    result = {
        "interrupt": {
            "thread_id": run_id,
            "draft": {
                "a2ui": confirm_surface(f"{run_id}-surface"),
            },
        },
        "status": "input_required",
    }
    RunRegistry(tasks_db_path).create_run(
        local_run_spec(run_id, "u1", "chat"),
        outcome=RunOutcome(status="input_required", result=result),
        request_info=RunRequestInfo(query=query, locale=locale),
    )


@pytest.mark.parametrize(
    "case",
    [
        ("zh-CN", "English query", "zh-CN"),
        (None, "中文查询", "zh-CN"),
    ],
)
async def test_a2ui_action_inherits_or_backfills_locale(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[SupportedLocale | None, str, SupportedLocale],
) -> None:
    """A2UI action ignores headers and backfills legacy null locale rows."""
    stored_locale, query, expected_locale = case
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = f"run-locale-{expected_locale}-{stored_locale or 'legacy'}"
    _seed_a2ui_run(
        tasks_db_path,
        run_id=run_id,
        locale=stored_locale,
        query=query,
    )

    async def has_checkpoint(_app: Any, _thread_id: str) -> bool:
        return True

    async def resume(
        _app: Any,
        _thread_id: str,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        assert current_effective_locale() == expected_locale
        return {
            **chat_terminal_state(),
            "a2ui_surface": confirm_surface(f"{run_id}-surface"),
        }

    monkeypatch.setattr(api_app, "_has_graph_checkpoint", has_checkpoint)

    def chat_graph() -> object:
        """Return a graph sentinel for the resumed action."""
        return object()

    monkeypatch.setattr(api_app, "_chat_a2ui_stream_app", chat_graph)
    monkeypatch.setattr(api_app, "_resume_paused_run", resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={
            **_auth(issued_api_key),
            "Accept-Language": "en-US",
        },
        json={
            "run_id": run_id,
            "surface_id": f"{run_id}-surface",
            "widget": "confirm",
            "action_id": f"action-{run_id}",
            "payload": {"accepted": True},
        },
    )

    assert response.status_code == 200
    record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
    assert record is not None
    assert record.request_info.locale == expected_locale
