# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Data and Chat context route tests."""

from __future__ import annotations

from tests.server.test_query_route import (
    UUID,
    Any,
    ConversationContextStore,
    Path,
    SimpleNamespace,
    ToolSelection,
    _assert_data_projection,
    _context_follow_up_envelope,
    _conversation_envelope,
    _data_route_config,
    _post_query_route,
    _reject_explicit_data_router,
    api_app,
    httpx,
    mcp_handlers,
    pytest,
    server,
)
from tests.support.handler_fakes import (
    patch_chat_completion_service,
    patch_context_chat_runtime,
)

from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id as context_agent_thread_id,
)
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


async def test_context_expert_data_reuses_ids_and_stages_bounded_intent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expert Data continues intent through the real private handler seam."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    calls: list[dict[str, Any]] = []

    async def fake_rewrite_nl2sql(
        user_query: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append({"user_query": user_query, **kwargs})
        return {
            "header": [
                {"caption": "tissue"},
                {"caption": "expression"},
            ],
            "data": [["leaf", 10], ["root", 5]],
            "dataset_id": "expression",
            "table_id": "expression_table",
            "artifact_id": "artifact-expression",
            "summary": f"Aggregate for {user_query}",
            "sql": "SELECT * FROM private_table",
            "database_url": "postgresql://user:pass@example/db",
        }

    monkeypatch.setattr(
        api_app, "select_agent_tool", _reject_explicit_data_router
    )
    monkeypatch.setattr(mcp_handlers, "DataConfig", _data_route_config)
    monkeypatch.setattr(
        mcp_handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(sensitive=object()),
    )
    monkeypatch.setattr(
        mcp_handlers, "chat_kwargs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(mcp_handlers, "rewrite_nl2sql", fake_rewrite_nl2sql)

    conversation_key = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
    expected_thread_id = context_agent_thread_id(conversation_key, "DataAgent")
    store = ConversationContextStore(str(db_path))

    first = _conversation_envelope(
        requested_agent_id="DataAgent",
        allowed_agent_ids=["DataAgent"],
    )
    first["current_message"]["content"] = "Show expression by tissue"
    first["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Show expression by tissue",
        }
    ]
    first["artifact_refs"] = [
        {
            "artifact_id": "artifact-expression",
            "display_name": "expression.csv",
        }
    ]

    first_status = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["DataAgent"],
            "conversation": first,
        },
    )

    assert first_status.status_code == 200
    _assert_data_projection(
        store,
        conversation_key,
        "1",
        (
            "data:dataset:expression",
            "data:table:expression_table",
            "dimension:tissue",
            "column:expression",
            "row_count:2",
            "artifact-expression",
        ),
        (
            "leaf",
            "SELECT * FROM private_table",
            "postgresql://user:pass@example/db",
        ),
    )
    store.commit_staged_turn(
        str(conversation_key),
        "1",
        first["ledger_version"],
        "b" * 64,
    )

    second = _context_follow_up_envelope(
        "DataAgent",
        "Only rice",
        artifact_refs=first["artifact_refs"],
    )

    second_status = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["DataAgent"],
            "conversation": second,
        },
    )

    assert second_status.status_code == 200
    assert [call["user_query"] for call in calls] == [
        "Show expression by tissue",
        "Show expression by tissue for rice",
    ]
    assert [call["thread_id"] for call in calls] == [
        expected_thread_id,
        expected_thread_id,
    ]
    assert [call["dialog_id"] for call in calls] == [
        f"{expected_thread_id}-nl2sql",
        f"{expected_thread_id}-nl2sql",
    ]
    _assert_data_projection(
        store,
        conversation_key,
        "2",
        ("data:dataset:expression", "dimension:tissue", "filter:species=rice"),
        (
            "leaf",
            "SELECT * FROM private_table",
            "postgresql://user:pass@example/db",
        ),
    )


async def test_context_expert_chat_keeps_thread_private_to_primary_call(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit Chat keeps the stable thread private to the first call."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))
    captured: list[dict[str, Any]] = []

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        captured.append(dict(kwargs))
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Chat answer" if len(captured) == 1 else "[]"
                        )
                    }
                }
            ]
        }

    patch_context_chat_runtime(monkeypatch, include_obs_file_list=True)
    patch_chat_completion_service(monkeypatch, fake_phyto_chat)
    envelope = _conversation_envelope(
        requested_agent_id="ChatAgent",
        allowed_agent_ids=["ChatAgent"],
    )
    envelope["turn_id"] = "7"
    envelope["request_id"] = "request-7"
    envelope["ledger_cursor"] = 7
    envelope["current_message"]["content"] = "What about its drought response?"
    envelope["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Tell me about rice gene OsDREB1A.",
        },
        {
            "turn_id": "2",
            "role": "assistant",
            "content": (
                "OsDREB1A is a rice stress-response " "transcription factor."
            ),
            "summary": (
                "OsDREB1A is a rice stress-response " "transcription factor."
            ),
        },
        {
            "turn_id": "7",
            "role": "user",
            "content": "What about its drought response?",
        },
    ]

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ChatAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    assert response.json()["conversation_context"]["selected_agent_id"] == (
        "ChatAgent"
    )
    expected_thread_id = context_agent_thread_id(
        UUID(envelope["conversation_key"]),
        "ChatAgent",
    )
    assert captured[0] == {
        "user_query": "What about its drought response?",
        "locale": "en-US",
        "obs_file_list": [],
        "semaphore": None,
        "conversation_messages": (
            {"role": "user", "content": "Tell me about rice gene OsDREB1A."},
            {
                "role": "assistant",
                "content": (
                    "OsDREB1A is a rice stress-response transcription factor."
                ),
            },
        ),
        "thread_id": expected_thread_id,
    }
    assert captured[1] == {
        "user_query": "follow-up",
        "locale": "en-US",
        "semaphore": None,
        "prompt_file": chat_service.CHAT_CONFIG.PROMPT_FILE,
        "conversation_messages": (
            {"role": "user", "content": "Tell me about rice gene OsDREB1A."},
            {
                "role": "assistant",
                "content": (
                    "OsDREB1A is a rice stress-response transcription factor."
                ),
            },
        ),
    }
    assert "thread_id" not in captured[1]


async def test_context_expert_router_keeps_full_allowlist_and_async_202(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Automatic V1 routing stages its accepted async turn."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))
    received: list[str] = []
    received_history: tuple[dict[str, str], ...] = ()
    allowed = [
        "ChatAgent",
        "DataAgent",
        "AnalystAgent",
        "DeepGenomeAgent",
        "InSilicoResearchAgent",
        "DigitalDesignAgent",
        "GeneNetworkAgent",
    ]

    async def select(
        _query: str, _history: Any, *, allowed_tools: Any, forced_tool: Any
    ) -> ToolSelection:
        nonlocal received_history
        received.extend(allowed_tools)
        received_history = _history
        assert forced_tool is None
        return ToolSelection(
            "AnalystAgent",
            {
                "goal_description": "assemble",
                "data_list": {},
                "obs_file_list": [],
            },
        )

    async def submit(_args: Any) -> dict[str, Any]:
        return {"task_id": "T-context", "output_dir": "/obs/context"}

    monkeypatch.setattr(api_app, "select_agent_tool", select)
    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(submit),
    )
    envelope = _conversation_envelope(allowed_agent_ids=allowed)
    envelope["turn_id"] = "5"
    envelope["request_id"] = "request-5"
    envelope["ledger_cursor"] = 5
    envelope["history_delta"] = [
        {"turn_id": "1", "role": "user", "content": "U1"},
        {
            "turn_id": "2",
            "role": "assistant",
            "content": "A1",
            "summary": "A1",
        },
        {"turn_id": "3", "role": "user", "content": "U2"},
        {
            "turn_id": "4",
            "role": "assistant",
            "content": "A2",
            "summary": "A2",
        },
        {"turn_id": "5", "role": "user", "content": "U3"},
    ]
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query",
            "allowed_tools": allowed,
            "conversation": envelope,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["conversation_context"]["selected_agent_id"] == (
        "AnalystAgent"
    )
    assert body["conversation_context"]["route_source"] == "router"
    assert body["conversation_context"]["route_reason_code"] == (
        "ROUTER_SELECTED"
    )
    assert received == allowed
    assert received_history == (
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "U3"},
    )
