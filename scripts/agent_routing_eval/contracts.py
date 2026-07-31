# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared report-shape keys and bounded inventory validation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from .dataset import AgentRoutingCase
from .runner import RunOutcome

METRIC_KEY_ORDER: Final = (
    "schema_version",
    "case_count",
    "planned_runs",
    "completed_records",
    "run_level",
    "majority",
    "per_agent",
    "macro",
    "by_language",
    "confusion_matrix",
    "stability",
    "core_arguments",
    "errors",
    "provider_completion",
    "latency_ms",
)
MAJORITY_KEYS: Final = frozenset(
    {
        "top1_correct",
        "top1_accuracy",
        "dispatchable_correct",
        "dispatchable_accuracy",
        "wilson_95",
    }
)
AGENT_ROW_KEYS: Final = frozenset(
    {"support", "predicted", "true_positive", "precision", "recall", "f1"}
)
LANGUAGE_ROW_KEYS: Final = frozenset(
    {
        "case_count",
        "top1_correct",
        "top1_accuracy",
        "dispatchable_correct",
        "dispatchable_accuracy",
    }
)


def validate_known_inventory(
    cases: Sequence[AgentRoutingCase],
    outcomes: Sequence[RunOutcome],
    repeat_count: int,
) -> tuple[dict[str, AgentRoutingCase], set[tuple[str, int]]]:
    """Validate case identity and the known portion of one run inventory."""
    if (
        not isinstance(repeat_count, int)
        or isinstance(repeat_count, bool)
        or repeat_count not in {1, 3}
    ):
        raise ValueError("repeat_count must be 1 or 3")
    case_by_id: dict[str, AgentRoutingCase] = {}
    for case in cases:
        if case.case_id in case_by_id:
            raise ValueError(f"duplicate case ID: {case.case_id}")
        case_by_id[case.case_id] = case
    observed: set[tuple[str, int]] = set()
    for outcome in outcomes:
        key = (outcome.case_id, outcome.repeat_index)
        if outcome.case_id not in case_by_id:
            raise ValueError(f"outcome has unknown case ID: {outcome.case_id}")
        if key in observed:
            raise ValueError(
                f"duplicate outcome: {outcome.case_id}/{outcome.repeat_index}"
            )
        if outcome.repeat_index not in range(1, repeat_count + 1):
            raise ValueError(f"invalid repeat index: {outcome.repeat_index}")
        case = case_by_id[outcome.case_id]
        if outcome.expected_agent != case.expected_agent:
            raise ValueError(
                f"outcome expected agent mismatch: {outcome.case_id}"
            )
        if outcome.language != case.language:
            raise ValueError(f"outcome language mismatch: {outcome.case_id}")
        observed.add(key)
    return case_by_id, observed
