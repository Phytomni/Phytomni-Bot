# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure public-result projection for the canonical execution Runtime."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, TypeGuard

from .execution_journal_v2 import ExecutionStatus, TrackingHealth
from .execution_runtime_contracts import (
    ExecutionArtifactRef,
    TransportNeutralResult,
    TransportNeutralTabular,
)
from .public_execution_safety import (
    PublicExecutionDataError,
    validate_public_execution_value,
)

_PRIVATE_VALUE_UNSET = object()
_PUBLIC_CITATION_FIELDS = (
    "title",
    "au",
    "ti",
    "so",
    "vl",
    "bp",
    "ep",
    "ar",
    "py",
    "di",
    "pm",
)
_MAX_PUBLIC_CITATION_REFERENCES = 64
_MAX_PUBLIC_CITATION_BYTES = 6144
_MAX_PUBLIC_TABULAR_BYTES = 16 * 1024 * 1024
_TASK_IDENTIFIER_KEYS = frozenset(
    {
        "poll_task_id",
        "provider_task_id",
        "source_task_id",
        "submitted_task_id",
        "task_id",
        "task_ids",
    }
)
_ACKNOWLEDGEMENT_PREFIXES = (
    "analysis submitted",
    "submission accepted",
    "task created successfully",
    "task submission failed",
    "tasks created successfully",
)
_TASK_STATUS_WORDS = frozenset(
    {
        "accepted",
        "cancelled",
        "completed",
        "failed",
        "pending",
        "queued",
        "running",
        "submitted",
        "succeeded",
        "timed_out",
        "unknown",
    }
)

PublicScalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class PrivateInvocation:
    """Ephemeral value/error carrier; never written to journal or storage."""

    value: Any = None
    error: BaseException | None = None


@dataclass(slots=True)
class _ProjectedResult:
    """Mutable builder for one bounded public result."""

    answer: str = ""
    follow_up_questions: tuple[str, ...] = ()
    references: tuple[Mapping[str, str | bool], ...] = ()
    tabular: TransportNeutralTabular | None = None
    artifacts: list[ExecutionArtifactRef] = dataclass_field(
        default_factory=list
    )
    metadata: dict[str, PublicScalar] = dataclass_field(default_factory=dict)

    def add_archive(self, archive: ExecutionArtifactRef | None) -> None:
        """Add a ready archive unless the envelope already declared it."""
        if archive is not None and all(
            artifact.target_id != archive.target_id
            for artifact in self.artifacts
        ):
            self.artifacts.append(archive)


@dataclass(frozen=True, slots=True)
class _ArchiveInputs:
    """Validated values needed to build one private archive reference."""

    digest: str
    inventory_ref: str
    name: str
    media_type: str
    size_bytes: int
    public_ref: str


@dataclass(frozen=True, slots=True)
class _ArchiveMetadata:
    """Untrusted archive fields kept together for one validation pass."""

    name: str
    media_type: str
    size_bytes: int
    public_ref: str


def default_status(value: Any) -> ExecutionStatus:
    """Map the existing compatibility envelope onto a Runtime status."""
    if isinstance(value, tuple) and len(value) == 2:
        body, status_code = value
        if isinstance(body, dict):
            status = body.get("status")
            mapping = {
                "running": ExecutionStatus.RUNNING,
                "input_required": ExecutionStatus.WAITING_INPUT,
                "waiting_input": ExecutionStatus.WAITING_INPUT,
                "paused": ExecutionStatus.WAITING_INPUT,
                "partial": ExecutionStatus.PARTIAL,
                "failed": ExecutionStatus.FAILED,
                "cancelled": ExecutionStatus.CANCELLED,
                "timed_out": ExecutionStatus.TIMED_OUT,
            }
            if status in mapping:
                return mapping[status]
        if isinstance(status_code, int) and status_code == 202:
            return ExecutionStatus.RUNNING
    return ExecutionStatus.SUCCEEDED


def default_tracking_health(value: Any) -> TrackingHealth:
    """Project explicit compatibility tracking state into Runtime V2."""
    candidate = (
        value[0] if isinstance(value, tuple) and len(value) == 2 else value
    )
    if not isinstance(candidate, Mapping):
        return TrackingHealth.HEALTHY
    if candidate.get("degraded_tracking") is True:
        return TrackingHealth.DEGRADED
    result = candidate.get("result")
    if not isinstance(result, Mapping):
        return TrackingHealth.HEALTHY
    execution = result.get("execution")
    if not isinstance(execution, Mapping):
        return TrackingHealth.HEALTHY
    tracking = execution.get("tracking")
    if isinstance(tracking, Mapping) and tracking.get("degraded") is True:
        return TrackingHealth.DEGRADED
    return TrackingHealth.HEALTHY


def public_result_with_private(
    value: Any,
    *,
    private_value: Any = _PRIVATE_VALUE_UNSET,
    agent_slug: str | None = None,
) -> TransportNeutralResult:
    """Project common transport values without copying Agent decisions."""
    candidate = _result_candidate(value)
    projected = _project_candidate(candidate, agent_slug=agent_slug)
    return TransportNeutralResult(
        answer=projected.answer,
        follow_up_questions=projected.follow_up_questions,
        references=projected.references,
        tabular=projected.tabular,
        artifacts=tuple(projected.artifacts),
        public_metadata=projected.metadata or None,
        private_value=PrivateInvocation(
            value=(
                value
                if private_value is _PRIVATE_VALUE_UNSET
                else private_value
            )
        ),
    )


def _result_candidate(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2:
        return value[0]
    return value


def _project_candidate(
    candidate: Any, *, agent_slug: str | None
) -> _ProjectedResult:
    projected = _ProjectedResult()
    if not isinstance(candidate, Mapping):
        return projected
    result = candidate.get("result")
    result_mapping = result if isinstance(result, Mapping) else candidate
    formatted = result_mapping.get("formatted")
    formatted_mapping = (
        formatted if isinstance(formatted, Mapping) else result_mapping
    )
    projected.answer = _project_answer(formatted_mapping, candidate)
    projected.follow_up_questions = _project_follow_up(formatted_mapping)
    projected.references = _safe_public_references(
        formatted_mapping.get("references")
    )
    projected.tabular = _safe_public_tabular(formatted_mapping.get("tabular"))
    projected.metadata.update(_public_metadata(result_mapping))
    execution = result_mapping.get("execution")
    execution_mapping = (
        execution if isinstance(execution, Mapping) else result_mapping
    )
    projected.artifacts.extend(_public_artifacts(execution_mapping))
    projected.add_archive(
        _public_result_archive(
            result_mapping,
            execution_mapping,
            agent_slug=agent_slug,
        )
    )
    return projected


def _project_answer(
    formatted: Mapping[str, Any], candidate: Mapping[str, Any]
) -> str:
    raw_answer = formatted.get("answer")
    if not isinstance(raw_answer, str):
        return ""
    return _safe_public_answer(
        raw_answer,
        task_identifiers=_task_identifiers(candidate),
    )


def _project_follow_up(formatted: Mapping[str, Any]) -> tuple[str, ...]:
    raw_follow_up = formatted.get("follow_up_questions")
    if not _is_non_string_sequence(raw_follow_up):
        return ()
    return tuple(
        projected
        for item in raw_follow_up
        if isinstance(item, str)
        and (projected := _safe_public_text(item, max_chars=2048))
    )


def _public_metadata(result: Mapping[str, Any]) -> dict[str, PublicScalar]:
    metadata: dict[str, PublicScalar] = {}
    for key in ("stream", "partial", "truncated"):
        value = result.get(key)
        if isinstance(value, (str, int, float, bool)) or (
            value is None and key in result
        ):
            metadata[key] = value
    return metadata


def _public_artifacts(
    execution: Mapping[str, Any],
) -> list[ExecutionArtifactRef]:
    raw_artifacts = execution.get("artifacts")
    if not _is_non_string_sequence(raw_artifacts):
        return []
    return [
        artifact
        for raw_artifact in raw_artifacts
        if (artifact := _public_artifact(raw_artifact)) is not None
    ]


def _is_non_string_sequence(value: object) -> TypeGuard[Sequence[Any]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _safe_public_tabular(value: object) -> TransportNeutralTabular | None:
    """Keep one complete, finite scalar table or omit the invalid shape."""
    if not isinstance(value, Mapping):
        return None
    headers = _safe_headers(value.get("headers"))
    if headers is None:
        return None
    rows = _safe_rows(value.get("rows"), width=len(headers))
    if rows is None:
        return None
    return _bounded_tabular(headers, rows)


def _safe_headers(value: object) -> tuple[str, ...] | None:
    if not _is_non_string_sequence(value) or not value or len(value) > 256:
        return None
    headers: list[str] = []
    for raw_header in value:
        header = _safe_header(raw_header)
        if header is None:
            return None
        headers.append(header)
    return tuple(headers)


def _safe_header(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    header = value.strip()
    if not header or len(header) > 512:
        return None
    try:
        validate_public_execution_value(header, max_string_chars=512)
    except PublicExecutionDataError:
        return None
    return header


def _safe_rows(
    value: object, *, width: int
) -> tuple[tuple[PublicScalar, ...], ...] | None:
    if not _is_non_string_sequence(value) or len(value) > 100_000:
        return None
    rows: list[tuple[PublicScalar, ...]] = []
    for raw_row in value:
        row = _safe_row(raw_row, width=width)
        if row is None:
            return None
        rows.append(row)
    return tuple(rows)


def _safe_row(value: object, *, width: int) -> tuple[PublicScalar, ...] | None:
    if not _is_non_string_sequence(value) or len(value) != width:
        return None
    row: list[PublicScalar] = []
    for raw_cell in value:
        valid, cell = _safe_cell(raw_cell)
        if not valid:
            return None
        row.append(cell)
    return tuple(row)


def _safe_cell(value: object) -> tuple[bool, PublicScalar]:
    if isinstance(value, str):
        return _safe_string_cell(value)
    if isinstance(value, bool) or value is None:
        return True, value
    if isinstance(value, int):
        cell = value if abs(value) <= 9_007_199_254_740_991 else str(value)
        return True, cell
    if isinstance(value, float) and math.isfinite(value):
        return True, value
    return False, None


def _safe_string_cell(value: str) -> tuple[bool, PublicScalar]:
    if len(value) > 8192:
        return False, None
    try:
        validate_public_execution_value(value, max_string_chars=8192)
    except PublicExecutionDataError:
        return False, None
    return True, value


def _bounded_tabular(
    headers: tuple[str, ...],
    rows: tuple[tuple[PublicScalar, ...], ...],
) -> TransportNeutralTabular | None:
    try:
        tabular = TransportNeutralTabular(headers=headers, rows=rows)
        encoded = json.dumps(
            tabular.to_public_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError):
        return None
    if len(encoded) > _MAX_PUBLIC_TABULAR_BYTES:
        return None
    return tabular


def _safe_public_references(
    value: object,
) -> tuple[Mapping[str, str | bool], ...]:
    """Keep an ordered, finite bibliography without paths or direct URLs."""
    if not _is_non_string_sequence(value):
        return ()
    projected: list[Mapping[str, str | bool]] = []
    for raw_reference in value[:_MAX_PUBLIC_CITATION_REFERENCES]:
        reference = _safe_public_reference(raw_reference)
        if reference is None:
            continue
        if (
            _references_size([*projected, reference])
            > _MAX_PUBLIC_CITATION_BYTES
        ):
            break
        projected.append(reference)
    return tuple(projected)


def _safe_public_reference(
    value: object,
) -> dict[str, str | bool] | None:
    if not isinstance(value, Mapping):
        return None
    reference: dict[str, str | bool] = {}
    for key in _PUBLIC_CITATION_FIELDS:
        projected_field = _safe_reference_field(value.get(key))
        if projected_field:
            reference[key] = projected_field
    raw_missing = value.get("doi_missing")
    if isinstance(raw_missing, bool):
        reference["doi_missing"] = raw_missing
    return reference


def _safe_reference_field(value: object) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    projected_field = str(value).strip()
    if not projected_field:
        return ""
    try:
        validate_public_execution_value(projected_field, max_string_chars=512)
    except PublicExecutionDataError:
        return ""
    return projected_field


def _references_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _safe_public_text(value: str, *, max_chars: int) -> str:
    projected = value[-max_chars:]
    try:
        validate_public_execution_value(projected, max_string_chars=max_chars)
    except PublicExecutionDataError:
        return ""
    return projected


def _safe_public_answer(
    value: str,
    *,
    task_identifiers: tuple[str, ...] = (),
) -> str:
    if _is_submission_acknowledgement(value, task_identifiers):
        return ""
    value = _redact_task_identifiers(value, task_identifiers)
    for index in range(0, len(value), 8192):
        try:
            validate_public_execution_value(
                value[slice(index, index + 8192)],
                max_string_chars=8192,
            )
        except PublicExecutionDataError:
            return ""
    return value


def _task_identifiers(value: object) -> tuple[str, ...]:
    """Collect opaque provider identifiers only for answer redaction."""
    found: list[str] = []

    def visit(item: object, *, depth: int, task_context: bool = False) -> None:
        if depth > 6 or len(found) >= 64:
            return
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).casefold()
                identifier_field = key in _TASK_IDENTIFIER_KEYS or (
                    task_context and key == "id"
                )
                if identifier_field:
                    visit_identifier(child, depth=depth + 1)
                else:
                    visit(child, depth=depth + 1, task_context=key == "tasks")
        elif _is_non_string_sequence(item):
            for child in item[:64]:
                visit(child, depth=depth + 1, task_context=task_context)

    def visit_identifier(item: object, *, depth: int) -> None:
        if isinstance(item, str):
            identifier = item.strip()
            if identifier and identifier not in found:
                found.append(identifier)
            return
        if isinstance(item, Mapping):
            for child in item.values():
                visit_identifier(child, depth=depth + 1)
        elif _is_non_string_sequence(item):
            for child in item[:64]:
                visit_identifier(child, depth=depth + 1)

    visit(value, depth=0)
    return tuple(found)


def _is_submission_acknowledgement(
    value: str,
    task_identifiers: tuple[str, ...],
) -> bool:
    normalized = " ".join(value.casefold().split())
    if normalized.startswith(_ACKNOWLEDGEMENT_PREFIXES):
        return True
    without_ids = _redact_task_identifiers(
        normalized,
        task_identifiers,
        replacement="",
    )
    words = without_ids.replace(":", " ").replace(".", " ").split()
    return bool(
        words
        and words[0] in {"task", "tasks"}
        and any(word in _TASK_STATUS_WORDS for word in words[1:])
        and len(words) <= 8
    )


def _redact_task_identifiers(
    value: str,
    task_identifiers: tuple[str, ...],
    *,
    replacement: str = "the analysis task",
) -> str:
    redacted = value
    for identifier in sorted(task_identifiers, key=len, reverse=True):
        redacted = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])",
            replacement,
            redacted,
        )
    return redacted


def _public_artifact(value: Any) -> ExecutionArtifactRef | None:
    """Accept only opaque, bounded descriptors from the existing envelope."""
    if not isinstance(value, Mapping):
        return None
    delivery_ref = value.get("download_ref")
    target_id = value.get("id", value.get("artifact_id"))
    if target_id is None and isinstance(delivery_ref, str) and delivery_ref:
        target_id = (
            "artifact-"
            + hashlib.sha256(
                delivery_ref.encode("utf-8", errors="strict")
            ).hexdigest()[:32]
        )
    artifact_values = _artifact_values(value, target_id=target_id)
    if artifact_values is None:
        return None
    target_id, role, name, media_type, size_bytes = artifact_values
    try:
        return ExecutionArtifactRef(
            role=role,
            target_kind="artifact",
            target_id=target_id,
            name=name,
            media_type=media_type,
            size_bytes=size_bytes,
            private_delivery_ref=(
                delivery_ref
                if isinstance(delivery_ref, str) and delivery_ref
                else None
            ),
        )
    except ValueError:
        return None


def _artifact_values(
    value: Mapping[str, Any], *, target_id: object
) -> tuple[str, str, str | None, str, int] | None:
    role = value.get("role")
    name = value.get("name")
    media_type = value.get("media_type", "application/octet-stream")
    size_bytes = value.get("size_bytes", 0)
    if not isinstance(target_id, str) or not isinstance(role, str):
        return None
    if name is not None and not isinstance(name, str):
        return None
    if not isinstance(media_type, str) or not isinstance(size_bytes, int):
        return None
    return target_id, role, name, media_type, size_bytes


def _public_result_archive(
    result: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    agent_slug: str | None,
) -> ExecutionArtifactRef | None:
    """Bind one ready archive to its private object without leaking paths."""
    inputs = _archive_inputs(result, execution, agent_slug=agent_slug)
    if inputs is None or agent_slug is None:
        return None
    run_root = _archive_run_root(inputs.inventory_ref, inputs.digest)
    if run_root is None:
        return None
    target_id = (
        "download-"
        + hashlib.sha256(inputs.public_ref.encode()).hexdigest()[:32]
    )
    try:
        return ExecutionArtifactRef(
            role="result_archive",
            target_kind="download",
            target_id=target_id,
            name=inputs.name,
            media_type=inputs.media_type,
            size_bytes=inputs.size_bytes,
            private_delivery_ref=(
                f"{run_root}/delivery/"
                f"{inputs.digest.removeprefix('sha256:')}/{inputs.name}"
            ),
        )
    except ValueError:
        return None


def _archive_inputs(
    result: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    agent_slug: str | None,
) -> _ArchiveInputs | None:
    if not _valid_agent_slug(agent_slug):
        return None
    delivery = execution.get("delivery")
    private = result.get("delivery_internal")
    if not isinstance(delivery, Mapping) or not isinstance(private, Mapping):
        return None
    archive = delivery.get("archive")
    digest = delivery.get("inventory_digest")
    inventory_ref = private.get("inventory_ref")
    if delivery.get("status") != "ready":
        return None
    if not isinstance(archive, Mapping):
        return None
    if not _valid_digest(digest) or not _valid_inventory_ref(inventory_ref):
        return None
    return _validated_archive_inputs(
        archive,
        digest=digest,
        inventory_ref=inventory_ref,
        agent_slug=agent_slug,
    )


def _valid_agent_slug(value: str | None) -> TypeGuard[str]:
    return bool(value and value.replace("_", "").isalnum())


def _valid_digest(value: object) -> TypeGuard[str]:
    return bool(
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _valid_inventory_ref(value: object) -> TypeGuard[str]:
    return bool(
        isinstance(value, str)
        and value
        and "\\" not in value
        and len(value) <= 4096
    )


def _validated_archive_inputs(
    archive: Mapping[str, Any],
    *,
    digest: str,
    inventory_ref: str,
    agent_slug: str,
) -> _ArchiveInputs | None:
    metadata = _archive_metadata(archive)
    if metadata is None:
        return None
    if not _valid_archive_metadata(
        archive,
        metadata,
        digest=digest,
        agent_slug=agent_slug,
    ):
        return None
    return _ArchiveInputs(
        digest=digest,
        inventory_ref=inventory_ref,
        name=metadata.name,
        media_type=metadata.media_type,
        size_bytes=metadata.size_bytes,
        public_ref=metadata.public_ref,
    )


def _archive_metadata(
    archive: Mapping[str, Any],
) -> _ArchiveMetadata | None:
    name = archive.get("name")
    media_type = archive.get("media_type")
    size_bytes = archive.get("size_bytes")
    public_ref = archive.get("download_ref")
    if not isinstance(name, str) or not isinstance(media_type, str):
        return None
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        return None
    if not isinstance(public_ref, str):
        return None
    return _ArchiveMetadata(name, media_type, size_bytes, public_ref)


def _valid_archive_metadata(
    archive: Mapping[str, Any],
    metadata: _ArchiveMetadata,
    *,
    digest: str,
    agent_slug: str,
) -> bool:
    identity_valid = (
        archive.get("role") == "result_archive"
        and metadata.name == f"{agent_slug}-results.zip"
        and metadata.media_type == "application/zip"
        and isinstance(metadata.public_ref, str)
        and metadata.public_ref == f"result-archive:{digest}"
    )
    policy_valid = (
        metadata.size_bytes >= 0
        and archive.get("downloadable") is True
        and archive.get("report_context_eligible") is False
    )
    return identity_valid and policy_valid


def _archive_run_root(inventory_ref: str, digest: str) -> str | None:
    marker = f"/delivery/{digest.removeprefix('sha256:')}/"
    run_root, matched, leaf = inventory_ref.rpartition(marker)
    if not run_root or matched != marker:
        return None
    if leaf != ".phytomni-result-inventory.json":
        return None
    return run_root
