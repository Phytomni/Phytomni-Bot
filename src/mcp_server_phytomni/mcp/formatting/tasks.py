# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Format tabular, asynchronous-task, and report-status MCP responses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...contracts.deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
    sanitize_nonnegative_int,
)
from ...runtime.terminal_artifacts import collect_terminal_artifacts
from ..universal_failures import (
    project_interop_metadata,
    project_universal_failure_metadata,
    redact_failure_message,
)
from ._shared import (
    _METADATA_TEXT_TRUNCATE_BYTES,
    mapping_sequence,
    normalize_compute_resource,
    phytomni_state,
    string_or_none,
    truncate_text,
)
from .models import FormattedToolResult

_DEEP_GENOME_FAILURE_MESSAGES = {
    "succeeded": "analysis task unavailable",
    "failed": "analysis task failed",
    "cancelled": "analysis task cancelled",
    "timed_out": "analysis task timed out",
}


def format_data_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format natural-language SQL table output."""
    del arguments
    headers = [
        str(column.get("caption") or column.get("name") or "")
        for column in mapping_sequence(content.get("header"))
    ]
    raw_rows = content.get("data", [])
    rows = list(raw_rows) if isinstance(raw_rows, Sequence) else []
    row_count = len(rows)
    column_count = len(headers)
    row_label = "row" if row_count == 1 else "rows"
    column_label = "column" if column_count == 1 else "columns"
    state = phytomni_state(content)
    return FormattedToolResult(
        answer=f"{row_count} {row_label} x {column_count} {column_label}",
        metadata={
            "user_query": state.get("user_query"),
            "rewrite_query": state.get("rewrite_query"),
            "is_rewrite": state.get("is_rewrite"),
        },
        tabular={"headers": headers, "rows": rows},
    )


def format_task_result(content: Mapping[str, Any]) -> FormattedToolResult:
    """Format one async task submission response."""
    task_id = string_or_none(content.get("task_id"))
    output_dir = string_or_none(content.get("output_dir"))
    compute_resource = normalize_compute_resource(
        string_or_none(content.get("compute_resource"))
    )
    failed = not task_id
    supplied_answer = string_or_none(content.get("answer"))
    return FormattedToolResult(
        answer=supplied_answer
        or (
            "Task submission failed: missing task_id"
            if failed
            else f"Task created successfully:{task_id}"
        ),
        metadata={
            "task_id": task_id,
            "output_dir": output_dir,
            "compute_resource": compute_resource,
            "status": "FAILED" if failed else "RUNNING",
            "log_status": "sync_failed" if failed else "sync_running",
        },
    )


def format_analyst_task_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format an AnalystAgent submit response with planning metadata."""
    del arguments
    base = format_task_result(content)
    state = phytomni_state(content)
    plan_text = state.get("plan")
    truncated_plan = (
        truncate_text(
            str(plan_text),
            _METADATA_TEXT_TRUNCATE_BYTES,
            "raw.phytomni_state.plan",
        )
        if isinstance(plan_text, str)
        else None
    )
    extracted_tools = state.get("extracted_tools")
    method_context = state.get("method_context")
    enriched_metadata = {
        **base.metadata,
        "plan": truncated_plan,
        "plan_retries": state.get("plan_retries"),
        "extracted_tools": (
            tuple(str(tool) for tool in extracted_tools)
            if isinstance(extracted_tools, Sequence)
            and not isinstance(extracted_tools, str)
            else ()
        ),
        "method_context_keys": (
            tuple(str(key) for key in method_context)
            if isinstance(method_context, Mapping)
            else ()
        ),
    }
    return FormattedToolResult(
        answer=base.answer,
        follow_up_questions=base.follow_up_questions,
        metadata=enriched_metadata,
        references=base.references,
        tabular=base.tabular,
        output_dirs=base.output_dirs,
    )


def format_network_task_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format a GeneNetworkAgent submit response with goal metadata."""
    del arguments
    base = format_task_result(network_task_payload(content))
    state = phytomni_state(content)
    goal_text = state.get("goal_description")
    truncated_goal = (
        truncate_text(
            str(goal_text),
            256,
            "raw.phytomni_state.goal_description",
        )
        if isinstance(goal_text, str)
        else None
    )
    universal = project_universal_failure_metadata(content)
    raw_task_ids = content.get("task_ids")
    task_ids = (
        tuple(
            task_id
            for value in raw_task_ids
            if (task_id := string_or_none(value)) is not None
        )
        if isinstance(raw_task_ids, (list, tuple))
        else ()
    )
    enriched_metadata = {
        **base.metadata,
        "goal_description": truncated_goal,
        "task_ids": task_ids,
        "status": universal["status"],
        "succeeded_count": universal["succeeded_count"],
        "failed_count": universal["failed_count"],
        "failures": universal["failures"],
    }
    if not enriched_metadata.get("task_id"):
        enriched_metadata.update(status="FAILED", log_status="sync_failed")
    elif task_ids:
        enriched_metadata["task_id"] = task_ids[0]
    return FormattedToolResult(
        answer=base.answer,
        follow_up_questions=base.follow_up_questions,
        metadata=enriched_metadata,
        references=base.references,
        tabular=base.tabular,
        output_dirs=base.output_dirs,
    )


def format_deep_genome_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format a DeepGenome submit envelope."""
    arguments = arguments or {}
    server_id = string_or_none(content.get("task_id"))
    failed = not server_id
    return FormattedToolResult(
        answer=(
            "Task submission failed: missing task_id"
            if failed
            else f"Task created successfully:{server_id}"
        ),
        metadata={
            "task_id": server_id,
            "output_dir": string_or_none(content.get("output_dir")),
            "species_code": string_or_none(arguments.get("species_code")),
            "gene_id": string_or_none(arguments.get("gene_id")),
            "compute_resource": string_or_none(
                content.get("compute_resource")
            ),
            "status": "FAILED" if failed else "RUNNING",
            "log_status": "sync_failed" if failed else "sync_running",
        },
    )


def format_in_silico_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format an InSilicoResearchAgent submit response."""
    del arguments
    task_ids_mapping = content.get("task_ids")
    task_id_values: Sequence[Any]
    if isinstance(task_ids_mapping, Mapping):
        task_id_values = tuple(task_ids_mapping.values())
    elif isinstance(task_ids_mapping, (list, tuple)):
        task_id_values = task_ids_mapping
    else:
        task_id_values = ()
    task_ids = tuple(
        tid
        for tid in (string_or_none(value) for value in task_id_values)
        if tid is not None
    )
    goals = tuple(
        str(item.get("goal", ""))
        for item in mapping_sequence(content.get("goals"))
    )
    output_dir = string_or_none(content.get("output_dir"))
    primary_task_id = task_ids[0] if task_ids else None
    universal = project_universal_failure_metadata(content)
    failures_list = content.get("failures") or []
    raw_legacy_error = (
        failures_list[-1]["message"]
        if failures_list
        else string_or_none(content.get("error"))
    )
    legacy_error = (
        redact_failure_message(raw_legacy_error)
        if isinstance(raw_legacy_error, str)
        else raw_legacy_error
    )
    failed = not task_ids
    metadata = {
        "task_id": primary_task_id,
        "task_ids": task_ids,
        "output_dir": output_dir,
        "goals": goals,
        "error": legacy_error,
        "status": "FAILED" if failed else universal["status"],
        "succeeded_count": universal["succeeded_count"],
        "failed_count": universal["failed_count"],
        "failures": universal["failures"],
        "log_status": "sync_failed" if failed else "sync_running",
    }
    metadata.update(project_interop_metadata(phytomni_state(content)))
    return FormattedToolResult(
        answer=(
            "Task submission failed: no task ids"
            if failed
            else f"Tasks created successfully: {','.join(task_ids)}"
        ),
        metadata=metadata,
    )


def format_design_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format task output from DigitalDesignAgent."""
    del arguments
    tasks = design_tasks(content)
    if not tasks:
        return empty_design_result(content)
    primary_task = tasks[0]
    raw_task_ids = content.get("task_ids")
    if isinstance(raw_task_ids, (list, tuple)):
        task_ids = tuple(
            task_id
            for value in raw_task_ids
            if (task_id := string_or_none(value)) is not None
        )
        _, output_dirs = design_outputs(tasks)
    else:
        task_ids, output_dirs = design_outputs(tasks)
    universal = project_universal_failure_metadata(content)
    metadata: dict[str, Any] = {
        "task_id": string_or_none(primary_task.get("task_id")),
        "output_dir": output_dirs[0] if output_dirs else None,
        "compute_resource": string_or_none(
            primary_task.get("compute_resource")
        ),
        "status": universal["status"],
        "log_status": (
            "sync_failed"
            if universal["status"] == "FAILED"
            else "sync_running"
        ),
        "task_ids": task_ids,
        "goal_description": design_goal_description(content),
        "succeeded_count": universal["succeeded_count"],
        "failed_count": universal["failed_count"],
        "failures": universal["failures"],
    }
    metadata.update(project_interop_metadata(phytomni_state(content)))
    return FormattedToolResult(
        answer=f"Tasks created successfully: {','.join(task_ids)}",
        metadata=metadata,
        output_dirs=output_dirs,
    )


def format_task_status_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format a ``GetTaskStatus`` lookup result with report metadata."""
    del arguments
    task_id = string_or_none(content.get("task_id"))
    status = string_or_none(content.get("status")) or "unknown"
    output_dir = string_or_none(content.get("output_dir"))
    artifacts = collect_terminal_artifacts([dict(content)])
    final_report = nonblank_report(content.get("final_report"))
    intermediate_report = nonblank_report(content.get("intermediate_report"))
    answer = final_report or intermediate_report
    if answer is None:
        answer = f"Task {task_id or '?'}: {status}"
    report_stage = report_stage_value(
        content.get("report_stage"),
        final_report=final_report,
        intermediate_report=intermediate_report,
    )
    report_completeness = report_completeness_value(
        content.get("report_completeness"),
        final_report=final_report,
        intermediate_report=intermediate_report,
    )
    return FormattedToolResult(
        answer=answer,
        metadata={
            "task_id": task_id,
            "status": status,
            "output_dir": output_dir,
            "analysis_id": string_or_none(content.get("analysis_id")),
            "live_status": content.get("live_status"),
            "artifacts": artifacts,
            "report_stage": report_stage,
            "report_completeness": report_completeness,
            "report_revision": sanitize_nonnegative_int(
                content.get("report_revision")
            ),
            "report_updated_at": string_or_none(
                content.get("report_updated_at")
            ),
            "progress": deep_genome_progress(content.get("progress")),
            "degraded": content.get("degraded") is True,
            "degraded_reason": string_or_none(content.get("degraded_reason")),
            "failures": deep_genome_failures(content.get("failures")),
        },
    )


def network_task_payload(content: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the nested GeneNetwork task payload when present."""
    network_task = content.get("network_task")
    return network_task if isinstance(network_task, Mapping) else content


def design_tasks(content: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return mapping entries from a DigitalDesign task list."""
    design_results = content.get("design_task_result")
    if not isinstance(design_results, list):
        return []
    return [task for task in design_results if isinstance(task, Mapping)]


def design_outputs(
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return task IDs and output directories in dispatch order."""
    task_ids = tuple(
        task_id
        for task in tasks
        if (task_id := string_or_none(task.get("task_id"))) is not None
    )
    output_dirs = tuple(
        str(task.get("output_dir"))
        for task in tasks
        if task.get("output_dir") is not None
    )
    return task_ids, output_dirs


def design_goal_description(content: Mapping[str, Any]) -> str | None:
    """Return the bounded DigitalDesign goal description."""
    goal = phytomni_state(content).get("goal_description")
    if not goal:
        return None
    return truncate_text(
        str(goal),
        256,
        "raw.phytomni_state.goal_description",
    )


def empty_design_result(content: Mapping[str, Any]) -> FormattedToolResult:
    """Format a DigitalDesign response without submitted tasks."""
    universal = project_universal_failure_metadata(content)
    failures = universal["failures"]
    answer = "No tasks found"
    if failures:
        first = failures[0].get("message", "")
        answer = (
            f"No tasks found ({universal['failed_count']} failed: {first})"
        )
    return FormattedToolResult(
        answer=answer,
        metadata={
            "status": "FAILED",
            "log_status": "sync_failed",
            "succeeded_count": universal["succeeded_count"],
            "failed_count": universal["failed_count"],
            "failures": failures,
            **project_interop_metadata(phytomni_state(content)),
        },
    )


def nonblank_report(value: Any) -> str | None:
    """Return report Markdown unchanged; whitespace-only text is absent."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def report_stage_value(
    value: Any,
    *,
    final_report: str | None,
    intermediate_report: str | None,
) -> str | None:
    """Return a bounded report-stage value with a report fallback."""
    stage = string_or_none(value)
    if stage in {"waiting_for_brief_gene", "intermediate", "final"}:
        return stage
    if final_report is not None:
        return "final"
    if intermediate_report is not None:
        return "intermediate"
    return None


def report_completeness_value(
    value: Any,
    *,
    final_report: str | None,
    intermediate_report: str | None,
) -> str | None:
    """Return a bounded report-completeness value with a safe fallback."""
    completeness = string_or_none(value)
    if completeness in {"none", "partial", "complete"}:
        return completeness
    if final_report is not None:
        return "complete"
    if intermediate_report is not None:
        return "partial"
    return None


def deep_genome_progress(value: Any) -> dict[str, Any]:
    """Copy only the public DeepGenome progress counters."""
    if not isinstance(value, Mapping):
        return {}
    progress: dict[str, Any] = {}
    for key in DEEP_GENOME_PROGRESS_FIELDS:
        raw = value.get(key)
        if key == "planning_complete":
            progress[key] = raw if isinstance(raw, bool) else False
        elif key == "brief_gene_status":
            progress[key] = (
                raw.strip().lower()
                if isinstance(raw, str) and raw.strip()
                else "unknown"
            )
        else:
            progress[key] = sanitize_nonnegative_int(raw)
    return progress


def deep_genome_failures(value: Any) -> list[dict[str, str]]:
    """Copy terminal failures with fixed, non-sensitive public messages."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    failures: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        work_item_key = string_or_none(item.get("work_item_key"))
        status = string_or_none(item.get("status"))
        if (
            work_item_key is None
            or status not in _DEEP_GENOME_FAILURE_MESSAGES
        ):
            continue
        failures.append(
            {
                "work_item_key": work_item_key,
                "status": status,
                "message": _DEEP_GENOME_FAILURE_MESSAGES[status],
            }
        )
    return failures


__all__ = [
    "format_analyst_task_result",
    "format_data_result",
    "format_deep_genome_result",
    "format_design_result",
    "format_in_silico_result",
    "format_network_task_result",
    "format_task_result",
    "format_task_status_result",
]
