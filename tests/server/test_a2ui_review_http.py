# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP tests for Review A2UI dual-transport projection and resume."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, NoReturn

import httpx
import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage

pytestmark = pytest.mark.server

_SENSITIVE_ERROR = (
    "Bearer bearer-secret postgresql://db-user:db-password@"
    "db.internal/db SELECT secret_token FROM private_table"
)


def _patch_review_app(monkeypatch: pytest.MonkeyPatch, app: Any) -> None:
    """Point Review HTTP helpers at a fake compiled graph app."""
    monkeypatch.setattr(api_app_module, "_review_stream_app", lambda: app)
    monkeypatch.setattr(
        api_app_module,
        "_review_initial_state",
        lambda _args: {"seed": "review"},
    )


async def _post_review_chat_completion(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    *,
    stream: bool,
    content: str,
) -> httpx.Response:
    """Post one canonical Review chat-completion test request."""
    payload: dict[str, Any] = {
        "model": "phyto-review",
        "messages": [{"role": "user", "content": content}],
    }
    if stream:
        payload["stream"] = True
    return await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=payload,
    )


def _assert_review_persistence_failure(body: str) -> None:
    """Assert the safe terminal shape for an unpersisted Review stream."""
    unique_markers = (
        "event: RunError\n",
        '"code": "run_persistence_failed"',
        '"message": "The completed run could not be persisted."',
        "data: [DONE]",
    )
    assert all(body.count(marker) == 1 for marker in unique_markers)
    assert all(
        marker not in body
        for marker in ("event: RunFinished\n", "phyto.a2ui")
    )
    assert body.rstrip().endswith("data: [DONE]")


async def test_review_pause_flag_off_has_no_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """With A2UI disabled, Review pauses keep a plain draft interrupt."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "false")
    _patch_review_app(monkeypatch, review_app_factory())
    response = await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis.",
                "obs_file_list": [],
            }
        },
    )
    assert response.status_code == 200
    draft = response.json()["interrupt"]["draft"]
    assert draft == {"draft": "draft review"}
    assert "a2ui" not in draft


async def test_review_pause_flag_on_projects_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """With A2UI enabled, Review pauses attach a confirm surface."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, review_app_factory())
    response = await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis.",
                "obs_file_list": [],
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    draft = body["interrupt"]["draft"]
    assert draft["draft"] == "draft review"
    assert draft["a2ui"]["widget"] == "confirm"
    assert draft["a2ui"]["props"]["body"] == "draft review"
    # Registry must match response (GET /v1/runs/{id})
    got = await api_client.get(
        f"/v1/runs/{body['id']}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert got.status_code == 200
    stored = got.json()["result"]["interrupt"]["draft"]
    assert stored["a2ui"]["surface_id"] == draft["a2ui"]["surface_id"]


async def test_review_stream_runtime_failure_emits_error_and_fails_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    assert_failed_stream: Any,
) -> None:
    """A Review A2UI fault after RunStarted emits one error and fails."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")

    async def _fail_review(
        _state: dict[str, Any],
        *,
        config: dict[str, Any],
    ) -> NoReturn:
        """Raise a backend failure after the opening event."""
        del _state, config
        raise RuntimeError(_SENSITIVE_ERROR)

    _patch_review_app(monkeypatch, SimpleNamespace(ainvoke=_fail_review))
    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="Review this.",
    )

    assert response.status_code == 200
    body = response.text
    await assert_failed_stream(body)


async def test_review_stream_pause_settle_failure_suppresses_a2ui_and_finish(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """An unpersisted Review pause exposes one safe terminal error only."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    monkeypatch.setattr(
        api_app_module,
        "_settle_stream_run",
        lambda *_args, **_kwargs: False,
    )
    _patch_review_app(monkeypatch, review_app_factory())

    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="Review this.",
    )

    assert response.status_code == 200
    body = response.text
    _assert_review_persistence_failure(body)


async def test_review_stream_success_settle_failure_suppresses_finish(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """An unpersisted terminal Review result cannot expose success."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    monkeypatch.setattr(
        api_app_module,
        "_settle_stream_run",
        lambda *_args, **_kwargs: False,
    )
    review_app = review_app_factory()

    async def _succeed(
        _state: dict[str, Any],
        *,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        del config
        return review_app.success_response

    _patch_review_app(
        monkeypatch,
        SimpleNamespace(ainvoke=_succeed),
    )

    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="Review this.",
    )

    assert response.status_code == 200
    body = response.text
    _assert_review_persistence_failure(body)


async def test_review_chat_completion_pause_projects_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Chat-completions Review pauses also project a2ui when enabled."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, review_app_factory())
    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=False,
        content="Review this.",
    )
    assert response.status_code == 200
    assert "a2ui" in response.json()["interrupt"]["draft"]


async def test_review_stream_validation_fails_before_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid Review stream arguments remain a pre-header 400."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    payload = ChatCompletionRequest(
        model="phyto-review",
        messages=[ChatMessage(role="user", content="Review this.")],
        stream=True,
    )

    with pytest.raises(HTTPException) as caught:
        stream_pause = getattr(api_app_module, "_stream_review_a2ui_pause")
        await stream_pause(
            arguments={},
            payload=payload,
            user_query="Review this.",
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == "invalid ReviewAgent arguments"


async def test_review_a2ui_action_approve_matches_resume_kernel(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """A2UI accept resumes Review through the shared kernel."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    review_app = review_app_factory()
    _patch_review_app(monkeypatch, review_app)
    paused = await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis.",
                "obs_file_list": [],
            }
        },
    )
    body = paused.json()
    run_id = body["id"]
    surface_id = body["interrupt"]["draft"]["a2ui"]["surface_id"]

    calls: list[dict[str, Any]] = []

    async def _spy_resume(
        _app: Any,
        _thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(dict(resume_payload))
        return review_app.success_response

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _spy_resume)

    resumed = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": surface_id,
            "widget": "confirm",
            "action_id": "act-1",
            "payload": {"accepted": True},
        },
    )
    assert resumed.status_code == 200
    out = resumed.json()
    assert out["status"] == "succeeded"
    assert out["agent"] == "review"
    assert out["result"]["a2ui"]["props"]["status"] == "submitted"
    assert out["result"]["a2ui"]["props"]["accepted"] is True
    assert out["result"]["a2ui"]["surface_id"] == surface_id
    assert len(calls) == 1
    assert calls[0]["approved"] is True
    assert calls[0]["edits"] is None
    assert calls[0]["widget"] == "confirm"
    assert calls[0]["surface_id"] == surface_id
    assert calls[0]["action_id"] == "act-1"


async def test_review_resume_includes_result_a2ui_when_projected(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Classic /resume also returns submitted a2ui when surface was open."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, review_app_factory())
    paused = await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis.",
                "obs_file_list": [],
            }
        },
    )
    body = paused.json()
    run_id = body["id"]
    surface_id = body["interrupt"]["draft"]["a2ui"]["surface_id"]

    resumed = await api_client.post(
        f"/v1/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True},
    )
    assert resumed.status_code == 200
    out = resumed.json()
    assert out["status"] == "succeeded"
    assert out["result"]["a2ui"]["props"]["status"] == "submitted"
    assert out["result"]["a2ui"]["props"]["accepted"] is True
    assert out["result"]["a2ui"]["surface_id"] == surface_id


async def test_review_a2ui_then_resume_second_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Dual-transport: first winner settles; second path 409."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, review_app_factory())
    paused = await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis.",
                "obs_file_list": [],
            }
        },
    )
    body = paused.json()
    run_id = body["id"]
    surface_id = body["interrupt"]["draft"]["a2ui"]["surface_id"]

    first = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": surface_id,
            "widget": "confirm",
            "action_id": "act-1",
            "payload": {"accepted": True},
        },
    )
    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"

    second = await api_client.post(
        f"/v1/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == 409


async def test_review_reject_a2ui_mints_new_surface_on_reinterrupt(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Reject resume that re-interrupts projects a new surface_id."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, review_app_factory(reinterrupt=True))
    paused = await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis.",
                "obs_file_list": [],
            }
        },
    )
    body = paused.json()
    run_id = body["id"]
    old_surface_id = body["interrupt"]["draft"]["a2ui"]["surface_id"]

    rejected = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": old_surface_id,
            "widget": "confirm",
            "action_id": "act-1",
            "payload": {"accepted": False},
        },
    )
    assert rejected.status_code == 200
    out = rejected.json()
    assert out["status"] == "input_required"
    new_surface_id = out["interrupt"]["draft"]["a2ui"]["surface_id"]
    assert new_surface_id != old_surface_id
    assert out["interrupt"]["draft"]["draft"] == "revised draft"


async def test_review_stream_flag_off_still_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With A2UI disabled, Review streaming stays rejected."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "false")
    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="Review this topic.",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 400
    assert "human-in-the-loop review" in response.json()["error"]["message"]


async def test_review_stream_flag_on_emits_phyto_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    stream_test_tools: Any,
    review_app_factory: Any,
) -> None:
    """With A2UI enabled, Review streaming pauses with phyto.a2ui."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, review_app_factory())
    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="Review this topic.",
    )
    assert response.status_code == 200
    body = response.text
    a2ui = stream_test_tools.extract_custom_a2ui(body)
    assert a2ui is not None
    assert a2ui["widget"] == "confirm"
    assert "phyto.a2ui" in body

    run_id = stream_test_tools.extract_run_started_id(body)
    assert run_id

    got = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert got.status_code == 200
    record = got.json()
    assert record["status"] == "input_required"
    assert record["result"]["interrupt"]["draft"]["a2ui"]["surface_id"] == (
        a2ui["surface_id"]
    )
