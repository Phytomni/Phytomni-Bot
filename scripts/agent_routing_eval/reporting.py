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
from typing import Any, Final, Literal, NamedTuple, cast
from uuid import uuid4

from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS

from .dataset import AgentRoutingCase
from .metrics import compute_metrics, thresholds_pass
from .reporting_markdown import format_markdown_value, render_markdown
from .runner import RunOutcome

RunCommand = Callable[..., object]

_CANONICAL_AGENTS: Final = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)
_METRIC_KEYS: Final = tuple(
    [
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
    ]
)
_SAFE_ERROR_CODES: Final = frozenset(
    [
        "provider_timeout_exhausted",
        "provider_failure_exhausted",
        "routing_contract_error",
        "routing_missing_selection",
        "schema_validation_error",
        "core_argument_mismatch",
    ]
)
_SAFE_VALIDATION_CODES: Final = frozenset(
    [
        "assertion_error",
        "bool_type",
        "dict_type",
        "extra_forbidden",
        "float_parsing",
        "float_type",
        "int_parsing",
        "int_type",
        "json_invalid",
        "literal_error",
        "list_type",
        "missing",
        "model_type",
        "none_required",
        "string_pattern_mismatch",
        "string_too_long",
        "string_type",
        "too_long",
        "unknown_agent",
        "value_error",
    ]
)
_REPORT_KEYS: Final = frozenset(
    ["schema_version", "status", "provenance", "metrics", "runs"]
)
_STATUS_KEYS: Final = frozenset(
    ["state", "headline", "current_accuracy", "thresholds_passed"]
)
_PROVENANCE_KEYS: Final = frozenset(
    [
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
    ]
)
_PROVENANCE_ORDER: Final = (
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
)
_INCOMPLETE_METRIC_KEYS: Final = frozenset(
    ["planned_runs", "completed_records", "status"]
)
_RUN_KEYS: Final = frozenset(
    [
        "case_id",
        "expected_agent",
        "predicted_agent",
        "repeat",
        "language",
        "agent_correct",
        "schema_valid",
        "dispatchable",
        "core_args_correct",
        "provider_completed",
        "attempts",
        "latency_ms",
        "selected_arguments",
        "error_code",
        "validation_codes",
    ]
)
_SAFE_STEM_RE: Final = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)
_SAFE_BRANCH_RE: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", re.ASCII
)
_SAFE_ARGUMENT_KEYS: Final = frozenset(
    [
        "user_query",
        "goal_description",
        "data_list",
        "obs_file_list",
        "species_code",
        "gene_id",
        "to_id",
        "locale",
        "interop_mode",
        "interop_targets",
        "task_id",
    ]
)
_METRIC_MAP_KEYS: Final = {
    "run_level": frozenset(["top1_accuracy", "dispatchable_accuracy"]),
    "majority": frozenset(
        [
            "top1_correct",
            "top1_accuracy",
            "dispatchable_correct",
            "dispatchable_accuracy",
            "wilson_95",
        ]
    ),
    "macro": frozenset(["basis", "precision", "recall", "f1"]),
    "by_language": frozenset(["basis", "en", "zh"]),
    "confusion_matrix": frozenset({"basis", *_CANONICAL_AGENTS}),
    "stability": frozenset(["exact", "modal_agreement"]),
    "core_arguments": frozenset(["eligible", "correct", "accuracy"]),
    "errors": frozenset(["provider", "routing", "schema"]),
    "latency_ms": frozenset(["p50", "p95"]),
}
_AGENT_ROW_KEYS: Final = frozenset(
    ["support", "predicted", "true_positive", "precision", "recall", "f1"]
)
_LANGUAGE_ROW_KEYS: Final = frozenset(
    [
        "case_count",
        "top1_correct",
        "top1_accuracy",
        "dispatchable_correct",
        "dispatchable_accuracy",
    ]
)
_CONFUSION_ROW_KEYS: Final = frozenset(
    {
        *_CANONICAL_AGENTS,
        "__PROVIDER_ERROR__",
        "__ROUTING_ERROR__",
        "__NO_MAJORITY__",
    }
)
_SAFE_PREDICTED_AGENTS: Final = frozenset(
    {
        *_CANONICAL_AGENTS,
        "__PROVIDER_ERROR__",
        "__ROUTING_ERROR__",
        "__NO_MAJORITY__",
    }
)
_SENSITIVE_KEY_RE: Final = re.compile(
    r"(?i)(?:\bapi[-_ ]?key\b|\bauthorization\b|\bbearer\b|"
    r"\bpassword\b|\bsecret\b|\bcredential\b|\btoken\b|\bjwt\b|"
    r"\bcookie\b|\bsession\b|\baccess[_ -]?token\b|"
    r"\brefresh[_ -]?token\b|\bexception\b|\btraceback\b|"
    r"\braw[_ -]?(?:error|detail)\b)"
)
_SENSITIVE_TEXT_RE: Final = re.compile(
    r"(?i)(?:https?://[^\s\"'<>]+|\b(?:localhost|127(?:\.\d+){3}|"
    r"0\.0\.0\.0)(?::\d+)?(?:/[^\s\"'<>]*)?|"
    r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/[^\s\"'<>]*)?|"
    r"\b(?:api[-_ ]?key|authorization|bearer|password|secret|credential|"
    r"token|jwt|cookie|session|access[_ -]?token|refresh[_ -]?token)\b|"
    r"\b(?:sk|pk)[-_:=][a-z0-9._~+/=-]{8,}\b|"
    r"\b(?:token|jwt|key|secret)[-_:=][a-z0-9._~+/=-]{8,}\b|"
    r"\b(?:connection\s+reset|connection\s+refused|stack\s+trace|"
    r"raw\s+exception|provider\s+raw)\b|\bexception\b|\btraceback\b)"
)
_SHA256_RE: Final = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True, slots=True)
class GitState:
    """Minimal Git state persisted in a report."""

    branch: str
    head: str
    dirty: bool


class ReportContext(NamedTuple):
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


def _safe_mapping(
    value: Mapping[str, object],
    *,
    allowed_keys: frozenset[str] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if _SENSITIVE_KEY_RE.search(key_text) or (
            allowed_keys is not None and key_text not in allowed_keys
        ):
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
        result = _safe_mapping(cast(Mapping[str, object], value))
    elif isinstance(value, (list, tuple)):
        result = [_safe_json(item) for item in value]
    return result


def _safe_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip()
    if code in _SAFE_ERROR_CODES or code in _SAFE_VALIDATION_CODES:
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


def _mapping_with_allowed_keys(
    value: object,
    allowed_keys: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    """Require a mapping whose keys belong to a report field allowlist."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    unexpected = {str(key) for key in value} - allowed_keys
    if unexpected:
        raise ValueError(f"unexpected fields in {label}")
    return cast(Mapping[str, object], value)


def _safe_metric_mapping(
    value: object,
    allowed_keys: frozenset[str],
    label: str,
) -> dict[str, object]:
    """Sanitize one bounded metric mapping."""
    mapping = _mapping_with_allowed_keys(value, allowed_keys, label)
    return {str(key): _safe_json(item) for key, item in mapping.items()}


def _safe_per_agent(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(
        value,
        frozenset({"basis", *_CANONICAL_AGENTS}),
        "metrics.per_agent",
    )
    result: dict[str, object] = {}
    for agent, row in mapping.items():
        if agent == "basis":
            result[agent] = _safe_json(row)
        else:
            result[agent] = _safe_metric_mapping(
                row, _AGENT_ROW_KEYS, f"metrics.per_agent.{agent}"
            )
    return result


def _safe_language_rows(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(
        value, frozenset({"basis", "en", "zh"}), "metrics.by_language"
    )
    result: dict[str, object] = {}
    for language, row in mapping.items():
        if language == "basis":
            result[language] = _safe_json(row)
        else:
            result[language] = _safe_metric_mapping(
                row,
                _LANGUAGE_ROW_KEYS,
                f"metrics.by_language.{language}",
            )
    return result


def _safe_confusion_rows(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(
        value,
        frozenset({"basis", *_CANONICAL_AGENTS}),
        "metrics.confusion_matrix",
    )
    result: dict[str, object] = {}
    for agent, row in mapping.items():
        if agent == "basis":
            result[agent] = _safe_json(row)
        else:
            result[agent] = _safe_metric_mapping(
                row,
                _CONFUSION_ROW_KEYS,
                f"metrics.confusion_matrix.{agent}",
            )
    return result


def _safe_complete_metrics(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(
        value, frozenset(_METRIC_KEYS), "metrics"
    )
    if set(mapping) != set(_METRIC_KEYS):
        raise ValueError("complete metrics are incomplete")
    result: dict[str, object] = {}
    for key in _METRIC_KEYS:
        item = mapping[key]
        if key in {"majority", "stability"} and item is None:
            result[key] = None
        elif key == "per_agent":
            result[key] = _safe_per_agent(item)
        elif key == "by_language":
            result[key] = _safe_language_rows(item)
        elif key == "confusion_matrix":
            result[key] = _safe_confusion_rows(item)
        elif key in _METRIC_MAP_KEYS:
            result[key] = _safe_metric_mapping(
                item, _METRIC_MAP_KEYS[key], f"metrics.{key}"
            )
        else:
            result[key] = _safe_json(item)
    return result


def _safe_incomplete_metrics(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(
        value, _INCOMPLETE_METRIC_KEYS, "metrics"
    )
    if set(mapping) != set(_INCOMPLETE_METRIC_KEYS):
        raise ValueError("incomplete metrics must contain only bounded counts")
    planned = _safe_int(mapping["planned_runs"])
    completed = _safe_int(mapping["completed_records"])
    if planned is None or completed is None or completed > planned:
        raise ValueError("invalid incomplete metric counts")
    return {
        "planned_runs": planned,
        "completed_records": completed,
        "status": "incomplete",
    }


def _validate_partial_inventory(
    cases: Sequence[AgentRoutingCase],
    outcomes: Sequence[RunOutcome],
    repeat_count: int,
) -> None:
    """Validate the known portion of an incomplete run inventory."""
    if repeat_count not in {1, 3}:
        raise ValueError("repeat_count must be 1 or 3")
    case_by_id: dict[str, AgentRoutingCase] = {}
    for case in cases:
        if case.case_id in case_by_id:
            raise ValueError(f"duplicate case ID: {case.case_id}")
        if case.expected_agent not in _CANONICAL_AGENTS:
            raise ValueError(f"unknown expected agent: {case.expected_agent}")
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
                "selected_arguments": _safe_mapping(
                    outcome.selected_arguments,
                    allowed_keys=_SAFE_ARGUMENT_KEYS,
                ),
                "error_code": _safe_code(outcome.error_code),
                "validation_codes": validation_codes,
            }
        )
    return records


def _primary_accuracy(
    context: ReportContext,
    metrics: Mapping[str, object],
    provider_completion: float | None,
    complete: bool,
) -> float | str:
    """Return accuracy only when provider completion is trustworthy."""
    if (
        not complete
        or provider_completion is None
        or provider_completion < 0.99
    ):
        return "Unknown"
    source = metrics.get(
        "majority" if context.repeat_count == 3 else "run_level"
    )
    value = (
        _safe_float(source.get("top1_accuracy"))
        if isinstance(source, Mapping)
        else None
    )
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
    if complete:
        try:
            computed_metrics = compute_metrics(
                cases, outcomes, context.repeat_count
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("complete report inventory is invalid") from exc
        if dict(metrics) != computed_metrics:
            raise ValueError("supplied metrics do not match outcomes")
        safe_metrics = _safe_complete_metrics(computed_metrics)
    else:
        _validate_partial_inventory(cases, outcomes, context.repeat_count)
        planned_runs = len(cases) * context.repeat_count
        completed_records = len(outcomes)
        if (
            _safe_int(metrics.get("planned_runs")) != planned_runs
            or _safe_int(metrics.get("completed_records")) != completed_records
        ):
            raise ValueError("partial report counts do not match inventory")
        safe_metrics = {
            "planned_runs": planned_runs,
            "completed_records": completed_records,
            "status": "incomplete",
        }
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


def _safe_status(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(value, _STATUS_KEYS, "status")
    state = mapping.get("state")
    headline = mapping.get("headline")
    current_accuracy = mapping.get("current_accuracy")
    thresholds = mapping.get("thresholds_passed")
    if state not in {"complete", "incomplete"}:
        raise ValueError("invalid report status")
    if headline not in {
        "Stable baseline",
        "Diagnostic (dirty tree)",
        "Diagnostic",
        "Unknown",
        "Incomplete diagnostic",
    }:
        raise ValueError("invalid report headline")
    if current_accuracy != "Unknown":
        accuracy = _safe_float(current_accuracy)
        if accuracy is None or not 0.0 <= accuracy <= 1.0:
            raise ValueError("invalid current accuracy")
        current_accuracy = accuracy
    if not isinstance(thresholds, bool):
        raise ValueError("invalid threshold status")
    return {
        "state": state,
        "headline": headline,
        "current_accuracy": current_accuracy,
        "thresholds_passed": thresholds,
    }


def _safe_provenance(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(value, _PROVENANCE_KEYS, "provenance")
    if set(mapping) != set(_PROVENANCE_KEYS):
        raise ValueError("provenance is incomplete")
    branch = _safe_text(mapping["branch"])
    head = _safe_text(mapping["head"])
    if _SAFE_BRANCH_RE.fullmatch(branch) is None:
        raise ValueError("invalid report branch")
    if _SAFE_BRANCH_RE.fullmatch(head) is None:
        raise ValueError("invalid report head")
    mode = mapping["mode"]
    if mode not in {"quick", "benchmark"}:
        raise ValueError("invalid report mode")
    repeat_count = _safe_int(mapping["repeat_count"])
    concurrency = _safe_int(mapping["concurrency"])
    elapsed = _safe_float(mapping["elapsed_seconds"], nonnegative=True)
    if (
        repeat_count not in {1, 3}
        or concurrency is None
        or not 1 <= concurrency <= 32
    ):
        raise ValueError("invalid report execution settings")
    if elapsed is None:
        raise ValueError("invalid report elapsed time")
    dirty = mapping["dirty"]
    allow_dirty = mapping["allow_dirty"]
    if not isinstance(dirty, bool) or not isinstance(allow_dirty, bool):
        raise ValueError("invalid report dirty state")
    model_id = _safe_text(mapping["model_id"])
    dataset_path = _safe_text(mapping["dataset_path"])
    started_at = _safe_text(mapping["started_at"])
    return {
        "branch": branch,
        "head": head,
        "dirty": dirty,
        "model_id": model_id,
        "provider_endpoint_hash": _safe_digest(
            mapping["provider_endpoint_hash"]
        ),
        "dataset_path": dataset_path,
        "dataset_sha256": _safe_digest(mapping["dataset_sha256"]),
        "description_sha256": _safe_digest(mapping["description_sha256"]),
        "mode": mode,
        "repeat_count": repeat_count,
        "concurrency": concurrency,
        "started_at": started_at,
        "elapsed_seconds": elapsed,
        "allow_dirty": allow_dirty,
    }


def _safe_run_mapping(value: object) -> dict[str, object]:
    mapping = _mapping_with_allowed_keys(value, _RUN_KEYS, "run")
    result: dict[str, object] = {}
    for key, item in mapping.items():
        key_text = str(key)
        if key_text == "selected_arguments":
            if not isinstance(item, Mapping):
                raise ValueError("run.selected_arguments must be a mapping")
            result[key_text] = _safe_mapping(
                item,
                allowed_keys=_SAFE_ARGUMENT_KEYS,
            )
        elif key_text == "error_code":
            result[key_text] = _safe_code(item)
        elif key_text == "validation_codes":
            if not isinstance(item, (list, tuple)):
                raise ValueError("run.validation_codes must be a list")
            result[key_text] = [
                code
                for raw_code in item
                if (code := _safe_code(raw_code)) is not None
            ]
        elif key_text in {
            "case_id",
            "expected_agent",
            "predicted_agent",
            "language",
        }:
            result[key_text] = _safe_text(item)
        else:
            result[key_text] = _safe_json(item)
    return result


def _validate_complete_run_inventory(
    metrics: Mapping[str, object],
    provenance: Mapping[str, object],
    runs: Sequence[Mapping[str, object]],
) -> None:
    """Keep the public writer from emitting an internally forged report."""
    case_count = _safe_int(metrics.get("case_count"))
    planned_runs = _safe_int(metrics.get("planned_runs"))
    completed_records = _safe_int(metrics.get("completed_records"))
    repeat_count = _safe_int(provenance.get("repeat_count"))
    if (
        case_count is None
        or case_count <= 0
        or planned_runs is None
        or completed_records is None
        or repeat_count not in {1, 3}
        or planned_runs != case_count * repeat_count
        or completed_records != planned_runs
        or completed_records != len(runs)
    ):
        raise ValueError("complete report counts do not match runs")

    by_case: dict[str, set[int]] = {}
    expected_by_case: dict[str, str] = {}
    for run in runs:
        if set(run) != _RUN_KEYS:
            raise ValueError("complete report run is incomplete")
        case_id = run["case_id"]
        expected_agent = run["expected_agent"]
        predicted_agent = run["predicted_agent"]
        repeat = run["repeat"]
        language = run["language"]
        if (
            not isinstance(case_id, str)
            or not case_id
            or not isinstance(expected_agent, str)
            or expected_agent not in _CANONICAL_AGENTS
            or predicted_agent not in _SAFE_PREDICTED_AGENTS
            or not isinstance(repeat, int)
            or isinstance(repeat, bool)
            or repeat not in range(1, repeat_count + 1)
            or language not in {"en", "zh"}
        ):
            raise ValueError("complete report run contains invalid identity")
        if any(
            not isinstance(run[key], bool)
            for key in (
                "agent_correct",
                "schema_valid",
                "dispatchable",
                "provider_completed",
            )
        ):
            raise ValueError("complete report run contains invalid flags")
        core_args = run["core_args_correct"]
        if core_args is not None and not isinstance(core_args, bool):
            raise ValueError("complete report run contains invalid core flag")
        attempts = run["attempts"]
        latency_ms = run["latency_ms"]
        if (
            not isinstance(attempts, int)
            or isinstance(attempts, bool)
            or attempts < 0
            or not isinstance(latency_ms, (int, float))
            or isinstance(latency_ms, bool)
            or not math.isfinite(float(latency_ms))
            or latency_ms < 0
        ):
            raise ValueError("complete report run contains invalid timing")
        selected_arguments = run["selected_arguments"]
        validation_codes = run["validation_codes"]
        if not isinstance(selected_arguments, Mapping) or not isinstance(
            validation_codes, list
        ):
            raise ValueError("complete report run contains invalid details")
        prior_expected = expected_by_case.setdefault(case_id, expected_agent)
        if prior_expected != expected_agent:
            raise ValueError("complete report expected agents disagree")
        repeats = by_case.setdefault(case_id, set())
        if repeat in repeats:
            raise ValueError("complete report contains duplicate runs")
        repeats.add(repeat)

    if len(by_case) != case_count or any(
        repeats != set(range(1, repeat_count + 1))
        for repeats in by_case.values()
    ):
        raise ValueError("complete report case inventory is incomplete")


def _safe_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and sanitize the public writer boundary."""
    mapping = _mapping_with_allowed_keys(report, _REPORT_KEYS, "report")
    if set(mapping) != set(_REPORT_KEYS):
        raise ValueError("report is incomplete")
    if mapping["schema_version"] != 1:
        raise ValueError("unsupported report schema")
    status = _safe_status(mapping["status"])
    complete = status["state"] == "complete"
    provenance = _safe_provenance(mapping["provenance"])
    metrics = (
        _safe_complete_metrics(mapping["metrics"])
        if complete
        else _safe_incomplete_metrics(mapping["metrics"])
    )
    runs = mapping["runs"]
    if not isinstance(runs, (list, tuple)):
        raise ValueError("report runs must be a list")
    safe_runs = [_safe_run_mapping(run) for run in runs]
    if complete:
        _validate_complete_run_inventory(metrics, provenance, safe_runs)
    return {
        "schema_version": 1,
        "status": status,
        "provenance": provenance,
        "metrics": metrics,
        "runs": safe_runs,
    }


def _format_markdown_value(value: object) -> str:
    """Keep the historical private formatter import stable for tests."""
    return format_markdown_value(value)


def _render_markdown(report: Mapping[str, Any]) -> str:
    """Render one validated report as readable Markdown."""
    return render_markdown(report, _CANONICAL_AGENTS, _PROVENANCE_ORDER)


def _write_temporary(path: Path, content: str) -> Path:
    """Write one flushed temporary artifact beside its destination."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise
    return temporary


def _remove_temporary(path: Path | None) -> None:
    if path is not None:
        with suppress(FileNotFoundError):
            path.unlink()


def _restore_destination(path: Path, previous: bytes | None) -> None:
    """Restore one destination after a pair publication failure."""
    if previous is None:
        with suppress(FileNotFoundError):
            path.unlink()
        return
    path.write_bytes(previous)


def write_report_pair(
    report: Mapping[str, Any], output_dir: Path, stem: str
) -> tuple[Path, Path]:
    """Write a validated JSON/Markdown pair with rollback on failure."""
    if (
        not isinstance(stem, str)
        or _SAFE_STEM_RE.fullmatch(stem) is None
        or stem in {".", ".."}
        or ".." in stem
    ):
        raise ValueError("stem must be a direct ASCII basename")
    safe_report = _safe_report(report)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_content = (
        json.dumps(
            safe_report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    markdown_content = _render_markdown(safe_report)
    previous = {
        json_path: json_path.read_bytes() if json_path.exists() else None,
        markdown_path: (
            markdown_path.read_bytes() if markdown_path.exists() else None
        ),
    }
    json_temporary: Path | None = None
    markdown_temporary: Path | None = None
    try:
        json_temporary = _write_temporary(json_path, json_content)
        markdown_temporary = _write_temporary(markdown_path, markdown_content)
        json_temporary.replace(json_path)
        markdown_temporary.replace(markdown_path)
    except BaseException:
        _remove_temporary(json_temporary)
        _remove_temporary(markdown_temporary)
        for destination, old_content in previous.items():
            with suppress(OSError):
                _restore_destination(destination, old_content)
        raise
    finally:
        _remove_temporary(json_temporary)
        _remove_temporary(markdown_temporary)
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
