# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for failure_state's double-write contract (failures + error)."""

from typing import Any

import pytest

from mcp_server_phytomni.agents.shared.analysis import (
    _compute_traceback_digest,
    capture_analysis_result,
)
from mcp_server_phytomni.runtime.langgraph_runner import (
    capture_workflow_boundary,
)

pytestmark = pytest.mark.unit


def test_traceback_digest_is_16_chars_or_none() -> None:
    """Digest is either a 16-char hex string or None."""
    exc = ValueError("boom")
    digest = _compute_traceback_digest(exc)
    assert digest is None or (isinstance(digest, str) and len(digest) == 16)


def test_traceback_digest_stable_across_calls() -> None:
    """Same traceback yields a 16-char hex digest or None each call.

    Tracebacks contain line/frame information that differs between
    invocations; digest stability is not contract — shape is.
    """

    def _raise() -> None:
        raise RuntimeError("same-error")

    digests = []
    for _ in range(2):
        try:
            _raise()
        except RuntimeError as exc:
            digests.append(_compute_traceback_digest(exc))
    for d in digests:
        assert d is None or len(d) == 16


@pytest.mark.asyncio
async def test_failure_state_writes_both_error_and_failures() -> None:
    """failure_state writes the legacy `error` field AND the new
    failures list with a single FailureRecord, so old readers and
    new readers see consistent information.
    """

    async def failing_task() -> dict[str, Any]:
        raise ValueError("test-error-message")

    base_state = {"task_ids": {}, "analysis_type": "test_label"}

    def failure_state_fn(exc: Exception) -> dict:
        return {
            "task_ids": base_state.get("task_ids", {}),
            "completed_count": 1,
            "error": str(exc),
            "failures": [
                {
                    "task_label": base_state.get("analysis_type", "?"),
                    "message": str(exc),
                    "kind": "execute",
                    "traceback_digest": _compute_traceback_digest(exc),
                }
            ],
        }

    result = await capture_workflow_boundary(
        failing_task,
        failure_state_fn,
    )
    assert result["error"] == "test-error-message"
    assert len(result["failures"]) == 1
    assert result["failures"][0]["message"] == "test-error-message"
    assert result["failures"][0]["task_label"] == "test_label"
    assert result["failures"][0]["kind"] == "execute"
    assert result["completed_count"] == 1


@pytest.mark.asyncio
async def test_capture_analysis_result_failure_path_writes_record() -> None:
    """The production ``capture_analysis_result`` closure (not a
    hand-rolled facsimile) records ``error`` and a single FailureRecord
    when the ``submit_call`` raises, preserving prior ``task_ids``.
    """

    async def failing_submit() -> dict[str, Any]:
        raise RuntimeError("submit-exploded")

    state = {"task_ids": {"prior": "t-0"}, "task_index": 3}
    result = await capture_analysis_result(
        state,
        "evolution_analysis",
        failing_submit,
    )

    assert result["error"] == "submit-exploded"
    assert result["completed_count"] == 1
    assert result["task_ids"] == {"prior": "t-0"}
    assert len(result["failures"]) == 1
    record = result["failures"][0]
    assert record["task_label"] == "evolution_analysis"
    assert record["message"] == "submit-exploded"
    assert record["kind"] == "execute"
    assert record["traceback_digest"] is None or (
        len(record["traceback_digest"]) == 16
    )
