# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed models for MCP result and streaming formatting."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ...runtime.execution_models import ExecutionWarning

_ARCHIVE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ARCHIVE_NAME = re.compile(
    r"(?:analyst|research|network|design)-results\.zip\Z"
)
_DELIVERY_STATUSES = frozenset({"pending", "ready", "failed"})


def _is_plain_int(value: object) -> bool:
    """Return whether a value is an integer but not a Boolean."""
    return isinstance(value, int) and value.__class__ is int


def _is_valid_archive_identity(
    role: object,
    name: object,
    media_type: object,
) -> bool:
    """Return whether the archive identity uses supported public values."""
    return (
        role == "result_archive"
        and isinstance(name, str)
        and _ARCHIVE_NAME.fullmatch(name) is not None
        and media_type == "application/zip"
    )


def _is_valid_archive_access(
    size_bytes: object,
    downloadable: object,
    report_context_eligible: object,
    download_ref: object,
) -> bool:
    """Return whether public archive access data is resolver-safe."""
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        return False
    if size_bytes < 0:
        return False
    if downloadable is not True or report_context_eligible is not False:
        return False
    if not isinstance(download_ref, str):
        return False
    digest = download_ref.removeprefix("result-archive:")
    return (
        download_ref.startswith("result-archive:")
        and _ARCHIVE_DIGEST.fullmatch(digest) is not None
    )


def _is_valid_delivery_basics(delivery: ResultDelivery) -> bool:
    """Return whether delivery fields independent of status are valid."""
    return (
        _is_plain_int(delivery.schema_version)
        and delivery.schema_version == 1
        and delivery.required is True
        and isinstance(delivery.status, str)
        and delivery.status in _DELIVERY_STATUSES
        and _is_plain_int(delivery.revision)
        and delivery.revision >= 1
        and _is_valid_delivery_digest(delivery.inventory_digest)
        and isinstance(delivery.retryable, bool)
        and (
            delivery.archive is None
            or isinstance(delivery.archive, ResultArchiveDescriptor)
        )
        and _is_valid_delivery_error(delivery.error_code)
    )


def _is_valid_delivery_digest(value: object) -> bool:
    """Return whether an optional delivery inventory digest is valid."""
    return isinstance(value, str) and (
        not value or _ARCHIVE_DIGEST.fullmatch(value) is not None
    )


def _is_valid_delivery_error(value: object) -> bool:
    """Return whether an optional public delivery error code is valid."""
    return value is None or (isinstance(value, str) and bool(value))


def _is_valid_delivery_state(delivery: ResultDelivery) -> bool:
    """Return whether status-dependent delivery fields agree."""
    if delivery.status == "pending":
        return (
            delivery.archive is None
            and delivery.error_code is None
            and not delivery.retryable
        )
    if delivery.status == "ready":
        return _is_valid_ready_delivery(delivery)
    return _is_valid_failed_delivery(delivery)


def _is_valid_ready_delivery(delivery: ResultDelivery) -> bool:
    """Return whether a ready delivery has its matching archive."""
    if (
        not delivery.inventory_digest
        or delivery.archive is None
        or delivery.error_code is not None
        or delivery.retryable
    ):
        return False
    return delivery.archive.download_ref == (
        f"result-archive:{delivery.inventory_digest}"
    )


def _is_valid_failed_delivery(delivery: ResultDelivery) -> bool:
    """Return whether a failed delivery has a public error code."""
    return (
        delivery.archive is None
        and delivery.error_code is not None
        and (bool(delivery.inventory_digest) or not delivery.retryable)
    )


@dataclass(frozen=True)
class FormattedToolResult:
    """Normalized client-facing representation of one tool response."""

    answer: str
    follow_up_questions: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    references: tuple[Mapping[str, Any], ...] = ()
    tabular: Mapping[str, Any] | None = None
    output_dirs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportExecution:
    """Safe report assembly state kept outside scientific display fields."""

    state: Literal["none", "intermediate", "final", "degraded"] = "none"
    degraded: bool = False
    source_artifact_count: int = 0


@dataclass(frozen=True, slots=True)
class ResultArchiveDescriptor:
    """Public, resolver-safe descriptor for one delivered result archive."""

    role: Literal["result_archive"]
    name: str
    media_type: Literal["application/zip"]
    size_bytes: int
    downloadable: bool
    report_context_eligible: bool
    download_ref: str

    def __post_init__(self) -> None:
        if not _is_valid_archive_identity(
            self.role,
            self.name,
            self.media_type,
        ) or not _is_valid_archive_access(
            self.size_bytes,
            self.downloadable,
            self.report_context_eligible,
            self.download_ref,
        ):
            raise ValueError("invalid result archive descriptor")


@dataclass(frozen=True, slots=True)
class _ResultDeliveryState:
    """Stable delivery fields that precede optional archive state."""

    schema_version: Literal[1]
    required: bool
    status: Literal["pending", "ready", "failed"]
    revision: int
    inventory_digest: str


@dataclass(frozen=True, slots=True)
class ResultDelivery(_ResultDeliveryState):
    """Canonical archive-delivery state for a report-producing run."""

    archive: ResultArchiveDescriptor | None
    error_code: str | None
    retryable: bool

    def __post_init__(self) -> None:
        if not _is_valid_delivery_basics(self) or not _is_valid_delivery_state(
            self
        ):
            raise ValueError("invalid result delivery state")


@dataclass(frozen=True, slots=True)
class _ExecutionProjectionState:
    """Core execution fields retained in every result projection."""

    tracking: Mapping[str, Any] = field(
        default_factory=lambda: {"degraded": False}
    )
    warnings: tuple[ExecutionWarning, ...] = ()
    tasks: tuple[Mapping[str, Any], ...] = ()
    artifacts: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionProjection(_ExecutionProjectionState):
    """Canonical operational projection for one normalized tool result."""

    output_dirs: tuple[str, ...] = ()
    report: ReportExecution | None = None
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    delivery: ResultDelivery | None = None


@dataclass(frozen=True)
class ToolResultEnvelope:
    """Full tool response carrying display, execution, and raw payloads."""

    formatted: FormattedToolResult
    raw: Any
    execution: ExecutionProjection = field(default_factory=ExecutionProjection)


@dataclass(frozen=True)
class FormattedToolChunk:
    """One streamed provider chunk emitted by ``invoke_tool_streamed``."""

    payload: Mapping[str, Any]


@dataclass(frozen=True)
class AguiEvent:
    """One AG-UI SSE event frame with a redundant payload type key."""

    type: str
    data: Mapping[str, Any]


def format_tool_chunk(payload: Mapping[str, Any]) -> FormattedToolChunk:
    """Wrap one streamed chunk without copying or mutating its payload."""
    return FormattedToolChunk(payload=payload)


__all__ = [
    "AguiEvent",
    "ExecutionProjection",
    "ExecutionWarning",
    "FormattedToolChunk",
    "FormattedToolResult",
    "ReportExecution",
    "ResultArchiveDescriptor",
    "ResultDelivery",
    "ToolResultEnvelope",
    "format_tool_chunk",
]
