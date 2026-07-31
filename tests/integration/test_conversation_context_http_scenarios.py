# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP and retry boundary scenarios for conversation continuity."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_conversation_context_scenarios import (
    _ARTIFACT_A,
    _ARTIFACT_B,
    _CANONICAL_AGENT_IDS,
    _async_acceptance_delegate,
    _commit,
    _conversation_key,
    _envelope,
    _outcome,
    _service,
)
from tests.support.chat_fakes import install_chat_handler
from tests.support.http_fakes import open_asgi_client

import mcp_server_phytomni.api.app as api_app
from mcp_server_phytomni.agents.expert import ToolSelection
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.runtime.conversation_context.adapters import (
    ConversationContextExecutor,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
    AgentSelection,
    AsyncAgentAcceptance,
    PrepareStatus,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)

pytestmark = pytest.mark.server


def _context_app(executor: ConversationContextExecutor) -> Any:
    """Build a context-enabled app without repeating route setup syntax."""
    return create_app(context_executor=executor)


async def test_permission_revocation_blocks_explicit_and_automatic_selection(
    tmp_path: Path,
) -> None:
    """A fresh allowlist prevents forced and automatic agent selection."""
    invoked = 0

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("KnowledgeAgent", "ROUTER")

    async def invoke(*_args: Any, **_kwargs: Any) -> AgentOutcome:
        nonlocal invoked
        invoked += 1
        return _outcome("KnowledgeAgent")

    delegate = _async_acceptance_delegate()

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(5)
    established = _envelope(
        key=key,
        turn_id="1",
        message="Establish bounded evidence.",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=("KnowledgeAgent", "ChatAgent"),
    )
    await _commit(service, established)
    with pytest.raises(ValueError, match="requested_agent_id must be allowed"):
        _envelope(
            key=key,
            turn_id="2",
            message="Use the revoked agent.",
            requested_agent_id="KnowledgeAgent",
            allowed_agent_ids=("ChatAgent",),
            base_version=1,
        )
    automatic = _envelope(
        key=key,
        turn_id="2",
        message="Route this with the current permission set.",
        allowed_agent_ids=("ChatAgent",),
        base_version=1,
    )
    with pytest.raises(ValueError, match="outside the envelope allowlist"):
        await service.execute_turn(automatic)
    assert invoked == 1
    failed = service.store.load_turn(str(key), "2")
    assert failed is not None
    assert failed.state == "failed"


async def test_bot_restart_rebuilds_from_bounded_go_summaries_and_runs_once(
    tmp_path: Path,
) -> None:
    """A fresh store asks for rebuild, then executes the rebuilt turn once."""
    invoked = 0

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, _projection: Any
    ) -> AgentOutcome:
        nonlocal invoked
        invoked += 1
        return _outcome(agent)

    delegate = _async_acceptance_delegate()

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(6)
    stale = _envelope(
        key=key,
        turn_id="2",
        message="Continue from the bounded summary.",
        base_version=1,
        history=[
            {"turn_id": "1", "role": "user", "content": "First bounded turn."},
            {
                "turn_id": "2",
                "role": "assistant",
                "summary": "Prior bounded summary.",
            },
            {
                "turn_id": "2",
                "role": "user",
                "content": "Continue from the bounded summary.",
            },
        ],
    )
    first = await service.execute_turn(stale)
    assert first.status is PrepareStatus.REBUILD_REQUIRED
    assert invoked == 0

    rebuilt = _envelope(
        key=key,
        turn_id="3",
        message="Continue after rebuild.",
        operation="rebuild",
        history=[
            {"turn_id": "1", "role": "user", "content": "First bounded turn."},
            {
                "turn_id": "2",
                "role": "assistant",
                "summary": "Prior bounded summary.",
            },
            {
                "turn_id": "3",
                "role": "user",
                "content": "Continue after rebuild.",
            },
        ],
    )
    prepared = await service.execute_turn(rebuilt)

    assert prepared.status is PrepareStatus.RETURN_STAGED
    assert prepared.stage is not None
    assert prepared.stage.context_rebuilt is True
    assert invoked == 1


async def test_browser_and_go_retry_reuses_one_staged_turn_and_invocation(
    tmp_path: Path,
) -> None:
    """A changed transport request ID cannot duplicate a stable turn ID."""
    invoked = 0

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, _projection: Any
    ) -> AgentOutcome:
        nonlocal invoked
        invoked += 1
        return _outcome(agent)

    delegate = _async_acceptance_delegate()

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(7)
    envelope = _envelope(key=key, turn_id="1", message="Execute once.")
    await _commit(service, envelope)
    retry = envelope.model_copy(update={"request_id": "request-retry"})
    duplicate = await service.execute_turn(retry)

    assert duplicate.status is PrepareStatus.RETURN_COMMITTED
    assert duplicate.result == {"status": "succeeded", "agent": "ChatAgent"}
    assert invoked == 1


async def test_chat_cancellation_fails_turn_without_assistant_summary(
    tmp_path: Path,
) -> None:
    """Cancellation before terminal output never stages assistant context."""

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(*_args: Any, **_kwargs: Any) -> AgentOutcome:
        raise asyncio.CancelledError

    async def delegate(*_args: Any, **_kwargs: Any) -> AsyncAgentAcceptance:
        raise AssertionError("Chat cancellation must not delegate")

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(8)
    envelope = _envelope(
        key=key, turn_id="1", message="Cancel this chat turn."
    )
    with pytest.raises(asyncio.CancelledError):
        await service.execute_turn(envelope)

    stored = service.store.load_turn(str(key), "1")
    assert stored is not None
    assert stored.state == "failed"
    assert service.store.load_context(str(key)) is None


async def test_cross_owner_boundary_isolates_dialogues_and_artifacts(
    tmp_path: Path,
) -> None:
    """An owner-scoped gateway rejects foreign keys before Bot execution."""
    captured: list[tuple[str, list[str]]] = []

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, projection: Any
    ) -> AgentOutcome:
        captured.append(
            (agent, [item.artifact_id for item in projection.artifact_refs])
        )
        return _outcome(agent)

    delegate = _async_acceptance_delegate()

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    owner_keys = {
        "owner-a": _conversation_key(9),
        "owner-b": _conversation_key(10),
    }

    async def gateway(owner: str, envelope: ConversationEnvelopeV1) -> Any:
        if owner_keys.get(owner) != envelope.conversation_key:
            raise PermissionError("dialogue is not owned by caller")
        return await service.execute_turn(envelope)

    owner_a_turn = _envelope(
        key=owner_keys["owner-a"],
        turn_id="1",
        message="Owner A request.",
        artifacts=(_ARTIFACT_A,),
    )
    await gateway("owner-a", owner_a_turn)
    foreign = owner_a_turn.model_copy(update={"request_id": "request-foreign"})
    with pytest.raises(PermissionError, match="not owned"):
        await gateway("owner-b", foreign)
    owner_b_turn = _envelope(
        key=owner_keys["owner-b"],
        turn_id="1",
        message="Owner B request.",
        artifacts=(_ARTIFACT_B,),
    )
    await gateway("owner-b", owner_b_turn)

    assert captured == [
        ("ChatAgent", ["artifact-owner-a"]),
        ("ChatAgent", ["artifact-owner-b"]),
    ]


async def test_async_expert_selection_keeps_running_202_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The public Expert route maps an async selection to HTTP 202."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "conversation.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite")).create(user_id="u1").api_key
    )
    observed: dict[str, Any] = {}

    async def select_agent(
        _query: str,
        _history: Any,
        *,
        allowed_tools: Any,
        forced_tool: Any,
    ) -> ToolSelection:
        observed["allowed_tools"] = tuple(allowed_tools)
        observed["forced_tool"] = forced_tool
        return ToolSelection(
            "AnalystAgent",
            {
                "goal_description": "Submit the bounded analysis.",
                "data_list": {},
                "obs_file_list": [],
            },
        )

    async def fake_invoke_agent_run(
        *,
        agent: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        observed.update(
            agent=agent,
            arguments=arguments,
            conversation_messages=kwargs["conversation_messages"],
        )
        return (
            {
                "id": "run-opaque",
                "run_id": "run-opaque",
                "object": "agent.run",
                "agent": agent,
                "status": "running",
                "task_ids": ["task-opaque"],
                "result": {"formatted": {}},
            },
            202,
        )

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(str(db_path)),
        select_agent=select_agent,
    )
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke_agent_run)
    envelope = _envelope(
        key=_conversation_key(11),
        turn_id="1",
        message="Submit the bounded analysis.",
        allowed_agent_ids=_CANONICAL_AGENT_IDS,
    )
    async with open_asgi_client(
        monkeypatch,
        _context_app(executor),
        base_url="http://api.context.test",
    ) as client:
        response = await client.post(
            "/v1/query/route",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "user_query": "legacy query is ignored by V1 dispatch",
                "allowed_tools": list(_CANONICAL_AGENT_IDS),
                "conversation": envelope.model_dump(mode="json"),
            },
        )
        settled = await client.post(
            "/v1/conversation-context/settle",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "schema_version": 1,
                "conversation_key": str(envelope.conversation_key),
                "turn_id": envelope.turn_id,
                "ledger_version": envelope.ledger_version,
            },
        )

    assert response.status_code == 202
    expected_result = empty_execution_projection()
    expected_result["execution"]["tasks"] = [
        {"id": "task-opaque", "accepted": True}
    ]
    body = response.json()
    context = body.pop("conversation_context")
    assert body == {
        "id": "run-opaque",
        "run_id": "run-opaque",
        "object": "agent.run",
        "agent": "analyst",
        "status": "running",
        "task_ids": ["task-opaque"],
        "result": expected_result,
    }
    assert context["schema_version"] == 1
    assert context["turn_id"] == "1"
    assert context["selected_agent_id"] == "AnalystAgent"
    assert context["route_source"] == "router"
    assert context["route_reason_code"] == "ROUTER_SELECTED"
    assert context["base_business_context_version"] == 0
    assert context["proposed_business_context_version"] == 1
    assert context["last_applied_ledger_cursor"] == 1
    assert context["context_truncated"] is False
    assert context["context_rebuilt"] is True
    assert context["context_degraded"] is False
    assert {
        key: observed[key] for key in ("allowed_tools", "forced_tool")
    } == {
        "allowed_tools": _CANONICAL_AGENT_IDS,
        "forced_tool": None,
    }
    assert {
        key: observed[key]
        for key in ("agent", "arguments", "conversation_messages")
    } == {
        "agent": "analyst",
        "arguments": {
            "goal_description": "Submit the bounded analysis.",
            "data_list": {},
            "obs_file_list": [],
            "locale": "en-US",
        },
        "conversation_messages": (),
    }

    assert settled.status_code == 200
    assert settled.json() == {
        "schema_version": 1,
        "state": "committed",
        "context_version": 1,
    }


async def test_explicit_async_expert_selection_records_explicit_route_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicitly requested async agent bypasses the Expert router."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "conversation.sqlite"
    keys_path = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_path))
    key = ApiKeyStore(str(keys_path)).create(user_id="u1").api_key

    async def forbidden_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit selection must not invoke the router")

    async def fake_invoke_agent_run(
        *,
        agent: str,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        return (
            {
                "id": "run-explicit",
                "run_id": "run-explicit",
                "object": "agent.run",
                "agent": agent,
                "status": "running",
                "task_ids": ["task-explicit"],
                "result": {"formatted": {}},
            },
            202,
        )

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(str(db_path)),
        select_agent=forbidden_select,
    )
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke_agent_run)
    envelope = _envelope(
        key=_conversation_key(12),
        turn_id="1",
        message="Submit the explicit analysis.",
        requested_agent_id="AnalystAgent",
        allowed_agent_ids=_CANONICAL_AGENT_IDS,
    )
    async with open_asgi_client(
        monkeypatch, _context_app(executor), base_url="http://api.context.test"
    ) as client:
        response = await client.post(
            "/v1/query/route",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "user_query": "ignored by explicit V1 dispatch",
                "allowed_tools": list(_CANONICAL_AGENT_IDS),
                "conversation": envelope.model_dump(mode="json"),
            },
        )

    assert response.status_code == 202
    context = response.json()["conversation_context"]
    assert context["selected_agent_id"] == "AnalystAgent"
    assert context["route_source"] == "explicit_selection"
    assert context["route_reason_code"] == "EXPLICIT_SELECTION"


async def test_legacy_request_and_response_shape_stay_v0_when_context_is_off(
    api_client: Any,
    issued_api_key: str,
    chat_completion: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a V1 envelope, the ordinary ChatCompletion path is unchanged."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "0")
    captured: dict[str, Any] = {}
    install_chat_handler(monkeypatch, captured, content="legacy answer")

    request = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="legacy request")],
    )
    assert request.conversation is None
    assert request.model_dump(exclude_none=True) == {
        "model": "phyto-chat",
        "messages": [{"role": "user", "content": "legacy request"}],
        "stream": False,
    }

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="legacy request",
    )
    body = response.json()
    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["model"] == "phyto-chat"
    assert body["choices"][0]["message"]["content"] == "legacy answer"
    assert "conversation_context" not in body
    assert captured["user_query"] == "legacy request"
