# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic join reduction for durable remote fan-out work."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .execution_journal_v2 import WorkUnitStatus
from .execution_work_store_v2 import JoinPolicy, WorkUnitRecord

JoinOutcome = Literal["waiting", "succeeded", "partial", "failed"]

_SUCCESS = {WorkUnitStatus.SUCCEEDED}
_FAILURE = {
    WorkUnitStatus.FAILED,
    WorkUnitStatus.CANCELLED,
    WorkUnitStatus.TIMED_OUT,
}
_TERMINAL = _SUCCESS | _FAILURE | {WorkUnitStatus.PARTIAL}


@dataclass(frozen=True, slots=True)
class FanoutJoinResult:
    """Order-independent public-neutral join decision."""

    outcome: JoinOutcome
    succeeded: tuple[str, ...]
    failed: tuple[str, ...]
    pending: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _JoinState:
    succeeded: tuple[str, ...]
    failed: tuple[str, ...]
    pending: tuple[str, ...]

    @property
    def branch_count(self) -> int:
        """Return the number of normalized fan-out branches."""
        return len(self.succeeded) + len(self.failed) + len(self.pending)


def _quorum_outcome(
    state: _JoinState,
    quorum: int | None,
) -> JoinOutcome:
    """Return the deterministic outcome for a quorum join."""
    required = quorum if quorum is not None else state.branch_count
    if required < 1 or required > state.branch_count:
        raise ValueError("invalid fanout quorum")
    if len(state.succeeded) >= required:
        return "succeeded" if not state.failed else "partial"
    if len(state.succeeded) + len(state.pending) < required:
        return "failed" if not state.succeeded else "partial"
    return "waiting"


def _settled_outcome(policy: JoinPolicy, state: _JoinState) -> JoinOutcome:
    """Return the outcome for a join whose branches are all terminal."""
    if policy == "all":
        return "succeeded" if not state.failed else "failed"
    if policy != "best_effort":
        raise ValueError("unsupported fanout join policy")
    if state.succeeded and state.failed:
        return "partial"
    return "succeeded" if state.succeeded else "failed"


def _join_outcome(
    policy: JoinPolicy,
    state: _JoinState,
    quorum: int | None,
) -> JoinOutcome:
    """Select an outcome once branch state has been normalized."""
    if policy == "fail_fast" and state.failed:
        return "failed"
    if policy == "quorum":
        return _quorum_outcome(state, quorum)
    if state.pending:
        return "waiting"
    return _settled_outcome(policy, state)


def reduce_fanout_join(
    units: Sequence[WorkUnitRecord],
    *,
    policy: JoinPolicy,
    quorum: int | None = None,
) -> FanoutJoinResult:
    """Reduce branch states without invoking Agent synthesis/business code."""
    if not units:
        raise ValueError("fanout join requires at least one branch")
    identities = [unit.work_unit_id for unit in units]
    if len(set(identities)) != len(identities):
        raise ValueError("fanout branch identities must be unique")
    execution_ids = {unit.execution_id for unit in units}
    parent_spans = {unit.parent_span_id for unit in units}
    if len(execution_ids) != 1 or len(parent_spans) != 1:
        raise ValueError("fanout branches must share execution and parent")
    ordered = sorted(units, key=lambda unit: unit.work_unit_id)
    succeeded = tuple(
        unit.work_unit_id for unit in ordered if unit.status in _SUCCESS
    )
    failed = tuple(
        unit.work_unit_id
        for unit in ordered
        if unit.status in _FAILURE or unit.status is WorkUnitStatus.PARTIAL
    )
    pending = tuple(
        unit.work_unit_id for unit in ordered if unit.status not in _TERMINAL
    )

    state = _JoinState(succeeded=succeeded, failed=failed, pending=pending)
    outcome = _join_outcome(policy, state, quorum)
    return FanoutJoinResult(
        outcome=outcome,
        succeeded=succeeded,
        failed=failed,
        pending=pending,
    )
