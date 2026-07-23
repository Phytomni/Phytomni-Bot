# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Neutral DeepGenome task-state builders for offline E2E contracts."""

from __future__ import annotations

from e2e.helpers.polling import TaskState

__all__ = ["build_task_state"]


def build_task_state() -> TaskState:
    """Build a complete public task snapshot without assertion logic."""
    return TaskState(
        task_id="task-1",
        status="failed",
        analysis_id="",
        output_dir="",
        intermediate_report="# profile",
        final_report=None,
        report_stage="intermediate",
        report_completeness="partial",
        report_revision=2,
        report_updated_at=None,
        progress={"brief_gene_status": "succeeded"},
        degraded=True,
        degraded_reason="1 of 12 optional analyses unavailable",
        brief_gene_status="succeeded",
        failures=(),
        artifacts=(),
        output_dirs=(),
    )
