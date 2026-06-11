# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the shared analyst dedup helpers.

Pins ``analyst_task_fingerprint`` (input-identity invariants),
``should_reuse_prior_task`` (case-insensitive status matrix), and
``verify_live_status`` (live-probe + is_polling reuse decision) now
shared from ``runtime/task_dedup.py`` by both analyst entry points.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.analyst import task_ops
from mcp_server_phytomni.runtime import task_dedup
from mcp_server_phytomni.runtime.task_dedup import (
    analyst_task_fingerprint,
    should_reuse_prior_task,
)
from mcp_server_phytomni.runtime.task_manager import Submission, TaskManager

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


_PRIOR = {
    "task_id": "T-prior",
    "status": "submitted",
    "analysis_id": "A-1",
    "output_dir": "/out/prior",
}


def test_verify_reuses_succeeded_for_both_polling_modes() -> None:
    """SUCCEEDED is reusable regardless of require_terminal_success."""
    assert (
        task_dedup.verify_live_status(
            dict(_PRIOR),
            live_status="SUCCEEDED",
            require_terminal_success=True,
        )
        is True
    )
    assert (
        task_dedup.verify_live_status(
            dict(_PRIOR),
            live_status="SUCCEEDED",
            require_terminal_success=False,
        )
        is True
    )


def test_verify_running_reuses_only_for_fire_and_poll() -> None:
    """RUNNING reuses for fire-and-poll but resubmits for a polling caller."""
    assert (
        task_dedup.verify_live_status(
            dict(_PRIOR), live_status="RUNNING", require_terminal_success=False
        )
        is True
    )
    assert (
        task_dedup.verify_live_status(
            dict(_PRIOR), live_status="RUNNING", require_terminal_success=True
        )
        is False
    )


def test_verify_dead_resubmits_and_writes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAILED never reuses and flips the local row to 'failed'."""
    db = str(tmp_path / "tasks.sqlite")
    TaskManager(db).record(
        Submission(
            task_id="T-prior",
            status="submitted",
            output_dir="/out/prior",
            input_fingerprint="fp-x",
        )
    )
    monkeypatch.setattr(task_dedup, "resolve_tasks_db_path", lambda: db)

    verdict = task_dedup.verify_live_status(
        dict(_PRIOR), live_status="FAILED", require_terminal_success=False
    )

    assert verdict is False
    row = TaskManager(db).get_task("T-prior")
    assert row is not None
    assert row["status"] == "failed"


def test_verify_probe_failure_is_fail_safe() -> None:
    """A None live status (probe failure) resolves to no-reuse."""
    assert (
        task_dedup.verify_live_status(
            dict(_PRIOR), live_status=None, require_terminal_success=False
        )
        is False
    )


async def test_probe_live_status_upper_cases_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """probe_live_status returns the platform status upper-cased."""

    async def fake_status(task_id: str, **kwargs: Any) -> dict:
        del task_id, kwargs
        return {"status": "running"}

    monkeypatch.setattr(task_ops, "task_status", fake_status)

    assert await task_ops.probe_live_status("T-prior") == "RUNNING"


async def test_probe_live_status_none_on_mcperror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe McpError surfaces as None so callers fail safe."""

    async def boom(task_id: str, **kwargs: Any) -> dict:
        del task_id, kwargs
        raise McpError(ErrorData(code=INTERNAL_ERROR, message="probe down"))

    monkeypatch.setattr(task_ops, "task_status", boom)

    assert await task_ops.probe_live_status("T-prior") is None
