# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Focused tests for deterministic agent-routing evaluation metrics."""

from __future__ import annotations

import json
from typing import Any

import pytest
from scripts.agent_routing_eval.dataset import AgentRoutingCase
from scripts.agent_routing_eval.metrics import (
    NO_MAJORITY,
    _validated_threshold_report,
    _validate_stability,
    compute_metrics,
    thresholds_pass,
)
from scripts.agent_routing_eval.runner import (
    PROVIDER_ERROR,
    ROUTING_ERROR,
    RunOutcome,
)

from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

pytestmark = pytest.mark.unit


CANONICAL_AGENTS = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)


def case(
    case_id: str,
    expected_agent: str,
    *,
    language: str = "en",
    expected_core_args: dict[str, Any] | None = None,
) -> AgentRoutingCase:
    return AgentRoutingCase.model_validate(
        {
            "case_id": case_id,
            "question": f"Question for {case_id}",
            "expected_agent": expected_agent,
            "expected_core_args": expected_core_args or {},
            "language": language,
            "source": {
                "kind": "authored_chat",
                "category": "general_knowledge",
                "rationale": "Metrics fixture has no external provenance.",
            },
            "transformation": {"kind": "authored_chat"},
        }
    )


def outcome(
    case_id: str,
    repeat_index: int,
    expected_agent: str,
    predicted_agent: str,
    *,
    language: str = "en",
    schema_valid: bool = True,
    dispatchable: bool | None = None,
    core_args_correct: bool | None = None,
    provider_completed: bool = True,
    latency_ms: float = 10.0,
    error_code: str | None = None,
) -> RunOutcome:
    if dispatchable is None:
        dispatchable = predicted_agent == expected_agent and schema_valid
    return RunOutcome(
        case_id=case_id,
        repeat_index=repeat_index,
        expected_agent=expected_agent,
        predicted_agent=predicted_agent,
        language=language,
        agent_correct=predicted_agent == expected_agent,
        schema_valid=schema_valid,
        dispatchable=dispatchable,
        core_args_correct=core_args_correct,
        provider_completed=provider_completed,
        attempts=1,
        latency_ms=latency_ms,
        selected_arguments={},
        error_code=error_code,
        validation_codes=(),
    )


def test_two_of_three_is_a_correct_majority() -> None:
    metrics = compute_metrics(
        cases=(case("test-chat-001", "ChatAgent"),),
        outcomes=(
            outcome("test-chat-001", 1, "ChatAgent", "ChatAgent"),
            outcome("test-chat-001", 2, "ChatAgent", "ChatAgent"),
            outcome("test-chat-001", 3, "ChatAgent", "KnowledgeAgent"),
        ),
        repeat_count=3,
    )

    assert metrics["majority"]["top1_correct"] == 1
    assert metrics["majority"]["top1_accuracy"] == 1.0
    assert metrics["stability"]["exact"] == 0.0
    assert metrics["stability"]["modal_agreement"] == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    ("predictions", "expected_bucket", "top1_correct"),
    [
        (("ChatAgent",) * 3, "ChatAgent", 1),
        (("KnowledgeAgent",) * 3, "KnowledgeAgent", 0),
        (
            ("ChatAgent", "KnowledgeAgent", "DataAgent"),
            NO_MAJORITY,
            0,
        ),
    ],
)
def test_majority_and_confusion_bucket_are_deterministic(
    predictions: tuple[str, str, str],
    expected_bucket: str,
    top1_correct: int,
) -> None:
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=tuple(
            outcome("case-001", index, "ChatAgent", prediction)
            for index, prediction in enumerate(predictions, start=1)
        ),
        repeat_count=3,
    )

    assert metrics["majority"]["top1_correct"] == top1_correct
    assert metrics["confusion_matrix"]["ChatAgent"][expected_bucket] == 1


def test_errors_without_a_canonical_majority_are_not_silently_correct() -> (
    None
):
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=(
            outcome("case-001", 1, "ChatAgent", "ChatAgent"),
            outcome(
                "case-001",
                2,
                "ChatAgent",
                PROVIDER_ERROR,
                provider_completed=False,
            ),
            outcome(
                "case-001",
                3,
                "ChatAgent",
                PROVIDER_ERROR,
                provider_completed=False,
            ),
        ),
        repeat_count=3,
    )

    assert metrics["majority"]["top1_correct"] == 0
    assert metrics["confusion_matrix"]["ChatAgent"][NO_MAJORITY] == 1
    assert metrics["errors"]["provider"] == 2
    assert metrics["run_level"]["top1_accuracy"] == pytest.approx(1 / 3)


def test_one_schema_valid_repeat_not_dispatchable_majority() -> None:
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=(
            outcome("case-001", 1, "ChatAgent", "ChatAgent"),
            outcome(
                "case-001",
                2,
                "ChatAgent",
                "ChatAgent",
                schema_valid=False,
                dispatchable=False,
                error_code="schema_validation_error",
            ),
            outcome(
                "case-001",
                3,
                "ChatAgent",
                "ChatAgent",
                schema_valid=False,
                dispatchable=False,
                error_code="schema_validation_error",
            ),
        ),
        repeat_count=3,
    )

    assert metrics["majority"]["top1_correct"] == 1
    assert metrics["majority"]["dispatchable_correct"] == 0
    assert metrics["errors"]["schema"] == 2


def test_unknown_routing_contract_prediction_counts_as_routing_error() -> None:
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=(
            outcome(
                "case-001",
                1,
                "ChatAgent",
                "UnknownAgent",
                schema_valid=False,
                dispatchable=False,
                error_code="routing_contract_error",
            ),
        ),
        repeat_count=1,
    )

    assert metrics["errors"]["routing"] == 1
    assert metrics["confusion_matrix"]["ChatAgent"][ROUTING_ERROR] == 1
    assert metrics["confusion_matrix"]["ChatAgent"][NO_MAJORITY] == 0


def test_run_level_denominators_retain_provider_and_routing_failures() -> None:
    cases = (
        case("case-001", "ChatAgent"),
        case("case-002", "KnowledgeAgent"),
        case("case-003", "DataAgent"),
    )
    metrics = compute_metrics(
        cases=cases,
        outcomes=(
            outcome("case-001", 1, "ChatAgent", "ChatAgent"),
            outcome(
                "case-002",
                1,
                "KnowledgeAgent",
                PROVIDER_ERROR,
                provider_completed=False,
            ),
            outcome(
                "case-003",
                1,
                "DataAgent",
                ROUTING_ERROR,
            ),
        ),
        repeat_count=1,
    )

    assert metrics["run_level"] == {
        "top1_accuracy": pytest.approx(1 / 3),
        "dispatchable_accuracy": pytest.approx(1 / 3),
    }
    assert metrics["errors"]["provider"] == 1
    assert metrics["errors"]["routing"] == 1
    assert metrics["provider_completion"] == pytest.approx(2 / 3)


def test_incomplete_repeat_inventory_is_rejected() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        compute_metrics(
            cases=(case("case-001", "ChatAgent"),),
            outcomes=(outcome("case-001", 1, "ChatAgent", "ChatAgent"),),
            repeat_count=3,
        )


def test_duplicate_repeat_inventory_is_rejected() -> None:
    repeated = outcome("case-001", 1, "ChatAgent", "ChatAgent")
    with pytest.raises(ValueError, match="duplicate outcome"):
        compute_metrics(
            cases=(case("case-001", "ChatAgent"),),
            outcomes=(repeated, repeated),
            repeat_count=1,
        )


def test_per_agent_language_and_macro_metrics_use_single_run_basis() -> None:
    cases = (
        case("case-001", "ChatAgent", language="en"),
        case("case-002", "KnowledgeAgent", language="zh"),
    )
    metrics = compute_metrics(
        cases=cases,
        outcomes=(
            outcome("case-001", 1, "ChatAgent", "ChatAgent", language="en"),
            outcome(
                "case-002", 1, "KnowledgeAgent", "ChatAgent", language="zh"
            ),
        ),
        repeat_count=1,
    )

    assert metrics["per_agent"]["basis"] == "single_run"
    assert metrics["per_agent"]["ChatAgent"] == {
        "support": 1,
        "predicted": 2,
        "true_positive": 1,
        "precision": pytest.approx(0.5),
        "recall": 1.0,
        "f1": pytest.approx(2 / 3),
    }
    assert metrics["per_agent"]["KnowledgeAgent"]["recall"] == 0.0
    assert metrics["per_agent"]["DigitalDesignAgent"] == {
        "support": 0,
        "predicted": 0,
        "true_positive": 0,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }
    assert metrics["by_language"]["basis"] == "single_run"
    assert metrics["by_language"]["en"]["top1_correct"] == 1
    assert metrics["by_language"]["zh"]["top1_correct"] == 0
    assert metrics["macro"]["basis"] == "single_run"


def test_confusion_matrix_has_all_canonical_and_failure_columns() -> None:
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=(outcome("case-001", 1, "ChatAgent", PROVIDER_ERROR),),
        repeat_count=1,
    )

    expected_columns = (
        *CANONICAL_AGENTS,
        PROVIDER_ERROR,
        ROUTING_ERROR,
        NO_MAJORITY,
    )
    assert metrics["confusion_matrix"]["basis"] == "single_run"
    assert tuple(metrics["confusion_matrix"]["ChatAgent"]) == (
        *expected_columns,
    )


def test_majority_language_slices_use_case_majority() -> None:
    cases = (
        case("case-en", "ChatAgent", language="en"),
        case("case-zh", "ChatAgent", language="zh"),
    )
    outcomes = (
        outcome("case-en", 1, "ChatAgent", "ChatAgent", language="en"),
        outcome("case-en", 2, "ChatAgent", "ChatAgent", language="en"),
        outcome("case-en", 3, "ChatAgent", "KnowledgeAgent", language="en"),
        outcome("case-zh", 1, "ChatAgent", "KnowledgeAgent", language="zh"),
        outcome("case-zh", 2, "ChatAgent", "KnowledgeAgent", language="zh"),
        outcome("case-zh", 3, "ChatAgent", "ChatAgent", language="zh"),
    )

    metrics = compute_metrics(cases, outcomes, repeat_count=3)

    assert metrics["by_language"]["basis"] == "case_majority"
    assert metrics["by_language"]["en"]["top1_correct"] == 1
    assert metrics["by_language"]["zh"]["top1_correct"] == 0
    assert metrics["by_language"]["en"]["case_count"] == 1
    assert metrics["by_language"]["zh"]["case_count"] == 1


def test_core_accuracy_reports_only_correct_schema_valid_denominator() -> None:
    cases = (
        case("case-001", "ChatAgent", expected_core_args={"x": "y"}),
        case("case-002", "ChatAgent", expected_core_args={"x": "y"}),
        case("case-003", "ChatAgent", expected_core_args={}),
    )
    metrics = compute_metrics(
        cases=cases,
        outcomes=(
            outcome(
                "case-001",
                1,
                "ChatAgent",
                "ChatAgent",
                core_args_correct=True,
            ),
            outcome(
                "case-002",
                1,
                "ChatAgent",
                "ChatAgent",
                core_args_correct=False,
            ),
            outcome(
                "case-003",
                1,
                "ChatAgent",
                "KnowledgeAgent",
            ),
        ),
        repeat_count=1,
    )

    assert metrics["core_arguments"] == {
        "eligible": 2,
        "correct": 1,
        "accuracy": 0.5,
    }


def test_latency_percentiles_use_linear_interpolation() -> None:
    cases = tuple(
        case(f"case-{index:03d}", "ChatAgent") for index in range(1, 5)
    )
    outcomes = tuple(
        outcome(
            f"case-{index:03d}",
            1,
            "ChatAgent",
            "ChatAgent",
            latency_ms=float(index * 10),
        )
        for index in range(1, 5)
    )

    metrics = compute_metrics(cases=cases, outcomes=outcomes, repeat_count=1)

    assert metrics["latency_ms"] == {"p50": 25.0, "p95": 38.5}


def test_wilson_interval_handles_zero_and_full_success() -> None:
    cases = tuple(
        case(f"case-{index:03d}", "ChatAgent") for index in range(1, 4)
    )
    wrong = tuple(
        outcome(f"case-{index:03d}", 1, "ChatAgent", "KnowledgeAgent")
        for index in range(1, 4)
    )
    full = tuple(
        outcome(f"case-{index:03d}", 1, "ChatAgent", "ChatAgent")
        for index in range(1, 4)
    )

    zero_metrics = compute_metrics(cases=cases, outcomes=wrong, repeat_count=1)
    full_metrics = compute_metrics(cases=cases, outcomes=full, repeat_count=1)

    assert zero_metrics["majority"] is None
    assert full_metrics["run_level"]["top1_accuracy"] == 1.0

    repeated_wrong = tuple(
        outcome(f"case-{index:03d}", repeat, "ChatAgent", "KnowledgeAgent")
        for index in range(1, 4)
        for repeat in range(1, 4)
    )
    repeated_full = tuple(
        outcome(f"case-{index:03d}", repeat, "ChatAgent", "ChatAgent")
        for index in range(1, 4)
        for repeat in range(1, 4)
    )
    wrong_report = compute_metrics(
        cases=cases, outcomes=repeated_wrong, repeat_count=3
    )
    full_report = compute_metrics(
        cases=cases, outcomes=repeated_full, repeat_count=3
    )

    assert wrong_report["majority"]["wilson_95"][0] == 0.0
    assert wrong_report["majority"]["wilson_95"][1] < 1.0
    assert full_report["majority"]["wilson_95"][1] == 1.0
    assert full_report["majority"]["wilson_95"][0] > 0.0


def test_wilson_interval_values_are_exact_and_deterministic() -> None:
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=(
            outcome("case-001", 1, "ChatAgent", "ChatAgent"),
            outcome("case-001", 2, "ChatAgent", "ChatAgent"),
            outcome("case-001", 3, "ChatAgent", "KnowledgeAgent"),
        ),
        repeat_count=3,
    )

    assert metrics["majority"]["wilson_95"] == [
        0.20654931437723742,
        1.0,
    ]


def test_quick_mode_has_single_run_aggregates_and_no_majority() -> None:
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=(outcome("case-001", 1, "ChatAgent", "ChatAgent"),),
        repeat_count=1,
    )

    assert metrics["majority"] is None
    assert metrics["stability"] is None
    assert metrics["per_agent"]["basis"] == "single_run"
    assert metrics["confusion_matrix"]["basis"] == "single_run"


def test_metrics_are_json_compatible_and_have_fixed_schema_version() -> None:
    metrics = compute_metrics(
        cases=(case("case-001", "ChatAgent"),),
        outcomes=(outcome("case-001", 1, "ChatAgent", "ChatAgent"),),
        repeat_count=1,
    )

    assert metrics["schema_version"] == 1
    json.dumps(metrics, ensure_ascii=False, allow_nan=False)


def _complete_threshold_report() -> dict[str, Any]:
    cases = tuple(
        case(f"case-{index:03d}", agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
    )
    outcomes = tuple(
        outcome(f"case-{index:03d}", repeat, agent, agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
        for repeat in range(1, 4)
    )
    return compute_metrics(cases=cases, outcomes=outcomes, repeat_count=3)


def test_thresholds_require_complete_three_repeat_report() -> None:
    cases = tuple(
        case(f"case-{index:03d}", agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
    )
    outcomes = tuple(
        outcome(
            f"case-{index:03d}",
            repeat,
            agent,
            agent,
        )
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
        for repeat in range(1, 4)
    )
    metrics = compute_metrics(cases=cases, outcomes=outcomes, repeat_count=3)

    assert thresholds_pass(metrics) is True

    quick = compute_metrics(
        cases=(cases[0],),
        outcomes=(outcomes[0],),
        repeat_count=1,
    )
    assert thresholds_pass(quick) is False

    incomplete = dict(metrics)
    incomplete["completed_records"] = metrics["planned_runs"] - 1
    assert thresholds_pass(incomplete) is False

    missing_recall = dict(metrics)
    missing_recall["per_agent"] = {
        **metrics["per_agent"],
        "ChatAgent": {
            key: value
            for key, value in metrics["per_agent"]["ChatAgent"].items()
            if key != "recall"
        },
    }
    assert thresholds_pass(missing_recall) is False


def test_thresholds_reject_nonnumeric_values() -> None:
    cases = tuple(
        case(f"case-{index:03d}", agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
    )
    outcomes = tuple(
        outcome(f"case-{index:03d}", repeat, agent, agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
        for repeat in range(1, 4)
    )
    metrics = compute_metrics(cases=cases, outcomes=outcomes, repeat_count=3)
    invalid = dict(metrics)
    invalid["provider_completion"] = "1.0"

    assert thresholds_pass(invalid) is False


def test_thresholds_rejects_minimal_forged_report() -> None:
    assert (
        thresholds_pass(
            {
                "schema_version": 1,
                "case_count": 1,
                "planned_runs": 3,
                "completed_records": 3,
                "majority": {"top1_accuracy": 1.0},
                "provider_completion": 1.0,
            }
        )
        is False
    )


def test_thresholds_rejects_structural_and_count_inconsistency() -> None:
    cases = tuple(
        case(f"case-{index:03d}", agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
    )
    outcomes = tuple(
        outcome(f"case-{index:03d}", repeat, agent, agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
        for repeat in range(1, 4)
    )
    metrics = compute_metrics(cases=cases, outcomes=outcomes, repeat_count=3)

    extra = {**metrics, "unexpected": True}
    assert thresholds_pass(extra) is False

    mismatched = {
        **metrics,
        "majority": {
            **metrics["majority"],
            "top1_correct": metrics["majority"]["top1_correct"] - 1,
        },
    }
    assert thresholds_pass(mismatched) is False


def test_thresholds_rejects_cross_block_projection_mismatch() -> None:
    report = _complete_threshold_report()
    majority = {
        **report["majority"],
        "top1_correct": 9,
        "top1_accuracy": 0.9,
        "dispatchable_correct": 9,
        "dispatchable_accuracy": 0.9,
        "wilson_95": [0.5958499732047616, 0.9821237869049271],
    }
    language = {
        **report["by_language"],
        "en": {
            **report["by_language"]["en"],
            "top1_correct": 9,
            "top1_accuracy": 0.9,
            "dispatchable_correct": 9,
            "dispatchable_accuracy": 0.9,
        },
    }
    forged = {**report, "majority": majority, "by_language": language}

    assert thresholds_pass(forged) is False


def test_thresholds_rejects_majority_language_total_mismatch() -> None:
    report = _complete_threshold_report()
    majority = {
        **report["majority"],
        "top1_correct": 9,
        "top1_accuracy": 0.9,
        "dispatchable_correct": 9,
        "dispatchable_accuracy": 0.9,
        "wilson_95": [0.5958499732047616, 0.9821237869049271],
    }

    assert thresholds_pass({**report, "majority": majority}) is False


def test_thresholds_rejects_language_dispatchable_above_top1() -> None:
    report = _complete_threshold_report()
    language = {
        **report["by_language"],
        "en": {
            **report["by_language"]["en"],
            "top1_correct": 0,
            "top1_accuracy": 0.0,
            "dispatchable_correct": 1,
            "dispatchable_accuracy": 0.1,
        },
    }

    assert thresholds_pass({**report, "by_language": language}) is False


def test_thresholds_rejects_confusion_diagonal_mismatch() -> None:
    report = _complete_threshold_report()
    chat_row = {
        **report["confusion_matrix"]["ChatAgent"],
        "ChatAgent": 0,
        PROVIDER_ERROR: 1,
    }
    confusion = {
        **report["confusion_matrix"],
        "ChatAgent": chat_row,
    }

    assert thresholds_pass({**report, "confusion_matrix": confusion}) is False


def test_thresholds_rejects_forged_provider_errors_and_completion() -> None:
    report = _complete_threshold_report()
    errors = {**report["errors"], "provider": 1}

    assert thresholds_pass({**report, "errors": errors}) is False

    overlapping = {
        **report["errors"],
        "provider": 10,
        "routing": 10,
        "schema": 11,
    }
    forged = {
        **report,
        "errors": overlapping,
        "provider_completion": 2 / 3,
    }
    assert thresholds_pass(forged) is False


def test_thresholds_rejects_errors_above_run_level_incorrect_count() -> None:
    report = _complete_threshold_report()
    forged_errors = {
        **report["errors"],
        "routing": 1,
        "schema": 1,
    }
    forged = {
        **report,
        "errors": forged_errors,
        "provider_completion": 1.0,
    }
    assert thresholds_pass(forged) is False

    provider_forged = {
        **report,
        "errors": {**report["errors"], "provider": 1},
        "provider_completion": 29 / 30,
    }
    assert thresholds_pass(provider_forged) is False


def test_thresholds_rejects_dispatchable_rate_above_top1_rate() -> None:
    report = _complete_threshold_report()
    forged = {
        **report,
        "run_level": {
            "top1_accuracy": 0.0,
            "dispatchable_accuracy": 1 / 30,
        },
    }
    assert thresholds_pass(forged) is False


def test_thresholds_accepts_one_provider_error_with_matching_run_counts() -> (
    None
):
    cases = tuple(
        case(f"case-{index:03d}", agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
    )
    outcomes = tuple(
        outcome(
            f"case-{index:03d}",
            repeat,
            agent,
            PROVIDER_ERROR if index == 1 and repeat == 1 else agent,
            provider_completed=not (index == 1 and repeat == 1),
        )
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
        for repeat in range(1, 4)
    )
    report = compute_metrics(cases, outcomes, repeat_count=3)

    assert report["run_level"] == {
        "top1_accuracy": 29 / 30,
        "dispatchable_accuracy": 29 / 30,
    }
    assert report["errors"] == {"provider": 1, "routing": 0, "schema": 0}
    assert report["provider_completion"] == 29 / 30
    assert _validated_threshold_report(report) is not None
    assert thresholds_pass(report) is False


def test_thresholds_rejects_unrepresentable_stability() -> None:
    report = _complete_threshold_report()
    forged = {
        **report,
        "stability": {"exact": 0.91, "modal_agreement": 0.91},
    }

    assert thresholds_pass(forged) is False


def test_thresholds_rejects_impossible_stability_bounds() -> None:
    report = _complete_threshold_report()
    forged = {
        **report,
        "stability": {"exact": 0.2, "modal_agreement": 0.4},
    }

    assert thresholds_pass(forged) is False


def test_provider_error_zero_modal_fails_completion() -> None:
    cases = tuple(
        case(f"case-{index:03d}", agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
    )
    provider_outcomes = tuple(
        outcome(
            "case-001",
            repeat,
            "ChatAgent",
            PROVIDER_ERROR,
            provider_completed=False,
        )
        for repeat in range(1, 4)
    )
    stable_outcomes = tuple(
        outcome(f"case-{index:03d}", repeat, agent, agent)
        for index, agent in enumerate(CANONICAL_AGENTS, start=1)
        if index != 1
        for repeat in range(1, 4)
    )
    report = compute_metrics(
        cases,
        provider_outcomes + stable_outcomes,
        repeat_count=3,
    )

    assert report["stability"] == {"exact": 0.9, "modal_agreement": 0.9}
    assert _validate_stability(report["stability"], case_count=10) is not None
    assert report["provider_completion"] == 0.9
    assert thresholds_pass(report) is False
