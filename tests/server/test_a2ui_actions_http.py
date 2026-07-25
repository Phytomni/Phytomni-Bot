# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP tests for ``POST /v1/runs/{run_id}/a2ui-actions``."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tests.support.a2ui_contract_fakes import (
    cancelled_response,
    gene_id_form_props,
)

from mcp_server_phytomni.agents.chat.a2ui_graph import _CANCEL_MESSAGE
from mcp_server_phytomni.agents.shared.a2ui import A2UI_CATALOG_VERSION
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


@pytest.fixture(autouse=True)
def _checkpoint_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make seeded HTTP pauses expose the graph checkpoint seam."""

    async def _has_checkpoint(_app: Any, _thread_id: str) -> bool:
        return True

    monkeypatch.setattr(
        api_app_module, "_has_graph_checkpoint", _has_checkpoint
    )


def _open_surface(surface_id: str) -> dict[str, Any]:
    """Build a paused confirm surface stored on the run row."""
    return {
        "catalog_version": A2UI_CATALOG_VERSION,
        "surface_id": surface_id,
        "widget": "confirm",
        "props": {"title": "Confirm", "body": "Proceed?"},
    }


def _seed_a2ui_run(
    tasks_db_path: str,
    *,
    run_id: str,
    surface_id: str,
    status: str = "input_required",
    user_id: str = "u1",
) -> str:
    """Seed a chat run paused on an A2UI confirm surface."""
    result = {
        "interrupt": {
            "thread_id": run_id,
            "draft": {"a2ui": _open_surface(surface_id)},
        },
        "status": "input_required",
    }
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
            run_id=run_id,
            user_id=user_id,
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status=status, result=result),
    )
    return run_id


def _action_body(
    *,
    run_id: str,
    surface_id: str,
    accepted: bool,
) -> dict[str, Any]:
    """Build a confirm action envelope for the HTTP route."""
    return {
        "run_id": run_id,
        "surface_id": surface_id,
        "widget": "confirm",
        "action_id": "act-confirm",
        "payload": {"accepted": accepted},
    }


def _accepted_final_state() -> dict[str, Any]:
    """Terminal graph state after an accepted confirm resume."""
    return {
        "response": {
            "choices": [
                {
                    "message": {
                        "content": "Analysis complete.",
                        "follow_up_questions": [],
                    }
                }
            ]
        },
        "a2ui_surface": _open_surface("sfc-accepted"),
    }


def _large_succeeded_response(run_id: str) -> dict[str, Any]:
    """Build a valid terminal response large enough for the size guard."""
    return {
        "id": run_id,
        "run_id": run_id,
        "object": "agent.run",
        "agent": "chat",
        "status": "succeeded",
        "task_ids": [],
        "result": {
            "formatted": {
                "answer": "x" * (1_048_576 + 1),
            },
            "execution": {},
        },
    }


async def test_a2ui_action_accept_succeeds(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted confirm resumes through the kernel and settles success."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-accept",
        surface_id="sfc-open-accept",
    )
    calls: list[tuple[Any, ...]] = []

    async def _fake_resume(
        app: Any,
        thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append((app, thread_id, resume_payload))
        return _accepted_final_state()

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _fake_resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-open-accept",
            accepted=True,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["formatted"]["answer"] == "Analysis complete."
    assert body["result"]["a2ui"]["props"]["status"] == "submitted"
    assert body["result"]["a2ui"]["props"]["accepted"] is True
    assert len(calls) == 1
    assert calls[0][1] == run_id
    assert calls[0][2]["accepted"] is True

    fetched = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert fetched.json()["status"] == "succeeded"


async def test_a2ui_action_reject_cancels_without_llm(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejected confirm settles the short cancel message."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-reject",
        surface_id="sfc-open-reject",
    )

    async def _fake_resume(
        _app: Any,
        _thread_id: str,
        _resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **cancelled_response(_CANCEL_MESSAGE),
            "a2ui_surface": _open_surface("sfc-open-reject"),
        }

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _fake_resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-open-reject",
            accepted=False,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["formatted"]["answer"] == _CANCEL_MESSAGE
    assert body["result"]["a2ui"]["props"]["accepted"] is False


async def test_a2ui_action_flag_off_returns_403(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route is gated behind the A2UI feature flag."""
    monkeypatch.delenv("PHYTOMNI_A2UI_ENABLED", raising=False)
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-flag-off",
        surface_id="sfc-flag-off",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-flag-off",
            accepted=True,
        ),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_a2ui_action_oversized_body_returns_413(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct A2UI callers cannot send a body above the Web-compatible cap."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    response = await api_client.post(
        "/v1/runs/run-a2ui-oversized/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "surface_id": "surface-1",
            "widget": "form",
            "action_id": "action-1",
            "run_id": "run-a2ui-oversized",
            "payload": {
                "fields": {
                    f"field-{index}": "x" * 4_096 for index in range(16)
                }
            },
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_a2ui_action_oversized_response_returns_413(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2UI resume responses are capped before they reach the wire."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-large-response",
        surface_id="sfc-large-response",
    )

    async def _large_resume(**kwargs: Any) -> tuple[dict[str, Any], int]:
        return _large_succeeded_response(str(kwargs["run_id"])), 200

    monkeypatch.setattr(api_app_module, "_resume_a2ui_run", _large_resume)
    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-large-response",
            accepted=True,
        ),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_a2ui_action_wrong_widget_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Body widget must match the open interrupt draft surface."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-wrong-widget",
        surface_id="sfc-open-widget",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": "sfc-open-widget",
            "widget": "form",
            "action_id": "act-form",
            "payload": {"fields": {"name": "x"}},
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_argument"
    assert response.json()["error"]["message"] == "invalid request"


async def test_a2ui_action_wrong_surface_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Body surface_id must match the open interrupt draft."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-wrong-surface",
        surface_id="sfc-open-real",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-other",
            accepted=True,
        ),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_state_conflict"


async def test_a2ui_action_not_input_required_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only paused runs accept A2UI actions."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = "run-a2ui-terminal"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
            run_id=run_id,
            user_id="u1",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(
            status="succeeded",
            result={"formatted": {"answer": "done"}},
        ),
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-terminal",
            accepted=True,
        ),
    )

    assert response.status_code == 409


async def test_a2ui_action_other_owner_returns_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs owned by another user are invisible."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-foreign",
        surface_id="sfc-foreign",
        user_id="other-user",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-foreign",
            accepted=True,
        ),
    )

    assert response.status_code == 404


async def test_a2ui_action_kernel_spy_calls_aresume_graph(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP adapter must resume via the shared kernel helper."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-spy",
        surface_id="sfc-spy",
    )
    calls: list[tuple[Any, ...]] = []

    async def _spy_resume(
        app: Any,
        thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append((app, thread_id, resume_payload))
        return _accepted_final_state()

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _spy_resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-spy",
            accepted=True,
        ),
    )

    assert response.status_code == 200
    assert len(calls) == 1


async def test_a2ui_action_duplicate_after_success_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second identical POST after success is rejected."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-duplicate",
        surface_id="sfc-dup",
    )

    async def _fake_resume(
        _app: Any,
        _thread_id: str,
        _resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return _accepted_final_state()

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _fake_resume)

    body = _action_body(
        run_id=run_id,
        surface_id="sfc-dup",
        accepted=True,
    )
    first = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=body,
    )
    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"

    second = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=body,
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "a2ui_action_conflict"


async def test_a2ui_action_path_body_run_id_mismatch_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path run_id must match the body run_id echo."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-mismatch",
        surface_id="sfc-mismatch",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id="run-a2ui-other",
            surface_id="sfc-mismatch",
            accepted=True,
        ),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_argument"
    assert response.json()["error"]["message"] == "invalid request"


async def test_a2ui_action_invalid_confirm_payload_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm actions require an accepted boolean in the payload."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-bad-payload",
        surface_id="sfc-bad-payload",
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": "sfc-bad-payload",
            "widget": "confirm",
            "action_id": "act-confirm",
            "payload": {},
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_argument"
    assert response.json()["error"]["message"] == "invalid request"


async def test_a2ui_action_no_checkpoint_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing LangGraph checkpoint surfaces as a 409 conflict."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_run(
        tasks_db_path,
        run_id="run-a2ui-no-checkpoint",
        surface_id="sfc-no-checkpoint",
    )

    async def _no_checkpoint(_app: Any, _thread_id: str) -> bool:
        return False

    monkeypatch.setattr(
        api_app_module,
        "_has_graph_checkpoint",
        _no_checkpoint,
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-no-checkpoint",
            accepted=True,
        ),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "checkpoint_not_available"
    assert error["message"] == "This input request is no longer available."
    assert error["stage"] == "resume_checkpoint"
    assert error["retryable"] is False
    assert (
        RunRegistry(tasks_db_path).list_a2ui_actions(owner="u1", run_id=run_id)
        == []
    )


def _open_form_surface(surface_id: str) -> dict[str, Any]:
    """Build a paused form surface stored on the run row."""
    return {
        "catalog_version": A2UI_CATALOG_VERSION,
        "surface_id": surface_id,
        "widget": "form",
        "props": {
            "title": "Form",
            "fields": [
                {
                    "name": "value",
                    "label": "Value",
                    "type": "text",
                    "required": True,
                }
            ],
        },
    }


def _seed_a2ui_form_run(
    tasks_db_path: str,
    *,
    run_id: str,
    surface_id: str,
    user_id: str = "u1",
) -> str:
    """Seed a chat run paused on an A2UI form surface."""
    result = {
        "interrupt": {
            "thread_id": run_id,
            "draft": {"a2ui": _open_form_surface(surface_id)},
        },
        "status": "input_required",
    }
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
            run_id=run_id,
            user_id=user_id,
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(status="input_required", result=result),
    )
    return run_id


def _form_submitted_final_state() -> dict[str, Any]:
    """Terminal graph state after a submitted form resume."""
    return {
        "response": {
            "choices": [
                {
                    "message": {
                        "content": "Got form.",
                        "follow_up_questions": [],
                    }
                }
            ]
        },
        "a2ui_surface": _open_form_surface("sfc-form-submit"),
    }


async def test_a2ui_action_form_submit_succeeds(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Form submit resumes and returns submitted fields on result.a2ui."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_form_run(
        tasks_db_path,
        run_id="run-a2ui-form-submit",
        surface_id="sfc-open-form-submit",
    )
    calls: list[tuple[Any, ...]] = []

    async def _fake_resume(
        app: Any,
        thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append((app, thread_id, resume_payload))
        return _form_submitted_final_state()

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _fake_resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": "sfc-open-form-submit",
            "widget": "form",
            "action_id": "act-form-submit",
            "payload": {"fields": {"value": "AT1G01010"}},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["formatted"]["answer"] == "Got form."
    assert body["result"]["a2ui"]["props"]["status"] == "submitted"
    assert body["result"]["a2ui"]["props"]["fields"] == {"value": "AT1G01010"}
    assert len(calls) == 1
    assert calls[0][1] == run_id
    assert calls[0][2]["fields"] == {"value": "AT1G01010"}


async def test_a2ui_action_form_cancel_succeeds(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Form cancel returns cancelled submitted snapshot + cancel answer."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_a2ui_form_run(
        tasks_db_path,
        run_id="run-a2ui-form-cancel",
        surface_id="sfc-open-form-cancel",
    )

    async def _fake_resume(
        _app: Any,
        _thread_id: str,
        _resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **cancelled_response(_CANCEL_MESSAGE),
            "a2ui_surface": _open_form_surface("sfc-open-form-cancel"),
        }

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _fake_resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": "sfc-open-form-cancel",
            "widget": "form",
            "action_id": "act-form-cancel",
            "payload": {"cancelled": True},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["formatted"]["answer"] == _CANCEL_MESSAGE
    assert body["result"]["a2ui"]["props"]["cancelled"] is True


async def test_a2ui_action_missing_draft_surface_returns_409(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs paused without an a2ui draft cannot accept actions."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = "run-a2ui-no-draft"
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
            run_id=run_id,
            user_id="u1",
            agent="chat",
            origin="local",
        ),
        outcome=RunOutcome(
            status="input_required",
            result={
                "interrupt": {
                    "thread_id": run_id,
                    "draft": {"summary": "not a2ui"},
                },
                "status": "input_required",
            },
        ),
    )

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json=_action_body(
            run_id=run_id,
            surface_id="sfc-missing",
            accepted=True,
        ),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_state_conflict"


def _open_review_form_surface(surface_id: str) -> dict[str, Any]:
    """Build a paused Review form surface stored on the run row."""
    return {
        "catalog_version": A2UI_CATALOG_VERSION,
        "surface_id": surface_id,
        "widget": "form",
        "props": gene_id_form_props(),
    }


def _seed_review_a2ui_form_run(
    tasks_db_path: str,
    *,
    run_id: str,
    surface_id: str,
    user_id: str = "u1",
) -> str:
    """Seed a review run paused on an A2UI form surface."""
    result = {
        "interrupt": {
            "thread_id": run_id,
            "draft": {
                "draft": "请填写 gene id",
                "a2ui": _open_review_form_surface(surface_id),
            },
        },
        "status": "input_required",
    }
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
            run_id=run_id,
            user_id=user_id,
            agent="review",
            origin="local",
        ),
        outcome=RunOutcome(status="input_required", result=result),
    )
    return run_id


async def test_a2ui_action_review_form_submit_succeeds(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review form submit resumes and echoes fields on result.a2ui."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_review_a2ui_form_run(
        tasks_db_path,
        run_id="run-a2ui-review-form-submit",
        surface_id="sfc-open-review-form",
    )
    calls: list[tuple[Any, ...]] = []
    fake_app = object()
    monkeypatch.setattr(
        api_app_module,
        "_review_stream_app",
        lambda: fake_app,
    )

    async def _fake_resume(
        app: Any,
        thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append((app, thread_id, resume_payload))
        return {
            "choices": [
                {
                    "message": {
                        "content": "Review form accepted.",
                        "doc_list": [],
                        "follow_up_questions": [],
                    }
                }
            ]
        }

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _fake_resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": "sfc-open-review-form",
            "widget": "form",
            "action_id": "act-review-form-submit",
            "payload": {"fields": {"gene_id": "AT1G01010"}},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["agent"] == "review"
    assert body["result"]["a2ui"]["props"]["status"] == "submitted"
    assert body["result"]["a2ui"]["props"]["fields"] == {
        "gene_id": "AT1G01010"
    }
    assert "accepted" not in body["result"]["a2ui"]["props"]
    assert len(calls) == 1
    assert calls[0][0] is fake_app
    assert calls[0][1] == run_id
    assert calls[0][2]["fields"] == {"gene_id": "AT1G01010"}
    assert calls[0][2]["approved"] is True
    assert calls[0][2]["edits"] is None


async def test_a2ui_action_review_form_cancel_succeeds(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review form cancel resumes and echoes cancelled on result.a2ui."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    run_id = _seed_review_a2ui_form_run(
        tasks_db_path,
        run_id="run-a2ui-review-form-cancel",
        surface_id="sfc-open-review-form-cancel",
    )
    calls: list[tuple[Any, ...]] = []
    fake_app = object()
    monkeypatch.setattr(
        api_app_module,
        "_review_stream_app",
        lambda: fake_app,
    )

    async def _fake_resume(
        app: Any,
        thread_id: str,
        resume_payload: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append((app, thread_id, resume_payload))
        return {
            "choices": [
                {
                    "message": {
                        "content": "Review form cancelled.",
                        "doc_list": [],
                        "follow_up_questions": [],
                    }
                }
            ]
        }

    monkeypatch.setattr(api_app_module, "_resume_paused_run", _fake_resume)

    response = await api_client.post(
        f"/v1/runs/{run_id}/a2ui-actions",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "run_id": run_id,
            "surface_id": "sfc-open-review-form-cancel",
            "widget": "form",
            "action_id": "act-review-form-cancel",
            "payload": {"cancelled": True},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["agent"] == "review"
    assert body["result"]["a2ui"]["props"]["status"] == "submitted"
    assert body["result"]["a2ui"]["props"]["cancelled"] is True
    assert "accepted" not in body["result"]["a2ui"]["props"]
    assert len(calls) == 1
    assert calls[0][0] is fake_app
    assert calls[0][1] == run_id
    assert calls[0][2]["cancelled"] is True
    assert calls[0][2]["approved"] is False
    assert calls[0][2]["edits"] is None
