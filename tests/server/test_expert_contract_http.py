# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Expert parity tests for the shared native HTTP run contract."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.support.http_fakes import assert_degraded_tracking_response

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.expert import ToolSelection
from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.runtime import (
    submit_recorder as submit_recorder_module,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission

pytestmark = pytest.mark.server


def _auth(api_key: str) -> dict[str, str]:
    """Return the bearer header used by the API fixtures."""
    return {"Authorization": f"Bearer {api_key}"}


def _patch_selection(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Make the Expert selector return one deterministic selection."""

    async def fake_select(*_args: Any, **_kwargs: Any) -> ToolSelection:
        return ToolSelection(tool_name, arguments)

    monkeypatch.setattr(api_app, "select_agent_tool", fake_select)


def _patch_analyst_selection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    goal: str,
) -> None:
    """Install the common valid Analyst selection shape."""
    _patch_selection(
        monkeypatch,
        "AnalystAgent",
        {"goal_description": goal, "data_list": {}, "obs_file_list": []},
    )


async def _post_forced_expert(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tool_name: str,
    *,
    body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Post one forced Expert request with optional contract fields."""
    payload: dict[str, Any] = {
        "user_query": "q",
        "allowed_tools": [tool_name],
        "forced_tool": tool_name,
    }
    if body:
        payload.update(body)
    return await api_client.post(
        "/v1/query/route",
        headers={**_auth(issued_api_key), **(extra_headers or {})},
        json=payload,
    )


def _contract_shape(body: dict[str, Any]) -> dict[str, Any]:
    """Keep stable envelope structure while ignoring generated identities."""
    result = body["result"]
    formatted = result["formatted"]
    execution = result["execution"]
    return {
        "object": body["object"],
        "agent": body["agent"],
        "status": body["status"],
        "task_count": len(body["task_ids"]),
        "has_run_id": "run_id" in body,
        "result_keys": tuple(sorted(result)),
        "formatted_keys": tuple(sorted(formatted)),
        "execution_keys": tuple(sorted(execution)),
    }


@dataclass(frozen=True, slots=True)
class _ParityCase:
    """One direct/native versus Expert request pair."""

    tool_name: str
    slug: str
    expected_status: int
    native_args: dict[str, Any]
    selected_args: dict[str, Any]


_PARITY_CASES = (
    pytest.param(
        _ParityCase(
            "ChatAgent",
            "chat",
            200,
            {"user_query": "q", "obs_file_list": []},
            {"user_query": "q"},
        ),
        id="chat-sync",
    ),
    pytest.param(
        _ParityCase(
            "DataAgent",
            "data",
            200,
            {"user_query": "q"},
            {"user_query": "q"},
        ),
        id="data-sync",
    ),
    pytest.param(
        _ParityCase(
            "AnalystAgent",
            "analyst",
            202,
            {
                "goal_description": "q",
                "data_list": {},
                "obs_file_list": [],
            },
            {
                "goal_description": "q",
                "data_list": {},
                "obs_file_list": [],
            },
        ),
        id="analyst-remote",
    ),
    pytest.param(
        _ParityCase(
            "InSilicoResearchAgent",
            "research",
            202,
            {"user_query": "q", "data_list": {}, "obs_file_list": []},
            {"user_query": "q", "data_list": {}, "obs_file_list": []},
        ),
        id="research-remote",
    ),
)


@pytest.mark.parametrize(
    "case",
    _PARITY_CASES,
)
async def test_expert_uses_native_run_contract(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: _ParityCase,
) -> None:
    """Expert and direct native runs expose the same envelope structure."""
    tool_name = case.tool_name
    slug = case.slug
    expected_status = case.expected_status
    calls = 0

    async def fake(_args: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if expected_status == 202 and slug == "research":
            return {
                "task_ids": [f"expert-parity-{calls}"],
                "output_dir": "tenant/expert-parity",
            }
        if expected_status == 202:
            return {
                "task_id": f"expert-parity-{calls}",
                "output_dir": "tenant/expert-parity",
            }
        return {"answer": f"answer-{calls}", "doc_list": []}

    handler = (
        records_submission(slug)(fake) if expected_status == 202 else fake
    )
    monkeypatch.setitem(server.TOOL_HANDLERS, tool_name, handler)

    direct = await api_client.post(
        f"/v1/agents/{slug}/runs",
        headers=_auth(issued_api_key),
        json={"arguments": case.native_args},
    )
    assert direct.status_code == expected_status

    _patch_selection(monkeypatch, tool_name, case.selected_args)
    routed = await _post_forced_expert(
        api_client,
        issued_api_key,
        tool_name,
    )

    assert routed.status_code == expected_status
    routed_body = routed.json()
    assert routed_body["agent"] == slug
    assert routed_body["agent"] != "expert"
    assert _contract_shape(routed_body) == _contract_shape(direct.json())


async def test_expert_partial_remote_preserves_execution_warnings(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial remote acceptance keeps warnings in the shared result."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {
            "task_ids": ["expert-partial-1"],
            "output_dir": "tenant/expert-partial",
            "submission_warnings": [
                {
                    "code": "partial_submission",
                    "retryable": False,
                    "rejected_count": 1,
                    "private_error": "must not leak",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        records_submission("research")(fake),
    )
    _patch_selection(
        monkeypatch,
        "InSilicoResearchAgent",
        {"user_query": "q", "data_list": {}, "obs_file_list": []},
    )

    response = await _post_forced_expert(
        api_client,
        issued_api_key,
        "InSilicoResearchAgent",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["agent"] == "research"
    assert body["id"]
    assert body["task_ids"] == ["expert-partial-1"]
    assert body["result"]["execution"]["warnings"] == [
        {
            "code": "partial_submission",
            "retryable": False,
            "stage": None,
        }
    ]
    assert "must not leak" not in response.text


async def test_expert_degraded_remote_preserves_accepted_task_ids(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A registry write failure keeps real remote work in the response."""

    def _raising_create_run(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.OperationalError("private registry failure")

    def _exploding_registry_factory(_db_path: str) -> SimpleNamespace:
        return SimpleNamespace(create_run=_raising_create_run)

    monkeypatch.setattr(
        submit_recorder_module,
        "RunRegistry",
        _exploding_registry_factory,
    )

    async def fake(_args: Any) -> dict[str, Any]:
        return {
            "task_id": "expert-degraded-1",
            "output_dir": "tenant/expert-degraded",
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )
    _patch_analyst_selection(monkeypatch, goal="q")

    response = await _post_forced_expert(
        api_client,
        issued_api_key,
        "AnalystAgent",
    )

    body = assert_degraded_tracking_response(response, "expert-degraded-1")
    assert body["agent"] == "analyst"
    assert body["result"]["execution"]["tracking"] == {"degraded": True}
    assert not RunRegistry(tasks_db_path).list_runs(owner="u1")


async def test_expert_run_info_keeps_native_identity_without_router_payload(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Expert rows persist the original query and only safe route metadata."""

    async def fake(_args: Any) -> dict[str, Any]:
        return {"task_id": "expert-identity-1", "output_dir": "tenant/out"}

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.ANALYST_AGENT.value,
        records_submission("analyst")(fake),
    )
    _patch_analyst_selection(monkeypatch, goal="selected goal")

    response = await _post_forced_expert(
        api_client,
        issued_api_key,
        "AnalystAgent",
        body={
            "user_query": "original user question",
            "history": [{"role": "user", "content": "private history"}],
            "dialogue_id": "dialogue-expert-1",
            "locale": "zh-CN",
        },
        extra_headers={"Accept-Language": "en-US"},
    )

    assert response.status_code == 202
    record = RunRegistry(tasks_db_path).list_runs(owner="u1")[0]
    info = record.request_info
    assert info.query == "original user question"
    assert info.dialogue_id == "dialogue-expert-1"
    assert info.tool_name == "AnalystAgent"
    assert info.locale == "zh-CN"
    assert info.request_json is not None
    assert json.loads(info.request_json) == {
        "agent": "analyst",
        "tool_name": "AnalystAgent",
        "user_query": "original user question",
        "dialogue_id": "dialogue-expert-1",
        "locale": "zh-CN",
    }
    assert "private history" not in info.request_json
