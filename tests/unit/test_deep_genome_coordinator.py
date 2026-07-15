# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for DeepGenome remote-submission identities."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.deep_genome.coordinator import (
    SubmissionProtocolError,
    normalize_submission,
)

pytestmark = pytest.mark.unit


def test_normalize_submission_preserves_dedup_source_identity() -> None:
    """Keep caller and effective remote polling ids separate."""
    normalized = normalize_submission(
        {
            "task_id": "caller-2",
            "source_task_id": "source-1",
            "output_dir": "obs://bucket/out",
        }
    )
    assert normalized.submitted_task_id == "caller-2"
    assert normalized.poll_task_id == "source-1"
    assert normalized.output_dir == "obs://bucket/out"


@pytest.mark.parametrize("field", ["task_id", "output_dir"])
def test_normalize_submission_rejects_missing_required_field(
    field: str,
) -> None:
    """Reject incomplete platform acknowledgements without echoing payloads."""
    payload = {"task_id": "task-1", "output_dir": "obs://bucket/out"}
    payload.pop(field)
    with pytest.raises(
        SubmissionProtocolError,
        match="invalid analysis submission",
    ):
        normalize_submission(payload)
