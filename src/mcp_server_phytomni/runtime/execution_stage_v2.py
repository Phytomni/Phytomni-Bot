# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Authoritative non-regressing end-to-end stage and surface reducer."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, model_validator

from .execution_liveness_v2 import ExecutionLivenessClocks


class ExecutionStage(StrEnum):
    """Finite public stages, ordered from admission to answer settlement."""

    ORCHESTRATION = "orchestration"
    SCIENTIFIC_EXECUTION = "scientific_execution"
    CONSOLIDATION = "consolidation"
    RESPONSE_SETTLEMENT = "response_settlement"


class ExecutionStageSignal(StrEnum):
    """Finite authoritative inputs accepted by the stage reducer."""

    PROVIDER_SUBMITTED = "provider_submitted"
    PROVIDER_STATE_CHANGED = "provider_state_changed"
    SCIENTIFIC_EXECUTION_STARTED = "scientific_execution_started"
    PROVIDER_CONTACT_UNCHANGED = "provider_contact_unchanged"
    STREAM_CONTACT = "stream_contact"
    CONSOLIDATION_STARTED = "consolidation_started"
    ANSWER_AVAILABLE = "answer_available"
    ROOT_SUCCEEDED = "root_succeeded"
    ROOT_PARTIAL = "root_partial"
    ROOT_FAILED = "root_failed"
    ROOT_CANCELLED = "root_cancelled"
    ROOT_TIMED_OUT = "root_timed_out"


TodoId = Literal["planning", "analysis", "consolidation", "response"]
TodoStatus = Literal[
    "pending", "in_progress", "completed", "failed", "skipped"
]
RootStatus = Literal[
    "running", "succeeded", "partial", "failed", "cancelled", "timed_out"
]
ChildStatus = Literal[
    "running", "succeeded", "partial", "failed", "cancelled", "timed_out"
]


class ExecutionStageTodo(BaseModel):
    """One derived generic Todo item for the end-to-end lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: TodoId
    status: TodoStatus


_STAGES = tuple(ExecutionStage)
_TODO_IDS: tuple[TodoId, ...] = (
    "planning",
    "analysis",
    "consolidation",
    "response",
)
_PENDING_KEYS = {
    ExecutionStage.ORCHESTRATION: "execution.pending.submitted",
    ExecutionStage.SCIENTIFIC_EXECUTION: "execution.pending.running",
    ExecutionStage.CONSOLIDATION: "execution.pending.consolidating",
    ExecutionStage.RESPONSE_SETTLEMENT: "execution.pending.settling",
}
_TERMINAL_ROOT_STATUSES = {
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "timed_out",
}


class ExecutionStageState(BaseModel):
    """Validated stage plus every user-facing state derived from that stage."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, use_enum_values=True
    )

    stage: ExecutionStage = ExecutionStage.ORCHESTRATION
    child_status: ChildStatus | None = None
    root_status: RootStatus = "running"
    answer_available: bool = False
    todos: tuple[ExecutionStageTodo, ...]
    pending_status_key: str | None
    clocks: ExecutionLivenessClocks = ExecutionLivenessClocks()

    @staticmethod
    def derive_todos(
        stage: ExecutionStage | str,
        root_status: RootStatus,
    ) -> tuple[ExecutionStageTodo, ...]:
        """Derive the generic Todo snapshot; producers cannot override it."""
        current = _STAGES.index(ExecutionStage(stage))
        if root_status in {"succeeded", "partial"}:
            statuses: tuple[TodoStatus, ...] = (
                "completed",
                "completed",
                "completed",
                "completed",
            )
        elif root_status in {"failed", "timed_out"}:
            statuses = tuple(
                (
                    "completed"
                    if index < current
                    else "failed" if index == current else "pending"
                )
                for index in range(len(_TODO_IDS))
            )
        elif root_status == "cancelled":
            statuses = tuple(
                "completed" if index < current else "skipped"
                for index in range(len(_TODO_IDS))
            )
        else:
            statuses = tuple(
                (
                    "completed"
                    if index < current
                    else "in_progress" if index == current else "pending"
                )
                for index in range(len(_TODO_IDS))
            )
        return tuple(
            ExecutionStageTodo(id=todo_id, status=status)
            for todo_id, status in zip(_TODO_IDS, statuses, strict=True)
        )

    @model_validator(mode="after")
    def validate_derived_surface(self) -> Self:
        expected_todos = self.derive_todos(self.stage, self.root_status)
        if self.todos != expected_todos:
            raise ValueError("todo_stage_mismatch")
        expected_pending = (
            None
            if self.root_status in _TERMINAL_ROOT_STATUSES
            else _PENDING_KEYS[ExecutionStage(self.stage)]
        )
        if self.pending_status_key != expected_pending:
            raise ValueError("pending_answer_mismatch")
        if self.root_status in {"succeeded", "partial"} and (
            self.stage != ExecutionStage.RESPONSE_SETTLEMENT.value
            or not self.answer_available
        ):
            raise ValueError("root_terminal_before_settlement")
        return self


def empty_execution_stage_state(
    *,
    clocks: ExecutionLivenessClocks | None = None,
) -> ExecutionStageState:
    """Create the one valid initial surface state."""
    stage = ExecutionStage.ORCHESTRATION
    status: RootStatus = "running"
    return ExecutionStageState(
        stage=stage,
        root_status=status,
        todos=ExecutionStageState.derive_todos(stage, status),
        pending_status_key=_PENDING_KEYS[stage],
        clocks=clocks or ExecutionLivenessClocks(),
    )


def reduce_execution_stage(
    state: ExecutionStageState,
    signal: ExecutionStageSignal,
    *,
    occurred_at: str,
) -> ExecutionStageState:
    """Apply one live or replayed signal through identical transition rules."""
    signal = ExecutionStageSignal(signal)
    if signal is ExecutionStageSignal.STREAM_CONTACT:
        return state.model_copy(
            update={"clocks": state.clocks.observe_stream_contact(occurred_at)}
        )
    if signal is ExecutionStageSignal.PROVIDER_CONTACT_UNCHANGED:
        return state.model_copy(
            update={
                "clocks": state.clocks.observe_provider_contact(
                    occurred_at,
                    semantic_changed=False,
                )
            }
        )

    target_stage = _target_stage(signal, state.stage)
    if _STAGES.index(target_stage) < _STAGES.index(
        ExecutionStage(state.stage)
    ):
        raise ValueError("stage_regression")
    if state.root_status in _TERMINAL_ROOT_STATUSES:
        raise ValueError("root_already_terminal")

    child_status = state.child_status
    root_status = state.root_status
    answer_available = state.answer_available
    clocks = state.clocks
    if signal is ExecutionStageSignal.PROVIDER_SUBMITTED:
        child_status = "succeeded"
        clocks = clocks.observe_provider_contact(
            occurred_at,
            semantic_changed=True,
        )
    elif signal is ExecutionStageSignal.PROVIDER_STATE_CHANGED:
        clocks = clocks.observe_provider_contact(
            occurred_at,
            semantic_changed=True,
        )
    else:
        clocks = clocks.observe_execution_fact(occurred_at)

    if signal is ExecutionStageSignal.ANSWER_AVAILABLE:
        answer_available = True
    terminal_status = _root_status(signal)
    if terminal_status is not None:
        if terminal_status in {"succeeded", "partial"} and (
            target_stage is not ExecutionStage.RESPONSE_SETTLEMENT
            or not answer_available
        ):
            raise ValueError("root_terminal_before_settlement")
        root_status = terminal_status

    return ExecutionStageState(
        stage=target_stage,
        child_status=child_status,
        root_status=root_status,
        answer_available=answer_available,
        todos=ExecutionStageState.derive_todos(target_stage, root_status),
        pending_status_key=(
            None
            if root_status in _TERMINAL_ROOT_STATUSES
            else _PENDING_KEYS[target_stage]
        ),
        clocks=clocks,
    )


def _target_stage(
    signal: ExecutionStageSignal,
    current: ExecutionStage | str,
) -> ExecutionStage:
    return {
        ExecutionStageSignal.PROVIDER_SUBMITTED: ExecutionStage.ORCHESTRATION,
        ExecutionStageSignal.PROVIDER_STATE_CHANGED: (
            ExecutionStage.SCIENTIFIC_EXECUTION
        ),
        ExecutionStageSignal.SCIENTIFIC_EXECUTION_STARTED: (
            ExecutionStage.SCIENTIFIC_EXECUTION
        ),
        ExecutionStageSignal.CONSOLIDATION_STARTED: ExecutionStage.CONSOLIDATION,
        ExecutionStageSignal.ANSWER_AVAILABLE: ExecutionStage.RESPONSE_SETTLEMENT,
    }.get(signal, ExecutionStage(current))


def _root_status(signal: ExecutionStageSignal) -> RootStatus | None:
    return cast(
        RootStatus | None,
        {
            ExecutionStageSignal.ROOT_SUCCEEDED: "succeeded",
            ExecutionStageSignal.ROOT_PARTIAL: "partial",
            ExecutionStageSignal.ROOT_FAILED: "failed",
            ExecutionStageSignal.ROOT_CANCELLED: "cancelled",
            ExecutionStageSignal.ROOT_TIMED_OUT: "timed_out",
        }.get(signal),
    )
