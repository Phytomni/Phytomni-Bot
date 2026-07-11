# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for internal-to-A2A task-state projection."""

from __future__ import annotations

import pytest
from a2a.types import TaskState
from google.protobuf import json_format

from mcp_server_phytomni.api.a2a.status import (
    build_task_status,
    project_task_state,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("internal", "expected"),
    [
        ("submitted", "TASK_STATE_SUBMITTED"),
        ("pending", "TASK_STATE_SUBMITTED"),
        ("running", "TASK_STATE_WORKING"),
        ("working", "TASK_STATE_WORKING"),
        ("succeeded", "TASK_STATE_COMPLETED"),
        ("success", "TASK_STATE_COMPLETED"),
        ("completed", "TASK_STATE_COMPLETED"),
        ("failed", "TASK_STATE_FAILED"),
        ("error", "TASK_STATE_FAILED"),
        ("cancelled", "TASK_STATE_CANCELED"),
        ("canceled", "TASK_STATE_CANCELED"),
        ("input_required", "TASK_STATE_INPUT_REQUIRED"),
        ("auth-required", "TASK_STATE_AUTH_REQUIRED"),
        ("rejected", "TASK_STATE_REJECTED"),
    ],
)
def test_internal_statuses_map_to_explicit_a2a_states(
    internal: str,
    expected: str,
) -> None:
    """Known internal statuses use the corresponding v1 enum value."""
    projection = project_task_state(internal)

    assert TaskState.Name(projection.task_state) == expected
    assert projection.detail is None


def test_unknown_status_fails_closed_with_audit_detail() -> None:
    """Unknown backend values never silently become WORKING or COMPLETED."""
    projection = project_task_state("backend_maybe_done")

    assert projection.task_state == TaskState.TASK_STATE_FAILED
    assert (
        projection.detail
        == "unmapped internal run status: 'backend_maybe_done'"
    )


def test_phase_is_a_status_message_not_a_task_state() -> None:
    """Progress phase metadata cannot change the lifecycle enum."""
    status = build_task_status("running", phase="retrieval")
    payload = json_format.MessageToDict(status)

    assert status.state == TaskState.TASK_STATE_WORKING
    assert payload["message"]["parts"][0]["text"] == "phase: retrieval"


def test_unknown_detail_and_explicit_detail_share_one_status_message() -> None:
    """Audit details remain visible without changing the failed state."""
    status = build_task_status(
        "new_backend_state",
        detail="registry reconciliation required",
    )

    assert status.state == TaskState.TASK_STATE_FAILED
    assert "unmapped internal run status" in status.message.parts[0].text
    assert "registry reconciliation required" in status.message.parts[0].text
