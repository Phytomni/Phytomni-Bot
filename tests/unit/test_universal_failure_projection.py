# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for project_universal_failure_metadata."""

import pytest

from mcp_server_phytomni.mcp.result_formatting import (
    project_universal_failure_metadata,
)

pytestmark = pytest.mark.unit


def test_status_pending_when_both_empty() -> None:
    """No tasks dispatched yet -> PENDING."""
    result = project_universal_failure_metadata({})
    assert result == {
        "status": "PENDING",
        "succeeded_count": 0,
        "failed_count": 0,
        "failures": [],
    }


def test_status_success_when_no_failures() -> None:
    """All tasks succeeded -> SUCCESS, no failures listed."""
    result = project_universal_failure_metadata(
        {
            "task_ids": {"a": "1", "b": "2"},
            "failures": [],
        }
    )
    assert result["status"] == "SUCCESS"
    assert result["succeeded_count"] == 2
    assert result["failed_count"] == 0
    assert result["failures"] == []


def test_status_partial_when_mixed() -> None:
    """Some succeed, some fail -> PARTIAL."""
    result = project_universal_failure_metadata(
        {
            "task_ids": {"a": "1"},
            "failures": [
                {
                    "task_label": "b",
                    "message": "boom",
                    "kind": "execute",
                    "traceback_digest": "0123456789abcdef",
                }
            ],
        }
    )
    assert result["status"] == "PARTIAL"
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1
    assert result["failures"] == [
        {"task_label": "b", "kind": "execute", "message": "boom"},
    ]


def test_status_failed_when_all_fail() -> None:
    """All tasks failed -> FAILED, succeeded_count=0."""
    result = project_universal_failure_metadata(
        {
            "task_ids": {},
            "failures": [
                {
                    "task_label": "a",
                    "message": "m1",
                    "kind": "execute",
                    "traceback_digest": None,
                },
                {
                    "task_label": "b",
                    "message": "m2",
                    "kind": "execute",
                    "traceback_digest": None,
                },
            ],
        }
    )
    assert result["status"] == "FAILED"
    assert result["succeeded_count"] == 0
    assert result["failed_count"] == 2


def test_traceback_digest_stripped_from_output() -> None:
    """traceback_digest must NEVER appear in the projected list."""
    result = project_universal_failure_metadata(
        {
            "task_ids": {},
            "failures": [
                {
                    "task_label": "x",
                    "message": "y",
                    "kind": "execute",
                    "traceback_digest": "0123456789abcdef",
                }
            ],
        }
    )
    assert "traceback_digest" not in result["failures"][0]
    assert set(result["failures"][0].keys()) == {
        "task_label",
        "kind",
        "message",
    }


def test_none_inputs_treated_as_empty() -> None:
    """task_ids=None and failures=None both safe (defensive)."""
    result = project_universal_failure_metadata(
        {
            "task_ids": None,
            "failures": None,
        }
    )
    assert result["status"] == "PENDING"
