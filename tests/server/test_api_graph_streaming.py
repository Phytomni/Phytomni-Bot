# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for cited-agent graph streaming and terminal run projection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from tests.support.graph_streaming import (
    FakeCitedStreamApp,
    assert_default_cited_customs,
    guard_network_escape,
)

from mcp_server_phytomni.agents.shared.citation_metadata import (
    CITATION_STATUS_KEY,
    CITATION_STATUS_MISSING,
)
from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.runtime import run_registry as run_registry_module
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


async def test_stream_phyto_knowledge_emits_agui_frames(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    extract_run_started_id: Callable[[str], str],
) -> None:
    """Knowledge graph streaming emits stage, cited, and terminal frames."""
    guard_network_escape(monkeypatch)
    fake_app = FakeCitedStreamApp(
        stage_node="retrieve_node", answer="Rice photosynthesis [1]."
    )

    def _fake_target(
        _user_query: str,
        obs_file_list: Any = None,
        locale: Any = None,
        *,
        conversation_messages: Any = (),
        retrieval_query: Any = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Return the fake app and a minimal initial state."""
        del obs_file_list, locale, conversation_messages, retrieval_query
        return fake_app, {"user_query": _user_query}

    monkeypatch.setattr(mcp_app, "knowledge_stream_target", _fake_target)

    async def _mark_missing(_tool_name: str, raw: Any) -> None:
        """Project a deterministic metadata miss without external lookup."""
        raw["choices"][0]["message"]["doc_list"][0][
            CITATION_STATUS_KEY
        ] = CITATION_STATUS_MISSING

    monkeypatch.setattr(mcp_app, "_maybe_enrich_cited", _mark_missing)
    response = await chat_completion(
        api_client, issued_api_key, model="phyto-knowledge", stream=True
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: RunStarted\n" in body
    assert "event: StepStarted\n" in body
    assert "event: TextMessageContent\n" in body
    assert "event: Custom\n" in body
    assert "event: RunFinished\n" in body
    assert "Rice photosynthesis <sup>1</sup>." in body
    assert '"formatted_citation": "T1"' in body
    assert '"name": "phyto.metadata"' in body
    assert '"citation_metadata_degraded": true' in body
    assert body.rstrip().endswith("data: [DONE]")
    assert fake_app.thread_id() == extract_run_started_id(body)


async def test_stream_phyto_review_emits_agui_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review graph streaming preserves stage and cited custom frames."""
    guard_network_escape(monkeypatch)
    fake_app = FakeCitedStreamApp(
        stage_node="retrieve_reduce_node", answer="Review evidence [1]."
    )

    def _fake_target(
        _user_query: str, obs_file_list: Any = None, locale: Any = None
    ) -> tuple[Any, dict[str, Any]]:
        """Return the fake app and a minimal initial state."""
        del obs_file_list, locale
        return fake_app, {"user_query": _user_query}

    monkeypatch.setattr(mcp_app, "review_stream_target", _fake_target)

    async def _no_enrich(_tool_name: str, _raw: Any) -> None:
        """Skip bibliographic enrichment for this offline graph test."""

    monkeypatch.setattr(mcp_app, "_maybe_enrich_cited", _no_enrich)
    events = [
        event
        async for event in mcp_app.invoke_tool_streamed(
            "ReviewAgent",
            {"user_query": "review", "obs_file_list": []},
            run_id="run-review",
            dialogue_id="dlg-review",
        )
    ]

    types = [event.type for event in events]
    assert types[0] == "RunStarted"
    assert "StepStarted" in types
    assert "TextMessageContent" in types
    assert "Custom" in types
    assert types[-1] == "RunFinished"
    assert any(
        event.type == "StepStarted" and event.data["step_name"] == "retrieving"
        for event in events
    )
    customs = {
        event.data["name"]: event.data["value"]
        for event in events
        if event.type == "Custom"
    }
    assert_default_cited_customs(customs)


async def test_streamed_knowledge_run_reconcile_short_circuits(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    stream_test_tools: Any,
) -> None:
    """A terminal streamed run skips child-task reconciliation."""
    guard_network_escape(monkeypatch)
    fake_app = FakeCitedStreamApp(
        stage_node="retrieve_node", answer="Rice photosynthesis [1]."
    )

    def _fake_target(
        _user_query: str,
        obs_file_list: Any = None,
        locale: Any = None,
        *,
        conversation_messages: Any = (),
        retrieval_query: Any = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Return the fake app and a minimal initial state."""
        del obs_file_list, locale, conversation_messages, retrieval_query
        return fake_app, {"user_query": _user_query}

    monkeypatch.setattr(mcp_app, "knowledge_stream_target", _fake_target)

    async def _no_enrich(_tool_name: str, _raw: Any) -> None:
        """Skip bibliographic enrichment for this offline graph test."""

    monkeypatch.setattr(mcp_app, "_maybe_enrich_cited", _no_enrich)

    async def _boom_reconcile(_task_id: str) -> dict[str, Any]:
        """Fail loudly if reconcile fires on a terminal run."""
        raise AssertionError(
            "reconcile_task must not run for a terminal streamed run"
        )

    monkeypatch.setattr(run_registry_module, "reconcile_task", _boom_reconcile)
    response = await chat_completion(
        api_client, issued_api_key, model="phyto-knowledge", stream=True
    )
    assert response.status_code == 200
    run_id = stream_test_tools.extract_run_started_id(response.text)

    registry = RunRegistry(db_path=stream_test_tools.tasks_db_path)
    settled = registry.get_run(run_id, owner="u1")
    assert settled is not None
    assert settled.status == "succeeded"

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "succeeded"
