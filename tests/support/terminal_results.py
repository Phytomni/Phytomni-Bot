# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral sensitive terminal-result fixtures for projection tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "SensitiveTerminalResultSpec",
    "public_partial_warning",
    "public_report_projection",
    "public_scientific_table_artifact",
    "sensitive_terminal_result",
]


def public_report_projection() -> dict[str, Any]:
    """Return the stable public report projection used by lifecycle tests."""
    return {
        "role": "scientific_report",
        "state": "complete",
        "artifact_id": "report-safe",
        "mime_type": "application/json",
        "size_bytes": 12,
    }


def public_partial_warning() -> dict[str, Any]:
    """Return the public warning retained by terminal projection."""
    return {
        "code": "partial",
        "stage": "projection",
        "retryable": False,
        "count": 1,
    }


def public_scientific_table_artifact() -> dict[str, Any]:
    """Return the public artifact retained by terminal projection."""
    return {
        "role": "scientific_table",
        "name": "result.tsv",
        "mime_type": "text/tab-separated-values",
        "size_bytes": 42,
    }


@dataclass(frozen=True)
class SensitiveTerminalResultSpec:
    """Public values varied by nested terminal-projection scenarios."""

    answer: str
    task_id: str
    citation: tuple[str, str]
    table: tuple[list[str], list[list[Any]]]
    warning: tuple[str, str]


def sensitive_terminal_result(
    spec: SensitiveTerminalResultSpec,
) -> dict[str, Any]:
    """Build a nested result with public and private projection fields."""
    citation_key, citation_value = spec.citation
    table_headers, table_rows = spec.table
    warning_key, warning_value = spec.warning
    return {
        "provider_trace": "preserved",
        "raw": {"private_path": "/srv/private"},
        "formatted": {
            "answer": spec.answer,
            "follow_up_questions": ["next?"],
            "references": [
                {
                    "file_id": "doc-1",
                    "title": "Public title",
                    citation_key: citation_value,
                    "provider_payload": {"trace": "private"},
                }
            ],
            "tabular": {
                "headers": table_headers,
                "rows": table_rows,
                "provider_trace": "private",
            },
            "metadata": {
                "original_query": "public query",
                "provider_payload": {"trace": "private"},
            },
        },
        "execution": {
            "tracking": {
                "degraded": False,
                "provider_payload": {"trace": "private"},
            },
            "warnings": [
                {
                    **public_partial_warning(),
                    warning_key: warning_value,
                }
            ],
            "tasks": [
                {
                    "id": spec.task_id,
                    "accepted": True,
                    "status": "succeeded",
                    "provider_trace": "private",
                }
            ],
            "artifacts": [
                {
                    **public_scientific_table_artifact(),
                    "provider_payload": {"trace": "private"},
                }
            ],
            "output_dirs": [
                "/obs/public/result",
                {"private": "value"},
            ],
            "report": {
                "role": "scientific_report",
                "state": "complete",
                "artifact_id": "report-safe",
                "mime_type": "application/json",
                "size_bytes": 12,
                "provider_trace": "private",
            },
            "diagnostics": [
                {
                    "code": "upstream_partial",
                    "stage": "analysis",
                    "retryable": False,
                    "provider_trace": "private",
                }
            ],
            "provider_payload": {"trace": "private"},
        },
    }
