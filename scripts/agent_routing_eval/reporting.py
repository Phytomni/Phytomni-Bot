# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Safe, reproducible reports for selector-only routing evaluations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal
from uuid import uuid4

from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

from .dataset import AgentRoutingCase
from .metrics import thresholds_pass
from .runner import RunOutcome

RunCommand = Callable[..., object]

_CANONICAL_AGENTS: Final = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)
_METRIC_KEYS: Final = (
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
_SAFE_ERROR_CODES: Final = frozenset(
    {
        "provider_timeout_exhausted",
        "provider_failure_exhausted",
        "routing_contract_error",
        "routing_missing_selection",
        "schema_validation_error",
        "core_argument_mismatch",
    }
)
_SENSITIVE_KEY_RE: Final = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|"
    r"credential|access[_ -]?token|refresh[_ -]?token)"
)
_SENSITIVE_TEXT_RE: Final = re.compile(
    r"(?i)(https?://[^\s\"'<>]+|api[-_ ]?key|authorization|bearer\s+"
    r"|password|secret|credential|access[_ -]?token|refresh[_ -]?token"
    r"|\bexception\b|\btraceback\b)"
)
_SHA256_RE: Final = re.compile(r"[0-9a-fA-F]{64}")
_SAFE_CODE_RE: Final = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


@dataclass(frozen=True, slots=True)
class GitState:
    """Minimal Git state persisted in a report."""

    branch: str
    head: str
    dirty: bool


@dataclass(frozen=True, slots=True)
class ReportContext:
    """Inputs that bind one report to its execution environment."""

    mode: Literal["quick", "benchmark"]
    dataset_path: Path
    repeat_count: int
    concurrency: int
    model_id: str
    provider_endpoint_hash: str
    git: GitState
    started_at: datetime
    elapsed_seconds: float
    allow_dirty: bool


def _completed_stdout(result: object, command: Sequence[str]) -> str:
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"git provenance command failed: {command[0]}")
    stdout = getattr(result, "stdout", "")
    return stdout if isinstance(stdout, str) else ""


def collect_git_state(*, run_command: RunCommand = subprocess.run) -> GitState:
    """Collect branch, commit, and a boolean dirty flag without paths."""
    branch_command = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    head_command = ["git", "rev-parse", "HEAD"]
    status_command = [
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
    ]
    branch = _completed_stdout(
        run_command(
            branch_command,
            check=False,
            capture_output=True,
            text=True,
        ),
        branch_command,
    ).strip()
    head = _completed_stdout(
        run_command(
            head_command,
            check=False,
            capture_output=True,
            text=True,
        ),
        head_command,
    ).strip()
    status = _completed_stdout(
        run_command(
            status_command,
            check=False,
            capture_output=True,
            text=True,
        ),
        status_command,
    )
    if not branch or not head:
        raise RuntimeError("git provenance returned empty state")
    return GitState(branch=branch, head=head, dirty=bool(status.strip()))


def dataset_sha256(path: Path) -> str:
    """Hash the exact bytes of a dataset file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _value_text(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def description_sha256(
    tool_definitions: Sequence[tuple[object, object, object]] | None = None,
) -> str:
    """Hash the ordered public agent names and descriptions."""
    definitions = (
        AGENT_TOOL_DEFINITIONS
        if tool_definitions is None
        else tool_definitions
    )
    payload = "".join(
        f"{_value_text(name)}\0{_value_text(description)}\n"
        for name, description, _model in definitions
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def provider_endpoint_sha256(base_url: str) -> str:
    """Hash a provider endpoint without retaining the endpoint text."""
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()


def _safe_text(value: object) -> str:
    text = str(value).strip()
    return "[redacted]" if _SENSITIVE_TEXT_RE.search(text) else text


def _safe_digest(value: object) -> str:
    text = str(value).strip()
    if _SHA256_RE.fullmatch(text):
        return text.lower()
    return provider_endpoint_sha256(text)


def _safe_mapping(value: Mapping[object, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if _SENSITIVE_KEY_RE.search(key_text):
            continue
        result[_safe_text(key_text)] = _safe_json(item)
    return result


def _safe_json(value: object) -> object:
    result: object = None
    if value is None or isinstance(value, (bool, int)):
        result = value
    elif isinstance(value, float):
        result = value if math.isfinite(value) else None
    elif isinstance(value, str):
        result = _safe_text(value)
    elif isinstance(value, Mapping):
        result = _safe_mapping(value)
    elif isinstance(value, (list, tuple)):
        result = [_safe_json(item) for item in value]
    return result


def _safe_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip()
    if (
        code in _SAFE_ERROR_CODES or _SAFE_CODE_RE.fullmatch(code) is not None
    ) and _SENSITIVE_TEXT_RE.search(code) is None:
        return code
    return None


def _safe_float(value: object, *, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0):
        return None
    return number


def _safe_int(value: object, *, nonnegative: bool = True) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if nonnegative and value < 0:
        return None
    return value


def _safe_metrics(
    metrics: Mapping[str, Any], *, complete: bool
) -> dict[str, object]:
    if not complete:
        planned = _safe_int(metrics.get("planned_runs"))
        completed = _safe_int(metrics.get("completed_records"))
        if planned is None or completed is None or completed > planned:
            raise ValueError("invalid incomplete metric counts")
        return {
            "planned_runs": planned,
            "completed_records": completed,
            "status": "incomplete",
        }
    return {
        key: _safe_json(metrics[key]) for key in _METRIC_KEYS if key in metrics
    }


def _safe_runs(outcomes: Sequence[RunOutcome]) -> list[dict[str, object]]:
    ordered = sorted(
        outcomes,
        key=lambda item: (str(item.case_id), item.repeat_index),
    )
    records: list[dict[str, object]] = []
    for outcome in ordered:
        validation_codes = [
            code
            for raw_code in outcome.validation_codes
            if (code := _safe_code(raw_code)) is not None
        ]
        records.append(
            {
                "case_id": _safe_text(outcome.case_id),
                "expected_agent": _safe_text(outcome.expected_agent),
                "predicted_agent": _safe_text(outcome.predicted_agent),
                "repeat": _safe_int(outcome.repeat_index),
                "language": (
                    outcome.language
                    if outcome.language in {"en", "zh"}
                    else "[unknown]"
                ),
                "agent_correct": (
                    outcome.agent_correct
                    if isinstance(outcome.agent_correct, bool)
                    else None
                ),
                "schema_valid": (
                    outcome.schema_valid
                    if isinstance(outcome.schema_valid, bool)
                    else None
                ),
                "dispatchable": (
                    outcome.dispatchable
                    if isinstance(outcome.dispatchable, bool)
                    else None
                ),
                "core_args_correct": (
                    outcome.core_args_correct
                    if outcome.core_args_correct is None
                    or isinstance(outcome.core_args_correct, bool)
                    else None
                ),
                "provider_completed": (
                    outcome.provider_completed
                    if isinstance(outcome.provider_completed, bool)
                    else None
                ),
                "attempts": _safe_int(outcome.attempts),
                "latency_ms": _safe_float(
                    outcome.latency_ms, nonnegative=True
                ),
                "selected_arguments": _safe_json(outcome.selected_arguments),
                "error_code": _safe_code(outcome.error_code),
                "validation_codes": validation_codes,
            }
        )
    return records


def _metric_mapping(
    metrics: Mapping[str, object], key: str
) -> Mapping[str, object] | None:
    value = metrics.get(key)
    return value if isinstance(value, Mapping) else None


def _primary_accuracy(
    context: ReportContext,
    metrics: Mapping[str, object],
    provider_completion: float | None,
    complete: bool,
) -> float | str:
    if (
        not complete
        or provider_completion is None
        or provider_completion < 0.99
    ):
        return "Unknown"
    source = (
        _metric_mapping(metrics, "majority")
        if context.repeat_count == 3
        else _metric_mapping(metrics, "run_level")
    )
    value = _safe_float(source.get("top1_accuracy")) if source else None
    return "Unknown" if value is None else value


def _status(
    context: ReportContext,
    metrics: Mapping[str, object],
    *,
    complete: bool,
) -> dict[str, object]:
    provider_completion = _safe_float(metrics.get("provider_completion"))
    primary_accuracy = _primary_accuracy(
        context, metrics, provider_completion, complete
    )
    stable = (
        complete
        and context.mode == "benchmark"
        and context.repeat_count == 3
        and context.dataset_path.name == "test_v1.jsonl"
        and not context.git.dirty
        and provider_completion is not None
        and provider_completion >= 0.99
    )
    if not complete:
        headline = "Incomplete diagnostic"
    elif stable:
        headline = "Stable baseline"
    elif context.git.dirty:
        headline = "Diagnostic (dirty tree)"
    elif provider_completion is None or provider_completion < 0.99:
        headline = "Unknown"
    else:
        headline = "Diagnostic"
    threshold_result = False
    if complete:
        try:
            threshold_result = thresholds_pass(metrics)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            threshold_result = False
    return {
        "state": "complete" if complete else "incomplete",
        "headline": headline,
        "current_accuracy": primary_accuracy,
        "thresholds_passed": threshold_result,
    }


def _utc_timestamp(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_report(
    context: ReportContext,
    cases: Sequence[AgentRoutingCase],
    outcomes: Sequence[RunOutcome],
    metrics: Mapping[str, Any],
    *,
    complete: bool,
) -> dict[str, Any]:
    """Build a JSON-compatible report without retaining sensitive inputs."""
    del cases
    safe_metrics = _safe_metrics(metrics, complete=complete)
    provenance = {
        "branch": _safe_text(context.git.branch),
        "head": _safe_text(context.git.head),
        "dirty": bool(context.git.dirty),
        "model_id": _safe_text(context.model_id),
        "provider_endpoint_hash": _safe_digest(context.provider_endpoint_hash),
        "dataset_path": _safe_text(str(context.dataset_path)),
        "dataset_sha256": dataset_sha256(context.dataset_path),
        "description_sha256": description_sha256(),
        "mode": context.mode,
        "repeat_count": _safe_int(context.repeat_count),
        "concurrency": _safe_int(context.concurrency),
        "started_at": _utc_timestamp(context.started_at),
        "elapsed_seconds": _safe_float(
            context.elapsed_seconds, nonnegative=True
        ),
        "allow_dirty": bool(context.allow_dirty),
    }
    return {
        "schema_version": 1,
        "status": _status(context, safe_metrics, complete=complete),
        "provenance": provenance,
        "metrics": safe_metrics,
        "runs": _safe_runs(outcomes),
    }


def _format_markdown_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _append_headline(lines: list[str], report: Mapping[str, Any]) -> None:
    """Append safe report status fields."""
    status = report.get("status")
    values = status if isinstance(status, Mapping) else {}
    lines.extend(
        [
            "",
            "## Headline",
            f"- State: {_format_markdown_value(values.get('state'))}",
            "- Headline: " f"{_format_markdown_value(values.get('headline'))}",
            "- Current accuracy: "
            f"{_format_markdown_value(values.get('current_accuracy'))}",
            "- Thresholds passed: "
            f"{_format_markdown_value(values.get('thresholds_passed'))}",
        ]
    )


def _append_provenance(lines: list[str], report: Mapping[str, Any]) -> None:
    """Append the non-secret provenance fields."""
    provenance = report.get("provenance")
    values = provenance if isinstance(provenance, Mapping) else {}
    lines.extend(["", "## Provenance"])
    for key in (
        "branch",
        "head",
        "dirty",
        "model_id",
        "provider_endpoint_hash",
        "dataset_path",
        "dataset_sha256",
        "description_sha256",
        "mode",
        "repeat_count",
        "concurrency",
        "started_at",
        "elapsed_seconds",
        "allow_dirty",
    ):
        lines.append(f"- {key}: {_format_markdown_value(values.get(key))}")


def _append_metric_summary(
    lines: list[str], metrics: Mapping[str, object]
) -> None:
    """Append primary metric values shared with the JSON report."""
    lines.extend(["", "## Metrics"])
    for key in (
        "case_count",
        "planned_runs",
        "completed_records",
        "provider_completion",
    ):
        if key in metrics:
            lines.append(f"- {key}: {_format_markdown_value(metrics[key])}")
    for metric_name, metric_keys in (
        ("run_level", ("top1_accuracy", "dispatchable_accuracy")),
        ("majority", ("top1_accuracy", "dispatchable_accuracy")),
        ("latency_ms", ("p50", "p95")),
    ):
        values = _metric_mapping(metrics, metric_name)
        if values is None:
            continue
        for key in metric_keys:
            lines.append(
                f"- {metric_name}.{key}: "
                f"{_format_markdown_value(values.get(key))}"
            )


def _append_per_agent(lines: list[str], metrics: Mapping[str, object]) -> None:
    """Append per-agent precision and recall rows."""
    per_agent = _metric_mapping(metrics, "per_agent")
    if per_agent is None:
        return
    lines.extend(
        [
            "",
            "## Per-agent",
            "| Agent | Support | Predicted | True positive | "
            "Precision | Recall | F1 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for agent in _CANONICAL_AGENTS:
        row = per_agent.get(agent)
        if not isinstance(row, Mapping):
            continue
        values = [
            agent,
            *(
                row.get(key)
                for key in (
                    "support",
                    "predicted",
                    "true_positive",
                    "precision",
                    "recall",
                    "f1",
                )
            ),
        ]
        lines.append(
            "| "
            + " | ".join(_format_markdown_value(value) for value in values)
            + " |"
        )


def _append_language(lines: list[str], metrics: Mapping[str, object]) -> None:
    """Append English and Chinese accuracy slices."""
    by_language = _metric_mapping(metrics, "by_language")
    if by_language is None:
        return
    lines.extend(
        [
            "",
            "## Language",
            "| Language | Cases | Top-1 correct | Top-1 accuracy | "
            "Dispatchable accuracy |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for language in ("en", "zh"):
        row = by_language.get(language)
        if not isinstance(row, Mapping):
            continue
        values = [
            language,
            *(
                row.get(key)
                for key in (
                    "case_count",
                    "top1_correct",
                    "top1_accuracy",
                    "dispatchable_accuracy",
                )
            ),
        ]
        lines.append(
            "| "
            + " | ".join(_format_markdown_value(value) for value in values)
            + " |"
        )


def _append_errors(lines: list[str], metrics: Mapping[str, object]) -> None:
    """Append bounded provider, routing, and schema error counts."""
    errors = _metric_mapping(metrics, "errors")
    if errors is None:
        return
    lines.extend(
        [
            "",
            "## Errors",
            "- provider: " f"{_format_markdown_value(errors.get('provider'))}",
            "- routing: " f"{_format_markdown_value(errors.get('routing'))}",
            "- schema: " f"{_format_markdown_value(errors.get('schema'))}",
        ]
    )


def _append_confusion(lines: list[str], metrics: Mapping[str, object]) -> None:
    """Append confusion rows without including question text."""
    confusion = _metric_mapping(metrics, "confusion_matrix")
    if confusion is None:
        return
    lines.extend(["", "## Confusion"])
    for agent in _CANONICAL_AGENTS:
        row = confusion.get(agent)
        if isinstance(row, Mapping):
            lines.append(f"- {agent}: {_format_markdown_value(dict(row))}")


def _append_runs(lines: list[str], report: Mapping[str, Any]) -> None:
    """Append sorted run identifiers and safe outcome fields."""
    runs = report.get("runs")
    if not isinstance(runs, list):
        return
    lines.extend(
        [
            "",
            "## Runs",
            "| Case ID | Repeat | Expected | Predicted | Language | "
            "Schema valid | Core args correct | Latency ms |",
            "| --- | ---: | --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        lines.append(
            "| "
            + " | ".join(
                _format_markdown_value(run.get(key))
                for key in (
                    "case_id",
                    "repeat",
                    "expected_agent",
                    "predicted_agent",
                    "language",
                    "schema_valid",
                    "core_args_correct",
                    "latency_ms",
                )
            )
            + " |"
        )


def _render_markdown(report: Mapping[str, Any]) -> str:
    """Render a deterministic Markdown view of a safe report."""
    metrics = report.get("metrics")
    metric_mapping = metrics if isinstance(metrics, Mapping) else {}
    lines = ["# Agent Routing Evaluation Report"]
    _append_headline(lines, report)
    _append_provenance(lines, report)
    _append_metric_summary(lines, metric_mapping)
    _append_per_agent(lines, metric_mapping)
    _append_language(lines, metric_mapping)
    _append_errors(lines, metric_mapping)
    _append_confusion(lines, metric_mapping)
    _append_runs(lines, report)
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def write_report_pair(
    report: Mapping[str, Any], output_dir: Path, stem: str
) -> tuple[Path, Path]:
    """Atomically write the JSON and Markdown artifacts for one report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_content = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    _atomic_write(json_path, json_content)
    _atomic_write(markdown_path, _render_markdown(report))
    return json_path, markdown_path


__all__ = [
    "GitState",
    "ReportContext",
    "RunCommand",
    "build_report",
    "collect_git_state",
    "dataset_sha256",
    "description_sha256",
    "provider_endpoint_sha256",
    "write_report_pair",
]
