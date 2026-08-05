# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed models for MCP result and streaming formatting."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ...runtime.execution_models import ExecutionWarning


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
        if (
            not self.name.endswith("-results.zip")
            or "/" in self.name
            or "\\" in self.name
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
            or not self.download_ref.startswith("result-archive:sha256:")
        ):
            raise ValueError("invalid result archive descriptor")


@dataclass(frozen=True, slots=True)
class ResultDelivery:
    """Canonical archive-delivery state for a report-producing run."""

    schema_version: Literal[1]
    required: bool
    status: Literal["pending", "ready", "failed"]
    revision: int
    inventory_digest: str
    archive: ResultArchiveDescriptor | None
    error_code: str | None
    retryable: bool

    def __post_init__(self) -> None:
        digest_present = bool(self.inventory_digest)
        digest_valid = self.inventory_digest.startswith("sha256:") and len(
            self.inventory_digest
        ) == 71
        if (
            self.revision < 1
            or (digest_present and not digest_valid)
            or (self.status == "ready" and not digest_valid)
            or (self.status == "pending" and (self.archive is not None or self.error_code is not None or self.retryable))
            or (self.status == "ready" and (self.archive is None or self.error_code is not None or self.retryable))
            or (self.status == "failed" and (self.archive is not None or not self.error_code))
        ):
            raise ValueError("invalid result delivery state")


@dataclass(frozen=True, slots=True)
class ExecutionProjection:
    """Canonical operational projection for one normalized tool result."""

    tracking: Mapping[str, Any] = field(
        default_factory=lambda: {"degraded": False}
    )
    warnings: tuple[ExecutionWarning, ...] = ()
    tasks: tuple[Mapping[str, Any], ...] = ()
    artifacts: tuple[Mapping[str, Any], ...] = ()
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
