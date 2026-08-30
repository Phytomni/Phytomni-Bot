# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for memory-class compute-resource relaunch helpers."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.shared.memory_relaunch import (
    START_COMPUTE_RESOURCE,
    TIERS,
    is_memory_class_failure,
    next_compute_resource,
)

pytestmark = pytest.mark.unit


def test_start_compute_resource_and_tiers() -> None:
    """The first persist always starts on the small compute tier."""
    assert START_COMPUTE_RESOURCE == "small"
    assert TIERS == ("small", "medium", "large")


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("small", "medium"),
        ("medium", "large"),
        ("large", None),
        ("tiny", None),
        ("SMALL", None),
        ("", None),
    ],
)
def test_next_compute_resource_advances_known_tiers(
    current: str, expected: str | None
) -> None:
    """Known tiers move one step; unknown names cannot relaunch."""
    assert next_compute_resource(current) is expected


@pytest.mark.parametrize(
    ("status_payload", "log_payload"),
    [
        (None, None),
        ({}, None),
        ({"status": "FAILED"}, None),
        ({"status": "FAILED"}, {"logs": []}),
        ({"status": "CANCELLED"}, None),
        (
            {"status": "FAILED", "message": "ValueError missing column"},
            None,
        ),
        ({"status": "FAILED"}, {"text": "ValueError missing column"}),
    ],
)
def test_is_memory_class_failure_rejects_non_memory_payloads(
    status_payload: object, log_payload: object
) -> None:
    """A bare FAILED/CANCELLED status is not a memory-class failure."""
    assert is_memory_class_failure(status_payload, log_payload) is False


@pytest.mark.parametrize(
    "token",
    [
        "MemoryError",
        "out of memory",
        "OOM",
        "Killed",
        "Cannot allocate",
        "std::bad_alloc",
        "exit code 137",
        "signal 9",
    ],
)
def test_is_memory_class_failure_matches_each_token(token: str) -> None:
    """Each documented token is a case-insensitive substring match."""
    assert is_memory_class_failure({"message": token}) is True
    assert is_memory_class_failure({"message": token.upper()}) is True
    assert is_memory_class_failure({"message": token.lower()}) is True


def test_is_memory_class_failure_reads_logs_and_text() -> None:
    """Log chunks and the projected text field join the same haystack."""
    assert (
        is_memory_class_failure(
            {"status": "FAILED"},
            {"logs": [{"content": "worker hit OOM during merge"}]},
        )
        is True
    )
    assert (
        is_memory_class_failure(
            {"status": "FAILED"},
            {"text": "std::bad_alloc in the solver"},
        )
        is True
    )
    assert (
        is_memory_class_failure(
            {
                "status": "FAILED",
                "logs": [{"content": "Killed"}],
            }
        )
        is True
    )
