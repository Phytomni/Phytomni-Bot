# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the HTTP human-in-the-loop resume adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.support.a2ui_contract_fakes import (
    chat_terminal_state,
    confirm_surface,
)
from tests.support.asyncio_helpers import wait_until

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    local_run_spec,
)

pytestmark = pytest.mark.server


def _install_a2ui_race_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    """Install a blocked terminal resume and return its controls."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def _has_checkpoint(_app: Any, _thread_id: str) -> bool:
        return True

    async def _resume(
        _app: Any,
        thread_id: str,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(thread_id)
        started.set()
        await release.wait()
        return chat_terminal_state()

    monkeypatch.setattr(
        api_app_module, "_has_graph_checkpoint", _has_checkpoint
    )
    monkeypatch.setattr(api_app_module, "_chat_a2ui_stream_app", object)
    monkeypatch.setattr(api_app_module, "_resume_paused_run", _resume)
    return SimpleNamespace(started=started, release=release, calls=calls)


def _seed_run(
    tasks_db_path: str,
    *,
    run_id: str,
    status: str,
    agent: str = "review",
    owner: str = "u1",
) -> str:
    """Seed an owner-scoped review run for resume tests."""
    result = None
    if status == "input_required":
        result = {
            "interrupt": {
                "thread_id": run_id,
                "draft": {
                    "a2ui": confirm_surface(
                        f"{run_id}-surface",
                        title="Review approval",
                        body="Review approval required.",
                    ),
                },
            },
            "status": "input_required",
        }
    RunRegistry(tasks_db_path).create_run(
        local_run_spec(run_id, owner, agent),
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


async def test_resume_foreign_owner_returns_same_404_as_unknown_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A foreign paused run stays indistinguishable from an unknown id."""
    run_id = _seed_run(
        tasks_db_path,
        run_id="run-foreign-owner",
        status="input_required",
        owner="other-user",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_two_http_clients_only_one_resumes_a2ui_graph(
    a2ui_client_pair: tuple[httpx.AsyncClient, httpx.AsyncClient],
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File-backed claim arbitration permits exactly one graph resume."""
    run_id = _seed_run(
        tasks_db_path,
        run_id="run-http-a2ui-race",
        status="input_required",
        agent="chat",
    )
    control = _install_a2ui_race_seams(monkeypatch)
    headers = {"Authorization": f"Bearer {issued_api_key}"}
    path = f"/v1/runs/{run_id}/a2ui-actions"
    surface_id = f"{run_id}-surface"
    body = {
        "run_id": run_id,
        "surface_id": surface_id,
        "widget": "confirm",
        "action_id": "action-http-race-1",
        "payload": {"accepted": True},
    }
    first, second = a2ui_client_pair
    tasks = [
        asyncio.create_task(first.post(path, headers=headers, json=body)),
        asyncio.create_task(
            second.post(
                path,
                headers=headers,
                json={**body, "action_id": "action-http-race-2"},
            )
        ),
    ]
    await control.started.wait()
    control.release.set()
    responses = await asyncio.gather(*tasks)

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(control.calls) == 1


async def test_classic_resume_missing_checkpoint_does_not_claim(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classic resume probes the checkpoint before writing an audit row."""
    run_id = _seed_run(
        tasks_db_path,
        run_id="run-classic-no-checkpoint",
        status="input_required",
    )

    async def _no_checkpoint(_app: Any, _thread_id: str) -> bool:
        return False

    async def _unexpected_resume(
        _app: Any,
        _thread_id: str,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise AssertionError("classic resume reached the graph")

    monkeypatch.setattr(
        api_app_module, "_has_graph_checkpoint", _no_checkpoint
    )
    monkeypatch.setattr(api_app_module, "_review_stream_app", object)
    monkeypatch.setattr(
        api_app_module, "_resume_paused_run", _unexpected_resume
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "checkpoint_not_available"
    assert (
        RunRegistry(tasks_db_path).list_a2ui_actions(owner="u1", run_id=run_id)
        == []
    )


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

    assert first.status_code == 202
    run_id = first.json()["id"]

    def paused() -> bool:
        record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
        return record is not None and record.status == "input_required"

    await wait_until(paused)
    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.status_code == 200
    interrupted = fetched.json()
    thread_id = interrupted["id"]
    assert interrupted["id"] == thread_id
    assert interrupted["run_id"] == thread_id
    assert interrupted["status"] == "input_required"
    assert interrupted["result"]["interrupt"]["draft"]["summary"] == (
        "draft review"
    )

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
    record = RunRegistry(tasks_db_path).get_run(thread_id, owner="u1")
    assert record is not None
    assert record.status == "succeeded"
    actions = RunRegistry(tasks_db_path).list_a2ui_actions(
        owner="u1", run_id=thread_id
    )
    assert len(actions) == 1
    assert actions[0].outcome == "succeeded"


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
