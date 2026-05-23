# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for analyst/submission.py pure helpers.

Pins ``_analyst_task_fingerprint`` (input-identity invariants),
``_should_reuse_prior_task`` (case-insensitive status matrix), and
``_submit_user_and_thread_id`` (RunIdentity-fallback branch).
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.analyst.submission import (
    _analyst_task_fingerprint,
    _should_reuse_prior_task,
    _submit_user_and_thread_id,
)

pytestmark = pytest.mark.unit


def test_fingerprint_is_stable_across_data_list_insertion_order() -> None:
    """Dict insertion order MUST NOT affect the fingerprint."""
    fp_a = _analyst_task_fingerprint(
        goal_description="goal",
        data_list={"a.csv": "first", "b.csv": "second"},
        obs_file_list=None,
    )
    fp_b = _analyst_task_fingerprint(
        goal_description="goal",
        data_list={"b.csv": "second", "a.csv": "first"},
        obs_file_list=None,
    )

    assert fp_a == fp_b


def test_fingerprint_is_stable_across_obs_file_list_order() -> None:
    """OBS upload order MUST NOT affect the fingerprint."""
    fp_a = _analyst_task_fingerprint(
        goal_description="goal",
        data_list={},
        obs_file_list=["/obs/x.txt", "/obs/y.txt"],
    )
    fp_b = _analyst_task_fingerprint(
        goal_description="goal",
        data_list={},
        obs_file_list=["/obs/y.txt", "/obs/x.txt"],
    )

    assert fp_a == fp_b


def test_fingerprint_treats_none_obs_list_as_empty() -> None:
    """``obs_file_list=None`` MUST hash the same as an empty list."""
    fp_none = _analyst_task_fingerprint(
        goal_description="goal", data_list={}, obs_file_list=None
    )
    fp_empty = _analyst_task_fingerprint(
        goal_description="goal", data_list={}, obs_file_list=[]
    )

    assert fp_none == fp_empty


def test_fingerprint_changes_with_goal_description() -> None:
    """A different research goal MUST yield a different fingerprint."""
    fp_a = _analyst_task_fingerprint(
        goal_description="goal A", data_list={}, obs_file_list=None
    )
    fp_b = _analyst_task_fingerprint(
        goal_description="goal B", data_list={}, obs_file_list=None
    )

    assert fp_a != fp_b


def test_fingerprint_changes_with_data_list_description() -> None:
    """A different per-file description MUST yield a different fingerprint."""
    fp_a = _analyst_task_fingerprint(
        goal_description="goal",
        data_list={"a.csv": "before"},
        obs_file_list=None,
    )
    fp_b = _analyst_task_fingerprint(
        goal_description="goal",
        data_list={"a.csv": "after"},
        obs_file_list=None,
    )

    assert fp_a != fp_b


@pytest.mark.parametrize(
    "status",
    [
        "submitted",
        "running",
        "pending",
        "SUBMITTED",
        "Running",
    ],
)
def test_should_reuse_returns_true_for_in_flight_status(status: str) -> None:
    """In-flight statuses MUST short-circuit a fresh submission."""
    assert _should_reuse_prior_task(status) is True


@pytest.mark.parametrize(
    "status",
    [
        "succeeded",
        "success",
        "completed",
        "done",
        "SUCCEEDED",
        "Done",
    ],
)
def test_should_reuse_returns_true_for_succeeded_status(status: str) -> None:
    """Succeeded statuses MUST hand the caller the prior task directly."""
    assert _should_reuse_prior_task(status) is True


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
def test_should_reuse_returns_false_for_terminal_or_unknown(
    status: str,
) -> None:
    """Failed / cancelled / unknown statuses MUST trigger fresh submission."""
    assert _should_reuse_prior_task(status) is False


def test_submit_user_and_thread_id_uses_user_id_when_present() -> None:
    """A populated user id is returned verbatim for both user and thread."""
    user_id, thread_id = _submit_user_and_thread_id(
        "alice", scope="analyst-submit", operation="submit"
    )

    assert user_id == "alice"
    assert thread_id == "alice"


def test_submit_user_and_thread_id_coerces_non_string_user_id() -> None:
    """Non-string user ids (e.g. ints) are coerced to str for both fields."""
    user_id, thread_id = _submit_user_and_thread_id(
        42, scope="analyst-submit", operation="submit"
    )

    assert user_id == "42"
    assert thread_id == "42"


def test_submit_user_and_thread_id_falls_back_to_run_identity() -> None:
    """Missing user id resolves through ``RunIdentity`` for both fields."""
    user_id, thread_id = _submit_user_and_thread_id(
        None, scope="analyst-submit", operation="submit"
    )

    assert isinstance(user_id, str)
    assert isinstance(thread_id, str)
    assert user_id  # anonymous fallback is non-empty
    assert thread_id  # scoped fallback is non-empty
    assert thread_id != user_id  # scoped id encodes operation, not just user
