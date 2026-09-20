# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review-specific HTTP A2UI action tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tests.server.test_a2ui_actions_http import (
    _seed_review_a2ui_form_run,
)
from tests.support.a2ui_contract_fakes import post_a2ui_action

from mcp_server_phytomni.api import app as api_app_module

pytestmark = pytest.mark.server


@pytest.fixture(autouse=True)
def _checkpoint_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make seeded review pauses expose the graph checkpoint seam."""

    async def _has_checkpoint(_app: Any, _thread_id: str) -> bool:
        return True

    monkeypatch.setattr(
        api_app_module, "_has_graph_checkpoint", _has_checkpoint
    )


def _install_review_resume(
    monkeypatch: pytest.MonkeyPatch,
    resume_handler: Any,
) -> object:
    """Install Review graph seams and return the selected app sentinel."""
    fake_app = object()
    monkeypatch.setattr(api_app_module, "_review_stream_app", lambda: fake_app)
    monkeypatch.setattr(
        api_app_module,
        "_resume_paused_run",
        resume_handler,
    )
    return fake_app


async def test_a2ui_action_review_form_submit_succeeds(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review form submit resumes and echoes fields on result.a2ui."""
    run_id = _seed_review_a2ui_form_run(
        tasks_db_path,
        run_id="run-a2ui-review-form-submit",
        surface_id="sfc-open-review-form",
    )
    calls: list[tuple[Any, ...]] = []

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

    fake_app = _install_review_resume(monkeypatch, _fake_resume)

    response = await post_a2ui_action(
        api_client,
        issued_api_key,
        run_id=run_id,
        surface_id="sfc-open-review-form",
        widget="form",
        action_id="act-review-form-submit",
        payload={"fields": {"gene_id": "AT1G01010"}},
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
    run_id = _seed_review_a2ui_form_run(
        tasks_db_path,
        run_id="run-a2ui-review-form-cancel",
        surface_id="sfc-open-review-form-cancel",
    )
    calls: list[tuple[Any, ...]] = []

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

    fake_app = _install_review_resume(monkeypatch, _fake_resume)

    response = await post_a2ui_action(
        api_client,
        issued_api_key,
        run_id=run_id,
        surface_id="sfc-open-review-form-cancel",
        widget="form",
        action_id="act-review-form-cancel",
        payload={"cancelled": True},
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
