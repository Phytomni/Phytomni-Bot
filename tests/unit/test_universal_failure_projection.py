# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for project_universal_failure_metadata."""

import pytest

from mcp_server_phytomni.mcp.result_formatting import (
    project_universal_failure_metadata,
)
from mcp_server_phytomni.mcp.universal_failures import (
    project_interop_metadata,
    redact_failure_message,
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


def test_redact_failure_message_strips_backend_url() -> None:
    """A backend URL (host/port/path) is replaced, error text kept."""
    raw = (
        "Connection failed for url "
        "'https://retrieve.internal.phytomni.cn:8443/v1/retrieve?token=abc'"
    )
    redacted = redact_failure_message(raw)
    assert "retrieve.internal.phytomni.cn" not in redacted
    assert "8443" not in redacted
    assert "abc" not in redacted
    assert "<redacted-url>" in redacted
    assert redacted.startswith("Connection failed for url")


def test_redact_failure_message_strips_secret_fragments() -> None:
    """Bare credential fragments (key=, Bearer) are scrubbed."""
    raw = "auth error: token=sk-secret-9f8a7b; Authorization: Bearer Zm9vYmF6"
    redacted = redact_failure_message(raw)
    assert "sk-secret-9f8a7b" not in redacted
    assert "Zm9vYmF6" not in redacted
    assert "<redacted-secret>" in redacted


def test_redact_failure_message_leaves_clean_text() -> None:
    """A message with no URL or secret is returned unchanged."""
    raw = "ValueError: gene id not found in species index"
    assert redact_failure_message(raw) == raw


def test_project_interop_metadata_is_bounded_and_secret_free() -> None:
    """Only the safe delegation summary reaches formatted metadata."""
    result = project_interop_metadata(
        {
            "interop": [
                {
                    "target_id": "peer",
                    "kind": "a2a",
                    "capability": "research",
                    "status": "completed",
                    "latency_ms": 12.5,
                    "url": "https://peer.example.test/private",
                    "token": "secret",
                },
                {"kind": "unknown", "status": "completed"},
            ],
            "degraded_interop": True,
        }
    )

    assert result == {
        "interop": [
            {
                "target_id": "peer",
                "kind": "a2a",
                "capability": "research",
                "status": "completed",
                "latency_ms": 12.5,
            }
        ],
        "degraded_interop": True,
    }


def test_project_interop_metadata_clamps_invalid_latency() -> None:
    """Malformed latency cannot produce non-finite client metadata."""
    result = project_interop_metadata(
        {
            "interop": [
                {
                    "target_id": "peer",
                    "kind": "mcp",
                    "capability": "design",
                    "status": "failed",
                    "latency_ms": float("inf"),
                }
            ]
        }
    )
    assert result["interop"][0]["latency_ms"] == 0.0


def test_projection_redacts_message_in_failures_list() -> None:
    """The projected failures[].message is redacted, not raw str(exc)."""
    result = project_universal_failure_metadata(
        {
            "task_ids": {},
            "failures": [
                {
                    "task_label": "draft:2",
                    "message": (
                        "HTTPStatusError for "
                        "https://bi.internal.phytomni.cn/api/data"
                    ),
                    "kind": "execute",
                    "traceback_digest": None,
                }
            ],
        }
    )
    message = result["failures"][0]["message"]
    assert "bi.internal.phytomni.cn" not in message
    assert "<redacted-url>" in message
