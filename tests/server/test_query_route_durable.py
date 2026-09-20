# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable local Expert route tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

from tests.server.test_query_route import (
    RunRegistry,
    ToolSelection,
    _post_query_route,
    _stub_tool_handler,
    api_app,
    httpx,
    pytest,
    server,
)
from tests.support.execution_contract_fixtures import install_successful_review

from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)

pytestmark = pytest.mark.server

_LOCAL_SYNC_CASES = (
    pytest.param(
        ("DataAgent", "data", {"user_query": "q"}),
        id="data-sync",
    ),
    pytest.param(
        ("ReviewAgent", "review", {"user_query": "q"}),
        id="review-sync",
    ),
)


@dataclass(frozen=True)
class _RouteCase:
    """One synchronous expert-route selection and its provider arguments."""

    tool_name: str
    slug: str
    arguments: dict[str, Any]


_SYNC_EXPERT_CASES = (
    pytest.param(
        _RouteCase("ChatAgent", "chat", {"user_query": "routed question"}),
        id="chat-sync",
    ),
    pytest.param(
        _RouteCase(
            "KnowledgeAgent",
            "knowledge",
            {"user_query": "routed question", "obs_file_list": []},
        ),
        id="knowledge-sync",
    ),
    pytest.param(
        _RouteCase(
            "BriefGeneAgent", "brief_gene", {"user_query": "AT1G01010"}
        ),
        id="brief-gene-sync",
    ),
)


@pytest.mark.parametrize("case", _LOCAL_SYNC_CASES)
async def test_expert_local_selection_returns_synchronous_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Expert Data and Review return one persisted synchronous run."""
    tool_name, slug, arguments = case
    if slug == "review":
        install_successful_review(monkeypatch, run_id=None)
    else:
        _stub_tool_handler(
            monkeypatch,
            server.PhytomniAgents.DATA_AGENT.value,
            {"answer": "ok", "doc_list": []},
        )
    selector = AsyncMock(return_value=ToolSelection(tool_name, arguments))
    monkeypatch.setattr(api_app, "select_agent_tool", selector)
    payload = {"user_query": "q", "allowed_tools": [tool_name]}
    response = await _post_query_route(
        api_client,
        issued_api_key,
        payload,
    )

    assert response.status_code == 200, response.text
    selector.assert_awaited_once()
    body = response.json()
    assert body["agent"] == slug
    assert body["status"] == "succeeded"
    assert body["id"] == body["run_id"]
    assert body["task_ids"] == []
    record = RunRegistry(tasks_db_path).get_run(body["run_id"], owner="u1")
    assert record is not None
    assert record.status == "succeeded"


@pytest.mark.parametrize("case", _SYNC_EXPERT_CASES)
async def test_route_sync_selection_runs_and_settles_in_runtime(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: _RouteCase,
) -> None:
    """Expert local selections invoke once and persist a terminal identity."""
    invoked: list[Any] = []

    async def provider(args: Any) -> dict[str, Any]:
        invoked.append(args)
        return {"answer": "ok", "doc_list": []}

    async def forbidden_stream(**_kwargs: Any) -> None:
        raise AssertionError("Expert local runs must not open an SSE stream")

    selector = AsyncMock(
        return_value=ToolSelection(case.tool_name, case.arguments)
    )
    monkeypatch.setattr(api_app, "select_agent_tool", selector)
    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        {
            "chat": server.PhytomniAgents.CHAT_AGENT.value,
            "knowledge": server.PhytomniAgents.KNOWLEDGE_AGENT.value,
            "brief_gene": server.PhytomniAgents.BRIEF_GENE_AGENT.value,
        }[case.slug],
        provider,
    )
    monkeypatch.setattr(api_app, "_stream_chat_response", forbidden_stream)

    execution_id = f"turn-expert-sync-{case.slug}"
    response = await api_client.post(
        "/v1/query/route",
        headers={
            "Authorization": f"Bearer {issued_api_key}",
            "X-Phyto-Execution-Id": execution_id,
        },
        json={
            "user_query": "routed question",
            "allowed_tools": [case.tool_name],
        },
    )

    assert response.status_code == 200, response.text
    selector.assert_awaited_once()
    assert len(invoked) == 1
    body = response.json()
    assert body["agent"] == case.slug
    assert body["status"] == "succeeded"
    assert body["id"] == body["run_id"]
    record = RunRegistry(tasks_db_path).get_run(body["run_id"], owner="u1")
    assert record is not None
    assert record.spec.agent == case.slug
    assert record.status == "succeeded"
    assert record.request_info.execution_id == execution_id
    reservation = SQLiteExecutionReservationRepository(tasks_db_path).get(
        owner="u1",
        execution_id=execution_id,
    )
    assert reservation.run_id == body["run_id"]
    assert reservation.status.value == "succeeded"
    page = SQLiteExecutionJournal(tasks_db_path).list_events(
        execution_id,
        owner="u1",
        limit=100,
    )
    assert page is not None
    assert [event.type.value for event in page.items].count(
        "execution.succeeded"
    ) == 1
