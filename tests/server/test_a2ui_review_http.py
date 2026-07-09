# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP tests for Review A2UI dual-transport projection and resume."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mcp_server_phytomni.api import app as api_app_module

pytestmark = pytest.mark.server


class _FakeCheckpointer:
    async def aget(self, _config: dict[str, Any]) -> object:
        return object()


class _FakeReviewAppPause:
    """Pause with production interrupt value shape."""

    checkpointer = _FakeCheckpointer()

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def ainvoke(self, payload: Any, *, config: dict[str, Any]) -> dict:
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


async def test_review_chat_completion_pause_projects_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert calls == [{"approved": True, "edits": None}]


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
