# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dispatch-validation error envelopes for Expert route."""

from __future__ import annotations

from typing import Any

import pytest
from tests.server.test_query_route import (
    RunRegistry,
    ToolSelection,
    _patch_select,
    _post_query_route,
    httpx,
    server,
)

from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)

pytestmark = pytest.mark.server


async def _post_invalid_knowledge_arguments(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[httpx.Response, int]:
    """Submit one schema-invalid KnowledgeAgent selection."""
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
    response = await _post_query_route(
        api_client,
        issued_api_key,
        {"user_query": "rice", "allowed_tools": ["KnowledgeAgent"]},
    )
    return response, invoked


async def test_route_invalid_arguments_returns_400_and_records_failure(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """Schema-invalid arguments create one sanitized durable failure.

    The KnowledgeAgent schema requires ``user_query``; an empty argument
    object makes ``invoke_tool_enveloped`` raise ``McpError`` with
    ``INVALID_PARAMS``, which the route maps to 400 rather than letting it
    fall through to the generic 500 handler.
    """
    response, invoked = await _post_invalid_knowledge_arguments(
        api_client, issued_api_key, monkeypatch
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "selected_agent_invalid_argument"
    )
    assert response.json()["error"]["stage"] == "dispatch_validation"
    assert response.json()["error"]["retryable"] is False
    assert invoked == 0
    records = RunRegistry(tasks_db_path).list_runs(owner="u1")
    assert len(records) == 1
    record = records[0]
    assert record.spec.agent == "knowledge"
    assert record.status == "failed"
    assert record.error is None
    assert record.failure is None

    execution_id = record.request_info.execution_id
    assert execution_id
    journal = SQLiteExecutionJournal(tasks_db_path)
    projection = journal.get_projection(execution_id, owner="u1")
    assert projection.status.value == "failed"
    assert projection.terminal is not None
    assert projection.terminal.status == "failed"

    page = journal.list_events(execution_id, owner="u1", limit=20)
    assert page is not None
    terminal = page.items[-1].to_public_dict()
    assert terminal["type"] == "execution.failed"
    assert terminal["public_payload"] == {
        "code": "business_execution_failed",
        "retryable": False,
    }
