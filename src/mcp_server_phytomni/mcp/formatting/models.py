# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed models for MCP result and streaming formatting."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FormattedToolResult:
    """Normalized client-facing representation of one tool response."""

    answer: str
    follow_up_questions: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    references: tuple[Mapping[str, Any], ...] = ()
    tabular: Mapping[str, Any] | None = None
    output_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResultEnvelope:
    """Full tool response carrying display and sanitized raw payloads."""

    formatted: FormattedToolResult
    raw: Any


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
    "FormattedToolChunk",
    "FormattedToolResult",
    "ToolResultEnvelope",
    "format_tool_chunk",
]
