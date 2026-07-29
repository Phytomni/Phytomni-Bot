# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Markdown rendering helpers for safe agent-routing reports."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

_RUN_MARKDOWN_KEYS = (
    "case_id",
    "repeat",
    "expected_agent",
    "predicted_agent",
    "language",
    "schema_valid",
    "core_args_correct",
    "latency_ms",
)


def format_markdown_value(value: object) -> str:
    """Format one value and escape Markdown table/control characters."""
    if value is None:
        text = "-"
    elif isinstance(value, float):
        text = f"{value:.6g}"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _append_pairs(
    lines: list[str],
    title: str,
    values: Mapping[str, object],
    keys: Sequence[str],
) -> None:
    """Append a small Markdown key/value section."""
    lines.extend(("", f"## {title}"))
    lines.extend(
        f"- {key}: {format_markdown_value(values.get(key))}" for key in keys
    )


def _append_table(
    lines: list[str],
    title: str,
    headers: Sequence[str],
    separators: Sequence[str],
    rows: Sequence[Sequence[object]],
) -> None:
    """Append a Markdown table with deterministic cell escaping."""
    lines.extend(
        (
            "",
            f"## {title}",
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(separators) + " |",
        )
    )
    lines.extend(
        "| " + " | ".join(format_markdown_value(value) for value in row) + " |"
        for row in rows
    )


def _append_metrics(lines: list[str], metrics: Mapping[str, object]) -> None:
    """Append scalar and nested metric summaries."""
    lines.extend(("", "## Metrics"))
    for key in (
        "case_count",
        "planned_runs",
        "completed_records",
        "provider_completion",
    ):
        if key in metrics:
            lines.append(f"- {key}: {format_markdown_value(metrics[key])}")
    for name, keys in (
        ("run_level", ("top1_accuracy", "dispatchable_accuracy")),
        ("majority", ("top1_accuracy", "dispatchable_accuracy")),
        ("latency_ms", ("p50", "p95")),
    ):
        row = metrics.get(name)
        if not isinstance(row, Mapping):
            continue
        for key in keys:
            lines.append(
                f"- {name}.{key}: {format_markdown_value(row.get(key))}"
            )


def _append_per_agent(
    lines: list[str],
    metrics: Mapping[str, object],
    canonical_agents: Sequence[str],
) -> None:
    """Append precision, recall, and F1 rows for each canonical agent."""
    values = metrics.get("per_agent")
    if not isinstance(values, Mapping):
        return
    rows = [
        [
            agent,
            *[
                row.get(key)
                for key in (
                    "support",
                    "predicted",
                    "true_positive",
                    "precision",
                    "recall",
                    "f1",
                )
            ],
        ]
        for agent in canonical_agents
        if isinstance(row := values.get(agent), Mapping)
    ]
    _append_table(
        lines,
        "Per-agent",
        (
            "Agent",
            "Support",
            "Predicted",
            "True positive",
            "Precision",
            "Recall",
            "F1",
        ),
        ("---", "---:", "---:", "---:", "---:", "---:", "---:"),
        rows,
    )


def _append_language(lines: list[str], metrics: Mapping[str, object]) -> None:
    """Append English and Chinese accuracy rows."""
    values = metrics.get("by_language")
    if not isinstance(values, Mapping):
        return
    rows = [
        [
            language,
            *[
                row.get(key)
                for key in (
                    "case_count",
                    "top1_correct",
                    "top1_accuracy",
                    "dispatchable_accuracy",
                )
            ],
        ]
        for language in ("en", "zh")
        if isinstance(row := values.get(language), Mapping)
    ]
    _append_table(
        lines,
        "Language",
        (
            "Language",
            "Cases",
            "Top-1 correct",
            "Top-1 accuracy",
            "Dispatchable accuracy",
        ),
        ("---", "---:", "---:", "---:", "---:"),
        rows,
    )


def _append_errors(lines: list[str], metrics: Mapping[str, object]) -> None:
    """Append bounded provider, routing, and schema error counts."""
    values = metrics.get("errors")
    if not isinstance(values, Mapping):
        return
    _append_pairs(lines, "Errors", values, ("provider", "routing", "schema"))


def _append_confusion(
    lines: list[str],
    metrics: Mapping[str, object],
    canonical_agents: Sequence[str],
) -> None:
    """Append confusion rows without including question text."""
    values = metrics.get("confusion_matrix")
    if not isinstance(values, Mapping):
        return
    lines.extend(("", "## Confusion"))
    for agent in canonical_agents:
        row = values.get(agent)
        if isinstance(row, Mapping):
            lines.append(f"- {agent}: {format_markdown_value(dict(row))}")


def _append_runs(lines: list[str], runs: Sequence[object]) -> None:
    """Append safe run identifiers and outcome fields."""
    rows = [
        [run.get(key) for key in _RUN_MARKDOWN_KEYS]
        for run in runs
        if isinstance(run, Mapping)
    ]
    _append_table(
        lines,
        "Runs",
        (
            "Case ID",
            "Repeat",
            "Expected",
            "Predicted",
            "Language",
            "Schema valid",
            "Core args correct",
            "Latency ms",
        ),
        ("---", "---:", "---", "---", "---", "---", "---", "---:"),
        rows,
    )


def render_markdown(
    report: Mapping[str, Any],
    canonical_agents: Sequence[str],
    provenance_keys: Sequence[str],
) -> str:
    """Render one validated report as deterministic, readable Markdown."""
    status = report.get("status")
    provenance = report.get("provenance")
    metrics = report.get("metrics")
    runs = report.get("runs")
    status_values = status if isinstance(status, Mapping) else {}
    provenance_values = provenance if isinstance(provenance, Mapping) else {}
    metric_values = metrics if isinstance(metrics, Mapping) else {}
    run_values = runs if isinstance(runs, (list, tuple)) else ()
    lines = ["# Agent Routing Evaluation Report"]
    _append_pairs(
        lines,
        "Headline",
        status_values,
        ("state", "headline", "current_accuracy", "thresholds_passed"),
    )
    _append_pairs(
        lines,
        "Provenance",
        provenance_values,
        provenance_keys,
    )
    _append_metrics(lines, metric_values)
    _append_per_agent(lines, metric_values, canonical_agents)
    _append_language(lines, metric_values)
    _append_errors(lines, metric_values)
    _append_confusion(lines, metric_values, canonical_agents)
    _append_runs(lines, run_values)
    return "\n".join(lines) + "\n"
