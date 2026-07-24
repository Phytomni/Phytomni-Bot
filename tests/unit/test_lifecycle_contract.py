# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the canonical public lifecycle contract helpers."""

from __future__ import annotations

from typing import Any

import pytest

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


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [("failed", "run failed"), ("succeeded", None)],
)
def test_persisted_record_redacts_error_and_result_details(
    status: str, expected_error: str | None
) -> None:
    """Persisted terminal reads keep only the safe canonical projection."""
    projected = canonicalize_run_record(
        {
            "run_id": f"run-{status}",
            "agent": "chat",
            "status": status,
            "task_ids": [],
            "error": "provider exception: /srv/private",
            "result": {
                "provider_trace": "private",
                "raw": {"path": "/srv/private"},
                "execution": {
                    "artifacts": [{"name": "result.tsv"}],
                    "provider_payload": {"trace": "private"},
                },
            },
        }
    )

    assert projected["result"] == {
        "formatted": empty_agent_result()["formatted"],
        "execution": {
            **empty_agent_result()["execution"],
            "artifacts": [{"name": "result.tsv"}],
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
