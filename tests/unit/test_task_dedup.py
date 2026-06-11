# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the shared analyst dedup helpers.

Pins ``analyst_task_fingerprint`` (input-identity invariants) and
``should_reuse_prior_task`` (case-insensitive status matrix) after they
moved out of ``agents/analyst/submission.py`` into the shared
``runtime/task_dedup.py`` consumed by both analyst entry points.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.task_dedup import (
    analyst_task_fingerprint,
    should_reuse_prior_task,
)

pytestmark = pytest.mark.unit


def test_fingerprint_is_stable_across_data_list_insertion_order() -> None:
    """Dict insertion order MUST NOT affect the fingerprint."""
    fp_a = analyst_task_fingerprint(
        goal_description="goal",
        data_list={"a.csv": "first", "b.csv": "second"},
        obs_file_list=None,
    )
    fp_b = analyst_task_fingerprint(
        goal_description="goal",
        data_list={"b.csv": "second", "a.csv": "first"},
        obs_file_list=None,
    )
    assert fp_a == fp_b


def test_fingerprint_is_stable_across_obs_file_list_order() -> None:
    """OBS upload order MUST NOT affect the fingerprint."""
    fp_a = analyst_task_fingerprint(
        goal_description="goal",
        data_list={},
        obs_file_list=["/obs/x.txt", "/obs/y.txt"],
    )
    fp_b = analyst_task_fingerprint(
        goal_description="goal",
        data_list={},
        obs_file_list=["/obs/y.txt", "/obs/x.txt"],
    )
    assert fp_a == fp_b


def test_fingerprint_treats_none_obs_list_as_empty() -> None:
    """``obs_file_list=None`` MUST hash the same as an empty list."""
    fp_none = analyst_task_fingerprint(
        goal_description="goal", data_list={}, obs_file_list=None
    )
    fp_empty = analyst_task_fingerprint(
        goal_description="goal", data_list={}, obs_file_list=[]
    )
    assert fp_none == fp_empty


def test_fingerprint_changes_with_goal_description() -> None:
    """A different research goal MUST yield a different fingerprint."""
    fp_a = analyst_task_fingerprint(
        goal_description="goal A", data_list={}, obs_file_list=None
    )
    fp_b = analyst_task_fingerprint(
        goal_description="goal B", data_list={}, obs_file_list=None
    )
    assert fp_a != fp_b


def test_fingerprint_changes_with_data_list_description() -> None:
    """A different per-file description MUST yield a different digest."""
    fp_a = analyst_task_fingerprint(
        goal_description="goal",
        data_list={"a.csv": "before"},
        obs_file_list=None,
    )
    fp_b = analyst_task_fingerprint(
        goal_description="goal",
        data_list={"a.csv": "after"},
        obs_file_list=None,
    )
    assert fp_a != fp_b


def test_fingerprint_is_sha256_hex() -> None:
    """Digest is a 64-char lower-hex SHA-256 (not a salted hash())."""
    fp = analyst_task_fingerprint(
        goal_description="x", data_list={}, obs_file_list=None
    )
    assert len(fp) == 64
    assert set(fp) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    "status",
    ["submitted", "running", "pending", "SUCCEEDED", "Done", "completed"],
)
def test_should_reuse_true_for_in_flight_or_succeeded(status: str) -> None:
    """In-flight + succeeded statuses pass the cheap reuse gate."""
    assert should_reuse_prior_task(status) is True


@pytest.mark.parametrize(
    "status",
    [
        "failed",
        "cancelled",
        "error",
        "failed_at_agent_level",
        "unknown",
        "",
    ],
)
def test_should_reuse_false_for_dead_or_unknown(status: str) -> None:
    """Failed / cancelled / unknown statuses fail the gate (resubmit)."""
    assert should_reuse_prior_task(status) is False
