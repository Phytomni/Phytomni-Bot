# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the shared degraded_labels helper."""

import pytest

from mcp_server_phytomni.agents.shared.parallel_dispatch import (
    DegradedRecord,
    degraded_labels,
)

pytestmark = pytest.mark.unit


def test_degraded_labels_dedupes_and_sorts() -> None:
    """``degraded_labels`` deduplicates task_label and returns sorted list."""
    records = [
        DegradedRecord(task_label="OsB", message="boom"),
        DegradedRecord(task_label="OsA", message="boom"),
        DegradedRecord(task_label="OsB", message="boom2"),
    ]
    assert degraded_labels(records) == ["OsA", "OsB"]


def test_degraded_labels_empty_returns_empty() -> None:
    """``degraded_labels`` on an empty list returns an empty list."""
    assert degraded_labels([]) == []
