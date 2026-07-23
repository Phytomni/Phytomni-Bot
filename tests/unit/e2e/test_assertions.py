# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline contracts for live terminal-report assertions."""

from __future__ import annotations

from dataclasses import replace

import pytest
from e2e.helpers.assertions import (
    assert_deep_genome_terminal,
    assert_terminal_report_and_artifacts,
)
from tests.unit.e2e.state_fakes import build_task_state


def test_brief_gene_failure_accepts_no_report() -> None:
    """Required-profile failure is terminal without any report."""
    state = replace(
        build_task_state(),
        intermediate_report=None,
        report_stage="waiting_for_brief_gene",
        report_completeness="none",
        report_revision=1,
        brief_gene_status="failed",
        degraded=False,
        degraded_reason=None,
    )

    assert_deep_genome_terminal(state)


def test_post_profile_failure_requires_intermediate_report() -> None:
    """A failure after BriefGene must retain the best report snapshot."""
    state = replace(build_task_state(), intermediate_report=None)

    with pytest.raises(AssertionError):
        assert_deep_genome_terminal(state)


def test_success_requires_final_report_and_artifact_paths() -> None:
    """Successful analyst-class runs need a final report and paths."""
    state = replace(
        build_task_state(),
        status="succeeded",
        intermediate_report="# profile",
        final_report="# final",
        report_stage="final",
        report_completeness="complete",
        report_revision=3,
        degraded=False,
        degraded_reason=None,
        artifacts=({"paths": ("/obs/result.txt",)},),
        output_dirs=("/obs/output",),
    )

    assert_deep_genome_terminal(state)
    assert_terminal_report_and_artifacts(state, needs_artifacts=True)
    assert_terminal_report_and_artifacts(
        replace(state, artifacts=(), output_dirs=()), needs_artifacts=False
    )


def test_artifact_requirement_rejects_directoryless_success() -> None:
    """Design/Network acceptance must not pass on a report alone."""
    state = replace(
        build_task_state(),
        status="succeeded",
        final_report="# final",
        report_stage="final",
        report_completeness="complete",
        report_revision=3,
    )

    with pytest.raises(AssertionError):
        assert_terminal_report_and_artifacts(state, needs_artifacts=True)
