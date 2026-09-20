# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""The end-to-end public stage is one monotonic source of surface truth."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support.execution_event_fixtures import (
    EXECUTION_TRACE_DETAIL_V1_FIXTURES,
)

from mcp_server_phytomni.runtime.execution_liveness_v2 import (
    ExecutionLivenessClocks,
)
from mcp_server_phytomni.runtime.execution_stage_v2 import (
    ExecutionStage,
    ExecutionStageSignal,
    ExecutionStageState,
    ExecutionStageTodo,
    empty_execution_stage_state,
    reduce_execution_stage,
)


def _stage_cases() -> dict[str, dict[str, object]]:
    fixtures = json.loads(
        EXECUTION_TRACE_DETAIL_V1_FIXTURES.read_text(encoding="utf-8")
    )
    return {case["name"]: case for case in fixtures["stage_transition_cases"]}


def _public(state) -> dict[str, object]:
    return state.model_dump(mode="json", exclude_none=False)


def test_shared_stage_cases_reduce_to_one_todo_and_pending_surface() -> None:
    """Verify shared stage cases reduce to one todo and pending surface."""

    cases = _stage_cases()
    state = empty_execution_stage_state(
        clocks=ExecutionLivenessClocks(
            last_stream_contact_at="2026-08-22T03:59:59Z"
        )
    )
    observations = [
        (ExecutionStageSignal.PROVIDER_SUBMITTED, "2026-08-22T04:00:00Z"),
        (ExecutionStageSignal.STREAM_CONTACT, "2026-08-22T04:00:04Z"),
        (ExecutionStageSignal.PROVIDER_STATE_CHANGED, "2026-08-22T04:00:05Z"),
        (
            ExecutionStageSignal.PROVIDER_CONTACT_UNCHANGED,
            "2026-08-22T04:00:15Z",
        ),
        (ExecutionStageSignal.STREAM_CONTACT, "2026-08-22T04:00:20Z"),
        (
            ExecutionStageSignal.PROVIDER_CONTACT_UNCHANGED,
            "2026-08-22T04:00:55Z",
        ),
        (ExecutionStageSignal.CONSOLIDATION_STARTED, "2026-08-22T04:01:00Z"),
        (ExecutionStageSignal.STREAM_CONTACT, "2026-08-22T04:01:09Z"),
        (ExecutionStageSignal.ANSWER_AVAILABLE, "2026-08-22T04:01:10Z"),
        (ExecutionStageSignal.STREAM_CONTACT, "2026-08-22T04:01:12Z"),
        (ExecutionStageSignal.ROOT_SUCCEEDED, "2026-08-22T04:01:12Z"),
    ]
    expected_after = {
        0: "child_submission_succeeded",
        2: "provider_running",
        3: "unchanged_provider_poll",
        4: "stream_heartbeat",
        6: "provider_terminal_consolidating",
        8: "answer_settling",
        10: "root_succeeded",
    }
    for index, (signal, occurred_at) in enumerate(observations):
        state = reduce_execution_stage(state, signal, occurred_at=occurred_at)
        if index in expected_after:
            assert _public(state) == cases[expected_after[index]]["state"]

    replayed = empty_execution_stage_state(
        clocks=ExecutionLivenessClocks(
            last_stream_contact_at="2026-08-22T03:59:59Z"
        )
    )
    for signal, occurred_at in observations:
        replayed = reduce_execution_stage(
            replayed,
            signal,
            occurred_at=occurred_at,
        )
    assert replayed == state


def test_stage_regression_and_premature_root_success_are_rejected() -> None:
    """Verify stage regression and premature root success are rejected."""

    state = reduce_execution_stage(
        empty_execution_stage_state(),
        ExecutionStageSignal.PROVIDER_STATE_CHANGED,
        occurred_at="2026-08-22T04:00:05Z",
    )
    with pytest.raises(ValueError, match="stage_regression"):
        reduce_execution_stage(
            state,
            ExecutionStageSignal.PROVIDER_SUBMITTED,
            occurred_at="2026-08-22T04:00:06Z",
        )
    with pytest.raises(ValueError, match="root_terminal_before_settlement"):
        reduce_execution_stage(
            state,
            ExecutionStageSignal.ROOT_SUCCEEDED,
            occurred_at="2026-08-22T04:00:07Z",
        )


def test_todo_and_pending_status_cannot_disagree_with_stage() -> None:
    """Verify todo and pending status cannot disagree with stage."""

    with pytest.raises(ValidationError, match="todo_stage_mismatch"):
        ExecutionStageState(
            stage=ExecutionStage.SCIENTIFIC_EXECUTION,
            todos=(
                ExecutionStageTodo(id="planning", status="in_progress"),
                ExecutionStageTodo(id="analysis", status="pending"),
                ExecutionStageTodo(id="consolidation", status="pending"),
                ExecutionStageTodo(id="response", status="pending"),
            ),
            pending_status_key="execution.pending.running",
        )
    with pytest.raises(ValidationError, match="pending_answer_mismatch"):
        ExecutionStageState(
            stage=ExecutionStage.SCIENTIFIC_EXECUTION,
            todos=ExecutionStageState.derive_todos(
                ExecutionStage.SCIENTIFIC_EXECUTION,
                "running",
            ),
            pending_status_key=None,
        )


def test_child_success_is_bounded_and_failure_terminalizes_current_todo() -> (
    None
):
    """Verify child success is bounded and failure terminalizes current
    todo."""

    submitted = reduce_execution_stage(
        empty_execution_stage_state(),
        ExecutionStageSignal.PROVIDER_SUBMITTED,
        occurred_at="2026-08-22T04:00:00Z",
    )
    assert submitted.child_status == "succeeded"
    assert submitted.root_status == "running"

    running = reduce_execution_stage(
        submitted,
        ExecutionStageSignal.PROVIDER_STATE_CHANGED,
        occurred_at="2026-08-22T04:00:05Z",
    )
    failed = reduce_execution_stage(
        running,
        ExecutionStageSignal.ROOT_FAILED,
        occurred_at="2026-08-22T04:00:06Z",
    )
    assert failed.root_status == "failed"
    assert failed.pending_status_key is None
    assert [todo.status for todo in failed.todos] == [
        "completed",
        "failed",
        "pending",
        "pending",
    ]
