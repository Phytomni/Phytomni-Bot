# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Validation helpers for complete routing-report run inventories."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

_REPEATS: Final = frozenset({1, 3})
_LANGUAGES: Final = frozenset({"en", "zh"})
_FLAGS: Final = (
    "agent_correct",
    "schema_valid",
    "dispatchable",
    "provider_completed",
)


@dataclass(frozen=True, slots=True)
class InventoryRules:
    """Allowlisted fields and agent identities for one report."""

    run_keys: frozenset[str]
    canonical_agents: frozenset[str]
    safe_predicted_agents: frozenset[str]


@dataclass(slots=True)
class _InventoryState:
    by_case: dict[str, set[int]]
    expected_by_case: dict[str, str]


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _complete_counts(
    metrics: Mapping[str, object],
    provenance: Mapping[str, object],
    run_count: int,
) -> tuple[int, int]:
    case_count = _safe_int(metrics.get("case_count"))
    planned_runs = _safe_int(metrics.get("planned_runs"))
    completed_records = _safe_int(metrics.get("completed_records"))
    repeat_count = _safe_int(provenance.get("repeat_count"))
    if case_count is None or case_count <= 0:
        raise ValueError("complete report counts do not match runs")
    if planned_runs is None or completed_records is None:
        raise ValueError("complete report counts do not match runs")
    if repeat_count not in _REPEATS:
        raise ValueError("complete report counts do not match runs")
    if planned_runs != case_count * repeat_count:
        raise ValueError("complete report counts do not match runs")
    if completed_records != planned_runs or completed_records != run_count:
        raise ValueError("complete report counts do not match runs")
    return case_count, repeat_count


def _run_identity(
    run: Mapping[str, object],
    repeat_count: int,
    canonical_agents: frozenset[str],
    safe_predicted_agents: frozenset[str],
) -> tuple[str, str, int]:
    case_id = run["case_id"]
    expected_agent = run["expected_agent"]
    predicted_agent = run["predicted_agent"]
    repeat = run["repeat"]
    language = run["language"]
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("complete report run contains invalid identity")
    if not isinstance(expected_agent, str):
        raise ValueError("complete report run contains invalid identity")
    if expected_agent not in canonical_agents:
        raise ValueError("complete report run contains invalid identity")
    if predicted_agent not in safe_predicted_agents:
        raise ValueError("complete report run contains invalid identity")
    if isinstance(repeat, bool) or not isinstance(repeat, int):
        raise ValueError("complete report run contains invalid identity")
    if repeat not in range(1, repeat_count + 1):
        raise ValueError("complete report run contains invalid identity")
    if language not in _LANGUAGES:
        raise ValueError("complete report run contains invalid identity")
    return case_id, expected_agent, repeat


def _validate_flags(run: Mapping[str, object]) -> None:
    if any(not isinstance(run[key], bool) for key in _FLAGS):
        raise ValueError("complete report run contains invalid flags")
    core_args = run["core_args_correct"]
    if core_args is not None and not isinstance(core_args, bool):
        raise ValueError("complete report run contains invalid core flag")


def _validate_timing(run: Mapping[str, object]) -> None:
    attempts = run["attempts"]
    latency_ms = run["latency_ms"]
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise ValueError("complete report run contains invalid timing")
    if attempts < 0:
        raise ValueError("complete report run contains invalid timing")
    if isinstance(latency_ms, bool) or not isinstance(latency_ms, (int, float)):
        raise ValueError("complete report run contains invalid timing")
    if not math.isfinite(float(latency_ms)) or latency_ms < 0:
        raise ValueError("complete report run contains invalid timing")


def _validate_details(run: Mapping[str, object]) -> None:
    if not isinstance(run["selected_arguments"], Mapping):
        raise ValueError("complete report run contains invalid details")
    if not isinstance(run["validation_codes"], list):
        raise ValueError("complete report run contains invalid details")


def _record_run(
    run: Mapping[str, object],
    repeat_count: int,
    rules: InventoryRules,
    state: _InventoryState,
) -> None:
    if set(run) != rules.run_keys:
        raise ValueError("complete report run is incomplete")
    case_id, expected_agent, repeat = _run_identity(
        run,
        repeat_count,
        rules.canonical_agents,
        rules.safe_predicted_agents,
    )
    _validate_flags(run)
    _validate_timing(run)
    _validate_details(run)
    prior_expected = state.expected_by_case.setdefault(case_id, expected_agent)
    if prior_expected != expected_agent:
        raise ValueError("complete report expected agents disagree")
    repeats = state.by_case.setdefault(case_id, set())
    if repeat in repeats:
        raise ValueError("complete report contains duplicate runs")
    repeats.add(repeat)


def _validate_case_inventory(
    by_case: Mapping[str, set[int]], case_count: int, repeat_count: int
) -> None:
    expected_repeats = set(range(1, repeat_count + 1))
    if len(by_case) != case_count:
        raise ValueError("complete report case inventory is incomplete")
    if any(repeats != expected_repeats for repeats in by_case.values()):
        raise ValueError("complete report case inventory is incomplete")


def validate_complete_run_inventory(
    metrics: Mapping[str, object],
    provenance: Mapping[str, object],
    runs: Sequence[Mapping[str, object]],
    *,
    rules: InventoryRules,
) -> None:
    """Keep the public writer from emitting a forged complete report."""
    case_count, repeat_count = _complete_counts(metrics, provenance, len(runs))
    state = _InventoryState(by_case={}, expected_by_case={})
    for run in runs:
        _record_run(run, repeat_count, rules, state)
    _validate_case_inventory(state.by_case, case_count, repeat_count)
