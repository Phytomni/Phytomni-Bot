# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Reconcile pins that settle fingerprint jobs from live task status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.runtime import task_reconcile as reconcile_mod
from mcp_server_phytomni.runtime.fingerprint_jobs import (
    FingerprintClaim,
    get_latest_job,
    register_submitted_job,
)
from mcp_server_phytomni.runtime.task_manager import (
    RunContext,
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.unit


def _seed_fingerprint_claim(
    db_path: str,
    *,
    task_id: str,
    analysis_id: str,
    fingerprint: str,
    run_id: str,
) -> None:
    """Persist one analyst child and its fingerprint generation."""
    TaskManager(db_path).record(
        Submission(
            task_id=task_id,
            status="submitted",
            output_dir="/obs/out",
            analysis_id=analysis_id,
            run_context=RunContext(agent="analyst"),
        )
    )
    register_submitted_job(
        db_path,
        FingerprintClaim(
            fingerprint=fingerprint,
            ei_task_id=analysis_id,
            output_dir="/obs/out",
            claimant_task_id=task_id,
            run_id=run_id,
            user_id="alice",
        ),
    )


def _patch_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    db_path: str,
    probe: Any,
) -> None:
    """Aim reconcile at ``db_path`` and install one live-status probe."""
    monkeypatch.setattr(
        reconcile_mod, "resolve_tasks_db_path", lambda: db_path
    )
    monkeypatch.setattr(reconcile_mod, "task_status", probe)


@pytest.mark.asyncio
async def test_reconcile_marks_fingerprint_job_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live SUCCEEDED probe must settle the matching fingerprint job."""
    db_path = str(tmp_path / "jobs.db")
    digest = "b" * 64
    _seed_fingerprint_claim(
        db_path,
        task_id="an-fp",
        analysis_id="EI-live",
        fingerprint=digest,
        run_id="run-fp",
    )

    async def _succeeded(t_id: str, **_: Any) -> dict[str, str]:
        assert t_id in {"EI-live", "an-fp"}
        return {"status": "SUCCEEDED", "output_dir": "/obs/out"}

    _patch_reconcile(monkeypatch, db_path, _succeeded)
    await reconcile_mod.reconcile_task("an-fp")
    job = get_latest_job(db_path, digest)
    assert job is not None
    assert job.status == "succeeded"


@pytest.mark.asyncio
async def test_reconcile_marks_fingerprint_job_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live CANCELLED probe must not store fingerprint status failed."""
    db_path = str(tmp_path / "jobs.db")
    digest = "c" * 64
    _seed_fingerprint_claim(
        db_path,
        task_id="an-can",
        analysis_id="EI-can",
        fingerprint=digest,
        run_id="run-can",
    )

    async def _cancelled(_t_id: str, **_: Any) -> dict[str, str]:
        return {"status": "CANCELLED", "output_dir": "/obs/out"}

    _patch_reconcile(monkeypatch, db_path, _cancelled)
    await reconcile_mod.reconcile_task("an-can")
    job = get_latest_job(db_path, digest)
    assert job is not None
    assert job.status == "cancelled"
