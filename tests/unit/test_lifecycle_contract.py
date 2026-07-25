# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the canonical public lifecycle contract helpers."""

from __future__ import annotations

from typing import Any

import pytest
from tests.support.terminal_results import (
    SensitiveTerminalResultSpec,
    public_partial_warning,
    public_report_projection,
    public_scientific_table_artifact,
    sensitive_terminal_result,
)

from mcp_server_phytomni.api.lifecycle_contract import (
    LifecycleInvariantError,
    build_agent_run_response,
    canonicalize_run_record,
    empty_agent_result,
)


def test_running_without_recoverable_work_is_rejected() -> None:
    """A running response must point at recoverable work."""
    with pytest.raises(LifecycleInvariantError, match="running_without_work"):
        build_agent_run_response(
            run_id=None,
            agent="research",
            status="running",
            task_ids=(),
            result=empty_agent_result(),
            persisted=False,
            degraded_tracking=True,
        )


def test_degraded_running_preserves_real_task_ids() -> None:
    """The degraded remote path still exposes accepted upstream task ids."""
    response = build_agent_run_response(
        run_id=None,
        agent="research",
        status="running",
        task_ids=("task-upstream-1",),
        result=empty_agent_result(degraded=True),
        persisted=False,
        degraded_tracking=True,
    )
    assert response["id"] is None
    assert "run_id" not in response
    assert response["task_ids"] == ["task-upstream-1"]
    assert response["degraded_tracking"] is True


def test_succeeded_requires_persistence() -> None:
    """A 200 succeeded response cannot escape before persistence."""
    with pytest.raises(
        LifecycleInvariantError, match="succeeded_without_persistence"
    ):
        build_agent_run_response(
            run_id="run-data-1",
            agent="data",
            status="succeeded",
            task_ids=(),
            result=empty_agent_result(),
            persisted=False,
        )


def test_input_required_requires_valid_surface() -> None:
    """Paused public runs must carry a valid supported A2UI surface."""
    with pytest.raises(
        LifecycleInvariantError, match="input_required_without_surface"
    ):
        build_agent_run_response(
            run_id="run-review-1",
            agent="review",
            status="input_required",
            task_ids=(),
            result={"interrupt": {"draft": {}}},
            persisted=True,
        )


def test_persisted_running_record_requires_recoverable_identity() -> None:
    """A read projection cannot invent recovery for an anonymous run."""
    with pytest.raises(
        LifecycleInvariantError, match="routing_contract_violation"
    ):
        canonicalize_run_record(
            {
                "agent": "chat",
                "status": "running",
                "result": None,
                "task_ids": [],
            }
        )


def test_persisted_review_interrupt_uses_safe_summary_fallback() -> None:
    """Unknown review draft fields cannot be rendered into public surfaces."""
    projected = canonicalize_run_record(
        {
            "run_id": "run-review-safe-summary",
            "agent": "review",
            "status": "input_required",
            "task_ids": [],
            "result": {
                "interrupt": {
                    "draft": {
                        "provider_payload": "/srv/private/provider",
                    }
                }
            },
        }
    )

    interrupt = projected["result"]["interrupt"]
    assert interrupt["draft"]["summary"] == "Review approval required."
    assert "/srv/private/provider" not in str(interrupt)


def test_terminal_projection_preserves_submitted_a2ui_value() -> None:
    """Terminal A2UI submission state survives canonicalization."""
    projected = canonicalize_run_record(
        {
            "run_id": "run-review-submitted",
            "agent": "review",
            "status": "succeeded",
            "task_ids": [],
            "result": {
                "formatted": {"answer": "Approved final review."},
                "a2ui": {
                    "catalog_version": "v1.0",
                    "surface_id": "review-surface",
                    "widget": "confirm",
                    "props": {
                        "title": "Review approval",
                        "body": "draft review",
                        "status": "submitted",
                        "accepted": True,
                    },
                },
            },
        }
    )

    assert projected["result"]["a2ui"]["props"] == {
        "title": "Review approval",
        "body": "draft review",
        "status": "submitted",
        "accepted": True,
    }


def test_persisted_record_drops_unknown_top_level_fields() -> None:
    """Only known run history fields and canonical result data are public."""
    projected = canonicalize_run_record(
        {
            "run_id": "run-top-level-projection",
            "agent": "chat",
            "origin": "local",
            "user_id": "u1",
            "status": "failed",
            "created_at": "2026-07-25T00:00:00+00:00",
            "updated_at": "2026-07-25T00:01:00+00:00",
            "expires_at": "2026-07-26T00:00:00+00:00",
            "dialogue_id": "dialogue-1",
            "query": "public query",
            "tool_name": "PhytoChat",
            "model": "phyto-chat",
            "a2a_task_id": "task-1",
            "a2a_context_id": "context-1",
            "a2a_message_id": "message-1",
            "task_ids": [],
            "answer": "/srv/private/legacy-answer",
            "error": "provider exception: /srv/private",
            "provider_payload": {"path": "/srv/private"},
            "raw": {"trace": "private"},
            "result": {
                "formatted": {"answer": "canonical answer"},
                "execution": {},
            },
        }
    )

    assert projected["answer"] == "canonical answer"
    assert projected["error"] == "run failed"
    assert "provider_payload" not in projected
    assert "raw" not in projected
    assert "/srv/private" not in str(projected)


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [("failed", "run failed"), ("succeeded", None)],
)
def test_persisted_record_redacts_error_and_result_details(
    status: str, expected_error: str | None
) -> None:
    """Persisted terminal reads deeply project nested public fields."""
    projected = canonicalize_run_record(
        {
            "run_id": f"run-{status}",
            "agent": "chat",
            "status": status,
            "task_ids": [],
            "error": "provider exception: /srv/private",
            "result": sensitive_terminal_result(
                SensitiveTerminalResultSpec(
                    answer="public answer",
                    task_id="task-1",
                    citation=("di", "10.1/example"),
                    table=(["gene", "score"], [["AT1G01010", 0.9]]),
                    warning=("exception", "private"),
                )
            ),
        }
    )

    assert projected["result"] == {
        "formatted": {
            "answer": "public answer",
            "follow_up_questions": ["next?"],
            "references": [
                {
                    "file_id": "doc-1",
                    "title": "Public title",
                    "di": "10.1/example",
                }
            ],
            "tabular": {
                "headers": ["gene", "score"],
                "rows": [["AT1G01010", 0.9]],
            },
            "metadata": {
                "original_query": "public query",
            },
        },
        "execution": {
            "tracking": {"degraded": False},
            "warnings": [public_partial_warning()],
            "tasks": [
                {
                    "id": "task-1",
                    "accepted": True,
                    "status": "succeeded",
                }
            ],
            "artifacts": [public_scientific_table_artifact()],
            "output_dirs": ["/obs/public/result"],
            "report": public_report_projection(),
            "diagnostics": [
                {
                    "code": "upstream_partial",
                    "stage": "analysis",
                    "retryable": False,
                }
            ],
        },
    }
    if expected_error is None:
        assert "error" not in projected
    else:
        assert projected["error"] == expected_error


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"persisted": True}, "missing required keyword-only argument"),
        ({"result": empty_agent_result()}, "missing required keyword-only"),
        (
            {
                "result": empty_agent_result(),
                "persisted": True,
                "unsupported": True,
            },
            "unexpected keyword argument",
        ),
    ],
)
def test_builder_rejects_missing_or_unknown_options(
    options: dict[str, Any],
    message: str,
) -> None:
    """The builder reports invalid keyword options as normal call errors."""
    with pytest.raises(TypeError, match=message):
        build_agent_run_response(
            run_id="run-options",
            agent="chat",
            status="succeeded",
            task_ids=(),
            **options,
        )
