# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP tests for Review A2UI dual-transport projection and resume."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

# Pytest resolves this namespace-package helper; standalone pylint does not.
# pylint: disable=import-error
from tests.server.test_api_chat_streaming import _extract_run_started_id

# pylint: enable=import-error
from mcp_server_phytomni.agents.shared.a2ui import A2UI_CUSTOM_NAME
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage

pytestmark = pytest.mark.server


class _FakeCheckpointer:
    async def aget(self, _config: dict[str, Any]) -> object:
        """Return a non-None checkpoint so resume paths stay open."""
        return object()


class _FakeReviewAppPause:
    """Pause with production interrupt value shape."""

    checkpointer = _FakeCheckpointer()

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def ainvoke(self, payload: Any, *, config: dict[str, Any]) -> dict:
        """Interrupt on first invoke; finish on Command(resume=...)."""
        self.calls.append((payload, config))
        if isinstance(payload, dict):
            return {
                "__interrupt__": [
                    SimpleNamespace(value={"draft": "draft review"})
                ]
            }
        # Command(resume=...) path — default approve finishes
        return {
            "final_response": {
                "choices": [
                    {
                        "message": {
                            "content": "Approved final review.",
                            "doc_list": [],
                            "follow_up_questions": [],
                        }
                    }
                ]
            }
        }


def _patch_review_app(monkeypatch: pytest.MonkeyPatch, app: Any) -> None:
    """Point Review HTTP helpers at a fake compiled graph app."""
    monkeypatch.setattr(api_app_module, "_review_stream_app", lambda: app)
    monkeypatch.setattr(
        api_app_module,
        "_review_initial_state",
        lambda _args: {"seed": "review"},
    )


async def test_review_pause_flag_off_has_no_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With A2UI disabled, Review pauses keep a plain draft interrupt."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "false")
    _patch_review_app(monkeypatch, _FakeReviewAppPause())
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
) -> None:
    """With A2UI enabled, Review pauses attach a confirm surface."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, _FakeReviewAppPause())
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
) -> None:
    """A Review A2UI fault after RunStarted emits one error and fails."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")

    class _FailingReviewApp:
        async def ainvoke(
            self,
            _state: dict[str, Any],
            *,
            config: dict[str, Any],
        ) -> dict[str, Any]:
            """Raise a backend failure after the opening event."""
            del config
            raise RuntimeError("backend token=hidden review failure")

    _patch_review_app(monkeypatch, _FailingReviewApp())
    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "model": "phyto-review",
            "stream": True,
            "messages": [{"role": "user", "content": "Review this."}],
        },
    )

    assert response.status_code == 200
    body = response.text
    assert body.count("event: RunError\n") == 1
    assert "event: RunFinished\n" not in body
    assert body.count("data: [DONE]") == 1
    run_id = _extract_run_started_id(body)
    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "failed"


async def test_review_chat_completion_pause_projects_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat-completions Review pauses also project a2ui when enabled."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, _FakeReviewAppPause())
    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "model": "phyto-review",
            "messages": [{"role": "user", "content": "Review this."}],
        },
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


class _FakeReviewAppRejectReinterrupt(_FakeReviewAppPause):
    """Re-interrupt after a reject resume with a revised draft."""

    async def ainvoke(self, payload: Any, *, config: dict[str, Any]) -> dict:
        self.calls.append((payload, config))
        if isinstance(payload, dict):
            return {
                "__interrupt__": [
                    SimpleNamespace(value={"draft": "draft review"})
                ]
            }
        return {
            "__interrupt__": [
                SimpleNamespace(value={"draft": "revised draft"})
            ]
        }


async def test_review_a2ui_action_approve_matches_resume_kernel(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2UI accept resumes Review through the shared kernel."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, _FakeReviewAppPause())
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
        return {
            "final_response": {
                "choices": [
                    {
                        "message": {
                            "content": "Approved final review.",
                            "doc_list": [],
                            "follow_up_questions": [],
                        }
                    }
                ]
            }
        }

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
) -> None:
    """Classic /resume also returns submitted a2ui when surface was open."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, _FakeReviewAppPause())
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
) -> None:
    """Dual-transport: first winner settles; second path 409."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, _FakeReviewAppPause())
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
) -> None:
    """Reject resume that re-interrupts projects a new surface_id."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, _FakeReviewAppRejectReinterrupt())
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


def _extract_custom_a2ui(body: str) -> dict[str, Any] | None:
    """Return the ``phyto.a2ui`` value from an SSE body, if present."""
    marker = "event: Custom\ndata: "
    for chunk in body.split("\n\n"):
        if not chunk.startswith(marker):
            continue
        payload = json.loads(chunk[len(marker) :])
        if payload.get("name") == A2UI_CUSTOM_NAME:
            value = payload.get("value")
            return value if isinstance(value, dict) else None
    return None


async def test_review_stream_flag_off_still_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With A2UI disabled, Review streaming stays rejected."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "false")
    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "model": "phyto-review",
            "stream": True,
            "messages": [{"role": "user", "content": "Review this topic."}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 400
    assert "human-in-the-loop review" in response.json()["error"]["message"]


async def test_review_stream_flag_on_emits_phyto_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With A2UI enabled, Review streaming pauses with phyto.a2ui."""
    _ = tasks_db_path
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    _patch_review_app(monkeypatch, _FakeReviewAppPause())
    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "model": "phyto-review",
            "stream": True,
            "messages": [{"role": "user", "content": "Review this topic."}],
        },
    )
    assert response.status_code == 200
    body = response.text
    a2ui = _extract_custom_a2ui(body)
    assert a2ui is not None
    assert a2ui["widget"] == "confirm"
    assert "phyto.a2ui" in body

    run_id = _extract_run_started_id(body)
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
