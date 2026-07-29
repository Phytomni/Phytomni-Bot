# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic metrics for selector-only agent-routing evaluations."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

from .dataset import AgentRoutingCase
from .runner import PROVIDER_ERROR, ROUTING_ERROR, RunOutcome

NO_MAJORITY: Final = "__NO_MAJORITY__"
_BASIS_SINGLE_RUN: Final = "single_run"
_BASIS_CASE_MAJORITY: Final = "case_majority"
_CANONICAL_AGENTS: Final = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)
_CONFUSION_COLUMNS: Final = (
    *_CANONICAL_AGENTS,
    PROVIDER_ERROR,
    ROUTING_ERROR,
    NO_MAJORITY,
)
_ERROR_KEYS: Final = ("provider", "routing", "schema")
_WILSON_Z: Final = 1.959963984540054


@dataclass(frozen=True, slots=True)
class _CaseProjection:
    """One classification record after the requested run aggregation."""

    expected_agent: str
    predicted_agent: str
    language: str
    top1_correct: bool
    dispatchable: bool


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _wilson_95(successes: int, observations: int) -> list[float]:
    """Return a two-sided 95% Wilson interval in JSON-compatible form."""
    if observations == 0:
        return [0.0, 1.0]
    if successes == 0:
        proportion = 0.0
    elif successes == observations:
        proportion = 1.0
    else:
        proportion = successes / observations
    z_squared = _WILSON_Z**2
    denominator = 1.0 + z_squared / observations
    centre = proportion + z_squared / (2.0 * observations)
    margin = _WILSON_Z * math.sqrt(
        proportion * (1.0 - proportion) / observations
        + z_squared / (4.0 * observations**2)
    )
    lower = (centre - margin) / denominator
    upper = (centre + margin) / denominator
    if successes == 0:
        lower = 0.0
    if successes == observations:
        upper = 1.0
    return [max(0.0, lower), min(1.0, upper)]


def _linear_quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    index = (len(values) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(values[lower])
    weight = index - lower
    return float(values[lower] + (values[upper] - values[lower]) * weight)


def _validate_inventory(
    cases: Sequence[AgentRoutingCase],
    outcomes: Sequence[RunOutcome],
    repeat_count: int,
) -> dict[str, tuple[RunOutcome, ...]]:
    if type(repeat_count) is not int or repeat_count not in {1, 3}:
        raise ValueError("repeat_count must be 1 or 3")

    case_by_id: dict[str, AgentRoutingCase] = {}
    for case in cases:
        if case.case_id in case_by_id:
            raise ValueError(f"duplicate case ID: {case.case_id}")
        if case.expected_agent not in _CANONICAL_AGENTS:
            raise ValueError(f"unknown expected agent: {case.expected_agent}")
        case_by_id[case.case_id] = case

    expected_keys = {
        (case_id, repeat_index)
        for case_id in case_by_id
        for repeat_index in range(1, repeat_count + 1)
    }
    actual_keys: set[tuple[str, int]] = set()
    grouped: dict[str, list[RunOutcome]] = {
        case_id: [] for case_id in case_by_id
    }
    for item in outcomes:
        key = (item.case_id, item.repeat_index)
        if item.case_id not in case_by_id:
            raise ValueError(f"outcome has unknown case ID: {item.case_id}")
        if key in actual_keys:
            raise ValueError(
                f"duplicate outcome: {item.case_id}/{item.repeat_index}"
            )
        actual_keys.add(key)
        case = case_by_id[item.case_id]
        if item.repeat_index not in range(1, repeat_count + 1):
            raise ValueError(f"invalid repeat index: {item.repeat_index}")
        if item.expected_agent != case.expected_agent:
            raise ValueError(
                f"outcome expected agent mismatch: {item.case_id}"
            )
        if item.language != case.language:
            raise ValueError(f"outcome language mismatch: {item.case_id}")
        if (
            not isinstance(item.latency_ms, (int, float))
            or isinstance(item.latency_ms, bool)
            or not math.isfinite(item.latency_ms)
            or item.latency_ms < 0
        ):
            raise ValueError(
                f"invalid latency: {item.case_id}/{item.repeat_index}"
            )
        grouped[item.case_id].append(item)

    if actual_keys != expected_keys:
        raise ValueError("incomplete outcome inventory")
    return {
        case_id: tuple(sorted(items, key=lambda item: item.repeat_index))
        for case_id, items in grouped.items()
    }


def _confusion_bucket(outcome: RunOutcome) -> str:
    if outcome.predicted_agent in _CANONICAL_AGENTS:
        return outcome.predicted_agent
    if outcome.predicted_agent in {PROVIDER_ERROR, ROUTING_ERROR}:
        return outcome.predicted_agent
    if outcome.error_code is not None and outcome.error_code.startswith(
        "routing_"
    ):
        return ROUTING_ERROR
    return NO_MAJORITY


def _majority_bucket(items: Sequence[RunOutcome]) -> str:
    canonical_counts = Counter(
        item.predicted_agent
        for item in items
        if item.predicted_agent in _CANONICAL_AGENTS
    )
    if canonical_counts:
        modal_count = max(canonical_counts.values())
        if modal_count >= 2:
            return next(
                agent
                for agent in _CANONICAL_AGENTS
                if canonical_counts[agent] == modal_count
            )
    buckets = {_confusion_bucket(item) for item in items}
    if buckets == {PROVIDER_ERROR}:
        return PROVIDER_ERROR
    if buckets == {ROUTING_ERROR}:
        return ROUTING_ERROR
    return NO_MAJORITY


def _project_case(
    case: AgentRoutingCase,
    items: Sequence[RunOutcome],
    repeat_count: int,
) -> _CaseProjection:
    if repeat_count == 1:
        item = items[0]
        return _CaseProjection(
            expected_agent=case.expected_agent,
            predicted_agent=_confusion_bucket(item),
            language=case.language,
            top1_correct=item.agent_correct,
            dispatchable=item.dispatchable,
        )

    predicted_agent = _majority_bucket(items)
    modal_dispatchable_votes = sum(
        item.dispatchable and item.predicted_agent == predicted_agent
        for item in items
    )
    return _CaseProjection(
        expected_agent=case.expected_agent,
        predicted_agent=predicted_agent,
        language=case.language,
        top1_correct=predicted_agent == case.expected_agent,
        dispatchable=(
            predicted_agent == case.expected_agent
            and modal_dispatchable_votes >= 2
        ),
    )


def _classification_rows(
    projections: Sequence[_CaseProjection],
) -> dict[str, dict[str, float | int]]:
    rows: dict[str, dict[str, float | int]] = {}
    for agent in _CANONICAL_AGENTS:
        support = sum(item.expected_agent == agent for item in projections)
        predicted = sum(item.predicted_agent == agent for item in projections)
        true_positive = sum(
            item.expected_agent == agent and item.predicted_agent == agent
            for item in projections
        )
        precision = _ratio(true_positive, predicted)
        recall = _ratio(true_positive, support)
        rows[agent] = {
            "support": support,
            "predicted": predicted,
            "true_positive": true_positive,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    return rows


def _language_rows(
    projections: Sequence[_CaseProjection],
) -> dict[str, dict[str, float | int]]:
    rows: dict[str, dict[str, float | int]] = {}
    for language in ("en", "zh"):
        items = [item for item in projections if item.language == language]
        top1_correct = sum(item.top1_correct for item in items)
        dispatchable_correct = sum(item.dispatchable for item in items)
        rows[language] = {
            "case_count": len(items),
            "top1_correct": top1_correct,
            "top1_accuracy": _ratio(top1_correct, len(items)),
            "dispatchable_correct": dispatchable_correct,
            "dispatchable_accuracy": _ratio(dispatchable_correct, len(items)),
        }
    return rows


def _confusion_rows(
    projections: Sequence[_CaseProjection],
) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    for expected_agent in _CANONICAL_AGENTS:
        row = {column: 0 for column in _CONFUSION_COLUMNS}
        for item in projections:
            if item.expected_agent == expected_agent:
                row[item.predicted_agent] += 1
        rows[expected_agent] = row
    return rows


def _majority_metrics(
    projections: Sequence[_CaseProjection],
) -> dict[str, float | int | list[float]]:
    top1_correct = sum(item.top1_correct for item in projections)
    dispatchable_correct = sum(item.dispatchable for item in projections)
    case_count = len(projections)
    return {
        "top1_correct": top1_correct,
        "top1_accuracy": _ratio(top1_correct, case_count),
        "dispatchable_correct": dispatchable_correct,
        "dispatchable_accuracy": _ratio(dispatchable_correct, case_count),
        "wilson_95": _wilson_95(top1_correct, case_count),
    }


def _stability_metrics(
    grouped: Mapping[str, Sequence[RunOutcome]],
) -> dict[str, float]:
    exact_count = 0
    modal_agreement = 0.0
    for items in grouped.values():
        canonical_predictions = [
            item.predicted_agent
            for item in items
            if item.predicted_agent in _CANONICAL_AGENTS
        ]
        if (
            len(canonical_predictions) == len(items)
            and len(set(canonical_predictions)) == 1
        ):
            exact_count += 1
        modal_count = max(Counter(canonical_predictions).values(), default=0)
        modal_agreement += modal_count / len(items)
    case_count = len(grouped)
    return {
        "exact": _ratio(exact_count, case_count),
        "modal_agreement": _ratio(modal_agreement, case_count),
    }


def compute_metrics(
    cases: Sequence[AgentRoutingCase],
    outcomes: Sequence[RunOutcome],
    repeat_count: int,
) -> dict[str, Any]:
    """Compute a complete, deterministic, JSON-compatible metric report."""
    case_records = tuple(cases)
    outcome_records = tuple(outcomes)
    grouped = _validate_inventory(case_records, outcome_records, repeat_count)
    projections = tuple(
        _project_case(
            case,
            grouped[case.case_id],
            repeat_count,
        )
        for case in sorted(case_records, key=lambda item: item.case_id)
    )
    run_count = len(outcome_records)
    top1_correct = sum(item.agent_correct for item in outcome_records)
    dispatchable_correct = sum(item.dispatchable for item in outcome_records)
    core_eligible = sum(
        item.agent_correct
        and item.schema_valid
        and item.core_args_correct is not None
        for item in outcome_records
    )
    core_correct = sum(
        item.agent_correct
        and item.schema_valid
        and item.core_args_correct is True
        for item in outcome_records
    )
    errors = {
        "provider": sum(
            _confusion_bucket(item) == PROVIDER_ERROR
            for item in outcome_records
        ),
        "routing": sum(
            _confusion_bucket(item) == ROUTING_ERROR
            for item in outcome_records
        ),
        "schema": sum(
            item.error_code == "schema_validation_error"
            and _confusion_bucket(item) not in {PROVIDER_ERROR, ROUTING_ERROR}
            for item in outcome_records
        ),
    }
    basis = _BASIS_SINGLE_RUN if repeat_count == 1 else _BASIS_CASE_MAJORITY
    per_agent = {"basis": basis}
    per_agent.update(_classification_rows(projections))
    by_language = {"basis": basis}
    by_language.update(_language_rows(projections))
    confusion_matrix = {"basis": basis}
    confusion_matrix.update(_confusion_rows(projections))
    macro_rows = _classification_rows(projections)
    macro = {
        "basis": basis,
        "precision": sum(
            float(macro_rows[agent]["precision"])
            for agent in _CANONICAL_AGENTS
        )
        / len(_CANONICAL_AGENTS),
        "recall": sum(
            float(macro_rows[agent]["recall"]) for agent in _CANONICAL_AGENTS
        )
        / len(_CANONICAL_AGENTS),
        "f1": sum(
            float(macro_rows[agent]["f1"]) for agent in _CANONICAL_AGENTS
        )
        / len(_CANONICAL_AGENTS),
    }
    latency_values = sorted(float(item.latency_ms) for item in outcome_records)
    result: dict[str, Any] = {
        "schema_version": 1,
        "case_count": len(case_records),
        "planned_runs": len(case_records) * repeat_count,
        "completed_records": len(outcome_records),
        "run_level": {
            "top1_accuracy": _ratio(top1_correct, run_count),
            "dispatchable_accuracy": _ratio(dispatchable_correct, run_count),
        },
        "majority": (
            _majority_metrics(projections) if repeat_count == 3 else None
        ),
        "per_agent": per_agent,
        "macro": macro,
        "by_language": by_language,
        "confusion_matrix": confusion_matrix,
        "stability": (
            _stability_metrics(grouped) if repeat_count == 3 else None
        ),
        "core_arguments": {
            "eligible": core_eligible,
            "correct": core_correct,
            "accuracy": (
                _ratio(core_correct, core_eligible) if core_eligible else None
            ),
        },
        "errors": errors,
        "provider_completion": _ratio(
            run_count - errors["provider"],
            run_count,
        ),
        "latency_ms": {
            "p50": _linear_quantile(latency_values, 0.50),
            "p95": _linear_quantile(latency_values, 0.95),
        },
    }
    return result


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _rate(value: object) -> bool:
    return _finite_number(value) and 0.0 <= float(value) <= 1.0


_TOP_LEVEL_KEYS: Final = frozenset(
    {
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
    }
)
_RUN_LEVEL_KEYS: Final = frozenset({"top1_accuracy", "dispatchable_accuracy"})
_MAJORITY_KEYS: Final = frozenset(
    {
        "top1_correct",
        "top1_accuracy",
        "dispatchable_correct",
        "dispatchable_accuracy",
        "wilson_95",
    }
)
_AGENT_ROW_KEYS: Final = frozenset(
    {"support", "predicted", "true_positive", "precision", "recall", "f1"}
)
_LANGUAGE_ROW_KEYS: Final = frozenset(
    {
        "case_count",
        "top1_correct",
        "top1_accuracy",
        "dispatchable_correct",
        "dispatchable_accuracy",
    }
)
_MACRO_KEYS: Final = frozenset({"basis", "precision", "recall", "f1"})
_STABILITY_KEYS: Final = frozenset({"exact", "modal_agreement"})
_CORE_KEYS: Final = frozenset({"eligible", "correct", "accuracy"})
_ERROR_KEYS_SET: Final = frozenset(_ERROR_KEYS)
_LATENCY_KEYS: Final = frozenset({"p50", "p95"})


def _has_exact_keys(value: object, expected: frozenset[str]) -> bool:
    return isinstance(value, Mapping) and set(value) == expected


def _valid_count(value: object, maximum: int | None = None) -> bool:
    return (
        type(value) is int
        and value >= 0
        and (maximum is None or value <= maximum)
    )


def _consistent_rate(value: object, numerator: int, denominator: int) -> bool:
    return _rate(value) and float(value) == _ratio(numerator, denominator)


def _validate_run_level(value: object) -> bool:
    if not _has_exact_keys(value, _RUN_LEVEL_KEYS):
        return False
    return all(
        _rate(value[key]) for key in _RUN_LEVEL_KEYS  # type: ignore[index]
    )


def _validate_majority(value: object, case_count: int) -> bool:
    if not _has_exact_keys(value, _MAJORITY_KEYS):
        return False
    top1_correct = value["top1_correct"]  # type: ignore[index]
    dispatchable_correct = value["dispatchable_correct"]  # type: ignore[index]
    if not _valid_count(top1_correct, case_count):
        return False
    if not _valid_count(dispatchable_correct, case_count):
        return False
    if dispatchable_correct > top1_correct:
        return False
    if not _consistent_rate(
        value["top1_accuracy"], top1_correct, case_count  # type: ignore[index]
    ):
        return False
    if not _consistent_rate(
        value["dispatchable_accuracy"],  # type: ignore[index]
        dispatchable_correct,
        case_count,
    ):
        return False
    interval = value["wilson_95"]  # type: ignore[index]
    return (
        isinstance(interval, list)
        and len(interval) == 2
        and all(_rate(item) for item in interval)
        and interval == _wilson_95(top1_correct, case_count)
    )


def _validate_per_agent(
    value: object, case_count: int
) -> Mapping[str, Mapping[str, object]] | None:
    expected_keys = frozenset({"basis", *_CANONICAL_AGENTS})
    if not _has_exact_keys(value, expected_keys):
        return None
    if value["basis"] != _BASIS_CASE_MAJORITY:  # type: ignore[index]
        return None
    rows: dict[str, Mapping[str, object]] = {}
    support_total = 0
    for agent in _CANONICAL_AGENTS:
        row = value[agent]  # type: ignore[index]
        if not _has_exact_keys(row, _AGENT_ROW_KEYS):
            return None
        support = row["support"]
        predicted = row["predicted"]
        true_positive = row["true_positive"]
        if not all(
            _valid_count(item, case_count)
            for item in (support, predicted, true_positive)
        ):
            return None
        if true_positive > min(support, predicted):
            return None
        precision = _ratio(true_positive, predicted)
        recall = _ratio(true_positive, support)
        if not _consistent_rate(row["precision"], true_positive, predicted):
            return None
        if not _consistent_rate(row["recall"], true_positive, support):
            return None
        if not _rate(row["f1"]) or row["f1"] != _f1(precision, recall):
            return None
        support_total += support
        rows[agent] = row
    if support_total != case_count:
        return None
    return rows


def _validate_macro(
    value: object, rows: Mapping[str, Mapping[str, object]]
) -> bool:
    if not _has_exact_keys(value, _MACRO_KEYS):
        return False
    if value["basis"] != _BASIS_CASE_MAJORITY:  # type: ignore[index]
        return False
    expected = {
        metric: sum(float(rows[agent][metric]) for agent in _CANONICAL_AGENTS)
        / len(_CANONICAL_AGENTS)
        for metric in ("precision", "recall", "f1")
    }
    return all(
        _rate(value[metric]) and value[metric] == expected[metric]
        # type: ignore[index]
        for metric in expected
    )


def _validate_languages(
    value: object,
    case_count: int,
    majority: Mapping[str, object],
) -> bool:
    if not _has_exact_keys(value, frozenset({"basis", "en", "zh"})):
        return False
    if value["basis"] != _BASIS_CASE_MAJORITY:  # type: ignore[index]
        return False
    case_total = top1_total = dispatchable_total = 0
    for language in ("en", "zh"):
        row = value[language]  # type: ignore[index]
        if not _has_exact_keys(row, _LANGUAGE_ROW_KEYS):
            return False
        row_case_count = row["case_count"]
        top1_correct = row["top1_correct"]
        dispatchable_correct = row["dispatchable_correct"]
        if not _valid_count(row_case_count, case_count):
            return False
        if not _valid_count(top1_correct, row_case_count):
            return False
        if not _valid_count(dispatchable_correct, row_case_count):
            return False
        if dispatchable_correct > top1_correct:
            return False
        if not _consistent_rate(
            row["top1_accuracy"], top1_correct, row_case_count
        ):
            return False
        if not _consistent_rate(
            row["dispatchable_accuracy"],
            dispatchable_correct,
            row_case_count,
        ):
            return False
        case_total += row_case_count
        top1_total += top1_correct
        dispatchable_total += dispatchable_correct
    return (
        case_total == case_count
        and top1_total == majority["top1_correct"]
        and dispatchable_total == majority["dispatchable_correct"]
    )


def _validate_confusion(
    value: object,
    case_count: int,
    rows: Mapping[str, Mapping[str, object]],
) -> dict[str, int] | None:
    expected_keys = frozenset({"basis", *_CANONICAL_AGENTS})
    if not _has_exact_keys(value, expected_keys):
        return None
    if value["basis"] != _BASIS_CASE_MAJORITY:  # type: ignore[index]
        return None
    columns = {column: 0 for column in _CONFUSION_COLUMNS}
    diagonal = {agent: 0 for agent in _CANONICAL_AGENTS}
    for agent in _CANONICAL_AGENTS:
        row = value[agent]  # type: ignore[index]
        if not _has_exact_keys(row, frozenset(_CONFUSION_COLUMNS)):
            return None
        row_total = 0
        for column in _CONFUSION_COLUMNS:
            count = row[column]
            if not _valid_count(count, case_count):
                return None
            row_total += count
            columns[column] += count
            if column == agent:
                diagonal[agent] = count
        if row_total != rows[agent]["support"]:
            return None
    if sum(columns.values()) != case_count:
        return None
    if not all(
        columns[agent] == rows[agent]["predicted"]
        for agent in _CANONICAL_AGENTS
    ):
        return None
    return diagonal


def _validate_stability(value: object, case_count: int) -> bool:
    if not _has_exact_keys(value, _STABILITY_KEYS):
        return False
    if not all(
        _rate(value[key]) for key in _STABILITY_KEYS  # type: ignore[index]
    ):
        return False
    exact = float(value["exact"])  # type: ignore[index]
    modal = float(value["modal_agreement"])  # type: ignore[index]
    if modal < 1.0 / 3.0 or exact > modal:
        return False
    exact_count = exact * case_count
    modal_votes = modal * (3 * case_count)
    tolerance = 1e-9
    if not math.isclose(
        exact_count, round(exact_count), rel_tol=0.0, abs_tol=tolerance
    ):
        return False
    if not math.isclose(
        modal_votes, round(modal_votes), rel_tol=0.0, abs_tol=tolerance
    ):
        return False
    stable_cases = round(exact_count)
    unstable_cases = case_count - stable_cases
    lower = (stable_cases + unstable_cases / 3.0) / case_count
    upper = (stable_cases + unstable_cases * 2.0 / 3.0) / case_count
    return lower - tolerance <= modal <= upper + tolerance


def _validate_core(value: object, planned_runs: int) -> bool:
    if not _has_exact_keys(value, _CORE_KEYS):
        return False
    eligible = value["eligible"]  # type: ignore[index]
    correct = value["correct"]  # type: ignore[index]
    accuracy = value["accuracy"]  # type: ignore[index]
    if not _valid_count(eligible, planned_runs):
        return False
    if not _valid_count(correct, eligible):
        return False
    return (
        accuracy is None
        if eligible == 0
        else _consistent_rate(accuracy, correct, eligible)
    )


def _validate_errors(value: object, planned_runs: int) -> bool:
    if not _has_exact_keys(value, _ERROR_KEYS_SET):
        return False
    if not all(
        _valid_count(value[key], planned_runs)  # type: ignore[index]
        for key in _ERROR_KEYS
    ):
        return False
    error_total = sum(value[key] for key in _ERROR_KEYS)  # type: ignore[index]
    return error_total <= planned_runs


def _validate_latency(value: object) -> bool:
    if not _has_exact_keys(value, _LATENCY_KEYS):
        return False
    if not all(
        _finite_number(value[key])  # type: ignore[index]
        for key in _LATENCY_KEYS
    ):
        return False
    return 0.0 <= value["p50"] <= value["p95"]  # type: ignore[index]


def thresholds_pass(metrics: Mapping[str, object]) -> bool:
    """Return whether a complete, internally consistent report passes gates."""
    try:
        if not _has_exact_keys(metrics, _TOP_LEVEL_KEYS):
            return False
        if (
            type(metrics["schema_version"]) is not int
            or metrics["schema_version"] != 1
            or type(metrics["case_count"]) is not int
        ):
            return False
        case_count = metrics["case_count"]
        planned_runs = metrics["planned_runs"]
        completed_records = metrics["completed_records"]
        if case_count <= 0 or not _valid_count(planned_runs):
            return False
        if not _valid_count(completed_records):
            return False
        if planned_runs != case_count * 3:
            return False
        if completed_records != planned_runs:
            return False
        if not _validate_run_level(metrics["run_level"]):
            return False
        majority = metrics["majority"]
        if not _validate_majority(majority, case_count):
            return False
        rows = _validate_per_agent(metrics["per_agent"], case_count)
        if rows is None:
            return False
        if not _validate_macro(metrics["macro"], rows):
            return False
        if not _validate_languages(
            metrics["by_language"], case_count, majority
        ):
            return False
        confusion_diagonal = _validate_confusion(
            metrics["confusion_matrix"], case_count, rows
        )
        if confusion_diagonal is None:
            return False
        true_positive_total = sum(
            rows[agent]["true_positive"] for agent in _CANONICAL_AGENTS
        )
        if majority["top1_correct"] != true_positive_total:
            return False
        if any(
            rows[agent]["true_positive"] != confusion_diagonal[agent]
            for agent in _CANONICAL_AGENTS
        ):
            return False
        if not _validate_stability(metrics["stability"], case_count):
            return False
        if not _validate_core(metrics["core_arguments"], planned_runs):
            return False
        errors = metrics["errors"]
        if not _validate_errors(errors, planned_runs):
            return False
        provider_completion = metrics["provider_completion"]
        if not _consistent_rate(
            provider_completion,
            planned_runs - errors["provider"],
            planned_runs,
        ):
            return False
        if not _validate_latency(metrics["latency_ms"]):
            return False
        majority_top1 = majority["top1_accuracy"]
        majority_dispatchable = majority["dispatchable_accuracy"]
        exact_stability = metrics["stability"]["exact"]
        return (
            float(majority_top1) >= 0.90
            and all(
                float(rows[agent]["recall"]) >= 0.80
                for agent in _CANONICAL_AGENTS
            )
            and float(majority_dispatchable) >= 0.85
            and float(exact_stability) >= 0.90
            and float(provider_completion) >= 0.99
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


__all__ = [
    "NO_MAJORITY",
    "compute_metrics",
    "thresholds_pass",
]
