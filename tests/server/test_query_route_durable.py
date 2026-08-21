# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable local-wait and stream-family Expert route tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi.responses import StreamingResponse
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
from tests.support.asyncio_helpers import wait_until
from tests.support.handler_fakes import review_success_result

from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.mcp.formatting.agui import (
    run_finished,
    run_started,
)

pytestmark = pytest.mark.server

_LOCAL_WAIT_CASES = (
    pytest.param(
        ("DataAgent", "data", {"user_query": "q"}),
        id="data-local-wait",
    ),
    pytest.param(
        ("ReviewAgent", "review", {"user_query": "q"}),
        id="review-local-wait",
    ),
)
_STREAM_EXPERT_CASES = (
    pytest.param(
        ("ChatAgent", "chat", {"user_query": "routed question"}),
        id="chat-stream",
    ),
    pytest.param(
        (
            "KnowledgeAgent",
            "knowledge",
            {"user_query": "routed question", "obs_file_list": []},
        ),
        id="knowledge-stream",
    ),
    pytest.param(
        ("BriefGeneAgent", "brief_gene", {"user_query": "AT1G01010"}),
        id="brief-gene-stream",
    ),
)


@pytest.mark.parametrize("case", _LOCAL_WAIT_CASES)
async def test_expert_local_wait_selection_uses_background_launcher(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Expert Data and Review expose one durable local-wait run."""
    tool_name, slug, arguments = case
    if slug == "review":

        async def fake_review(**kwargs: Any) -> Any:
            del kwargs
            return SimpleNamespace(
                status="succeeded",
                result=review_success_result(),
            )

        monkeypatch.setattr(
            api_app, "_execute_review_with_run_id", fake_review
        )
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

    assert response.status_code == 202, response.text
    selector.assert_awaited_once()
    body = response.json()
    assert body["agent"] == slug
    assert body["status"] == "running"

    def completed() -> bool:
        record = RunRegistry(tasks_db_path).get_run(body["run_id"], owner="u1")
        return record is not None and record.status == "succeeded"

    await wait_until(completed)


@pytest.mark.parametrize("case", _STREAM_EXPERT_CASES)
async def test_route_stream_selection_returns_persisted_stream_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """Expert stream families return the real persisted stream identity."""
    tool_name, slug, arguments = case
    started: list[dict[str, Any]] = []

    async def prepared_stream(
        selected_tool: str,
        selected_arguments: dict[str, Any],
        *,
        run_id: str,
        dialogue_id: str | None,
        **_kwargs: Any,
    ) -> AsyncIterator[Any]:
        started.append(
            {
                "tool_name": selected_tool,
                "arguments": selected_arguments,
                "run_id": run_id,
            }
        )
        yield run_started(run_id, dialogue_id)
        yield run_finished(run_id)

    async def forbidden_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("routed stream must not use blocking invoke")

    selector = AsyncMock(return_value=ToolSelection(tool_name, arguments))
    monkeypatch.setattr(api_app, "select_agent_tool", selector)
    monkeypatch.setattr(api_app, "prepare_tool_stream", prepared_stream)
    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "routed question",
            "allowed_tools": [tool_name],
        },
    )

    assert response.status_code == 202, response.text
    selector.assert_awaited_once()
    assert len(started) == 1
    run_id = started[0]["run_id"]
    assert response.json() == {
        "id": run_id,
        "object": "agent.run",
        "agent": slug,
        "status": "running",
        "task_ids": [],
        "result": empty_agent_result(),
        "run_id": run_id,
    }
    record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
    assert record is not None
    assert record.spec.agent == slug


@pytest.mark.parametrize("case", _STREAM_EXPERT_CASES)
async def test_route_stream_selection_rejects_missing_run_id(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, dict[str, Any]],
) -> None:
    """A malformed stream without its run identity fails safely."""
    tool_name, _slug, arguments = case
    streamed: list[dict[str, Any]] = []

    async def missing_run_id() -> AsyncIterator[str]:
        yield (
            "event: RunStarted\n"
            'data: {"type":"RunStarted","dialogue_id":null}\n\n'
        )

    async def fake_stream(**kwargs: Any) -> StreamingResponse:
        streamed.append(kwargs)
        return StreamingResponse(
            missing_run_id(),
            media_type="text/event-stream",
        )

    async def forbidden_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("routed stream must not use blocking invoke")

    selector = AsyncMock(return_value=ToolSelection(tool_name, arguments))
    monkeypatch.setattr(api_app, "select_agent_tool", selector)
    monkeypatch.setattr(api_app, "_stream_chat_completion", fake_stream)
    monkeypatch.setattr(api_app, "_invoke_agent_run", forbidden_invoke)

    response = await _post_query_route(
        api_client,
        issued_api_key,
        {
            "user_query": "routed question",
            "allowed_tools": [tool_name],
        },
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_invariant_failed"
    assert "run_id" not in response.text
    selector.assert_awaited_once()
    assert len(streamed) == 1
