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
from typing import Any, Final, TypedDict, cast

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
    expected_agent: str
    predicted_agent: str
    language: str
    top1_correct: bool
    dispatchable: bool


_AgentValues = TypedDict(
    "_AgentValues",
    {
        "support": int,
        "predicted": int,
        "true_positive": int,
        "precision": float,
        "recall": float,
        "f1": float,
    },
)
_MajorityValues = TypedDict(
    "_MajorityValues",
    {
        "top1_correct": int,
        "top1_accuracy": float,
        "dispatchable_correct": int,
        "dispatchable_accuracy": float,
        "wilson_95": list[float],
    },
)
_ErrorValues = TypedDict(
    "_ErrorValues", {"provider": int, "routing": int, "schema": int}
)
_StabilityValues = TypedDict(
    "_StabilityValues", {"exact": float, "modal_agreement": float}
)

_MetricMap = Mapping[str, object]
_AgentRows = Mapping[str, _AgentValues]
_ValidatedProjection = tuple[_MajorityValues, _AgentRows, _StabilityValues]
_ValidatedThresholdReport = tuple[_ValidatedProjection, float]
_IntPair = tuple[int, int]
_IntTriple = tuple[int, int, int]
_CountMap = dict[str, int]


def _ratio(numerator: int | float, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _f1(precision: float, recall: float) -> float:
    total = precision + recall
    return 0.0 if total == 0.0 else 2.0 * precision * recall / total


def _wilson_95(successes: int, observations: int) -> list[float]:
    if observations == 0:
        return [0.0, 1.0]
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
    lower = 0.0 if successes == 0 else lower
    upper = 1.0 if successes == observations else upper
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
    return (
        ROUTING_ERROR
        if outcome.error_code is not None
        and outcome.error_code.startswith("routing_")
        else NO_MAJORITY
    )


def _majority_bucket(items: Sequence[RunOutcome]) -> str:
    canonical_counts = Counter(
        item.predicted_agent
        for item in items
        if item.predicted_agent in _CANONICAL_AGENTS
    )
    if canonical_counts and max(canonical_counts.values()) >= 2:
        modal_count = max(canonical_counts.values())
        return next(
            agent
            for agent in _CANONICAL_AGENTS
            if canonical_counts[agent] == modal_count
        )
    buckets = {_confusion_bucket(item) for item in items}
    return (
        PROVIDER_ERROR
        if buckets == {PROVIDER_ERROR}
        else ROUTING_ERROR if buckets == {ROUTING_ERROR} else NO_MAJORITY
    )


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
) -> dict[str, _AgentValues]:
    rows: dict[str, _AgentValues] = {}
    for agent in _CANONICAL_AGENTS:
        support, predicted, true_positive = (
            sum(item.expected_agent == agent for item in projections),
            sum(item.predicted_agent == agent for item in projections),
            sum(
                item.expected_agent == agent and item.predicted_agent == agent
                for item in projections
            ),
        )
        precision = _ratio(true_positive, predicted)
        recall = _ratio(true_positive, support)
        rows[agent] = _AgentValues(
            support=support,
            predicted=predicted,
            true_positive=true_positive,
            precision=precision,
            recall=recall,
            f1=_f1(precision, recall),
        )
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
        counts = Counter(
            item.predicted_agent
            for item in projections
            if item.expected_agent == expected_agent
        )
        rows[expected_agent] = {
            column: counts[column] for column in _CONFUSION_COLUMNS
        }
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


def _run_metrics(outcomes: Sequence[RunOutcome]) -> dict[str, Any]:
    run_count = len(outcomes)
    top1_correct = sum(item.agent_correct for item in outcomes)
    dispatchable_correct = sum(item.dispatchable for item in outcomes)
    core_eligible = sum(
        item.agent_correct
        and item.schema_valid
        and item.core_args_correct is not None
        for item in outcomes
    )
    core_correct = sum(
        item.agent_correct
        and item.schema_valid
        and item.core_args_correct is True
        for item in outcomes
    )
    buckets = Counter(_confusion_bucket(item) for item in outcomes)
    errors = {
        "provider": buckets[PROVIDER_ERROR],
        "routing": buckets[ROUTING_ERROR],
        "schema": sum(
            item.error_code == "schema_validation_error"
            and _confusion_bucket(item) not in {PROVIDER_ERROR, ROUTING_ERROR}
            for item in outcomes
        ),
    }
    latency_values = sorted(float(item.latency_ms) for item in outcomes)
    return {
        "run_level": {
            "top1_accuracy": _ratio(top1_correct, run_count),
            "dispatchable_accuracy": _ratio(dispatchable_correct, run_count),
        },
        "core_arguments": {
            "eligible": core_eligible,
            "correct": core_correct,
            "accuracy": (
                _ratio(core_correct, core_eligible) if core_eligible else None
            ),
        },
        "errors": errors,
        "provider_completion": _ratio(
            run_count - errors["provider"], run_count
        ),
        "latency_ms": {
            "p50": _linear_quantile(latency_values, 0.50),
            "p95": _linear_quantile(latency_values, 0.95),
        },
    }


def compute_metrics(
    cases: Sequence[AgentRoutingCase],
    outcomes: Sequence[RunOutcome],
    repeat_count: int,
) -> dict[str, Any]:
    """Compute a complete, deterministic, JSON-compatible metric report."""
    cases = tuple(cases)
    outcomes = tuple(outcomes)
    grouped = _validate_inventory(cases, outcomes, repeat_count)
    projections = tuple(
        _project_case(
            case,
            grouped[case.case_id],
            repeat_count,
        )
        for case in sorted(cases, key=lambda item: item.case_id)
    )
    run_metrics = _run_metrics(outcomes)
    basis = _BASIS_SINGLE_RUN if repeat_count == 1 else _BASIS_CASE_MAJORITY
    classification_rows = _classification_rows(projections)
    per_agent: dict[str, object] = {"basis": basis, **classification_rows}
    by_language: dict[str, object] = {
        "basis": basis,
        **_language_rows(projections),
    }
    confusion_matrix: dict[str, object] = {
        "basis": basis,
        **_confusion_rows(projections),
    }
    macro = {
        "basis": basis,
        **{
            metric: sum(
                float(classification_rows[agent][metric])
                for agent in _CANONICAL_AGENTS
            )
            / len(_CANONICAL_AGENTS)
            for metric in ("precision", "recall", "f1")
        },
    }
    result: dict[str, Any] = {
        "schema_version": 1,
        "case_count": len(cases),
        "planned_runs": len(cases) * repeat_count,
        "completed_records": len(outcomes),
        "run_level": run_metrics["run_level"],
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
        "core_arguments": run_metrics["core_arguments"],
        "errors": run_metrics["errors"],
        "provider_completion": run_metrics["provider_completion"],
        "latency_ms": run_metrics["latency_ms"],
    }
    return result


def _float_value(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        return None
    return cast(Mapping[str, object], value)


_TOP_LEVEL_KEYS: Final = frozenset(
    "schema_version case_count planned_runs completed_records run_level "
    "majority per_agent macro by_language confusion_matrix stability "
    "core_arguments errors provider_completion latency_ms".split()
)
_RUN_LEVEL_KEYS: Final = frozenset(("top1_accuracy", "dispatchable_accuracy"))
_MAJORITY_KEYS: Final = frozenset(
    "top1_correct top1_accuracy dispatchable_correct "
    "dispatchable_accuracy wilson_95".split()
)
_AGENT_ROW_KEYS: Final = frozenset(
    "support predicted true_positive precision recall f1".split()
)
_LANGUAGE_ROW_KEYS: Final = frozenset(
    "case_count top1_correct top1_accuracy dispatchable_correct "
    "dispatchable_accuracy".split()
)
_MACRO_KEYS: Final = frozenset({"basis", "precision", "recall", "f1"})
_STABILITY_KEYS: Final = frozenset({"exact", "modal_agreement"})
_CORE_KEYS: Final = frozenset({"eligible", "correct", "accuracy"})
_ERROR_KEYS_SET: Final = frozenset(_ERROR_KEYS)
_LATENCY_KEYS: Final = frozenset({"p50", "p95"})


def _mapping_with_keys(
    value: object, expected: frozenset[str]
) -> Mapping[str, object] | None:
    mapping = _mapping(value)
    return (
        mapping if mapping is not None and set(mapping) == expected else None
    )


def _count_value(value: object, maximum: int | None = None) -> int | None:
    number = (
        value
        if isinstance(value, int) and not isinstance(value, bool)
        else None
    )
    if (
        number is None
        or number < 0
        or (maximum is not None and number > maximum)
    ):
        return None
    return number


def _rate_count(value: object, denominator: int) -> int | None:
    rate = _float_value(value)
    if rate is None or not 0.0 <= rate <= 1.0:
        return None
    count = rate * denominator
    rounded = round(count)
    return (
        rounded
        if math.isclose(count, rounded, rel_tol=0.0, abs_tol=1e-9)
        else None
    )


def _bounded_values(
    value: object, keys: Sequence[str], maximum: int
) -> tuple[int, ...] | None:
    mapping = _mapping(value)
    if mapping is None:
        return None
    values = tuple(_count_value(mapping[key], maximum) for key in keys)
    if any(item is None for item in values):
        return None
    return tuple(cast(int, item) for item in values)


def _consistent_rate(value: object, numerator: int, denominator: int) -> bool:
    number = _float_value(value)
    return number is not None and number == _ratio(numerator, denominator)


def _validate_run_level_counts(
    value: object, planned_runs: int
) -> _IntPair | None:
    mapping = _mapping_with_keys(value, _RUN_LEVEL_KEYS)
    if mapping is None:
        return None
    top1_correct = _rate_count(mapping["top1_accuracy"], planned_runs)
    dispatchable_correct = _rate_count(
        mapping["dispatchable_accuracy"], planned_runs
    )
    return (
        None
        if top1_correct is None
        or dispatchable_correct is None
        or dispatchable_correct > top1_correct
        else (top1_correct, dispatchable_correct)
    )


def _majority_counts(
    mapping: Mapping[str, object], case_count: int
) -> tuple[int, int, float, float] | None:
    counts = tuple(
        _count_value(mapping[key], case_count)
        for key in ("top1_correct", "dispatchable_correct")
    )
    top1_accuracy = _float_value(mapping["top1_accuracy"])
    dispatchable_accuracy = _float_value(mapping["dispatchable_accuracy"])
    if (
        any(value is None for value in counts)
        or top1_accuracy is None
        or dispatchable_accuracy is None
    ):
        return None
    top1_correct, dispatchable_correct = cast(tuple[int, int], counts)
    if dispatchable_correct > top1_correct:
        return None
    if not _consistent_rate(
        top1_accuracy, top1_correct, case_count
    ) or not _consistent_rate(
        dispatchable_accuracy, dispatchable_correct, case_count
    ):
        return None
    return (
        top1_correct,
        dispatchable_correct,
        top1_accuracy,
        dispatchable_accuracy,
    )


def _majority_interval(
    value: object, top1_correct: int, case_count: int
) -> list[float] | None:
    if not isinstance(value, list):
        return None
    interval_values = [
        number
        for item in value
        if (number := _float_value(item)) is not None and 0.0 <= number <= 1.0
    ]
    return (
        interval_values
        if len(interval_values) == 2
        and interval_values == _wilson_95(top1_correct, case_count)
        else None
    )


def _validate_majority(
    value: object, case_count: int
) -> _MajorityValues | None:
    mapping = _mapping_with_keys(value, _MAJORITY_KEYS)
    if mapping is None:
        return None
    counts = _majority_counts(mapping, case_count)
    if counts is None:
        return None
    (
        top1_correct,
        dispatchable_correct,
        top1_accuracy,
        dispatchable_accuracy,
    ) = counts
    interval = _majority_interval(
        mapping["wilson_95"], top1_correct, case_count
    )
    if interval is None:
        return None
    return {
        "top1_correct": top1_correct,
        "top1_accuracy": top1_accuracy,
        "dispatchable_correct": dispatchable_correct,
        "dispatchable_accuracy": dispatchable_accuracy,
        "wilson_95": interval,
    }


def _validate_agent_row(value: object, case_count: int) -> _AgentValues | None:
    keys = ("support", "predicted", "true_positive")
    counts = _bounded_values(value, keys, case_count)
    row = _mapping_with_keys(value, _AGENT_ROW_KEYS)
    if counts is None or row is None:
        return None
    support_value, predicted_value, true_positive_value = counts
    if true_positive_value > min(support_value, predicted_value):
        return None
    precision = _ratio(true_positive_value, predicted_value)
    recall = _ratio(true_positive_value, support_value)
    f1 = _float_value(row["f1"])
    if (
        not _consistent_rate(
            row["precision"], true_positive_value, predicted_value
        )
        or not _consistent_rate(
            row["recall"], true_positive_value, support_value
        )
        or f1 is None
        or f1 != _f1(precision, recall)
    ):
        return None
    return {
        "support": support_value,
        "predicted": predicted_value,
        "true_positive": true_positive_value,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _validate_per_agent(value: object, case_count: int) -> _AgentRows | None:
    expected_keys = frozenset({"basis", *_CANONICAL_AGENTS})
    mapping = _mapping_with_keys(value, expected_keys)
    if mapping is None or mapping["basis"] != _BASIS_CASE_MAJORITY:
        return None
    rows: dict[str, _AgentValues] = {}
    support_total = 0
    for agent in _CANONICAL_AGENTS:
        row = _validate_agent_row(mapping[agent], case_count)
        if row is None:
            return None
        support_total += row["support"]
        rows[agent] = row
    return rows if support_total == case_count else None


def _validate_macro(value: object, rows: _AgentRows) -> bool:
    mapping = _mapping_with_keys(value, _MACRO_KEYS)
    if mapping is None:
        return False
    expected = {
        metric: sum(float(rows[agent][metric]) for agent in _CANONICAL_AGENTS)
        / len(_CANONICAL_AGENTS)
        for metric in ("precision", "recall", "f1")
    }
    return mapping["basis"] == _BASIS_CASE_MAJORITY and all(
        _float_value(mapping[metric]) is not None
        and mapping[metric] == expected[metric]
        for metric in expected
    )


def _validate_languages(
    value: object,
    case_count: int,
    majority: _MajorityValues,
) -> bool:
    mapping = _mapping_with_keys(value, frozenset({"basis", "en", "zh"}))
    case_total = top1_total = dispatchable_total = 0
    if mapping is None or mapping["basis"] != _BASIS_CASE_MAJORITY:
        return False
    for language in ("en", "zh"):
        row = _validate_language_row(mapping[language], case_count)
        if row is None:
            return False
        row_case_count, top1_correct, dispatchable_correct = row
        case_total += row_case_count
        top1_total += top1_correct
        dispatchable_total += dispatchable_correct
    return (
        case_total == case_count
        and top1_total == majority["top1_correct"]
        and dispatchable_total == majority["dispatchable_correct"]
    )


def _validate_language_row(
    value: object, case_count: int
) -> _IntTriple | None:
    keys = ("case_count", "top1_correct", "dispatchable_correct")
    counts = _bounded_values(value, keys, case_count)
    row = _mapping_with_keys(value, _LANGUAGE_ROW_KEYS)
    if counts is None or row is None:
        return None
    row_case_count, top1_correct, dispatchable_correct = counts
    if top1_correct > row_case_count or dispatchable_correct > row_case_count:
        return None
    if dispatchable_correct > top1_correct:
        return None
    if not _consistent_rate(
        row["top1_accuracy"], top1_correct, row_case_count
    ) or not _consistent_rate(
        row["dispatchable_accuracy"], dispatchable_correct, row_case_count
    ):
        return None
    return (
        row_case_count,
        top1_correct,
        dispatchable_correct,
    )


def _validate_confusion_row(
    value: object, case_count: int
) -> _CountMap | None:
    values = _bounded_values(value, _CONFUSION_COLUMNS, case_count)
    return (
        dict(zip(_CONFUSION_COLUMNS, values)) if values is not None else None
    )


def _validate_confusion(
    value: object,
    case_count: int,
    rows: _AgentRows,
) -> dict[str, int] | None:
    expected_keys = frozenset({"basis", *_CANONICAL_AGENTS})
    mapping = _mapping_with_keys(value, expected_keys)
    columns = {column: 0 for column in _CONFUSION_COLUMNS}
    diagonal = {agent: 0 for agent in _CANONICAL_AGENTS}
    if mapping is None or mapping["basis"] != _BASIS_CASE_MAJORITY:
        return None
    for agent in _CANONICAL_AGENTS:
        counts = _validate_confusion_row(mapping[agent], case_count)
        if counts is None or sum(counts.values()) != rows[agent]["support"]:
            return None
        for column in _CONFUSION_COLUMNS:
            columns[column] += counts[column]
        diagonal[agent] = counts[agent]
    if sum(columns.values()) != case_count:
        return None
    if not all(
        columns[agent] == rows[agent]["predicted"]
        for agent in _CANONICAL_AGENTS
    ):
        return None
    return diagonal


def _validate_stability(
    value: object, case_count: int
) -> _StabilityValues | None:
    mapping = _mapping_with_keys(value, _STABILITY_KEYS)
    if mapping is None:
        return None
    exact = _float_value(mapping["exact"])
    modal = _float_value(mapping["modal_agreement"])
    if not _stability_is_representable(exact, modal, case_count):
        return None
    return {"exact": cast(float, exact), "modal_agreement": cast(float, modal)}


def _stability_is_representable(
    exact: float | None, modal: float | None, case_count: int
) -> bool:
    if (
        exact is None
        or modal is None
        or not 0.0 <= exact <= 1.0
        or not 0.0 <= modal <= 1.0
        or exact > modal
    ):
        return False
    tolerance = 1e-9
    exact_count = exact * case_count
    modal_votes = modal * (3 * case_count)
    if not all(
        math.isclose(value, round(value), rel_tol=0.0, abs_tol=tolerance)
        for value in (exact_count, modal_votes)
    ):
        return False
    stable_cases = round(exact_count)
    unstable_cases = case_count - stable_cases
    upper = (stable_cases + unstable_cases * 2.0 / 3.0) / case_count
    return stable_cases / case_count - tolerance <= modal <= upper + tolerance


def _validate_core(value: object, planned_runs: int) -> bool:
    mapping = _mapping_with_keys(value, _CORE_KEYS)
    if mapping is None:
        return False
    eligible = _count_value(mapping["eligible"], planned_runs)
    correct = _count_value(mapping["correct"], planned_runs)
    if eligible is None or correct is None or correct > eligible:
        return False
    return (
        mapping["accuracy"] is None
        if eligible == 0
        else _consistent_rate(mapping["accuracy"], correct, eligible)
    )


def _validate_errors(
    value: object, planned_runs: int, incorrect_runs: int
) -> _ErrorValues | None:
    mapping = _mapping_with_keys(value, _ERROR_KEYS_SET)
    counts = _bounded_values(mapping, _ERROR_KEYS, planned_runs)
    if counts is None:
        return None
    if sum(counts) > planned_runs or sum(counts) > incorrect_runs:
        return None
    return cast(_ErrorValues, dict(zip(_ERROR_KEYS, counts)))


def _validate_threshold_header(
    metrics: _MetricMap,
) -> _IntPair | None:
    if _count_value(metrics["schema_version"]) != 1:
        return None
    values = tuple(
        _count_value(metrics[key])
        for key in ("case_count", "planned_runs", "completed_records")
    )
    if any(value is None for value in values):
        return None
    case_count, planned_runs, completed_records = cast(
        tuple[int, int, int], values
    )
    valid = (
        case_count > 0
        and planned_runs == case_count * 3
        and completed_records == planned_runs
    )
    return (case_count, planned_runs) if valid else None


def _validate_threshold_projection(
    metrics: _MetricMap, case_count: int
) -> _ValidatedProjection | None:
    majority = _validate_majority(metrics["majority"], case_count)
    if majority is None:
        return None
    rows = _validate_per_agent(metrics["per_agent"], case_count)
    if rows is None or not _validate_macro(metrics["macro"], rows):
        return None
    if not _validate_languages(metrics["by_language"], case_count, majority):
        return None
    confusion_diagonal = _validate_confusion(
        metrics["confusion_matrix"], case_count, rows
    )
    stability = _validate_stability(metrics["stability"], case_count)
    if confusion_diagonal is None or stability is None:
        return None
    if majority["top1_correct"] != sum(
        rows[agent]["true_positive"] for agent in _CANONICAL_AGENTS
    ) or not all(
        rows[agent]["true_positive"] == confusion_diagonal[agent]
        for agent in _CANONICAL_AGENTS
    ):
        return None
    return majority, rows, stability


def _validate_threshold_operations(
    metrics: _MetricMap, planned_runs: int
) -> float | None:
    run_level_counts = _validate_run_level_counts(
        metrics["run_level"], planned_runs
    )
    if run_level_counts is None or not _validate_core(
        metrics["core_arguments"], planned_runs
    ):
        return None
    top1_correct, _dispatchable_correct = run_level_counts
    errors = _validate_errors(
        metrics["errors"], planned_runs, planned_runs - top1_correct
    )
    if errors is None:
        return None
    provider_completion = _float_value(metrics["provider_completion"])
    if provider_completion is None or not _consistent_rate(
        provider_completion, planned_runs - errors["provider"], planned_runs
    ):
        return None
    latency = _mapping_with_keys(metrics["latency_ms"], _LATENCY_KEYS)
    if latency is None:
        return None
    p50 = _float_value(latency["p50"])
    p95 = _float_value(latency["p95"])
    if p50 is None or p95 is None or not 0.0 <= p50 <= p95:
        return None
    return provider_completion


def _validated_threshold_report(
    metrics: _MetricMap,
) -> _ValidatedThresholdReport | None:
    if _mapping_with_keys(metrics, _TOP_LEVEL_KEYS) is None:
        return None
    header = _validate_threshold_header(metrics)
    if header is None:
        return None
    case_count, planned_runs = header
    projection = _validate_threshold_projection(metrics, case_count)
    provider_completion = _validate_threshold_operations(metrics, planned_runs)
    if projection is None or provider_completion is None:
        return None
    return projection, provider_completion


def _meets_thresholds(report: _ValidatedThresholdReport) -> bool:
    projection, provider_completion = report
    majority, rows, stability = projection
    return (
        majority["top1_accuracy"] >= 0.90
        and all(rows[agent]["recall"] >= 0.80 for agent in _CANONICAL_AGENTS)
        and majority["dispatchable_accuracy"] >= 0.85
        and stability["exact"] >= 0.90
        and provider_completion >= 0.99
    )


def thresholds_pass(metrics: _MetricMap) -> bool:
    """Return whether a complete, internally consistent report passes gates."""
    try:
        report = _validated_threshold_report(metrics)
        return report is not None and _meets_thresholds(report)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


__all__ = [
    "NO_MAJORITY",
    "compute_metrics",
    "thresholds_pass",
]
