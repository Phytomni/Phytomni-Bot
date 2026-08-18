# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for fingerprint job generations and claim refcount."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_phytomni.runtime.fingerprint_jobs import (
    FingerprintJobDeadError,
    attach_reuse_claim,
    cancel_run_claims,
    get_latest_job,
    mark_job_terminal,
    register_submitted_job,
)
from mcp_server_phytomni.runtime.task_manager import TaskManager

pytestmark = pytest.mark.unit

_FP = "a" * 64


def _register(
    db: str,
    *,
    ei_task_id: str,
    claimant: str,
    run_id: str,
    user_id: str,
    force_new: bool = False,
) -> object:
    """Register one submitted job/claim on the shared fingerprint."""
    return register_submitted_job(
        db,
        fingerprint=_FP,
        ei_task_id=ei_task_id,
        output_dir="/obs/out",
        claimant_task_id=claimant,
        run_id=run_id,
        user_id=user_id,
        force_new=force_new,
    )


def test_init_db_creates_fingerprint_tables(tmp_path: Path) -> None:
    """Opening TaskManager also creates the fingerprint job tables."""
    db = str(tmp_path / "tasks.sqlite")
    TaskManager(db)
    job = get_latest_job(db, _FP)
    assert job is None


def test_one_to_one_cancel_marks_job_for_terminate(tmp_path: Path) -> None:
    """A single live claim forwards Stop to the EI job."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-1",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )

    result = cancel_run_claims(db, run_id="run-alice", user_id="alice")

    assert result.detached_claimants == ("T-alice",)
    assert result.terminate_ei_ids == ("EI-1",)
    job = get_latest_job(db, _FP)
    assert job is not None
    assert job.status == "cancelling"
    assert job.generation == 1


def test_n_to_one_first_cancel_does_not_terminate(tmp_path: Path) -> None:
    """An earlier claimant only detaches while another claim stays active."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-1",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )
    attach_reuse_claim(
        db,
        fingerprint=_FP,
        ei_task_id="EI-1",
        output_dir="/obs/out",
        claimant_task_id="T-bob",
        run_id="run-bob",
        user_id="bob",
    )

    first = cancel_run_claims(db, run_id="run-alice", user_id="alice")

    assert first.detached_claimants == ("T-alice",)
    assert first.terminate_ei_ids == ()
    job = get_latest_job(db, _FP)
    assert job is not None
    assert job.status == "running"
    assert job.ei_task_id == "EI-1"


def test_n_to_one_last_cancel_terminates(tmp_path: Path) -> None:
    """The last active claim forwards Stop to the EI job."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-1",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )
    attach_reuse_claim(
        db,
        fingerprint=_FP,
        ei_task_id="EI-1",
        output_dir="/obs/out",
        claimant_task_id="T-bob",
        run_id="run-bob",
        user_id="bob",
    )
    cancel_run_claims(db, run_id="run-alice", user_id="alice")

    last = cancel_run_claims(db, run_id="run-bob", user_id="bob")

    assert last.terminate_ei_ids == ("EI-1",)
    job = get_latest_job(db, _FP)
    assert job is not None
    assert job.status == "cancelling"


def test_cancelled_generation_relaunches_next_submit(tmp_path: Path) -> None:
    """A cancelled generation is not reused; the next submit increments."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-1",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )
    cancel_run_claims(db, run_id="run-alice", user_id="alice")
    mark_job_terminal(db, "EI-1", "cancelled")

    with pytest.raises(FingerprintJobDeadError):
        attach_reuse_claim(
            db,
            fingerprint=_FP,
            ei_task_id="EI-1",
            output_dir="/obs/out",
            claimant_task_id="T-carol",
            run_id="run-carol",
            user_id="carol",
        )

    registered = _register(
        db,
        ei_task_id="EI-2",
        claimant="T-carol",
        run_id="run-carol",
        user_id="carol",
    )

    assert registered.job.generation == 2
    assert registered.job.ei_task_id == "EI-2"
    assert registered.orphan_ei_task_id is None


def test_failed_generation_relaunches_next_submit(tmp_path: Path) -> None:
    """A failed generation is skipped so the next submit starts fresh."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-1",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )
    assert mark_job_terminal(db, "EI-1", "failed") is True

    registered = _register(
        db,
        ei_task_id="EI-2",
        claimant="T-bob",
        run_id="run-bob",
        user_id="bob",
    )

    assert registered.job.generation == 2
    assert registered.job.ei_task_id == "EI-2"


def test_succeeded_job_survives_one_user_cancel(tmp_path: Path) -> None:
    """Detaching from a succeeded cache must not terminate the EI job."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-1",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )
    attach_reuse_claim(
        db,
        fingerprint=_FP,
        ei_task_id="EI-1",
        output_dir="/obs/out",
        claimant_task_id="T-bob",
        run_id="run-bob",
        user_id="bob",
        job_status="succeeded",
    )

    result = cancel_run_claims(db, run_id="run-alice", user_id="alice")

    assert result.terminate_ei_ids == ()
    job = get_latest_job(db, _FP)
    assert job is not None
    assert job.status == "succeeded"
    later = attach_reuse_claim(
        db,
        fingerprint=_FP,
        ei_task_id="EI-1",
        output_dir="/obs/out",
        claimant_task_id="T-dave",
        run_id="run-dave",
        user_id="dave",
        job_status="succeeded",
    )
    assert later.generation == 1
    assert later.ei_task_id == "EI-1"


def test_new_claim_during_last_cancel_relaunches(tmp_path: Path) -> None:
    """A claim that lands on cancelling must not attach; next gen is new."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-old",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )
    last = cancel_run_claims(db, run_id="run-alice", user_id="alice")
    assert last.terminate_ei_ids == ("EI-old",)

    with pytest.raises(FingerprintJobDeadError):
        attach_reuse_claim(
            db,
            fingerprint=_FP,
            ei_task_id="EI-old",
            output_dir="/obs/out",
            claimant_task_id="T-bob",
            run_id="run-bob",
            user_id="bob",
        )

    registered = _register(
        db,
        ei_task_id="EI-new",
        claimant="T-bob",
        run_id="run-bob",
        user_id="bob",
    )
    assert registered.job.ei_task_id == "EI-new"
    assert registered.job.generation == 2
    assert "EI-old" not in (registered.job.ei_task_id,)


def test_lost_submit_race_returns_orphan_ei_id(tmp_path: Path) -> None:
    """A late miss attaches to the winner and reports its own EI as orphan."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-winner",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )

    registered = _register(
        db,
        ei_task_id="EI-dup",
        claimant="T-bob",
        run_id="run-bob",
        user_id="bob",
        force_new=False,
    )

    assert registered.job.ei_task_id == "EI-winner"
    assert registered.orphan_ei_task_id == "EI-dup"


def test_force_new_opens_generation_while_running(tmp_path: Path) -> None:
    """Polling callers that cannot reuse in-flight work open generation+1."""
    db = str(tmp_path / "tasks.sqlite")
    _register(
        db,
        ei_task_id="EI-1",
        claimant="T-alice",
        run_id="run-alice",
        user_id="alice",
    )

    registered = _register(
        db,
        ei_task_id="EI-2",
        claimant="T-dg",
        run_id="run-dg",
        user_id="dg",
        force_new=True,
    )

    assert registered.job.generation == 2
    assert registered.job.ei_task_id == "EI-2"
    first = cancel_run_claims(db, run_id="run-alice", user_id="alice")
    assert first.terminate_ei_ids == ("EI-1",)
    second = cancel_run_claims(db, run_id="run-dg", user_id="dg")
    assert second.terminate_ei_ids == ("EI-2",)


def test_private_upload_path_is_one_to_one(tmp_path: Path) -> None:
    """A unique private-input fingerprint still terminates on the only claim."""
    db = str(tmp_path / "tasks.sqlite")
    private_fp = "b" * 64
    register_submitted_job(
        db,
        fingerprint=private_fp,
        ei_task_id="EI-private",
        output_dir="/obs/user/out",
        claimant_task_id="T-private",
        run_id="run-private",
        user_id="alice",
    )

    result = cancel_run_claims(db, run_id="run-private", user_id="alice")

    assert result.terminate_ei_ids == ("EI-private",)
