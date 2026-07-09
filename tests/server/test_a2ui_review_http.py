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
