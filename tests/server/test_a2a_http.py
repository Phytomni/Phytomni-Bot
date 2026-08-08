# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the flag-gated A2A v1 HTTP boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from a2a.types import (
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.a2a.executor import A2ARequestHandler
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.runtime.run_registry import (
    A2ACorrelation,
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.server
_REAL_ASYNC_REQUEST = httpx.AsyncClient.request


@pytest.fixture(name="client_bundle")
async def build_a2a_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Build an enabled A2A app and a scoped API key."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    monkeypatch.setenv("PHYTOMNI_A2A_ENABLED", "1")
    monkeypatch.setenv(
        "PHYTOMNI_A2A_PUBLIC_BASE_URL", "https://public.example/base"
    )
    db = str(tmp_path / "a2a-keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", db)
    key = ApiKeyStore(db).create(user_id="a2a-user", scopes=["agents"]).api_key
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://api.test",
    )
    try:
        yield client, key
    finally:
        await client.aclose()


async def test_agent_card_is_public_and_flag_gated(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Enabled discovery returns a card without requiring an API key."""
    client, _key = client_bundle

    response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    body = response.json()
    assert body["supportedInterfaces"][0]["url"] == (
        "https://public.example/base/a2a"
    )
    assert body["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert body["capabilities"]["streaming"] is True


async def test_a2a_requires_auth_and_exact_protocol_header(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Transport auth and version checks happen before JSON-RPC dispatch."""
    client, key = client_bundle
    payload = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {}}

    unauthorized = await client.post(
        "/a2a",
        json=payload,
        headers={"A2A-Version": "1.0"},
    )
    assert unauthorized.status_code == 401

    bad_version = await client.post(
        "/a2a",
        json=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "0.3",
        },
    )
    assert bad_version.status_code == 400
    assert "A2A-Version" in bad_version.json()["error"]["message"]


async def test_a2a_business_errors_stay_jsonrpc_http_200(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Unsupported methods are protocol errors, not transport failures."""
    client, key = client_bundle
    response = await client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "unsupported",
            "method": "ListTasks",
            "params": {},
        },
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "1.0",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "unsupported"
    assert body["error"]["code"] == -32004


async def test_a2a_unknown_skill_is_invalid_params_error(
    client_bundle: tuple[httpx.AsyncClient, str],
) -> None:
    """Unknown request-level skill ids never reach the agent invoker."""
    client, key = client_bundle
    response = await client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "SendMessage",
            "params": {
                "metadata": {"skill_id": "MissingAgent"},
                "message": {
                    "messageId": "m1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
            },
        },
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "1.0",
        },
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32602


async def test_a2a_get_task_projects_owned_run(
    client_bundle: tuple[httpx.AsyncClient, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GetTask reads the owner-scoped A2A projection from the registry."""
    db = str(tmp_path / "a2a-tasks.sqlite")
    monkeypatch.setattr(api_app_module, "resolve_tasks_db_path", lambda: db)
    RunRegistry(db).create_run(
        RunSpec("run-a2a-get", "a2a-user", "chat", "local"),
        outcome=RunOutcome(
            status="succeeded",
            result={"formatted": {"answer": "done"}},
        ),
        request_info=RunRequestInfo(
            request_json=(
                '{"message":{"messageId":"m-get","contextId":"c-get",'
                '"role":"ROLE_USER","parts":[{"text":"hello"}]}}'
            ),
        ),
        a2a=A2ACorrelation(
            task_id="task-get",
            context_id="c-get",
            message_id="m-get",
        ),
    )
    client, key = client_bundle
    response = await client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "get-1",
            "method": "GetTask",
            "params": {"id": "task-get", "historyLength": 1},
        },
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "1.0",
        },
    )

    assert response.status_code == 200
    body = response.json()["result"]
    assert body["id"] == "task-get"
    assert body["contextId"] == "c-get"
    assert body["status"]["state"] == "TASK_STATE_COMPLETED"
    assert body["history"][0]["messageId"] == "m-get"
    assert body["artifacts"][0]["parts"][0]["text"] == "done"


async def test_a2a_resume_checks_generation_and_reuses_resume_kernel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2A resume rejects stale input and calls the shared kernel once."""
    db = str(tmp_path / "a2a-resume.sqlite")

    def db_path() -> str:
        return db

    monkeypatch.setattr(api_app_module, "resolve_tasks_db_path", db_path)
    RunRegistry(db).create_run(
        RunSpec("run-a2a-resume", "anonymous", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={
                "interrupt": {"draft": {"summary": "draft"}},
                "generation": 0,
            },
        ),
        a2a=A2ACorrelation(task_id="task-resume", context_id="ctx-resume"),
    )
    resumed: list[dict[str, object]] = []

    async def fake_resume(
        _app: object,
        run_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        resumed.append({"run_id": run_id, **payload})
        return {"final": "state"}

    def fake_review_app() -> object:
        return object()

    monkeypatch.setattr(api_app_module, "_review_stream_app", fake_review_app)
    monkeypatch.setattr(api_app_module, "_resume_paused_run", fake_resume)
    monkeypatch.setattr(
        api_app_module,
        "_format_review_result",
        lambda _state: {"formatted": {"answer": "done"}, "raw": None},
    )

    resume_a2a_task = getattr(api_app_module, "_resume_a2a_task")
    result = await resume_a2a_task(
        "task-resume",
        "ctx-resume",
        {"generation": 0, "approved": True},
    )
    assert result is not None
    body, status_code = result

    assert status_code == 200
    assert body["status"] == "succeeded"
    assert resumed == [
        {"run_id": "run-a2a-resume", "approved": True, "edits": None}
    ]
    RunRegistry(db).create_run(
        RunSpec("run-a2a-resume", "anonymous", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={
                "interrupt": {"draft": {"summary": "next"}},
                "generation": 1,
            },
        ),
        a2a=A2ACorrelation(task_id="task-resume", context_id="ctx-resume"),
    )
    with pytest.raises(ValueError, match="generation mismatch"):
        await resume_a2a_task(
            "task-resume",
            "ctx-resume",
            {"generation": 0, "approved": True},
        )


async def test_a2a_streaming_method_returns_sse_events(
    client_bundle: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK dispatcher exposes SendStreamingMessage as SSE."""

    async def fake_stream(
        _self: A2ARequestHandler,
        _params: object,
        _context: object,
    ):
        yield Task(
            id="task-stream",
            context_id="context-stream",
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )
        yield TaskStatusUpdateEvent(
            task_id="task-stream",
            context_id="context-stream",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

    monkeypatch.setattr(
        A2ARequestHandler, "on_message_send_stream", fake_stream
    )
    client, key = client_bundle
    response = await client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "stream-1",
            "method": "SendStreamingMessage",
            "params": {
                "metadata": {"skill_id": "ChatAgent"},
                "message": {
                    "messageId": "m-stream",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                },
            },
        },
        headers={
            "Authorization": f"Bearer {key}",
            "A2A-Version": "1.0",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"statusUpdate"' in response.text
    assert '"state": "TASK_STATE_COMPLETED"' in response.text
