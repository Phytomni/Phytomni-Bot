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
from tests.support.asyncio_helpers import wait_until

from mcp_server_phytomni.api import a2ui_runtime
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.schemas import ChatCompletionRequest, ChatMessage
from mcp_server_phytomni.runtime.run_registry import RunRegistry

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


async def _post_native_review(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> httpx.Response:
    """Post one native Review run used by the A2UI HTTP tests."""
    return await api_client.post(
        "/v1/agents/review/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
                "user_query": "Review photosynthesis.",
                "obs_file_list": [],
            }
        },
    )


async def _wait_review_interrupt(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    response: httpx.Response,
) -> dict[str, Any]:
    """Wait until a 202 Review POST settles to input_required."""
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "running"
    run_id = body["id"]

    def paused() -> bool:
        record = RunRegistry(tasks_db_path).get_run(run_id, owner="u1")
        return record is not None and record.status == "input_required"

    await wait_until(paused)
    got = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert got.status_code == 200
    stored = got.json()
    assert stored["status"] == "input_required"
    return stored


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
        marker not in body for marker in ("event: RunFinished\n", "phyto.a2ui")
    )
    assert body.rstrip().endswith("data: [DONE]")


async def test_review_pause_flag_on_projects_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Review pauses attach a confirm surface."""
    _patch_review_app(monkeypatch, review_app_factory())
    response = await _post_native_review(api_client, issued_api_key)
    body = await _wait_review_interrupt(
        api_client, issued_api_key, tasks_db_path, response
    )
    draft = body["result"]["interrupt"]["draft"]
    assert draft["summary"] == "draft review"
    assert draft["a2ui"]["widget"] == "confirm"
    assert draft["a2ui"]["props"]["body"] == "draft review"
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
    monkeypatch.setattr(
        RunRegistry,
        "update_active_result",
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


async def test_review_stream_success_does_not_use_legacy_settle_seam(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Review success is settled only by Runtime, not the legacy seam."""
    assert not hasattr(api_app_module, "_settle_stream_run")
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
    assert "event: RunFinished\n" in body
    assert "event: RunError\n" not in body


async def test_review_chat_completion_pause_projects_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Chat-completions Review pauses also project a2ui when enabled."""
    _ = tasks_db_path
    _patch_review_app(monkeypatch, review_app_factory())
    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=False,
        content="Review this.",
    )
    assert response.status_code == 200
    assert "a2ui" in response.json()["interrupt"]["draft"]


async def test_review_projection_failure_persists_failed_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """A failed surface projection is persisted before the 500 response."""
    _patch_review_app(monkeypatch, review_app_factory())

    def fail_projection(_interrupt: Any) -> NoReturn:
        """Force the public projection seam to fail."""
        raise a2ui_runtime.ReviewSurfaceProjectionError("synthetic failure")

    monkeypatch.setattr(
        a2ui_runtime,
        "project_review_interrupt",
        fail_projection,
    )

    response = await _post_native_review(api_client, issued_api_key)

    assert response.status_code == 202, response.text

    def failed() -> bool:
        rows = RunRegistry(tasks_db_path).list_runs(owner="u1")
        return any(row.status == "failed" for row in rows)

    await wait_until(failed)
    listing = await api_client.get(
        "/v1/runs?status=failed&agent=review",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert listing.status_code == 200
    rows = listing.json()["data"]
    assert rows
    assert rows[-1]["status"] == "failed"
    assert rows[-1]["result"]["formatted"]["answer"] == ""


async def test_review_stream_projection_failure_is_safe_and_terminal(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """A stream projection failure emits no surface or contradictory finish."""
    _patch_review_app(monkeypatch, review_app_factory())

    def fail_projection(_interrupt: Any) -> NoReturn:
        """Force the stream projection seam to fail."""
        raise a2ui_runtime.ReviewSurfaceProjectionError("synthetic failure")

    monkeypatch.setattr(
        a2ui_runtime,
        "project_review_interrupt",
        fail_projection,
    )
    response = await _post_review_chat_completion(
        api_client,
        issued_api_key,
        stream=True,
        content="Review this.",
    )

    assert response.status_code == 200
    assert '"code": "projection_failed"' in response.text
    assert "phyto.a2ui" not in response.text
    assert "event: RunFinished\n" not in response.text


async def test_review_stream_validation_fails_before_sse() -> None:
    """Invalid Review stream arguments remain a pre-header 400."""
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
            runtime_run_id="run-review-validation",
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
    review_app = review_app_factory()
    _patch_review_app(monkeypatch, review_app)
    paused = await _post_native_review(api_client, issued_api_key)
    body = await _wait_review_interrupt(
        api_client, issued_api_key, tasks_db_path, paused
    )
    run_id = body["id"]
    surface_id = body["result"]["interrupt"]["draft"]["a2ui"]["surface_id"]

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
    _patch_review_app(monkeypatch, review_app_factory())
    paused = await _post_native_review(api_client, issued_api_key)
    body = await _wait_review_interrupt(
        api_client, issued_api_key, tasks_db_path, paused
    )
    run_id = body["id"]
    surface_id = body["result"]["interrupt"]["draft"]["a2ui"]["surface_id"]

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


async def test_review_classic_first_blocks_late_a2ui_action(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Classic Review resume claims the surface before a Web uplink."""
    _patch_review_app(monkeypatch, review_app_factory())
    paused = await _post_native_review(api_client, issued_api_key)
    body = await _wait_review_interrupt(
        api_client, issued_api_key, tasks_db_path, paused
    )
    run_id = body["id"]
    surface_id = body["result"]["interrupt"]["draft"]["a2ui"]["surface_id"]

    classic = await api_client.post(
        f"/v1/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={"approved": True},
    )
    assert classic.status_code == 200

    late_a2ui = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": surface_id,
            "widget": "confirm",
            "action_id": "late-a2ui",
            "payload": {"accepted": True},
        },
    )
    assert late_a2ui.status_code == 409
    assert late_a2ui.json()["error"]["code"] == "run_state_conflict"


async def test_review_a2ui_then_resume_second_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Dual-transport: first winner settles; second path 409."""
    _patch_review_app(monkeypatch, review_app_factory())
    paused = await _post_native_review(api_client, issued_api_key)
    body = await _wait_review_interrupt(
        api_client, issued_api_key, tasks_db_path, paused
    )
    run_id = body["id"]
    surface_id = body["result"]["interrupt"]["draft"]["a2ui"]["surface_id"]

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
    assert second.json()["error"]["code"] == "run_state_conflict"


async def test_review_reject_a2ui_mints_new_surface_on_reinterrupt(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    review_app_factory: Any,
) -> None:
    """Reject resume that re-interrupts projects a new surface_id."""
    _patch_review_app(monkeypatch, review_app_factory(reinterrupt=True))
    paused = await _post_native_review(api_client, issued_api_key)
    body = await _wait_review_interrupt(
        api_client, issued_api_key, tasks_db_path, paused
    )
    run_id = body["id"]
    old_surface_id = body["result"]["interrupt"]["draft"]["a2ui"]["surface_id"]

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
    assert out["interrupt"]["draft"]["summary"] == "revised draft"


async def test_review_stream_flag_on_emits_phyto_a2ui(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    stream_test_tools: Any,
    review_app_factory: Any,
) -> None:
    """With A2UI enabled, Review streaming pauses with phyto.a2ui."""
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
