# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for native agent runs carrying a V1 conversation envelope."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.support.http_fakes import (
    build_instant_chat_context_envelope,
    open_asgi_client,
    running_agent_run_body,
)
from tests.support.resumable_asset_fakes import (
    ResumableAssetSpec,
    build_resumable_asset,
)

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.api.schemas import AgentRunRequest
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

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


def _native_context_delegated_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, str]:
    """Build a context-enabled app with a files:delegate principal."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite"))
        .create(user_id="web-service", scopes=["agents", "files:delegate"])
        .api_key
    )
    return api_app_module.create_app(), key


def _install_context_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    owner: str = "delegated-owner",
) -> tuple[str, str]:
    """Create one context dataset and document sharing the task database."""
    db_path = str(tmp_path / "tasks.sqlite")
    dataset = build_resumable_asset(
        tmp_path,
        db_path=db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename="context-data.csv",
            content=b"gene,value\nAT1G01010,3\n",
            purpose="dataset",
        ),
    )
    document = build_resumable_asset(
        tmp_path,
        db_path=db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename="context.pdf",
            content=b"%PDF-1.4 context",
            purpose="chat_attachment",
        ),
    )
    monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: dataset.resolver,
    )
    return dataset.asset_id, document.asset_id


def _context_store_text(db_path: Path) -> str:
    """Return bounded serialized context rows for redaction assertions."""
    with sqlite3.connect(str(db_path)) as connection:
        contexts = connection.execute(
            "SELECT * FROM conversation_contexts"
        ).fetchall()
        turns = connection.execute(
            "SELECT * FROM conversation_turns"
        ).fetchall()
        rows = {"contexts": contexts, "turns": turns}
    return json.dumps(rows)


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


def _native_attachment_request(
    agent: str,
    tool_name: str,
    *,
    attachments: list[dict[str, str]],
) -> dict[str, Any]:
    """Build one attachment-bearing native context request."""
    request = _native_request(agent, tool_name)
    request.update(
        {
            "attachments": attachments,
            "owner_subject": "delegated-owner",
        }
    )
    return request


@dataclass(slots=True)
class _ContextAttachmentCallState:
    """Captured private preparation/invocation calls for context tests."""

    invoke_calls: list[dict[str, Any]]


def _patch_context_attachment_invocation(
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
) -> _ContextAttachmentCallState:
    """Patch native invocation for context replay tests."""
    state = _ContextAttachmentCallState([])

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        state.invoke_calls.append(kwargs)
        run_id = f"context-{agent}"
        evidence = kwargs["attachment_evidence"]
        assert evidence.attachment_owner == "delegated-owner"
        return (
            running_agent_run_body(run_id, agent),
            202,
        )

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fake_invoke)
    return state


async def _post_native_context_twice(
    monkeypatch: pytest.MonkeyPatch,
    app: Any,
    key: str,
    agent: str,
    request: dict[str, Any],
) -> tuple[Any, Any]:
    """POST the same native context request twice for replay assertions."""
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
    return response, retry


def test_native_agent_request_keeps_legacy_serialization_without_context() -> (
    None
):
    """The private context field is absent from legacy request JSON."""
    payload = AgentRunRequest(arguments={"user_query": "legacy"})

    assert AgentRunRequest.model_fields.get("dataset_description") is None
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
    ("agent", "tool_name"),
    [("chat", "ChatAgent"), ("analyst", "AnalystAgent")],
)
async def test_native_context_store_failure_returns_retryable_503(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    tool_name: str,
) -> None:
    """Pre-invocation storage failures are safe and do not call agents."""
    app, key = _native_context_setup(monkeypatch, tmp_path)
    calls: list[dict[str, Any]] = []

    async def fail_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        calls.append(kwargs)
        raise AssertionError("context storage failed before agent invocation")

    def fail_begin(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("private storage detail")

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fail_invoke)
    monkeypatch.setattr(ConversationContextStore, "begin_turn", fail_begin)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.native-context.test"
    ) as client:
        response = await client.post(
            f"/v1/agents/{agent}/runs",
            headers={"Authorization": f"Bearer {key}"},
            json=_native_request(agent, tool_name),
        )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "conversation_context_unavailable"
    assert error["message"] == "conversation context unavailable"
    assert error["stage"] == "context"
    assert error["retryable"] is True
    assert "private storage detail" not in response.text
    assert not calls


@pytest.mark.parametrize(
    ("agent", "tool_name", "status_code"),
    [("chat", "ChatAgent", 200), ("analyst", "AnalystAgent", 202)],
)
async def test_native_context_preserves_outcome_when_stage_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    tool_name: str,
    status_code: int,
) -> None:
    """Post-outcome stage loss keeps the original result and run identity."""
    app, key = _native_context_setup(monkeypatch, tmp_path)
    calls: list[dict[str, Any]] = []

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        calls.append(kwargs)
        run_id = f"opaque-{agent}"
        status = "running" if status_code == 202 else "succeeded"
        return (
            {
                "id": run_id,
                "run_id": run_id,
                "object": "agent.run",
                "agent": agent,
                "status": status,
                "task_ids": [],
                "result": empty_agent_result(),
            },
            status_code,
        )

    def fail_stage(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("private storage detail")

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fake_invoke)
    monkeypatch.setattr(ConversationContextStore, "stage_turn", fail_stage)
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

    assert response.status_code == status_code
    body = response.json()
    assert body["id"] == f"opaque-{agent}"
    assert body["run_id"] == body["id"]
    assert body["conversation_context_degraded"] is True
    assert "conversation_context" not in body
    assert retry.status_code == 409
    assert len(calls) == 1


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
        body: dict[str, Any] = {
            "id": run_id,
            "run_id": run_id,
            "object": "agent.run",
        }
        body.update(
            agent=agent,
            status="running",
            task_ids=[],
            result=empty_agent_result(),
        )
        return body, 202

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


@pytest.mark.parametrize(
    ("agent", "tool_name"),
    [("analyst", "AnalystAgent"), ("research", "InSilicoResearchAgent")],
)
async def test_native_context_dataset_attachments_prepare_once_and_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    tool_name: str,
) -> None:
    """Native context replays staged 202 without re-preparing attachments."""
    app, key = _native_context_delegated_setup(monkeypatch, tmp_path)
    dataset_id, document_id = _install_context_assets(monkeypatch, tmp_path)
    call_state = _patch_context_attachment_invocation(monkeypatch, agent)
    request = _native_attachment_request(
        agent,
        tool_name,
        attachments=[{"asset_id": dataset_id}, {"asset_id": document_id}],
    )
    request["dataset_description"] = (
        "stale context dataset_description should be dropped"
    )
    response, retry = await _post_native_context_twice(
        monkeypatch, app, key, agent, request
    )

    assert response.status_code == 202, response.text
    assert retry.status_code == 202
    assert retry.json()["run_id"] == response.json()["run_id"]
    assert len(call_state.invoke_calls) == 1
    arguments = call_state.invoke_calls[0]["arguments"]
    assert list(arguments["data_list"].values()) == [""]
    assert len(arguments["obs_file_list"]) == 1
    assert json.loads(call_state.invoke_calls[0]["request_json"] or "") == {
        "dialogue_id": None,
        "locale": "en-US",
        "route": agent,
    }
    assert "dataset_description" not in (
        call_state.invoke_calls[0]["request_json"] or ""
    )
    assert (
        call_state.invoke_calls[0]["attachment_evidence"].attachment_owner
        == "delegated-owner"
    )
    rendered = (
        response.text
        + retry.text
        + _context_store_text(tmp_path / "tasks.sqlite")
    )
    rendered += json.dumps(response.json(), sort_keys=True)
    rendered += json.dumps(retry.json(), sort_keys=True)
    for sentinel in (
        dataset_id,
        document_id,
        "delegated-owner",
        "context-data.csv",
        "stale context dataset_description should be dropped",
    ):
        assert sentinel not in rendered
    assert '"dataset_description"' not in rendered


async def test_native_context_unsupported_dataset_returns_attachment_422(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unsupported native context Agents keep attachment validation errors."""
    app, key = _native_context_delegated_setup(monkeypatch, tmp_path)
    dataset_id, _document_id = _install_context_assets(monkeypatch, tmp_path)

    async def fail_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("unsupported attachment reached invoke")

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fail_invoke)
    request = _native_attachment_request(
        "brief_gene",
        "BriefGeneAgent",
        attachments=[{"asset_id": dataset_id}],
    )
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.native-context.test"
    ) as client:
        response = await client.post(
            "/v1/agents/brief_gene/runs",
            headers={"Authorization": f"Bearer {key}"},
            json=request,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert "unsupported attachment reached invoke" not in response.text
