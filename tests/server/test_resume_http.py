# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the HTTP human-in-the-loop resume adapter."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


def _seed_run(
    tasks_db_path: str,
    *,
    run_id: str,
    status: str,
) -> str:
    """Seed an owner-scoped review run for resume tests."""
    result = None
    if status == "input_required":
        result = {
            "interrupt": {
                "thread_id": run_id,
                "draft": {
                    "a2ui": {
                        "catalog_version": "v1.0",
                        "surface_id": f"{run_id}-surface",
                        "widget": "confirm",
                        "props": {
                            "title": "Review approval",
                            "body": "Review approval required.",
                        },
                    }
                },
            },
            "status": "input_required",
        }
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
            run_id=run_id,
            user_id="u1",
            agent="review",
            origin="local",
        ),
        outcome=RunOutcome(status=status, result=result),
    )
    return run_id


async def test_resume_unknown_thread_returns_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Unknown run ids are invisible to the resume adapter."""
    _ = tasks_db_path

    response = await api_client.post(
        "/v1/runs/run-missing/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_resume_terminal_run_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Only ``input_required`` runs can be resumed."""
    run_id = _seed_run(
        tasks_db_path,
        run_id="run-review-terminal",
        status="succeeded",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_state_conflict"


async def test_resume_bad_body_returns_422(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """The resume body requires an explicit approval decision."""
    run_id = _seed_run(
        tasks_db_path,
        run_id="run-review-paused",
        status="input_required",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"edits": "please tighten the conclusion"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


async def test_get_run_preserves_input_required_status(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Paused local runs are not reconciled into ``running``."""
    run_id = _seed_run(
        tasks_db_path,
        run_id="run-review-input-required",
        status="input_required",
    )

    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "input_required"


async def test_review_run_interrupt_then_resume_finishes(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """A review run can pause for approval and resume to success."""
    _ = tasks_db_path
    fake_app = review_app_factory(interrupt_key="summary")
    monkeypatch.setattr(
        api_app_module,
        "_review_stream_app",
        lambda: fake_app,
    )
    monkeypatch.setattr(
        api_app_module,
        "_review_initial_state",
        lambda _args: {"seed": "review"},
    )

    first = await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis papers.",
                "obs_file_list": [],
            }
        },
    )

    assert first.status_code == 200
    interrupted = first.json()
    thread_id = interrupted["interrupt"]["thread_id"]
    assert interrupted["id"] == thread_id
    assert interrupted["run_id"] == thread_id
    assert interrupted["status"] == "input_required"
    assert interrupted["interrupt"]["draft"]["summary"] == "draft review"

    resumed = await api_client.post(
        f"/v1/runs/{thread_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True, "edits": "ship it"},
    )

    assert resumed.status_code == 200
    body = resumed.json()
    assert body["id"] == thread_id
    assert body["run_id"] == thread_id
    assert body["status"] == "succeeded"
    assert body["result"]["formatted"]["answer"] == "Approved final review."


async def test_review_chat_completion_interrupt_body(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Review chat completions return a direct interrupt body on pause."""
    _ = tasks_db_path
    fake_app = review_app_factory(interrupt_key="summary")
    monkeypatch.setattr(
        api_app_module,
        "_review_stream_app",
        lambda: fake_app,
    )
    monkeypatch.setattr(
        api_app_module,
        "_review_initial_state",
        lambda _args: {"seed": "review"},
    )

    response = await api_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "model": "phyto-review",
            "messages": [{"role": "user", "content": "Review this topic."}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "input_required"
    assert body["id"] == body["interrupt"]["thread_id"]
    assert body["run_id"] == body["interrupt"]["thread_id"]


async def test_review_chat_completion_stream_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Streaming review would bypass the human-in-the-loop resume path."""
    _ = tasks_db_path

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
    assert response.json()["error"]["code"] == "invalid_argument"
    assert response.json()["error"]["message"] == "invalid request"
