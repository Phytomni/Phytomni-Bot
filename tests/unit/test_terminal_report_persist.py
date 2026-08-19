# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Persistence and artifact-reference helpers for terminal reports."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime import terminal_report as report_mod
from mcp_server_phytomni.runtime.terminal_report import (
    TerminalReportResult,
    persist_terminal_report,
)

pytestmark = pytest.mark.unit


def test_persist_terminal_report_records_storage_failure() -> None:
    """A TaskManager OSError degrades the live row without leaking details."""

    class _BoomManager:
        def set_task_final_report(self, *_args: object) -> bool:
            """Fail persistence so the live row is marked degraded."""
            raise OSError("disk")

        def set_task_degraded(self, *_args: object) -> bool:
            """Record the degraded reason on the live row."""
            return True

    live = [{"task_id": "task-1", "status": "succeeded"}]
    persist_terminal_report(
        live,
        TerminalReportResult(
            final_report="report",
            answer="answer",
            degraded=True,
            degraded_reason="report_synthesis_failed",
        ),
        task_manager=_BoomManager(),
    )
    assert live[0]["degraded_reason"] == (
        "terminal report persistence failed: OSError"
    )


def test_bounded_utf8_text_applies_byte_and_char_caps() -> None:
    """UTF-8 byte truncation and leftover character caps are both applied."""
    text, truncated = getattr(report_mod, "_bounded_utf8_text")(
        "éééé", max_bytes=3, max_chars=10
    )
    assert truncated
    text, truncated = getattr(report_mod, "_bounded_utf8_text")(
        "abcdef", max_bytes=100, max_chars=3
    )
    assert truncated
    assert text == "abc"


def test_artifact_name_and_reference_fallbacks() -> None:
    """Name falls back to a generic label; missing refs raise OSError."""
    assert (
        getattr(report_mod, "_artifact_name")({"role": "scientific_report"})
        == "scientific-artifact"
    )
    with pytest.raises(OSError, match="artifact source reference unavailable"):
        getattr(report_mod, "_artifact_read_reference")(
            {"role": "scientific_report"}
        )
    assert (
        getattr(report_mod, "_artifact_read_reference")(
            {"download_ref": "owner/file.md"}
        )
        == "owner/file.md"
    )
