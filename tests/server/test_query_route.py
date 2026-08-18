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
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from tests.support.handler_fakes import (
    network_run_arguments,
    patch_handler_runtime,
    patch_knowledge_agent,
)

import mcp_server_phytomni.api.app as api_app
from mcp_server_phytomni import server
from mcp_server_phytomni.agents.expert import (
    ToolSelection,
)
from mcp_server_phytomni.agents.review import agent as review_agent
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.mcp import handlers as mcp_handlers
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.run_registry import RunRecord, RunRegistry

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


def _context_follow_up_envelope(
    agent: str,
    query: str,
    *,
    turn_id: str = "2",
    ledger_version: str = "c" * 64,
    artifact_refs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build the bounded second-turn envelope shared by context tests."""
    envelope = _conversation_envelope(
        turn_id=turn_id,
        requested_agent_id=agent,
        allowed_agent_ids=[agent],
        base_business_context_version=1,
    )
    envelope["ledger_cursor"] = int(turn_id)
    envelope["ledger_version"] = ledger_version
    envelope["current_message"]["content"] = query
    envelope["history_delta"] = [
        {"turn_id": turn_id, "role": "user", "content": query}
    ]
    if artifact_refs is not None:
        envelope["artifact_refs"] = artifact_refs
    return envelope


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


async def _post_query_route(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    payload: dict[str, Any],
) -> httpx.Response:
    """Post one payload to the authenticated Expert route."""
    return await api_client.post(
        "/v1/query/route",
        headers=_auth(issued_api_key),
        json=payload,
    )


async def _post_context_route(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    agent: str,
    envelope: dict[str, Any],
) -> httpx.Response:
    """Post one canonical context envelope to the Expert route."""
    return await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "legacy query is ignored by V1 dispatch",
            "allowed_tools": [agent],
            "conversation": envelope,
        },
    )


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


def _placeholder_config() -> object:
    """Return a dependency-free config placeholder for handler tests."""
    return object()


def _placeholder_override(**_kwargs: Any) -> object:
    """Return a dependency-free override placeholder for handler tests."""
    return object()


def _patch_review_runtime(
    monkeypatch: pytest.MonkeyPatch,
    fake_agent: Any,
) -> None:
    """Route Review through the offline handler and a fake graph agent."""
    monkeypatch.setattr(mcp_handlers, "ReviewConfig", _placeholder_config)
    patch_handler_runtime(monkeypatch, scratch_path="/tmp/review")
    monkeypatch.setattr(
        review_agent,
        "get_cached_agent",
        lambda *_args, **_kwargs: fake_agent,
    )


class _ReviewFakeApp:
    """Small graph-app double shared by the Review context tests."""

    def __init__(self, stable_state: dict[str, Any] | None = None) -> None:
        self.stable_state = stable_state if stable_state is not None else {}
        self.state_reads: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.deleted: list[str] = []

    async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
        """Record a state read and return the requested candidate state."""
        self.state_reads.append(config)
        thread_id = config["configurable"]["thread_id"]
        return self.states.get(thread_id, self.stable_state)

    async def aupdate_state(
        self, config: dict[str, Any], *, values: dict[str, Any]
    ) -> None:
        """Record updates; no test route should mutate the graph checkpoint."""
        self.updates.append((config, values))

    async def adelete_thread(self, thread_id: str) -> None:
        """Record candidate cleanup requested by a failed Review graph."""
        self.deleted.append(thread_id)


class _ReviewFakeAgent:
    """Configurable Review graph double with the public production seams."""

    def __init__(
        self,
        app: _ReviewFakeApp,
        config: SimpleNamespace | None = None,
    ) -> None:
        self.app = app
        self.config = config or SimpleNamespace()
        self.chat_prompts: list[str] = []
        self.graph_calls = 0
        self.graph_threads: list[str | None] = []

    async def _chat(self, prompt: str) -> dict[str, Any]:
        """Return the configured local Review response."""
        self.chat_prompts.append(prompt)
        chat_error = getattr(self.config, "chat_error", None)
        if chat_error is not None:
            raise chat_error
        response = getattr(self.config, "chat_response", None)
        if callable(response):
            return cast(dict[str, Any], response(prompt))
        return (
            response
            if response is not None
            else {"choices": [{"message": {"content": ""}}]}
        )

    async def chat(self, prompt: str) -> dict[str, Any]:
        """Expose the public Review chat seam used by production."""
        return await self._chat(prompt)

    async def arun(self, **kwargs: Any) -> dict[str, Any]:
        """Return or raise the configured graph outcome and record its
        thread."""
        self.graph_calls += 1
        thread_id = kwargs.get("thread_id")
        self.graph_threads.append(thread_id)
        run_error = getattr(self.config, "run_error", None)
        if run_error is not None:
            raise run_error
        run_state = getattr(self.config, "run_state", None)
        if thread_id is not None and run_state is not None:
            self.app.states[thread_id] = run_state
        response = getattr(self.config, "run_response", None)
        if callable(response):
            return cast(dict[str, Any], response(kwargs))
        if response is not None:
            return response
        return {
            "choices": [{"message": {"content": "Current public answer."}}]
        }


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


def _data_route_config() -> SimpleNamespace:
    """Return the private Data config used by the context route tests."""
    return SimpleNamespace(
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


async def _reject_explicit_data_router(
    *_args: Any, **_kwargs: Any
) -> ToolSelection:
    """Guard that explicit Data context never invokes autonomous routing."""
    raise AssertionError("explicit Data selection must not route")


def _assert_data_projection(
    store: ConversationContextStore,
    conversation_key: UUID,
    turn_id: str,
    required: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> None:
    """Assert that a stored Data delta keeps metadata but not row contents."""
    staged = store.load_turn(str(conversation_key), turn_id)
    assert staged is not None
    assert staged.delta is not None
    payload = json.dumps(staged.delta, sort_keys=True)
    for marker in required:
        assert marker in payload
    for marker in forbidden:
        assert marker not in payload


def _success_agent_body(agent: str, answer: str) -> dict[str, Any]:
    """Return the minimal successful native-agent result used by route
    tests."""
    return {
        "id": f"{agent}-run",
        "object": "agent.run",
        "agent": agent,
        "status": "succeeded",
        "task_ids": [],
        "result": {"formatted": {"answer": answer}},
    }


def _patch_knowledge_runtime(
    monkeypatch: pytest.MonkeyPatch,
    fake_agent: Any,
) -> None:
    """Install the offline Knowledge handler seams used by context tests."""
    monkeypatch.setattr(mcp_handlers, "KnowledgeConfig", _placeholder_config)
    patch_handler_runtime(monkeypatch, scratch_path="/tmp/knowledge")
    patch_knowledge_agent(
        monkeypatch,
        fake_agent,
        _placeholder_override,
        _placeholder_override,
    )


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
        {"species_code": "ath", "gene_id": "AT1G01010"},
    ),
    (
        "GeneNetworkAgent",
        "network",
        {"species_code": "ath", "to_id": "TO:0000001"},
    ),
)
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
            network_run_arguments(),
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
