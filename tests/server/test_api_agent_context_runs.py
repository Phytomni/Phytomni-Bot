# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for native agent runs carrying a V1 conversation envelope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.support.http_fakes import (
    build_instant_chat_context_envelope,
    open_asgi_client,
)

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.api.schemas import AgentRunRequest

pytestmark = pytest.mark.server


def _native_context_envelope(
    tool_name: str,
    *,
    mode: str = "expert",
    requested_agent_id: str | None = None,
    allowed_agent_ids: list[str] | None = None,
    turn_id: str = "1",
) -> dict[str, Any]:
    """Build one native-agent context envelope for route-level tests."""
    requested = tool_name if requested_agent_id is None else requested_agent_id
    envelope = build_instant_chat_context_envelope(turn_id)
    envelope.update(
        {
            "request_id": f"native-context-{turn_id}",
            "mode": mode,
            "current_message": {
                "content": "Continue the bounded task.",
                "locale": "en-US",
            },
            "requested_agent_id": requested,
            "allowed_agent_ids": allowed_agent_ids or [tool_name],
            "history_delta": [
                {
                    "turn_id": turn_id,
                    "role": "user",
                    "content": "Continue the bounded task.",
                }
            ],
        }
    )
    return envelope


def _native_context_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, str]:
    """Build an enabled app and key with isolated stores."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite")).create(user_id="u1").api_key
    )
    return api_app_module.create_app(), key


def _native_context_arguments(slug: str) -> dict[str, Any]:
    """Return schema-shaped arguments for one native agent slug."""
    arguments: dict[str, dict[str, Any]] = {
        "chat": {"user_query": "chat request", "obs_file_list": []},
        "knowledge": {"user_query": "knowledge request", "obs_file_list": []},
        "data": {"user_query": "count rice genes"},
        "brief_gene": {"user_query": "AT1G01010"},
        "analyst": {
            "goal_description": "bounded analysis",
            "data_list": {},
            "obs_file_list": [],
        },
        "deep_genome": {"species_code": "ath", "gene_id": "AT1G01010"},
        "research": {
            "user_query": "bounded research",
            "data_list": {},
            "obs_file_list": [],
        },
        "design": {
            "species_code": "ath",
            "gene_id": "AT1G01010",
            "obs_file_list": [],
        },
        "network": {
            "species_code": "ath",
            "to_id": "TO:0000001",
            "obs_file_list": [],
        },
    }
    return arguments[slug]


def _native_request(agent: str, tool_name: str) -> dict[str, Any]:
    """Build one context-bearing native request."""
    return {
        "arguments": _native_context_arguments(agent),
        "conversation": _native_context_envelope(tool_name),
    }


def test_native_agent_request_keeps_legacy_serialization_without_context() -> (
    None
):
    """The private context field is absent from legacy request JSON."""
    payload = AgentRunRequest(arguments={"user_query": "legacy"})

    assert payload.model_dump(exclude_none=True) == {
        "arguments": {"user_query": "legacy"}
    }


async def test_native_context_valid_envelope_is_rejected_when_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A valid V1 envelope cannot activate the protocol behind its flag."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "0")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite")).create(user_id="u1").api_key
    )

    async with open_asgi_client(
        monkeypatch,
        api_app_module.create_app(),
        base_url="http://api.native-context.test",
    ) as client:
        response = await client.post(
            "/v1/agents/chat/runs",
            headers={"Authorization": f"Bearer {key}"},
            json=_native_request("chat", "ChatAgent"),
        )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "resource not found"


@pytest.mark.parametrize(
    ("agent", "envelope", "expected_status"),
    [
        ("chat", _native_context_envelope("ChatAgent", mode="instant"), 422),
        (
            "chat",
            {
                **_native_context_envelope("ChatAgent"),
                "requested_agent_id": None,
            },
            422,
        ),
        (
            "chat",
            _native_context_envelope(
                "ChatAgent",
                requested_agent_id="DataAgent",
                allowed_agent_ids=["DataAgent"],
            ),
            422,
        ),
        ("missing", _native_context_envelope("ChatAgent"), 404),
    ],
)
async def test_native_context_validates_url_selected_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    envelope: dict[str, Any],
    expected_status: int,
) -> None:
    """Native context never lets the envelope select another URL slug."""
    app, key = _native_context_setup(monkeypatch, tmp_path)

    async def fail_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("invalid native context reached agent lifecycle")

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fail_invoke)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.native-context.test"
    ) as client:
        response = await client.post(
            f"/v1/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "arguments": _native_context_arguments("chat"),
                "conversation": envelope,
            },
        )

    assert response.status_code == expected_status
    assert "invalid native context reached" not in response.text


_NATIVE_CONTEXT_SYNC_CASES = (
    ("chat", "ChatAgent"),
    ("knowledge", "KnowledgeAgent"),
    ("data", "DataAgent"),
    ("brief_gene", "BriefGeneAgent"),
)


@pytest.mark.parametrize(("agent", "tool_name"), _NATIVE_CONTEXT_SYNC_CASES)
async def test_native_context_reuses_sync_lifecycle_for_sync_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    tool_name: str,
) -> None:
    """Sync native slugs reuse the lifecycle and stage only once."""
    app, key = _native_context_setup(monkeypatch, tmp_path)
    calls: list[dict[str, Any]] = []

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        calls.append(kwargs)
        run_id = f"native-{agent}"
        result = empty_agent_result()
        result["formatted"]["answer"] = "native answer"
        result["formatted"]["tabular"] = {
            "headers": ["gene"],
            "rows": [["AT1G01010"]],
        }
        return (
            {
                "id": run_id,
                "run_id": run_id,
                "object": "agent.run",
                "agent": agent,
                "status": "succeeded",
                "task_ids": [],
                "result": result,
            },
            200,
        )

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fake_invoke)
    request = _native_request(agent, tool_name)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.native-context.test"
    ) as client:
        response = await client.post(
            f"/v1/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {key}"},
            json=request,
        )
        retry = await client.post(
            f"/v1/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {key}"},
            json=request,
        )

    assert response.status_code == 200, response.text
    assert retry.status_code == 200
    assert response.json()["conversation_context"]["route_source"] == (
        "explicit_selection"
    )
    assert (
        retry.json()["conversation_context"]
        == response.json()["conversation_context"]
    )
    assert len(calls) == 1
    assert calls[0]["agent"] == agent
    assert calls[0]["conversation_messages"] == ()
    assert calls[0]["request_json"] is not None
    if agent == "data":
        context = response.json()["conversation_context"]
        assert context["selected_agent_id"] == tool_name
        assert "native answer" not in json.dumps(context)


_NATIVE_CONTEXT_ASYNC_CASES = (
    ("analyst", "AnalystAgent"),
    ("deep_genome", "DeepGenomeAgent"),
    ("research", "InSilicoResearchAgent"),
    ("design", "DigitalDesignAgent"),
    ("network", "GeneNetworkAgent"),
)


@pytest.mark.parametrize(("agent", "tool_name"), _NATIVE_CONTEXT_ASYNC_CASES)
async def test_native_context_reuses_async_acceptance_for_async_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    tool_name: str,
) -> None:
    """Async native slugs stage proven running acceptance without routing."""
    app, key = _native_context_setup(monkeypatch, tmp_path)
    calls: list[dict[str, Any]] = []

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        calls.append(kwargs)
        run_id = f"native-{agent}"
        return (
            {
                "id": run_id,
                "run_id": run_id,
                "object": "agent.run",
                "agent": agent,
                "status": "running",
                "task_ids": [],
                "result": empty_agent_result(),
            },
            202,
        )

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fake_invoke)
    request = _native_request(agent, tool_name)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.native-context.test"
    ) as client:
        response = await client.post(
            f"/v1/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {key}"},
            json=request,
        )
        retry = await client.post(
            f"/v1/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {key}"},
            json=request,
        )

    assert response.status_code == 202
    assert retry.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["id"] == body["run_id"]
    assert body["conversation_context"]["route_source"] == (
        "explicit_selection"
    )
    assert len(calls) == 1
    assert calls[0]["agent"] == agent
    assert calls[0]["arguments"]["locale"] == "en-US"
    if agent == "research":
        assert calls[0]["arguments"]["user_query"] == "bounded research"
    else:
        assert "user_query" not in calls[0]["arguments"]
