# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Report projections used by the run registry terminal settlement path."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import PurePosixPath
from typing import Any

from ..config.defaults import ServerConfig
from ..mcp.formatting.execution import apply_compatibility_projection
from ..mcp.formatting.models import (
    ExecutionProjection,
    FormattedToolResult,
    ResultDelivery,
)
from ..runtime.outbound import current_obs_runtime
from ..storage.artifact_listing import ListedArtifactObject
from ..storage.result_archive_storage import (
    persist_result_archive_inventory_with_runtime,
)
from .execution_models import ExecutionWarning
from .result_archive import (
    ResultArchiveError,
    build_result_archive_inventory,
)
from .run_registry_delivery import (
    initial_pending_delivery,
    mark_degraded_delivery_failure,
    result_delivery_from_result,
)
from .run_registry_models import (
    _FAILURE_STATUSES,
    _PARTIAL_CHILDREN_FAILED,
    RunOutcome,
    RunRecord,
    _has_partial_child_failure,
    _now_iso,
)
from .sqlite import sqlite_transaction
from .submission_outcome import project_submission_warnings
from .task_manager import TaskManager
from .terminal_artifacts import (
    ArtifactLister,
    ArtifactObjectLister,
    ManifestLoader,
    TerminalArtifactSet,
    collect_terminal_artifact_set,
)
from .terminal_report import (
    TerminalReportAssembly,
    TerminalReportContext,
    TerminalReportResult,
    assemble_terminal_report,
    persist_terminal_report,
)

_SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "done"})
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class ReportArtifactSources:
    """Optional artifact seams injected by run reconciliation tests/callers."""

    lister: ArtifactLister | None = None
    object_lister: ArtifactObjectLister | None = None
    manifest_loader: ManifestLoader | None = None


@dataclass(frozen=True, slots=True)
class ReportSettlementRequest:
    """Inputs for one report settlement pass."""

    registry: Any
    current: Any
    status: str
    live: list[dict[str, Any]]
    sources: ReportArtifactSources
    assembler: Any = None


_ReportSettlementRequest = ReportSettlementRequest


@dataclass(frozen=True, slots=True)
class ReportArtifactGroup:
    """One successful child task's classified terminal artifacts."""

    task_id: str
    output_dir: str
    artifact_set: TerminalArtifactSet


@dataclass(frozen=True, slots=True)
class ReportTerminalState:
    """All inputs needed to project one terminal report."""

    status: str
    live: list[dict[str, Any]]
    artifact_set: TerminalArtifactSet
    report: TerminalReportAssembly
    warnings: list[dict[str, Any]] | None = None
    delivery: ResultDelivery | None = None


async def collect_report_artifact_set(
    live: Sequence[dict[str, Any]],
    *,
    lister: ArtifactLister | None,
    object_lister: ArtifactObjectLister | None,
    manifest_loader: ManifestLoader | None,
) -> TerminalArtifactSet:
    """Collect classified artifacts for every successful child task."""
    groups = await collect_report_artifact_groups(
        live,
        lister=lister,
        object_lister=object_lister,
        manifest_loader=manifest_loader,
    )
    return merge_report_artifact_groups(groups)


async def collect_report_artifact_groups(
    live: Sequence[dict[str, Any]],
    *,
    lister: ArtifactLister | None,
    object_lister: ArtifactObjectLister | None,
    manifest_loader: ManifestLoader | None,
) -> tuple[ReportArtifactGroup, ...]:
    """Collect artifacts while retaining successful child identity."""
    groups: list[ReportArtifactGroup] = []
    for row in live:
        status = str(row.get("status") or "").lower()
        identity = _report_row_identity(
            status,
            output_dir=row.get("output_dir"),
            task_id=row.get("task_id"),
        )
        if identity is None:
            continue
        task_id, output_dir = identity
        if object_lister is not None:
            artifact_set = await collect_terminal_artifact_set(
                task_id=task_id,
                output_dir=output_dir,
                lister=object_lister,
                manifest_loader=manifest_loader,
            )
        elif lister is not None:
            artifact_set = await _collect_legacy_artifact_set(
                task_id=task_id,
                output_dir=output_dir,
                lister=lister,
                manifest_loader=manifest_loader,
            )
        else:
            artifact_set = await collect_terminal_artifact_set(
                task_id=task_id,
                output_dir=output_dir,
                manifest_loader=manifest_loader,
            )
        groups.append(
            ReportArtifactGroup(
                task_id=task_id,
                output_dir=output_dir,
                artifact_set=artifact_set,
            )
        )
    return tuple(groups)


def _report_row_identity(
    status: str,
    *,
    output_dir: object,
    task_id: object,
) -> tuple[str, str] | None:
    """Return typed task/output identity when the row is listable."""
    if (
        status not in _SUCCESS_STATUSES
        or not isinstance(output_dir, str)
        or not output_dir
        or not isinstance(task_id, str)
        or not task_id
    ):
        return None
    return task_id, output_dir


def merge_report_artifact_groups(
    groups: Sequence[ReportArtifactGroup],
) -> TerminalArtifactSet:
    """Combine child artifact sets while preserving child/list order."""
    return TerminalArtifactSet(
        artifacts=tuple(
            artifact
            for group in groups
            for artifact in group.artifact_set.artifacts
        ),
        warnings=tuple(
            warning
            for group in groups
            for warning in group.artifact_set.warnings
        ),
    )


async def _collect_legacy_artifact_set(
    *,
    task_id: str,
    output_dir: str,
    lister: ArtifactLister,
    manifest_loader: ManifestLoader | None,
) -> TerminalArtifactSet:
    """Adapt the historical path lister to the structured collector."""

    async def object_lister(directory: str) -> list[ListedArtifactObject]:
        """Convert path-only results without inferring scientific roles."""
        paths = await lister(directory)
        return [
            _listed_artifact_from_legacy_path(directory, path)
            for path in paths
            if isinstance(path, str) and path
        ]

    async def missing_manifest(_directory: str) -> None:
        """Keep path-only compatibility fail-closed without OBS reads."""
        return None

    return await collect_terminal_artifact_set(
        task_id=task_id,
        output_dir=output_dir,
        lister=object_lister,
        manifest_loader=manifest_loader or missing_manifest,
    )


def _listed_artifact_from_legacy_path(
    output_dir: str, path: str
) -> ListedArtifactObject:
    """Build an unknown-safe object record from a legacy public path."""
    prefix = output_dir.rstrip("/") + "/"
    relative_path = (
        path.removeprefix(prefix)
        if path.startswith(prefix)
        else PurePosixPath(path).name
    )
    return ListedArtifactObject(
        relative_path=relative_path or "artifact",
        source_path=path,
        size_bytes=0,
        download_ref=path,
    )


def persist_report_compatibility(
    live: list[dict[str, Any]],
    report: TerminalReportAssembly,
    db_path: str,
) -> None:
    """Keep the task-log report column aligned with canonical assembly."""
    reason = next(
        (
            warning.code
            for warning in report.warnings
            if warning.code.startswith("report_")
        ),
        None,
    )
    persist_terminal_report(
        live,
        TerminalReportResult(
            final_report=report.answer,
            answer=report.answer,
            degraded=report.report.degraded,
            degraded_reason=reason,
        ),
        task_manager=TaskManager(db_path),
    )


def _persist_running_scientific_report(
    registry: Any,
    current: Any,
    live: list[dict[str, Any]],
    report: TerminalReportAssembly,
) -> Any:
    """Publish the scientific answer before harvest or archive work.

    EI completion is independent of OBS listing and zip delivery. Persist
    the floor report on the still-running umbrella so Web can leave the
    submit-ack wait state without downloading the output tree.
    """
    current_result = current.result if isinstance(current.result, dict) else {}
    formatted = dict(current_result.get("formatted") or {})
    formatted["answer"] = report.answer
    next_result = {
        **current_result,
        "formatted": formatted,
        "final_report": report.answer,
    }
    updated = registry.update_running_result(
        current.spec.run_id,
        owner=current.spec.user_id,
        result=next_result,
    )
    if updated:
        persist_report_compatibility(live, report, registry.db_path)
        refreshed = registry.get_run(
            current.spec.run_id, owner=current.spec.user_id
        )
        if refreshed is not None:
            return refreshed
    return current


async def settle_report_terminal(request: ReportSettlementRequest) -> Any:
    """Assemble and persist one analyst-class terminal report."""
    current: Any = _persist_running_scientific_report(
        request.registry,
        request.current,
        request.live,
        await _assemble_report(
            request.current,
            request.status,
            request.live,
            TerminalArtifactSet(artifacts=(), warnings=()),
            None,
        ),
    )
    groups = await collect_report_artifact_groups(
        request.live,
        lister=request.sources.lister,
        object_lister=request.sources.object_lister,
        manifest_loader=request.sources.manifest_loader,
    )
    artifact_set = merge_report_artifact_groups(groups)
    transition = getattr(request.registry, "transition_research_stage", None)
    if callable(transition) and current.spec.agent == "research":
        transitioned: Any = transition(current, "report_assembly")
        if transitioned is None:
            return request.registry.get_run(
                current.spec.run_id, owner=current.spec.user_id
            )
        current = transitioned
    report = await _assemble_report(
        current,
        request.status,
        request.live,
        artifact_set,
        request.assembler,
    )
    warnings = stored_submission_warnings(current.result)
    marker = result_delivery_from_result(current.result)
    state = ReportTerminalState(
        status=request.status,
        live=request.live,
        artifact_set=artifact_set,
        report=report,
        warnings=warnings,
        delivery=marker,
    )
    if not marker or request.status != "succeeded":
        return _settle_report_without_delivery(
            request.registry, current, state
        )
    try:
        inventory = build_result_archive_inventory(groups)
        inventory_ref = await _persist_report_inventory(inventory)
    except ResultArchiveError as exc:
        state = replace(
            state,
            delivery=ResultDelivery(
                schema_version=1,
                required=True,
                status="failed",
                revision=1,
                inventory_digest="",
                archive=None,
                error_code=exc.code,
                retryable=False,
            ),
        )
        return _settle_report_inventory_failure(
            request.registry, current, state
        )
    delivery = initial_pending_delivery(inventory.digest)
    state = replace(state, delivery=delivery)
    return _store_pending_report_delivery(
        request.registry,
        current,
        state,
        inventory_ref,
        delivery,
    )


async def _assemble_report(
    current: Any,
    status: str,
    live: list[dict[str, Any]],
    artifact_set: TerminalArtifactSet,
    assembler: Any,
) -> TerminalReportAssembly:
    """Build one report through the injected compatibility seam."""
    report_builder = assembler or assemble_terminal_report
    return await report_builder(
        context=TerminalReportContext(
            agent=current.spec.agent,
            status=status,
            live=live,
            artifacts=artifact_set.artifacts,
            query=current.request_info.query,
            locale=current.request_info.locale or "en-US",
        ),
        artifacts=artifact_set.artifacts if status == "succeeded" else (),
    )


async def _persist_report_inventory(inventory: Any) -> str:
    """Persist the immutable inventory and return its private reference."""
    config = ServerConfig()
    return await persist_result_archive_inventory_with_runtime(
        inventory,
        bucket=config.BUCKET_NAME,
        obs_runtime=current_obs_runtime(),
    )


def _settle_report_without_delivery(
    registry: Any,
    current: Any,
    state: ReportTerminalState,
) -> Any:
    """Settle a report whose result has no required archive delivery."""
    result_payload, error = canonical_terminal_payload(state)
    settle_terminal = getattr(registry, "_settle_terminal")
    settled = settle_terminal(
        current, RunOutcome(state.status, result_payload, error)
    )
    if (
        settled is not None
        and settled.status == state.status
        and settled.result == result_payload
    ):
        persist_report_compatibility(
            state.live, state.report, registry.db_path
        )
    return settled


def _settle_report_inventory_failure(
    registry: Any,
    current: Any,
    state: ReportTerminalState,
) -> Any:
    """Settle a report while exposing only a bounded archive error code."""
    result_payload, _error = canonical_terminal_payload(state)
    mark_degraded_delivery_failure(result_payload, False)
    settle_terminal = getattr(registry, "_settle_terminal")
    settled = settle_terminal(
        current, RunOutcome("succeeded", result_payload, None)
    )
    if settled is not None and settled.result == result_payload:
        persist_report_compatibility(
            state.live, state.report, registry.db_path
        )
    return settled


def _store_pending_report_delivery(
    registry: Any,
    current: Any,
    state: ReportTerminalState,
    inventory_ref: str,
    delivery: ResultDelivery,
) -> Any:
    """Store pending delivery state and schedule its worker."""
    result_payload, _error = canonical_terminal_payload(state)
    result_payload["delivery_internal"] = {
        "inventory_ref": inventory_ref,
        "attempts_claimed": 0,
        "last_error_code": None,
    }
    if not registry.update_running_result(
        current.spec.run_id,
        owner=current.spec.user_id,
        result=result_payload,
    ):
        return registry.get_run(
            current.spec.run_id, owner=current.spec.user_id
        )
    settled = registry.get_run(current.spec.run_id, owner=current.spec.user_id)
    if settled is not None and settled.result == result_payload:
        persist_report_compatibility(
            state.live, state.report, registry.db_path
        )
        getattr(registry, "_schedule_delivery")(settled, delivery)
    return settled


def canonical_terminal_payload(
    state: ReportTerminalState,
) -> tuple[dict[str, Any], str | None]:
    """Build the single persisted projection for analyst-class runs."""
    execution = _build_execution_projection(state)
    formatted = apply_compatibility_projection(
        FormattedToolResult(answer=state.report.answer), execution
    )
    payload = _json_compatible(
        {"formatted": asdict(formatted), "execution": asdict(execution)}
    )
    return payload, _terminal_error(state.status, state.live)


def legacy_terminal_payload(
    status: str,
    live: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    answer: str,
    *,
    warnings: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build the historical projection for non-report terminal runs."""
    payload: dict[str, Any] = {
        "task_results": live,
        "live_status": live,
        "artifacts": artifacts,
        "final_report": _first_final_report(live),
        "degraded": any_degraded(live),
    }
    if answer:
        payload["formatted"] = {"answer": answer}
    if warnings:
        payload["execution"] = {"warnings": warnings}
    if status == "succeeded":
        return payload, None
    failed = [
        row.get("task_id", "?")
        for row in live
        if (row.get("status") or "").lower() in _FAILURE_STATUSES
    ]
    return payload, f"one or more tasks failed: {', '.join(failed)}"


def _first_final_report(live: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the first non-empty child ``final_report`` value."""
    for row in live:
        report = row.get("final_report")
        if isinstance(report, str) and report:
            return report
    return None


def _build_execution_projection(
    state: ReportTerminalState,
) -> ExecutionProjection:
    """Build the bounded operational projection for a terminal report."""
    submission_warnings = _execution_warnings(state.warnings)
    failure_warnings = _failure_warnings(state.status, state.live)
    return ExecutionProjection(
        tracking={
            "degraded": _tracking_is_degraded(
                state.status,
                state.live,
                submission_warnings,
                state.artifact_set,
                state.report,
            )
        },
        warnings=(
            *submission_warnings,
            *state.artifact_set.warnings,
            *failure_warnings,
            *state.report.warnings,
        ),
        tasks=tuple(_public_task_row(row) for row in state.live),
        artifacts=tuple(
            asdict(artifact.to_public())
            for artifact in state.artifact_set.artifacts
        ),
        output_dirs=tuple(_public_output_dirs(state.live)),
        report=state.report.report,
        diagnostics=tuple(_failure_diagnostics(state.status, state.live)),
        delivery=state.delivery,
    )


def _tracking_is_degraded(
    status: str,
    live: Sequence[Mapping[str, Any]],
    submission_warnings: Sequence[ExecutionWarning],
    artifact_set: TerminalArtifactSet,
    report: TerminalReportAssembly,
) -> bool:
    """Return whether any safe terminal signal requires degraded tracking."""
    return (
        status != "succeeded"
        or bool(submission_warnings)
        or bool(artifact_set.warnings)
        or bool(_failure_warnings(status, live))
        or report.report.degraded
        or any_degraded(live)
    )


def _terminal_error(
    status: str, live: Sequence[Mapping[str, Any]]
) -> str | None:
    """Return a stable terminal error without provider or exception text."""
    if status == "succeeded":
        return None
    failed = failed_task_ids(live)
    return f"one or more tasks failed: {', '.join(failed)}"


def stored_execution_tasks(
    result: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Return persisted execution.tasks mappings from one run result."""
    if not isinstance(result, Mapping):
        return []
    execution = result.get("execution")
    if not isinstance(execution, Mapping):
        return []
    tasks = execution.get("tasks")
    if not isinstance(tasks, list):
        return []
    return [item for item in tasks if isinstance(item, Mapping)]


def annotate_live_with_stored_tasks(
    live: list[dict[str, Any]],
    result: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Copy kind, error_code, and doomed children onto live task rows."""
    stored = stored_execution_tasks(result)
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in stored:
        task_id = item.get("id")
        if isinstance(task_id, str) and task_id:
            by_id[task_id] = item
    annotated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in live:
        item = dict(row)
        task_id = item.get("task_id")
        if isinstance(task_id, str) and task_id in by_id:
            previous = by_id[task_id]
            if "kind" not in item and "kind" in previous:
                item["kind"] = previous.get("kind")
            if "error_code" not in item and "error_code" in previous:
                item["error_code"] = previous.get("error_code")
            if "accepted" not in item and "accepted" in previous:
                item["accepted"] = previous.get("accepted")
            seen.add(task_id)
        annotated.append(item)
    for previous in stored:
        task_id = previous.get("id")
        if not isinstance(task_id, str) or task_id in seen:
            continue
        if previous.get("accepted") is False:
            annotated.append(
                {
                    "task_id": task_id,
                    "status": (
                        previous["status"]
                        if isinstance(previous.get("status"), str)
                        else "failed"
                    ),
                    "accepted": False,
                    "kind": previous.get("kind"),
                    "error_code": previous.get("error_code"),
                }
            )
    return annotated


def overlay_live_status_on_stored_tasks(
    result: Mapping[str, Any] | None,
    live: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Copy live child status onto stored five-key execution.tasks."""
    if live is None or not isinstance(result, Mapping):
        return None
    stored = stored_execution_tasks(result)
    if not stored:
        return None
    by_id: dict[str, str] = {}
    for row in live:
        task_id = row.get("task_id")
        status = row.get("status")
        if (
            isinstance(task_id, str)
            and task_id
            and isinstance(status, str)
            and status
        ):
            by_id[task_id] = status
    if not by_id:
        return None
    payload = dict(result)
    execution = dict(payload.get("execution") or {})
    tasks: list[dict[str, Any]] = []
    for item in stored:
        row = dict(item)
        task_id = row.get("id")
        if isinstance(task_id, str) and task_id in by_id:
            row["status"] = by_id[task_id]
        tasks.append(row)
    execution["tasks"] = tasks
    payload["execution"] = execution
    return payload


def touch_running_run(
    registry: Any,
    current: RunRecord,
    status: str,
    live: list[dict[str, Any]] | None = None,
) -> RunRecord | None:
    """Refresh a still-running owner row and overlay live child statuses."""
    now = _now_iso()
    overlay = overlay_live_status_on_stored_tasks(current.result, live)
    with sqlite_transaction(registry.db_path) as conn:
        if overlay is None:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? "
                "WHERE run_id = ? AND user_id = ? AND status = 'running'",
                (status, now, current.spec.run_id, current.spec.user_id),
            )
        else:
            conn.execute(
                "UPDATE runs SET status = ?, result_json = ?, updated_at = ? "
                "WHERE run_id = ? AND user_id = ? AND status = 'running'",
                (
                    status,
                    json.dumps(overlay),
                    now,
                    current.spec.run_id,
                    current.spec.user_id,
                ),
            )
    return registry.get_run(current.spec.run_id, owner=current.spec.user_id)


def attach_partial_child_degraded(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Mark tracking degraded and append a bounded partial-child warning."""
    payload = dict(result or {})
    execution = dict(payload.get("execution") or {})
    warnings = [
        dict(item) if isinstance(item, dict) else item
        for item in (execution.get("warnings") or [])
    ]
    if not any(
        isinstance(item, dict) and item.get("code") == _PARTIAL_CHILDREN_FAILED
        for item in warnings
    ):
        warnings.append({"code": _PARTIAL_CHILDREN_FAILED, "retryable": False})
    tracking = dict(execution.get("tracking") or {})
    tracking["degraded"] = True
    execution["warnings"] = warnings
    execution["tracking"] = tracking
    payload["execution"] = execution
    return payload


def mark_partial_child_failure(
    current: RunRecord,
    live: list[dict[str, Any]],
    new_status: str,
) -> tuple[RunRecord, list[dict[str, Any]], bool]:
    """Annotate stored doomed children and mark partial success degraded."""
    live = annotate_live_with_stored_tasks(live, current.result)
    partial = new_status == "succeeded" and _has_partial_child_failure(
        [str(row.get("status") or "") for row in live]
    )
    if not partial:
        return current, live, False
    current = replace(
        current,
        result=attach_partial_child_degraded(current.result),
    )
    return current, live, True


def _public_task_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project one reconciled task into the execution-only task shape."""
    task_id = row.get("task_id")
    projected: dict[str, Any] = {
        "id": str(task_id) if task_id is not None else "unknown",
        "accepted": (
            row["accepted"] if isinstance(row.get("accepted"), bool) else True
        ),
    }
    status = row.get("status")
    if isinstance(status, str) and status:
        projected["status"] = status
    if "kind" in row:
        kind = row.get("kind")
        projected["kind"] = (
            kind
            if isinstance(kind, str) and _KIND_PATTERN.fullmatch(kind)
            else ""
        )
    if "error_code" in row:
        error_code = row.get("error_code")
        projected["error_code"] = (
            error_code
            if isinstance(error_code, str)
            and _KIND_PATTERN.fullmatch(error_code)
            else None
        )
    return projected


def _public_output_dirs(live: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return nonblank owner-scoped output references in task order."""
    return [
        output_dir
        for row in live
        if isinstance(output_dir := row.get("output_dir"), str) and output_dir
    ]


def _execution_warnings(
    warnings: Sequence[Mapping[str, Any]] | None,
) -> tuple[ExecutionWarning, ...]:
    """Convert persisted submit warnings into the canonical warning type."""
    if not warnings:
        return ()
    projected: list[ExecutionWarning] = []
    for item in warnings:
        code = item.get("code")
        if not isinstance(code, str) or not code:
            continue
        stage = item.get("stage")
        projected.append(
            ExecutionWarning(
                code=code,
                retryable=item.get("retryable") is True,
                stage=stage if isinstance(stage, str) else "submission",
            )
        )
    return tuple(projected)


def _failure_warnings(
    status: str, live: Sequence[Mapping[str, Any]]
) -> tuple[ExecutionWarning, ...]:
    """Return one stable warning for a failed aggregate run."""
    if status == "succeeded":
        return ()
    code = "task_failed" if failed_task_ids(live) else "run_not_succeeded"
    return (ExecutionWarning(code=code, stage="reconcile"),)


def _failure_diagnostics(
    status: str, live: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return bounded diagnostics without task payloads or exceptions."""
    if status == "succeeded":
        return []
    code = "task_failed" if failed_task_ids(live) else "run_not_succeeded"
    return [{"code": code, "retryable": False, "stage": "reconcile"}]


def failed_task_ids(live: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return stable failed task ids for the terminal error string."""
    return [
        str(row.get("task_id", "?"))
        for row in live
        if str(row.get("status") or "").lower() in _FAILURE_STATUSES
    ]


def _json_compatible(value: Any) -> Any:
    """Normalize dataclass tuples to the JSON shape stored in SQLite."""
    return json.loads(json.dumps(value))


def stored_submission_warnings(
    result: Mapping[str, Any] | None,
) -> list[dict[str, object]]:
    """Carry safe submit-time warnings into the terminal result."""
    if not isinstance(result, Mapping):
        return []
    execution = result.get("execution")
    raw = (
        execution.get("warnings")
        if isinstance(execution, Mapping)
        else result.get("submission_warnings")
    )
    return project_submission_warnings(raw)


def any_degraded(live: Sequence[Mapping[str, Any]]) -> bool:
    """Return True when any reconciled child task is degraded."""
    return any(bool(row.get("degraded")) for row in live)
