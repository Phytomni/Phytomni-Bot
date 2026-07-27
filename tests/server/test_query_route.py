# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the autonomous Expert routing route ``POST /v1/query/route``.

The routing LLM is always mocked (``select_agent_tool`` patched), so the
suite stays offline. Covers the resolved-slug + formatted envelope (HR-1 /
HR-2), the remote running/task_ids shape (HR-3), strict no-selection handling,
the obs-injection gate, auth, and the forced_tool / unknown-tool / invalid-arg
error paths.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError
from tests.support.expert_router_fakes import patch_expert_router

import mcp_server_phytomni.api.app as api_app
from mcp_server_phytomni import server
from mcp_server_phytomni.agents.expert import (
    ToolSelection,
    ToolSelectionError,
)
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.api.schemas import ExpertQueryRequest
from mcp_server_phytomni.config.defaults import ApiConfig, ServerConfig
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission
from mcp_server_phytomni.runtime.upload_registry import (
    UploadMetadata,
    UploadRegistry,
)
from mcp_server_phytomni.storage.obs_storage import obs_path_from_key

pytestmark = pytest.mark.server


def _conversation_envelope(
    *,
    turn_id: str = "1",
    requested_agent_id: str | None = None,
    allowed_agent_ids: list[str] | None = None,
    base_business_context_version: int = 0,
) -> dict[str, Any]:
    """Build one Expert V1 envelope for routing tests."""
    return {
        "schema_version": 1,
        "conversation_key": str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")),
        "dialogue_id": str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad8")),
        "turn_id": turn_id,
        "request_id": f"request-{turn_id}",
        "operation": "append",
        "mode": "expert",
        "current_message": {
            "content": "Compare drought candidates",
            "locale": "en-US",
        },
        "requested_agent_id": requested_agent_id,
        "allowed_agent_ids": allowed_agent_ids or ["ChatAgent", "DataAgent"],
        "ledger_cursor": 1,
        "ledger_version": "a" * 64,
        "base_business_context_version": base_business_context_version,
        "history_delta": [
            {
                "turn_id": turn_id,
                "role": "user",
                "content": "Compare drought candidates",
            }
        ],
        "artifact_refs": [],
    }


def _patch_select(
    monkeypatch: pytest.MonkeyPatch, selection: ToolSelection | None
) -> None:
    """Patch the in-process router to return a fixed selection."""

    async def fake_select(
        user_query: str,
        history: Any = (),
        *,
        allowed_tools: Any = None,
        forced_tool: Any = None,
    ) -> ToolSelection | None:
        _ = (user_query, history, allowed_tools, forced_tool)
        return selection

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)


def _auth(key: str) -> dict[str, str]:
    """Return the bearer auth header for a key."""
    return {"Authorization": f"Bearer {key}"}


def _router_completion(
    *tool_calls: tuple[str, str], empty_choices: bool = False
) -> SimpleNamespace:
    """Build one OpenAI-compatible selector completion."""
    if empty_choices:
        return SimpleNamespace(choices=[])
    calls = [
        SimpleNamespace(
            function=SimpleNamespace(name=name, arguments=arguments)
        )
        for name, arguments in tool_calls
    ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(tool_calls=calls or None))
        ]
    )


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

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json=payload,
    )

    assert response.status_code == 422
    with pytest.raises(ValidationError, match=expected_fragment):
        ExpertQueryRequest.model_validate(payload)


async def test_route_rejects_more_than_ten_allowed_tools(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """Expert requests bound the allowlist independently of uniqueness."""
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
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
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": _conversation_envelope(
                requested_agent_id="KnowledgeAgent",
                allowed_agent_ids=["KnowledgeAgent"],
            ),
        },
    )

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

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["DataAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    stage = response.json()["conversation_context"]
    assert stage["selected_agent_id"] == "DataAgent"
    assert stage["route_source"] == "explicit_selection"
    duplicate = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
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


async def test_context_expert_router_keeps_full_allowlist_and_async_202(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Automatic V1 routing preserves async candidates without staging them."""
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
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query",
            "allowed_tools": allowed,
            "conversation": envelope,
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert "conversation_context" not in response.json()
    assert received == allowed
    assert received_history == (
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "U3"},
    )


async def test_context_expert_rebuilds_before_routing_when_state_is_missing(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale V1 base returns rebuild-required before selecting an agent."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError(
            "missing context must request rebuild before routing"
        )

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query",
            "allowed_tools": ["DataAgent"],
            "conversation": _conversation_envelope(
                allowed_agent_ids=["DataAgent"],
                base_business_context_version=1,
            ),
        },
    )

    assert response.status_code == 409
    assert (
        response.json()["error"]["code"]
        == "conversation_context_rebuild_required"
    )


async def test_context_expert_rejects_selected_agent_outside_allowlist(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A V1 selector cannot dispatch a tool outside Go's ordered allowlist."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "context.sqlite"))
    invoked = 0

    async def select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        return ToolSelection("DataAgent", {"user_query": "forbidden"})

    async def forbidden(_args: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("outside selection must not dispatch")

    monkeypatch.setattr(api_app, "select_agent_tool", select)
    monkeypatch.setitem(
        server.TOOL_HANDLERS, server.PhytomniAgents.DATA_AGENT.value, forbidden
    )
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query",
            "allowed_tools": ["ChatAgent"],
            "conversation": _conversation_envelope(
                allowed_agent_ids=["ChatAgent"]
            ),
        },
    )

    assert response.status_code == 502
    assert invoked == 0


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


def _stub_tool_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_value: str,
    payload: dict[str, Any],
) -> None:
    """Register a stub handler returning a canned payload for one tool."""

    async def handler(args: Any) -> dict[str, Any]:
        _ = args
        return payload

    monkeypatch.setitem(server.TOOL_HANDLERS, tool_value, handler)


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
    path = obs_path_from_key(
        ServerConfig().BUCKET_NAME,
        f"{ApiConfig().API_UPLOAD_PREFIX.strip('/')}/u1/expert/"
        "knowledge/context.pdf",
    )
    UploadRegistry(tasks_db_path).record(
        UploadMetadata(
            file_id="knowledge-context",
            user_id="u1",
            obs_path=path,
            filename="context.pdf",
            purpose="agent_context",
            byte_size=1_024,
            format="pdf",
            media_type="application/pdf",
            created_at="2026-07-25T00:00:00+00:00",
        )
    )

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
        json={
            "user_query": "rice drought",
            "obs_file_list": [path],
            "allowed_tools": ["KnowledgeAgent"],
        },
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
    assert captured["args"].obs_file_list == [path]
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
        json={
            "user_query": "assemble a genome",
            "allowed_tools": ["AnalystAgent"],
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == "analyst"
    assert body["status"] == "running"
    assert set(body["task_ids"]) == {"T-A"}
    assert body["id"]
    request_id = response.headers["X-Request-Id"]

    record = RunRegistry(tasks_db_path).list_runs(owner="u1")[0]
    assert record.spec.agent == "analyst"
    assert record.spec.origin == "remote"
    assert record.request_info.request_id == request_id


async def test_route_passes_constraints_to_selector_and_forces_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced request forwards its validated constraints to the selector."""
    captured: dict[str, object] = {}

    async def fake_select(
        user_query: str,
        history: Sequence[Mapping[str, Any]] = (),
        *,
        allowed_tools: Sequence[str] | None = None,
        forced_tool: str | None = None,
    ) -> ToolSelection:
        captured.update(
            {
                "user_query": user_query,
                "history": list(history),
                "allowed_tools": list(allowed_tools or []),
                "forced_tool": forced_tool,
            }
        )
        return ToolSelection(
            "DataAgent",
            {"user_query": "Compare drought candidates"},
        )

    _stub_tool_handler(
        monkeypatch,
        server.PhytomniAgents.DATA_AGENT.value,
        {"answer": "ok", "doc_list": []},
    )
    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "Compare drought candidates",
            "history": [{"role": "user", "content": "rice"}],
            "obs_file_list": [],
            "dialogue_id": "dialogue-1",
            "allowed_tools": ["ChatAgent", "DataAgent"],
            "forced_tool": "DataAgent",
        },
    )
    assert response.status_code == 200
    assert captured == {
        "user_query": "Compare drought candidates",
        "history": [{"role": "user", "content": "rice"}],
        "allowed_tools": ["ChatAgent", "DataAgent"],
        "forced_tool": "DataAgent",
    }
    assert response.json()["agent"] == "data"


_FORCED_ROUTE_CASES = (
    ("ChatAgent", "chat", {"user_query": "q", "obs_file_list": []}),
    ("KnowledgeAgent", "knowledge", {"user_query": "q", "obs_file_list": []}),
    ("DataAgent", "data", {"user_query": "q"}),
    (
        "AnalystAgent",
        "analyst",
        {"goal_description": "q", "data_list": {}, "obs_file_list": []},
    ),
    ("ReviewAgent", "review", {"user_query": "q", "obs_file_list": []}),
    ("BriefGeneAgent", "brief_gene", {"user_query": "AT1G01010"}),
    (
        "DeepGenomeAgent",
        "deep_genome",
        {"species_code": "ath", "gene_id": "AT1G01010"},
    ),
    (
        "InSilicoResearchAgent",
        "research",
        {"user_query": "q", "data_list": {}, "obs_file_list": []},
    ),
    (
        "DigitalDesignAgent",
        "design",
        {"species_code": "ath", "gene_id": "AT1G01010", "obs_file_list": []},
    ),
    (
        "GeneNetworkAgent",
        "network",
        {"species_code": "ath", "to_id": "TO:0000001", "obs_file_list": []},
    ),
)


@pytest.mark.parametrize("case", _FORCED_ROUTE_CASES)
async def test_route_forces_every_canonical_tool_to_its_native_slug(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Each shared canonical tool definition reaches its native slug."""
    tool_name, slug, arguments = case
    assert tuple(
        name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
    ) == tuple(case[0] for case in _FORCED_ROUTE_CASES)
    invoked: list[dict[str, Any]] = []

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        invoked.append(kwargs)
        return (
            {
                "id": f"route-{kwargs['agent']}",
                "object": "agent.run",
                "agent": kwargs["agent"],
                "status": "succeeded",
                "task_ids": [],
                "result": empty_agent_result(),
            },
            200,
        )

    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    _patch_select(monkeypatch, ToolSelection(tool_name, arguments))

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "q",
            "allowed_tools": [tool_name],
            "forced_tool": tool_name,
        },
    )

    assert response.status_code == 200
    assert invoked[0]["agent"] == slug


@pytest.mark.parametrize(
    "case",
    [
        (_router_completion(empty_choices=True), ["ChatAgent"], None),
        (_router_completion(), ["ChatAgent"], None),
        (
            _router_completion(("ChatAgent", "{}"), ("DataAgent", "{}")),
            ["ChatAgent", "DataAgent"],
            None,
        ),
        (_router_completion(("MissingAgent", "{}")), ["ChatAgent"], None),
        (_router_completion(("DataAgent", "{}")), ["ChatAgent"], None),
        (
            _router_completion(("DataAgent", "{}")),
            ["ChatAgent", "DataAgent"],
            "ChatAgent",
        ),
    ],
    ids=(
        "no-choice",
        "no-call",
        "multiple-calls",
        "unknown-call",
        "outside-allowlist",
        "forced-mismatch",
    ),
)
async def test_route_strict_failures_never_invoke_agent(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[SimpleNamespace, list[str], str | None],
) -> None:
    """Real strict selector contract failures stop before dispatch."""
    completion, allowed_tools, forced_tool = case
    invoked = 0

    async def forbidden_invoke(
        **_kwargs: object,
    ) -> tuple[dict[str, Any], int]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("agent invocation must not run")

    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)
    captured: dict[str, Any] = {}
    patch_expert_router(monkeypatch, expert_router, completion, captured)
    payload: dict[str, Any] = {
        "user_query": "q",
        "allowed_tools": allowed_tools,
    }
    if forced_tool is not None:
        payload["forced_tool"] = forced_tool
    response = await api_client.post(
        "/v1/query/route", headers=_auth(issued_api_key), json=payload
    )
    assert response.status_code in {400, 422, 502}
    assert invoked == 0
    if forced_tool is not None:
        assert captured["tool_choice"] == {
            "type": "function",
            "function": {"name": forced_tool},
        }


async def test_route_injects_obs_only_for_obs_capable_tool(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Expert validates attachments before forwarding supported arguments.

    Knowledge receives a registered document path. Data retains the existing
    no-forwarding argument shape, while the original attachment is still
    rejected by the shared capability validator.
    """
    captured: dict[str, dict[str, Any]] = {}
    registry = UploadRegistry(tasks_db_path)
    file_id = "expert-context"
    path = obs_path_from_key(
        ServerConfig().BUCKET_NAME,
        f"{ApiConfig().API_UPLOAD_PREFIX.strip('/')}/u1/expert/"
        f"{file_id}/context.pdf",
    )
    registry.record(
        UploadMetadata(
            file_id=file_id,
            user_id="u1",
            obs_path=path,
            filename="context.pdf",
            purpose="agent_context",
            byte_size=1_024,
            format="pdf",
            media_type="application/pdf",
            created_at="2026-07-25T00:00:00+00:00",
        )
    )

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
        json={
            "user_query": "q",
            "obs_file_list": [path],
            "allowed_tools": ["KnowledgeAgent"],
        },
    )
    assert captured["knowledge"]["obs_file_list"] == [path]

    _patch_select(monkeypatch, ToolSelection("DataAgent", {"user_query": "q"}))
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "q",
            "obs_file_list": [path],
            "allowed_tools": ["DataAgent"],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert "data" not in captured


async def test_route_requires_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """An unauthenticated caller sees the unified 401."""
    response = await api_client.post(
        "/v1/query/route",
        json={"user_query": "hi", "allowed_tools": ["ChatAgent"]},
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
        json={"user_query": "hi", "allowed_tools": ["ChatAgent"]},
    )
    assert response.status_code == 403


async def test_route_selection_failure_returns_sanitized_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selector contract failures never disclose routing inputs or output."""

    async def fake_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise ToolSelectionError(
            "DataAgent prohibited after model output: secret selection"
        )

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "hi",
            "allowed_tools": ["ChatAgent", "DataAgent"],
        },
    )
    assert response.status_code == 502
    assert (
        response.json()["error"]["message"]
        == "router did not resolve one permitted agent"
    )
    assert "DataAgent" not in response.text
    assert "secret selection" not in response.text


async def test_route_no_selection_returns_sanitized_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict routing does not fall back when the selector returns nothing."""
    _patch_select(monkeypatch, None)

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "hi",
            "allowed_tools": ["ChatAgent", "DataAgent"],
        },
    )

    assert response.status_code == 502
    assert (
        response.json()["error"]["message"]
        == "router did not resolve one permitted agent"
    )
    assert "ChatAgent" not in response.text
    assert "DataAgent" not in response.text


async def test_route_unknown_tool_returns_502(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool outside the agent set (e.g. GetTaskStatus) -> 502."""
    invoked = 0

    async def forbidden_invoke(
        **_kwargs: object,
    ) -> tuple[dict[str, Any], int]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("agent invocation must not run")

    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)
    _patch_select(
        monkeypatch, ToolSelection("GetTaskStatus", {"task_id": "T-1"})
    )
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "status?", "allowed_tools": ["ChatAgent"]},
    )
    assert response.status_code == 502
    assert invoked == 0


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
    invoked = 0

    async def forbidden_handler(_args: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("agent invocation must not run")

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.KNOWLEDGE_AGENT.value,
        forbidden_handler,
    )
    _patch_select(monkeypatch, ToolSelection("KnowledgeAgent", {}))
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={"user_query": "rice", "allowed_tools": ["KnowledgeAgent"]},
    )
    assert response.status_code == 400
    assert invoked == 0
