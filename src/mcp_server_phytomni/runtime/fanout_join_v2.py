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

    if policy == "fail_fast" and failed:
        outcome: JoinOutcome = "failed"
    elif policy == "quorum":
        required = quorum if quorum is not None else len(ordered)
        if required < 1 or required > len(ordered):
            raise ValueError("invalid fanout quorum")
        if len(succeeded) >= required:
            outcome = "succeeded" if not failed else "partial"
        elif len(succeeded) + len(pending) < required:
            outcome = "failed" if not succeeded else "partial"
        else:
            outcome = "waiting"
    elif pending:
        outcome = "waiting"
    elif policy == "all":
        outcome = "succeeded" if not failed else "failed"
    elif policy == "best_effort":
        if succeeded and failed:
            outcome = "partial"
        elif succeeded:
            outcome = "succeeded"
        else:
            outcome = "failed"
    else:
        raise ValueError("unsupported fanout join policy")
    return FanoutJoinResult(
        outcome=outcome,
        succeeded=succeeded,
        failed=failed,
        pending=pending,
    )
