# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for typed remote submission outcomes."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mcp_server_phytomni.runtime.submission_outcome import (
    AcceptedSubmission,
    RejectedSubmission,
    classify_submissions,
)

pytestmark = pytest.mark.unit


def test_partial_submission_keeps_only_real_ids() -> None:
    """Partial outcomes expose accepted IDs and structured warnings."""
    outcome = classify_submissions(
        accepted=[
            AcceptedSubmission(task_id="task-1", output_dir="tenant/out")
        ],
        rejected=[
            RejectedSubmission(goal="second goal", code="upstream_rejected")
        ],
    )

    assert outcome.kind == "partial"
    assert outcome.task_ids == ("task-1",)
    assert outcome.warnings == (
        {
            "code": "partial_submission",
            "retryable": False,
            "rejected_count": 1,
        },
    )


def test_full_and_rejected_outcomes_have_no_warnings() -> None:
    """Only mixed accepted/rejected submissions are degraded warnings."""
    accepted = AcceptedSubmission(task_id="task-1", output_dir="tenant/out")
    rejected = RejectedSubmission(goal="goal", code="upstream_rejected")

    assert (
        classify_submissions(accepted=[accepted], rejected=[]).kind == "full"
    )
    assert classify_submissions(accepted=[], rejected=[rejected]).kind == (
        "rejected"
    )
    assert not classify_submissions(accepted=[accepted], rejected=[]).warnings
    assert not classify_submissions(accepted=[], rejected=[rejected]).warnings


def test_accepted_submission_rejects_blank_task_id_and_is_frozen() -> None:
    """A blank identity cannot enter the accepted-task context."""
    with pytest.raises(ValueError, match="nonblank"):
        AcceptedSubmission(task_id="   ", output_dir="tenant/out")

    accepted = AcceptedSubmission(task_id="task-1", output_dir="tenant/out")
    with pytest.raises(FrozenInstanceError):
        setattr(accepted, "task_id", "task-2")
