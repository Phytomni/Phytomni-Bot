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

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError
from tests.support.chat_fakes import install_chat_handler
from tests.support.expert_router_fakes import patch_expert_router

import mcp_server_phytomni.api.a2a.messages as a2a_messages
import mcp_server_phytomni.api.app as api_app
from mcp_server_phytomni import server
from mcp_server_phytomni.agents.brief_gene import agent as brief_gene_agent
from mcp_server_phytomni.agents.chat import service as chat_service
from mcp_server_phytomni.agents.expert import (
    ToolSelection,
    ToolSelectionError,
)
from mcp_server_phytomni.agents.expert import router as expert_router
from mcp_server_phytomni.agents.knowledge import agent as knowledge_agent
from mcp_server_phytomni.agents.review import agent as review_agent
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.api.schemas import ExpertQueryRequest
from mcp_server_phytomni.config.defaults import ApiConfig, ServerConfig
from mcp_server_phytomni.mcp import handlers as mcp_handlers
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id as context_agent_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.run_registry import RunRecord, RunRegistry
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
    monkeypatch: pytest.MonkeyPatch,
    selection: ToolSelection | None,
    captured: dict[str, Any] | None = None,
) -> None:
    """Patch the in-process router to return a fixed selection."""

    async def fake_select(
        user_query: str,
        history: Any = (),
        *,
        allowed_tools: Any = None,
        forced_tool: Any = None,
    ) -> ToolSelection | None:
        if captured is not None:
            captured.update(
                {
                    "user_query": user_query,
                    "history": list(history),
                    "allowed_tools": list(allowed_tools or ()),
                    "forced_tool": forced_tool,
                }
            )
        return selection

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)


def _auth(key: str) -> dict[str, str]:
    """Return the bearer auth header for a key."""
    return {"Authorization": f"Bearer {key}"}


async def _wait_for_run_children(
    db_path: str,
    run_id: str,
    expected_task_ids: set[str],
    *,
    owner: str = "u1",
    attempts: int = 100,
) -> RunRecord:
    """Poll one owned run until its reserved children are queryable."""
    registry = RunRegistry(db_path)
    for _ in range(attempts):
        record = registry.get_run(run_id, owner=owner)
        if record is not None and set(record.task_ids) == expected_task_ids:
            await asyncio.sleep(0)
            return record
        await asyncio.sleep(0)
    pytest.fail(
        f"run {run_id} did not expose children {sorted(expected_task_ids)}"
    )


_REVIEW_REPORT = (
    "# Review summary\n\n"
    "Intro framing with [document:7].\n\n"
    "## Background\nBackground claim [document:1].\n\n"
    "## Evidence\nEvidence claim [document:2].\n\n"
    "## Limitations\nLimitations remain open [document:3].\n"
)


def _review_checkpoint_state() -> dict[str, Any]:
    """Return a private Review checkpoint with a byte-sensitive report."""
    return {
        "original_user_query": "Review drought tolerance in rice",
        "summary_content": _REVIEW_REPORT,
        "research_dimensions": ["Background", "Evidence", "Limitations"],
        "evidence_gaps": ["replication study"],
        "all_raw_doc_list": [{"doc_id": "source-1"}],
        "report_artifact_id": "report-1",
        "report_revision": 4,
    }


def _review_context_envelope(
    query: str,
    *,
    turn_id: str = "3",
) -> dict[str, Any]:
    """Build an active Review context envelope for a real route call."""
    envelope = _conversation_envelope(
        turn_id=turn_id,
        requested_agent_id="ReviewAgent",
        allowed_agent_ids=["ReviewAgent"],
    )
    envelope["current_message"]["content"] = query
    envelope["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Review drought tolerance in rice",
        },
        {
            "turn_id": "2",
            "role": "assistant",
            "content": "Review completed for drought tolerance in rice.",
            "summary": "Review completed for drought tolerance in rice.",
        },
        {"turn_id": turn_id, "role": "user", "content": query},
    ]
    return envelope


def _patch_review_runtime(
    monkeypatch: pytest.MonkeyPatch,
    fake_agent: Any,
) -> None:
    """Route Review through the offline handler and a fake graph agent."""
    monkeypatch.setattr(mcp_handlers, "ReviewConfig", lambda: object())
    monkeypatch.setattr(
        mcp_handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(
            sensitive=object(), obs_credentials=("a", "b")
        ),
    )
    monkeypatch.setattr(
        mcp_handlers, "scratch_server_dir", lambda *_args: "/tmp/review"
    )
    monkeypatch.setattr(
        mcp_handlers, "chat_kwargs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(mcp_handlers, "retrieve_kwargs", lambda _config: {})
    monkeypatch.setattr(mcp_handlers, "obs_kwargs", lambda *_args: {})
    monkeypatch.setattr(
        review_agent,
        "get_cached_agent",
        lambda *_args, **_kwargs: fake_agent,
    )


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


@pytest.mark.parametrize(
    ("query", "answer"),
    [
        ("What new evidence supports that claim?", "Bounded evidence answer."),
        (
            "Review the new evidence supporting that claim.",
            "Bounded evidence answer.",
        ),
        (
            "Rewrite the Evidence section to state the limitation.",
            _REVIEW_REPORT.replace(
                "Evidence claim [document:2].",
                "Evidence claim is qualified.",
            ),
        ),
    ],
)
async def test_context_expert_review_follow_up_and_revision_use_adapter(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    query: str,
    answer: str,
) -> None:
    """V1 Review follow-ups and edits bypass the A2UI full graph."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    checkpoint = _review_checkpoint_state()

    class FakeApp:
        def __init__(self) -> None:
            self.state_reads: list[dict[str, Any]] = []
            self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

        async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
            self.state_reads.append(config)
            return checkpoint

        async def aupdate_state(
            self, config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append((config, values))

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()
            self.chat_prompts: list[str] = []
            self.graph_calls = 0

        async def _chat(self, prompt: str) -> dict[str, Any]:
            self.chat_prompts.append(prompt)
            content = (
                "Bounded evidence answer."
                if "new evidence" in query
                else "Evidence claim is qualified."
            )
            return {"choices": [{"message": {"content": content}}]}

        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            self.graph_calls += 1
            raise AssertionError(
                "Review follow-up and local revision reran graph"
            )

    fake_agent = FakeAgent()
    _patch_review_runtime(monkeypatch, fake_agent)

    async def forbidden_review_graph(**_kwargs: Any) -> Any:
        raise AssertionError("V1 Review must not enter the A2UI graph")

    monkeypatch.setattr(
        api_app, "_run_review_with_interrupt", forbidden_review_graph
    )
    envelope = _review_context_envelope(query)

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_context"]["selected_agent_id"] == "ReviewAgent"
    assert body["result"]["formatted"]["answer"] == answer
    assert fake_agent.graph_calls == 0
    assert fake_agent.chat_prompts
    assert _REVIEW_REPORT not in fake_agent.chat_prompts[0]
    expected_thread = context_agent_thread_id(
        UUID(envelope["conversation_key"]), "ReviewAgent"
    )
    assert fake_agent.app.state_reads == [
        {"configurable": {"thread_id": expected_thread}},
        {"configurable": {"thread_id": expected_thread}},
    ]
    assert fake_agent.app.updates == []
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.delta is not None
    assert _REVIEW_REPORT not in json.dumps(staged.delta)


@pytest.mark.parametrize("scope_change", [False, True])
@pytest.mark.asyncio
async def test_context_expert_review_full_graph_uses_candidate_thread(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scope_change: bool,
) -> None:
    """V1 new and scope Review graphs cannot write the stable checkpoint."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    query = (
        "Start a new review of maize heat tolerance."
        if scope_change
        else "Review maize heat tolerance."
    )
    if scope_change:
        envelope = _review_context_envelope(query, turn_id="7")
        stable_checkpoint: dict[str, Any] = _review_checkpoint_state()
    else:
        envelope = _conversation_envelope(
            turn_id="7",
            requested_agent_id="ReviewAgent",
            allowed_agent_ids=["ReviewAgent"],
        )
        envelope["current_message"]["content"] = query
        envelope["history_delta"] = [
            {"turn_id": "7", "role": "user", "content": query}
        ]
        stable_checkpoint = {}

    class FakeApp:
        def __init__(self) -> None:
            self.state_reads: list[dict[str, Any]] = []
            self.states: dict[str, dict[str, Any]] = {}
            self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

        async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
            self.state_reads.append(config)
            thread_id = config["configurable"]["thread_id"]
            return self.states.get(thread_id, stable_checkpoint)

        async def aupdate_state(
            self, config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append((config, values))

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()
            self.graph_threads: list[str | None] = []

        async def arun(self, **kwargs: Any) -> dict[str, Any]:
            thread_id = kwargs["thread_id"]
            self.graph_threads.append(thread_id)
            self.app.states[thread_id] = {
                "original_user_query": query,
                "summary_content": "# Candidate report\n\nCandidate evidence.",
                "research_dimensions": ["Evidence"],
                "report_artifact_id": "report-1",
                "report_revision": 0,
            }
            return {
                "choices": [
                    {"message": {"content": "Candidate public answer."}}
                ],
                "phytomni_state": self.app.states[thread_id],
            }

    fake_agent = FakeAgent()
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review full graph must use the native candidate path"
        ),
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["formatted"]["answer"] == (
        "Candidate public answer."
    )
    stable_thread = context_agent_thread_id(
        UUID(envelope["conversation_key"]), "ReviewAgent"
    )
    assert len(fake_agent.graph_threads) == 1
    candidate_thread = fake_agent.graph_threads[0]
    assert candidate_thread is not None
    assert candidate_thread != stable_thread
    assert candidate_thread.startswith(f"{stable_thread}:candidate:")
    assert fake_agent.app.state_reads == [
        {"configurable": {"thread_id": stable_thread}},
        {"configurable": {"thread_id": candidate_thread}},
    ]
    assert fake_agent.app.updates == []
    assert (
        fake_agent.app.states.get(stable_thread, stable_checkpoint)
        == stable_checkpoint
    )
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "staged"
    assert staged.delta is not None
    assert staged.stage_metadata is not None
    assert (
        staged.stage_metadata["_review_settlement"]["candidate_thread_id"]
        == candidate_thread
    )
    assert "_review_settlement" not in response.text


@pytest.mark.asyncio
async def test_context_expert_review_missing_candidate_fails_before_staging(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A graph answer is not healthy until its candidate checkpoint exists."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    query = "Review maize heat tolerance."
    envelope = _conversation_envelope(
        turn_id="11",
        requested_agent_id="ReviewAgent",
        allowed_agent_ids=["ReviewAgent"],
    )
    envelope["current_message"]["content"] = query
    envelope["history_delta"] = [
        {"turn_id": "11", "role": "user", "content": query}
    ]

    class FakeApp:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def aupdate_state(
            self, _config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append(values)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()
            self.graph_calls = 0

        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            self.graph_calls += 1
            return {
                "choices": [{"message": {"content": "Current public answer."}}]
            }

    fake_agent = FakeAgent()
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must use the native graph invocation seam"
        ),
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.graph_calls == 1
    assert fake_agent.app.updates == []
    stored = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert stored is not None
    assert stored.state == "failed"
    assert stored.delta is None


@pytest.mark.asyncio
async def test_context_expert_review_graph_clarification_fails_without_staging(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A full-graph Review clarification cannot become a healthy stage."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    query = "Review maize heat tolerance."
    envelope = _conversation_envelope(
        turn_id="8",
        requested_agent_id="ReviewAgent",
        allowed_agent_ids=["ReviewAgent"],
    )
    envelope["current_message"]["content"] = query
    envelope["history_delta"] = [
        {"turn_id": "8", "role": "user", "content": query}
    ]

    class FakeApp:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []
            self.deleted: list[str] = []

        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def aupdate_state(
            self, _config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append(values)

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()
            self.graph_calls = 0

        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            self.graph_calls += 1
            raise review_agent.ReviewClarificationError("graph clarification")

    fake_agent = FakeAgent()
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must use the native graph invocation seam"
        ),
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.graph_calls == 1
    assert fake_agent.app.updates == []
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None


@pytest.mark.asyncio
async def test_context_expert_review_empty_local_revision_does_not_settle(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty section response leaves the Review revision unchanged."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    class FakeApp:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return _review_checkpoint_state()

        async def aupdate_state(
            self, _config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append(values)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()
            self.graph_calls = 0

        async def _chat(self, _prompt: str) -> dict[str, Any]:
            return {"choices": [{"message": {"content": ""}}]}

        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            self.graph_calls += 1
            raise AssertionError("empty local revision must not rerun graph")

    fake_agent = FakeAgent()
    _patch_review_runtime(monkeypatch, fake_agent)

    async def forbidden_review_graph(**_kwargs: Any) -> Any:
        raise AssertionError("V1 Review must not enter the A2UI graph")

    monkeypatch.setattr(
        api_app, "_run_review_with_interrupt", forbidden_review_graph
    )
    envelope = _review_context_envelope(
        "Rewrite the Evidence section to state the limitation."
    )
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.graph_calls == 0
    assert fake_agent.app.updates == []
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None
    assert "Review section revision completed." not in json.dumps(staged.delta)


@pytest.mark.asyncio
async def test_context_expert_review_empty_follow_up_fails_without_staging(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty Review follow-up is failed and cannot settle context."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    class FakeApp:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return _review_checkpoint_state()

        async def aupdate_state(
            self, _config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append(values)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()

        async def _chat(self, _prompt: str) -> dict[str, Any]:
            return {"choices": [{"message": {"content": ""}}]}

        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("empty follow-up must not rerun graph")

    fake_agent = FakeAgent()
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must not enter the full graph"
        ),
    )
    envelope = _review_context_envelope(
        "What new evidence supports that claim?", turn_id="4"
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.app.updates == []
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None


@pytest.mark.asyncio
async def test_context_expert_review_unknown_section_clarification_fails_turn(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unknown local section is clarification, never a healthy stage."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    class FakeApp:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return _review_checkpoint_state()

        async def aupdate_state(
            self, _config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append(values)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()

        async def _chat(self, _prompt: str) -> dict[str, Any]:
            raise AssertionError("unknown section must clarify before chat")

        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("unknown section must not rerun graph")

    fake_agent = FakeAgent()
    _patch_review_runtime(monkeypatch, fake_agent)
    monkeypatch.setattr(
        api_app,
        "_run_review_with_interrupt",
        lambda **_kwargs: pytest.fail(
            "V1 Review must not enter the full graph"
        ),
    )
    envelope = _review_context_envelope(
        "Rewrite the Methods section to be shorter.", turn_id="5"
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["ReviewAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 409
    assert fake_agent.app.updates == []
    staged = ConversationContextStore(str(db_path)).load_turn(
        envelope["conversation_key"], envelope["turn_id"]
    )
    assert staged is not None
    assert staged.state == "failed"
    assert staged.delta is None


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

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit Data selection must not route")

    data_config = SimpleNamespace(
        RETRIEVE_URL=None,
        DATA_REPO_ID=None,
        PAGE_NUM=1,
        DATA_PAGE_SIZE=10,
        FILTER_STRING=None,
        SCOPE=None,
        RERANK_URL=None,
        RERANK_BATCH_SIZE=10,
        SCORE_THRESHOLD=0.0,
        DATABASE_URL="private-database-url",
        WORKSPACE_ID="private-workspace",
        SUBJECT_ID="private-subject",
        DIALOG_ID=None,
        NEED_INSIGHT=True,
        SIMPLIFY_RESPONSE=True,
    )
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

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    monkeypatch.setattr(mcp_handlers, "DataConfig", lambda: data_config)
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
    expected_dialog_id = f"{expected_thread_id}-nl2sql"
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

    first_response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["DataAgent"],
            "conversation": first,
        },
    )

    assert first_response.status_code == 200
    first_staged = store.load_turn(str(conversation_key), "1")
    assert first_staged is not None
    assert first_staged.delta is not None
    first_payload = json.dumps(first_staged.delta, sort_keys=True)
    assert "data:dataset:expression" in first_payload
    assert "data:table:expression_table" in first_payload
    assert "dimension:tissue" in first_payload
    assert "column:expression" in first_payload
    assert "row_count:2" in first_payload
    assert "artifact-expression" in first_payload
    assert "leaf" not in first_payload
    assert "SELECT * FROM private_table" not in first_payload
    assert "postgresql://user:pass@example/db" not in first_payload
    store.commit_staged_turn(
        str(conversation_key),
        "1",
        first["ledger_version"],
        "b" * 64,
    )

    second = _conversation_envelope(
        turn_id="2",
        requested_agent_id="DataAgent",
        allowed_agent_ids=["DataAgent"],
        base_business_context_version=1,
    )
    second["ledger_cursor"] = 2
    second["ledger_version"] = "c" * 64
    second["current_message"]["content"] = "Only rice"
    second["history_delta"] = [
        {
            "turn_id": "2",
            "role": "user",
            "content": "Only rice",
        }
    ]
    second["artifact_refs"] = first["artifact_refs"]

    second_response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["DataAgent"],
            "conversation": second,
        },
    )

    assert second_response.status_code == 200
    assert [call["user_query"] for call in calls] == [
        "Show expression by tissue",
        "Show expression by tissue for rice",
    ]
    assert [call["thread_id"] for call in calls] == [
        expected_thread_id,
        expected_thread_id,
    ]
    assert [call["dialog_id"] for call in calls] == [
        expected_dialog_id,
        expected_dialog_id,
    ]
    second_staged = store.load_turn(str(conversation_key), "2")
    assert second_staged is not None
    assert second_staged.delta is not None
    second_payload = json.dumps(second_staged.delta, sort_keys=True)
    assert "data:dataset:expression" in second_payload
    assert "dimension:tissue" in second_payload
    assert "filter:species=rice" in second_payload
    assert "leaf" not in second_payload
    assert "SELECT * FROM private_table" not in second_payload
    assert "postgresql://user:pass@example/db" not in second_payload


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

    monkeypatch.setattr(
        mcp_handlers,
        "load_chat_runtime",
        lambda: (object(), object()),
    )
    monkeypatch.setattr(
        mcp_handlers,
        "scratch_server_dir",
        lambda *_args: "/tmp/chat",
    )
    monkeypatch.setattr(
        mcp_handlers,
        "chat_call_kwargs",
        lambda **kwargs: {
            "user_query": kwargs["request"].user_query,
            "locale": kwargs["request"].locale,
            "obs_file_list": kwargs["request"].obs_file_list,
        },
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.service.phyto_chat",
        fake_phyto_chat,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.chat.service.get_prompt",
        lambda *_args, **_kwargs: "follow-up",
    )
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

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
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


async def test_context_expert_knowledge_turn_separates_retrieval_context(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Knowledge resolves retrieval privately and stages bounded context."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    answer_marker = "KNOWLEDGE_ROUTE_ANSWER_OUTPUT_SENTINEL"

    class FakeKnowledgeAgent:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def arun(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {
                "choices": [
                    {
                        "message": {
                            "content": answer_marker,
                            "doc_list": [
                                {
                                    "file_id": "doc-1",
                                    "title": "Paper 1.pdf",
                                    "content": (
                                        "full report body that must "
                                        "not persist"
                                    ),
                                }
                            ],
                            "follow_up_questions": [
                                "What promoter evidence exists for OsDREB1?"
                            ],
                        }
                    }
                ]
            }

    fake_agent = FakeKnowledgeAgent()
    monkeypatch.setattr(mcp_handlers, "KnowledgeConfig", lambda: object())
    monkeypatch.setattr(
        mcp_handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(
            sensitive=object(), obs_credentials=("a", "b")
        ),
    )
    monkeypatch.setattr(
        mcp_handlers,
        "scratch_server_dir",
        lambda *_args: "/tmp/knowledge",
    )
    monkeypatch.setattr(
        mcp_handlers, "chat_kwargs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(mcp_handlers, "retrieve_kwargs", lambda _config: {})
    monkeypatch.setattr(mcp_handlers, "obs_kwargs", lambda *_args: {})
    monkeypatch.setattr(
        knowledge_agent,
        "get_cached_agent",
        lambda *_args, **_kwargs: fake_agent,
    )
    monkeypatch.setattr(
        knowledge_agent,
        "_knowledge_config_with_overrides",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        knowledge_agent,
        "_knowledge_sensitive_config_with_overrides",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.citation_enrichment.bi_query",
        AsyncMock(return_value={"message": "ok", "data": []}),
    )
    envelope = _conversation_envelope(
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    envelope["turn_id"] = "3"
    envelope["request_id"] = "request-3"
    envelope["ledger_cursor"] = 3
    envelope["current_message"]["content"] = "What evidence supports that?"
    envelope["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Tell me about rice gene OsDREB1.",
        },
        {
            "turn_id": "2",
            "role": "assistant",
            "content": "OsDREB1 improves drought tolerance [1].",
            "summary": "OsDREB1 improves drought tolerance [1].",
        },
        {
            "turn_id": "3",
            "role": "user",
            "content": "What evidence supports that?",
        },
    ]

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_context"]["selected_agent_id"] == (
        "KnowledgeAgent"
    )
    assert body["result"]["formatted"]["answer"] == (answer_marker)
    expected_thread_id = context_agent_thread_id(
        UUID(envelope["conversation_key"]), "KnowledgeAgent"
    )
    assert fake_agent.calls == [
        {
            "user_query": "What evidence supports that?",
            "obs_file_list": [],
            "repo_id_dict": None,
            "is_generate": True,
            "is_follow_up": True,
            "locale": "en-US",
            "retrieval_query": "What evidence supports OsDREB1?",
            "answer_context": (
                "[recent turn 1]\nuser: Tell me about rice gene OsDREB1.\n\n"
                "[recent turn 2]\nassistant: OsDREB1 improves drought "
                "tolerance [1]."
            ),
            "thread_id": expected_thread_id,
            "conversation_messages": (),
        }
    ]
    store = ConversationContextStore(str(db_path))
    staged = store.load_turn(str(UUID(envelope["conversation_key"])), "3")
    assert staged is not None
    assert staged.delta is not None
    assert [item["label"] for item in staged.delta["active_entities"]] == [
        "OsDREB1"
    ]
    staged_delta = json.dumps(staged.delta, sort_keys=True)
    staged_result = json.dumps(staged.result, sort_keys=True)
    assert answer_marker not in staged_delta
    assert answer_marker in staged_result
    assert "full report body" not in staged_delta
    assert staged.stage_metadata is not None
    assert staged.stage_metadata["selected_agent_id"] == "KnowledgeAgent"
    assert staged.stage_metadata["route_source"] == "explicit_selection"

    settled = store.commit_staged_turn(
        str(UUID(envelope["conversation_key"])),
        envelope["turn_id"],
        envelope["ledger_version"],
        envelope["ledger_version"],
    )
    assert settled.state == "committed"
    stored_context = store.load_context(
        str(UUID(envelope["conversation_key"]))
    )
    assert stored_context is not None
    stored_context_json = json.dumps(stored_context.context, sort_keys=True)
    assert answer_marker not in stored_context_json
    assert stored_context.context["active_entities"]

    replay = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": envelope,
        },
    )
    assert replay.status_code == 200
    assert replay.json()["result"]["formatted"]["answer"] == answer_marker
    replayed_context = store.load_context(
        str(UUID(envelope["conversation_key"]))
    )
    assert replayed_context is not None
    assert answer_marker not in json.dumps(
        replayed_context.context, sort_keys=True
    )


async def test_context_expert_knowledge_follow_up_returns_clarification(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unresolved Knowledge pronoun asks for clarification."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))
    calls: list[dict[str, Any]] = []

    class FakeKnowledgeAgent:
        async def arun(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "unexpected"}}]}

    monkeypatch.setattr(mcp_handlers, "KnowledgeConfig", lambda: object())
    monkeypatch.setattr(
        mcp_handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(
            sensitive=object(), obs_credentials=("a", "b")
        ),
    )
    monkeypatch.setattr(
        mcp_handlers,
        "scratch_server_dir",
        lambda *_args: "/tmp/knowledge",
    )
    monkeypatch.setattr(
        mcp_handlers, "chat_kwargs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(mcp_handlers, "retrieve_kwargs", lambda _config: {})
    monkeypatch.setattr(mcp_handlers, "obs_kwargs", lambda *_args: {})
    monkeypatch.setattr(
        knowledge_agent,
        "get_cached_agent",
        lambda *_args, **_kwargs: FakeKnowledgeAgent(),
    )
    monkeypatch.setattr(
        knowledge_agent,
        "_knowledge_config_with_overrides",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        knowledge_agent,
        "_knowledge_sensitive_config_with_overrides",
        lambda **_kwargs: object(),
    )
    envelope = _conversation_envelope(
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    envelope["turn_id"] = "4"
    envelope["request_id"] = "request-4"
    envelope["ledger_cursor"] = 4
    envelope["current_message"]["content"] = "What evidence supports that?"
    envelope["history_delta"] = [
        {
            "turn_id": "4",
            "role": "user",
            "content": "What evidence supports that?",
        }
    ]

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "clarify" in body["result"]["formatted"]["answer"].lower()
    assert calls == []
    store = ConversationContextStore(str(db_path))
    staged = store.load_turn(str(UUID(envelope["conversation_key"])), "4")
    assert staged is not None
    assert staged.delta is not None
    assert staged.delta["active_entities"] == []


async def test_context_expert_brief_gene_turn_stages_bounded_context_delta(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The native Brief Gene route returns its bounded context projection."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    class FakeBriefGeneAgent:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def arun(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "# Brief Gene Analysis\n\n"
                                "Rice gene summary [1]."
                            ),
                            "doc_list": [
                                {
                                    "file_id": "paper-1",
                                    "title": "Paper 1",
                                    "content": "full report body",
                                }
                            ],
                        }
                    }
                ],
                "phytomni_state": {
                    "gene_id": "Os01g0177400",
                    "species_code": "osa",
                    "report_summary": "Bounded rice gene summary.",
                    "report_artifact_id": "brief-report-1",
                    "report_revision": 4,
                    "retrieved_docs": [
                        {
                            "file_id": "paper-1",
                            "content": "full report body",
                        }
                    ],
                },
            }

    fake_agent = FakeBriefGeneAgent()
    monkeypatch.setattr(
        brief_gene_agent,
        "resolve_brief_gene_user_query",
        AsyncMock(
            return_value=SimpleNamespace(
                gene_id="Os01g0177400", species_code="osa"
            )
        ),
    )
    monkeypatch.setattr(
        brief_gene_agent,
        "get_cached_agent",
        lambda *_args, **_kwargs: fake_agent,
    )
    monkeypatch.setattr(
        mcp_handlers,
        "BriefGeneConfig",
        lambda: SimpleNamespace(MAX_CONCURRENCY=1),
    )
    monkeypatch.setattr(
        mcp_handlers,
        "load_handler_runtime",
        lambda: SimpleNamespace(
            sensitive=object(), obs_credentials=("a", "b")
        ),
    )
    monkeypatch.setattr(
        mcp_handlers, "chat_kwargs", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(mcp_handlers, "retrieve_kwargs", lambda _config: {})
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.citation_enrichment.bi_query",
        AsyncMock(return_value={"message": "ok", "data": []}),
    )

    envelope = _conversation_envelope(
        turn_id="6",
        requested_agent_id="BriefGeneAgent",
        allowed_agent_ids=["BriefGeneAgent"],
    )
    envelope["ledger_cursor"] = 6
    envelope["current_message"]["content"] = "Os01g0177400"
    envelope["history_delta"] = [
        {
            "turn_id": "6",
            "role": "user",
            "content": "Os01g0177400",
        }
    ]
    envelope["artifact_refs"] = [
        {
            "artifact_id": "brief-report-1",
            "display_name": "Brief Gene report",
        }
    ]

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["BriefGeneAgent"],
            "conversation": envelope,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_context"]["selected_agent_id"] == (
        "BriefGeneAgent"
    )
    assert body["conversation_context"]["context_degraded"] is False
    assert body["result"]["formatted"]["answer"] == (
        "# Brief Gene Analysis\n\nRice gene summary [1]."
    )
    expected_thread_id = context_agent_thread_id(
        UUID(envelope["conversation_key"]), "BriefGeneAgent"
    )
    assert fake_agent.calls == [
        {
            "user_query": "Os01g0177400",
            "locale": "en-US",
            "thread_id": expected_thread_id,
        }
    ]

    store = ConversationContextStore(str(db_path))
    staged = store.load_turn(str(UUID(envelope["conversation_key"])), "6")
    assert staged is not None
    assert staged.delta is not None
    labels = {item["label"] for item in staged.delta["active_entities"]}
    assert {
        "Os01g0177400",
        "osa",
        "paper-1",
        "brief-report-1",
        "report revision 4",
    } <= labels
    entity_ids = {
        item["entity_id"] for item in staged.delta["active_entities"]
    }
    assert {
        "brief_gene.gene.os01g0177400",
        "brief_gene.species.osa",
        "brief_gene.evidence.paper-1",
        "brief_gene.report_revision.4",
        "brief_gene.artifact.brief-report-1",
    } <= entity_ids
    assert all(":" not in entity_id for entity_id in entity_ids)
    assert staged.delta["task_summary"] == "Bounded rice gene summary."
    assert [
        item["artifact_id"] for item in staged.delta["artifact_index"]
    ] == ["brief-report-1"]
    assert "full report body" not in json.dumps(staged.delta)


async def test_context_expert_knowledge_switch_replaces_topic(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A successful topic switch commits only the new Knowledge topic."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit Knowledge selection must not route")

    def _success_body(agent: str, answer: str) -> dict[str, Any]:
        return {
            "id": f"{agent}-run",
            "object": "agent.run",
            "agent": agent,
            "status": "succeeded",
            "task_ids": [],
            "result": {"formatted": {"answer": answer}},
        }

    captured: list[dict[str, Any]] = []

    async def fake_invoke(
        *,
        agent: str,
        arguments: dict[str, Any],
        private_agent_state: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        captured.append(
            {
                "agent": agent,
                "arguments": dict(arguments),
                "private_agent_state": dict(private_agent_state or {}),
            }
        )
        query = arguments["user_query"]
        if query == "Tell me about OsDREB1 drought evidence.":
            return (
                _success_body(
                    agent,
                    "OsDREB1 improves drought tolerance [1].",
                ),
                200,
            )
        if query == "Tell me about OsNAC6 drought evidence.":
            return (
                _success_body(
                    agent,
                    "OsNAC6 improves drought tolerance [2].",
                ),
                200,
            )
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    conversation_key = str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"))
    store = ConversationContextStore(str(db_path))

    first = _conversation_envelope(
        turn_id="1",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    first["current_message"][
        "content"
    ] = "Tell me about OsDREB1 drought evidence."
    first["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Tell me about OsDREB1 drought evidence.",
        }
    ]

    first_response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": first,
        },
    )

    assert first_response.status_code == 200
    first_staged = store.load_turn(conversation_key, "1")
    assert first_staged is not None
    assert first_staged.delta is not None
    assert [
        item["label"] for item in first_staged.delta["active_entities"]
    ] == ["OsDREB1"]
    store.commit_staged_turn(
        conversation_key,
        "1",
        first["ledger_version"],
        "b" * 64,
    )

    second = _conversation_envelope(
        turn_id="2",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
        base_business_context_version=1,
    )
    second["ledger_cursor"] = 2
    second["ledger_version"] = "c" * 64
    second["current_message"][
        "content"
    ] = "Tell me about OsNAC6 drought evidence."
    second["history_delta"] = [
        {
            "turn_id": "2",
            "role": "user",
            "content": "Tell me about OsNAC6 drought evidence.",
        }
    ]

    second_response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": second,
        },
    )

    assert second_response.status_code == 200
    second_staged = store.load_turn(conversation_key, "2")
    assert second_staged is not None
    assert second_staged.delta is not None
    assert [
        item["label"] for item in second_staged.delta["active_entities"]
    ] == ["OsNAC6"]
    committed = store.commit_staged_turn(
        conversation_key,
        "2",
        second["ledger_version"],
        "d" * 64,
    )
    assert [
        item["label"] for item in committed.context.context["active_entities"]
    ] == ["OsNAC6"]
    assert captured[1]["private_agent_state"]["retrieval_query"] == (
        "Tell me about OsNAC6 drought evidence."
    )


async def test_context_expert_knowledge_failed_switch_keeps_prior_topic(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed explicit topic switch leaves the committed topic unchanged."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    db_path = tmp_path / "context.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(db_path))

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> ToolSelection:
        raise AssertionError("explicit Knowledge selection must not route")

    async def fake_invoke(
        *,
        agent: str,
        arguments: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        if (
            arguments["user_query"]
            == "Tell me about OsDREB1 drought evidence."
        ):
            return (
                {
                    "id": f"{agent}-run",
                    "object": "agent.run",
                    "agent": agent,
                    "status": "succeeded",
                    "task_ids": [],
                    "result": {
                        "formatted": {
                            "answer": "OsDREB1 improves drought tolerance [1]."
                        }
                    },
                },
                200,
            )
        return (
            {
                "id": f"{agent}-run",
                "object": "agent.run",
                "agent": agent,
                "status": "running",
                "task_ids": [],
                "result": {"formatted": {}},
            },
            200,
        )

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_router)
    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    conversation_key = str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"))
    store = ConversationContextStore(str(db_path))

    first = _conversation_envelope(
        turn_id="1",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
    )
    first["current_message"][
        "content"
    ] = "Tell me about OsDREB1 drought evidence."
    first["history_delta"] = [
        {
            "turn_id": "1",
            "role": "user",
            "content": "Tell me about OsDREB1 drought evidence.",
        }
    ]

    first_response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": first,
        },
    )

    assert first_response.status_code == 200
    store.commit_staged_turn(
        conversation_key,
        "1",
        first["ledger_version"],
        "b" * 64,
    )

    second = _conversation_envelope(
        turn_id="2",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=["KnowledgeAgent"],
        base_business_context_version=1,
    )
    second["ledger_cursor"] = 2
    second["ledger_version"] = "c" * 64
    second["current_message"][
        "content"
    ] = "Tell me about OsNAC6 drought evidence."
    second["history_delta"] = [
        {
            "turn_id": "2",
            "role": "user",
            "content": "Tell me about OsNAC6 drought evidence.",
        }
    ]

    second_response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": ["KnowledgeAgent"],
            "conversation": second,
        },
    )

    assert second_response.status_code == 409
    assert (
        second_response.json()["error"]["code"]
        == "conversation_context_turn_in_progress"
    )
    stored_context = store.load_context(conversation_key)
    assert stored_context is not None
    assert [
        item["label"] for item in stored_context.context["active_entities"]
    ] == ["OsDREB1"]
    failed_turn = store.load_turn(conversation_key, "2")
    assert failed_turn is not None
    assert failed_turn.state == "failed"


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


def test_expert_activation_stays_outside_bot_config() -> None:
    """Expert activation remains owned by the Web gateway boundary."""
    fields = vars(ApiConfig).get("model_fields", {})
    assert "EXPERT_ENABLED" not in fields


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


async def test_route_remote_agent_returns_reserved_run_before_child_ids(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A routed remote agent returns its umbrella before child persistence."""

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
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == []
    assert body["result"] == empty_agent_result()
    request_id = response.headers["X-Request-Id"]

    record = await _wait_for_run_children(
        tasks_db_path,
        body["run_id"],
        {"T-A"},
    )
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
    selector_call: dict[str, Any] = {}
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
    _patch_select(
        monkeypatch,
        ToolSelection(tool_name, arguments),
        selector_call,
    )

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
    assert len(invoked) == 1
    assert invoked[0]["agent"] == slug
    assert selector_call == {
        "user_query": "q",
        "history": [],
        "allowed_tools": [tool_name],
        "forced_tool": tool_name,
    }


_BACKGROUND_EXPERT_CASES = (
    pytest.param(
        (
            "AnalystAgent",
            "analyst",
            {
                "goal_description": "q",
                "data_list": {},
                "obs_file_list": [],
            },
            {"task_id": "expert-launch-analyst", "output_dir": "/obs/a"},
            {"expert-launch-analyst"},
        ),
        id="analyst-background",
    ),
    pytest.param(
        (
            "InSilicoResearchAgent",
            "research",
            {"user_query": "q", "data_list": {}, "obs_file_list": []},
            {
                "task_ids": ["expert-launch-research"],
                "output_dir": "/obs/r",
            },
            {"expert-launch-research"},
        ),
        id="research-background",
    ),
    pytest.param(
        (
            "DigitalDesignAgent",
            "design",
            {
                "species_code": "ath",
                "gene_id": "AT1G01010",
                "obs_file_list": [],
                "resolve_gene_id": False,
            },
            {
                "design_task_result": [
                    {"task_id": "expert-launch-design", "output_dir": "/obs/d"}
                ]
            },
            {"expert-launch-design"},
        ),
        id="design-background",
    ),
    pytest.param(
        (
            "GeneNetworkAgent",
            "network",
            {
                "species_code": "osa",
                "to_id": "TO:0000207",
                "obs_file_list": [],
                "resolve_to_id": False,
            },
            {
                "network_task": {
                    "task_id": "expert-launch-network",
                    "output_dir": "/obs/n",
                }
            },
            {"expert-launch-network"},
        ),
        id="network-background",
    ),
)


@pytest.mark.parametrize("case", _BACKGROUND_EXPERT_CASES)
async def test_expert_background_selection_launches_one_reserved_worker(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: tuple[str, str, dict[str, Any], dict[str, Any], set[str]],
) -> None:
    """Each background Expert selection uses one shared launcher call."""
    tool_name, slug, arguments, result, expected_task_ids = case
    launched: list[str] = []
    real_launch = api_app.launch_background_submission

    def capture_launch(
        reservation: Any,
        operation: Any,
        *,
        db_path: str,
    ) -> None:
        launched.append(reservation.agent)
        real_launch(reservation, operation, db_path=db_path)

    monkeypatch.setattr(
        api_app, "launch_background_submission", capture_launch
    )

    async def fake(_args: Any) -> dict[str, Any]:
        return result

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        {
            "analyst": server.PhytomniAgents.ANALYST_AGENT.value,
            "research": server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
            "design": server.PhytomniAgents.DIGITAL_DESIGN_AGENT.value,
            "network": server.PhytomniAgents.GENE_NETWORK_AGENT.value,
        }[slug],
        records_submission(slug)(fake),
    )
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

    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == slug
    assert body["status"] == "running"
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == []
    assert body["result"] == empty_agent_result()
    assert launched == [slug]
    await _wait_for_run_children(
        tasks_db_path,
        body["run_id"],
        expected_task_ids,
    )


_SYNC_EXPERT_CASES = (
    pytest.param(
        ("ChatAgent", "chat", {"user_query": "q", "obs_file_list": []}),
        id="chat-synchronous",
    ),
    pytest.param(
        (
            "KnowledgeAgent",
            "knowledge",
            {"user_query": "q", "obs_file_list": []},
        ),
        id="knowledge-synchronous",
    ),
    pytest.param(
        ("DataAgent", "data", {"user_query": "q"}),
        id="data-synchronous",
    ),
    pytest.param(
        ("ReviewAgent", "review", {"user_query": "q", "obs_file_list": []}),
        id="review-synchronous",
    ),
    pytest.param(
        ("BriefGeneAgent", "brief_gene", {"user_query": "AT1G01010"}),
        id="brief-gene-synchronous",
    ),
)


@pytest.mark.parametrize("case", _SYNC_EXPERT_CASES)
async def test_expert_synchronous_selection_skips_background_launcher(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Established synchronous Expert selections retain their native path."""
    tool_name, slug, arguments = case
    launched: list[str] = []
    real_launch = api_app.launch_background_submission

    def capture_launch(
        reservation: Any,
        operation: Any,
        *,
        db_path: str,
    ) -> None:
        launched.append(reservation.agent)
        real_launch(reservation, operation, db_path=db_path)

    monkeypatch.setattr(
        api_app, "launch_background_submission", capture_launch
    )
    if slug == "review":

        async def fake_review(**_kwargs: Any) -> Any:
            return api_app._ReviewExecution(
                run_id="expert-review-sync",
                status="succeeded",
                result={
                    "formatted": {"answer": "review ok", "metadata": {}},
                    "execution": {"warnings": []},
                    "raw": None,
                },
            )

        monkeypatch.setattr(api_app, "_run_review_with_interrupt", fake_review)
    else:
        _stub_tool_handler(
            monkeypatch,
            {
                "chat": server.PhytomniAgents.CHAT_AGENT.value,
                "knowledge": server.PhytomniAgents.KNOWLEDGE_AGENT.value,
                "data": server.PhytomniAgents.DATA_AGENT.value,
                "brief_gene": server.PhytomniAgents.BRIEF_GENE_AGENT.value,
            }[slug],
            {"answer": "ok", "doc_list": []},
        )
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
    body = response.json()
    assert body["agent"] == slug
    assert body["status"] == "succeeded"
    assert launched == []


async def test_route_autonomous_dispatches_one_allowed_tool(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Autonomous routing makes one constrained selection and dispatch."""
    captured: dict[str, Any] = {}
    invoked: list[dict[str, Any]] = []

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        invoked.append(kwargs)
        return (
            {
                "id": "route-chat",
                "object": "agent.run",
                "agent": kwargs["agent"],
                "status": "succeeded",
                "task_ids": [],
                "result": empty_agent_result(),
            },
            200,
        )

    monkeypatch.setattr(api_app, "_invoke_agent_run", fake_invoke)
    patch_expert_router(
        monkeypatch,
        expert_router,
        _router_completion(("ChatAgent", '{"user_query":"q"}')),
        captured,
    )

    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "q",
            "allowed_tools": ["ReviewAgent", "ChatAgent"],
        },
    )

    assert response.status_code == 200
    assert len(invoked) == 1
    assert invoked[0]["agent"] == "chat"
    assert captured["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in captured["tools"]] == [
        "ReviewAgent",
        "ChatAgent",
    ]


async def test_literal_agent_mention_stays_on_chat_surface(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Literal ``@DataAgent`` text does not invoke Expert routing."""
    captured: dict[str, Any] = {}
    install_chat_handler(monkeypatch, captured)

    async def forbidden_select(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("literal mentions must not enter Expert routing")

    monkeypatch.setattr(api_app, "select_agent_tool", forbidden_select)
    response = await api_client.post(
        "/v1/chat/completions",
        headers=_auth(issued_api_key),
        json={
            "model": "phyto-chat",
            "messages": [
                {
                    "role": "user",
                    "content": "Explain literal @DataAgent text",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "phyto-chat"
    assert captured["user_query"] == "Explain literal @DataAgent text"


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


@pytest.mark.parametrize(
    "case",
    [
        ("ChatAgent", "chat", True),
        ("KnowledgeAgent", "knowledge", True),
        ("DataAgent", "data", False),
        ("ReviewAgent", "review", True),
        ("BriefGeneAgent", "brief_gene", False),
        ("AnalystAgent", "analyst", False),
        ("DeepGenomeAgent", "deep_genome", False),
        ("InSilicoResearchAgent", "research", False),
        ("DigitalDesignAgent", "design", False),
        ("GeneNetworkAgent", "network", False),
    ],
)
async def test_route_attachment_forwarding_follows_capability_matrix(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: tuple[str, str, bool],
) -> None:
    """Expert forwarding follows the registry's exact ten-tool matrix."""
    tool_name, slug, forwarded = case
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
        monkeypatch,
        ToolSelection(
            tool_name,
            {"user_query": "q", "obs_file_list": ["selector-private"]},
        ),
    )
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "q",
            "obs_file_list": [path],
            "allowed_tools": [tool_name],
        },
    )
    if forwarded:
        assert response.status_code == 200
        assert captured[slug]["obs_file_list"] == [path]
    else:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == ("attachment_not_supported")
        assert slug not in captured


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
    assert response.json()["error"]["code"] == ("routing_contract_violation")
    assert response.json()["error"]["stage"] == "routing"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["error"]["message"] == (
        "The routing contract is invalid."
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
    assert response.json()["error"]["code"] == ("routing_contract_violation")
    assert response.json()["error"]["stage"] == "routing"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["error"]["message"] == (
        "The routing contract is invalid."
    )
    assert "ChatAgent" not in response.text
    assert "DataAgent" not in response.text


async def test_legacy_a2a_no_selection_cannot_relax_strict_route(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Legacy A2A optional selection cannot become strict-route fallback."""
    legacy_calls: list[str] = []

    async def legacy_no_selection(text: str) -> None:
        legacy_calls.append(text)
        return None

    monkeypatch.setattr(a2a_messages, "select_agent_tool", legacy_no_selection)
    assert await a2a_messages.select_agent_tool("legacy question") is None

    captured: dict[str, Any] = {}

    async def strict_no_selection(
        *,
        messages: list[dict[str, Any]],
        request: Any,
        completion: Any,
    ) -> SimpleNamespace:
        _ = messages, completion
        captured.update(
            {
                "tool_choice": request.tool_choice,
                "allowed_order": request.allowed_order,
            }
        )
        return _router_completion(empty_choices=True)

    monkeypatch.setattr(expert_router, "_run_completion", strict_no_selection)
    invoked = 0

    async def forbidden_invoke(
        **_kwargs: object,
    ) -> tuple[dict[str, Any], int]:
        nonlocal invoked
        invoked += 1
        raise AssertionError("strict route must stop before dispatch")

    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)
    response = await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json={
            "user_query": "strict question",
            "allowed_tools": ["ChatAgent", "DataAgent"],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == ("routing_contract_violation")
    assert captured == {
        "tool_choice": "required",
        "allowed_order": ("ChatAgent", "DataAgent"),
    }
    assert legacy_calls == ["legacy question"]
    assert invoked == 0
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")


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
    tasks_db_path: str,
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
    assert response.json()["error"]["code"] == (
        "selected_agent_invalid_argument"
    )
    assert response.json()["error"]["stage"] == "dispatch_validation"
    assert response.json()["error"]["retryable"] is False
    assert invoked == 0
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")
