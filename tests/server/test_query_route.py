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
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from tests.support.chat_fakes import install_chat_handler
from tests.support.expert_router_fakes import patch_expert_router

import mcp_server_phytomni.api.a2a.messages as a2a_messages
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
from mcp_server_phytomni.runtime.run_registry import RunRecord, RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission
from mcp_server_phytomni.runtime.upload_registry import (
    UploadMetadata,
    UploadRegistry,
)
from mcp_server_phytomni.storage.obs_storage import obs_path_from_key

pytestmark = pytest.mark.server


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
