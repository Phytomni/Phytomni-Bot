# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline tests for input-fingerprint dedup at the analyst seam.

Covers ``submit_analyst_via_subgraph``: a cache miss runs the analyst
subgraph and persists a queryable fingerprint row; a verified-live hit
reuses the prior task without re-running ``app.ainvoke``; and the
is_polling split (reuse an in-flight prior only for fire-and-poll
callers) is honoured.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst import task_ops as analyst_task_ops
from mcp_server_phytomni.graphs import analyst_dispatch_adapters as ada
from mcp_server_phytomni.runtime import task_dedup
from mcp_server_phytomni.runtime.fingerprint_jobs import (
    FingerprintClaim,
    register_submitted_job,
)
from mcp_server_phytomni.runtime.task_manager import Submission, TaskManager

from ._analyst_fakes import fake_submitting_agent

pytestmark = pytest.mark.agent


def _request() -> dict[str, Any]:
    """Return a dispatch request with the seam's expected shape."""
    return {
        "analysis_type": "evolution_analysis",
        "target_id": "AT1G01010",
        "prompt_parts": ("goal text", "meta plan", {"/obs/a.fa": "desc"}),
        "compute_resource": "small",
        "output_dir": "/obs/out",
    }


def _fingerprint() -> str:
    """Return the fingerprint the seam computes for ``_request``."""
    return task_dedup.analyst_task_fingerprint(
        goal_description="goal text",
        data_list={"/obs/a.fa": "desc"},
        obs_file_list=None,
    )


def _patch_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub OBS/thread prep so no real RunIdentity/OBS work happens."""

    async def prepare_context(
        _config: Any,
        _sensitive: Any,
        _request: Any,
        _fingerprint: str | None = None,
    ) -> SimpleNamespace:
        """Return a prepared context through the async helper seam."""
        return SimpleNamespace(
            analysis_type="evolution_analysis",
            target_id="AT1G01010",
            output_dir="/obs/out",
            thread_id="thread-1",
        )

    monkeypatch.setattr(
        ada,
        "prepare_analyst_dispatch_context",
        prepare_context,
    )


async def test_seam_miss_submits_and_writes_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A miss runs ainvoke and persists a queryable fingerprint row."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    _patch_context(monkeypatch)

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-new"),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert result["task_id"] == "T-new"
    found = TaskManager(db).get_task_by_fingerprint(_fingerprint())
    assert found is not None
    assert found["task_id"] == "T-new"


async def test_seam_unique_job_collision_keeps_submitted_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local ei_task_id unique collision must not fail the submit."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    _patch_context(monkeypatch)
    register_submitted_job(
        db,
        FingerprintClaim(
            fingerprint="b" * 64,
            ei_task_id="T-new",
            output_dir="/obs/other",
            claimant_task_id="T-new",
            run_id="other-run",
            user_id="other",
        ),
    )

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-new"),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert result["task_id"] == "T-new"


def _seed(db: str, task_id: str, status: str, fingerprint: str) -> None:
    """Persist one prior fingerprint row for reuse-path tests."""
    TaskManager(db).record(
        Submission(
            task_id=task_id,
            status=status,
            output_dir="/obs/prior",
            input_fingerprint=fingerprint,
        )
    )


def _no_submit_agent() -> SimpleNamespace:
    """An analyst_agent that fails the test if app.ainvoke is called."""

    async def ainvoke(state: Any, config: Any) -> dict[str, Any]:
        del state, config
        raise AssertionError("app.ainvoke must not run on a dedup reuse")

    return SimpleNamespace(app=SimpleNamespace(ainvoke=ainvoke))


def _stub_probe(monkeypatch: pytest.MonkeyPatch, status: str | None) -> None:
    """Force the live probe used by the seam to a scripted status."""

    async def fake_probe(task_id: str) -> str | None:
        del task_id
        return status

    monkeypatch.setattr(analyst_task_ops, "probe_live_status", fake_probe)


def _seed_and_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    prior_status: str,
    live_status: str | None,
) -> str:
    """Seed one prior fingerprint row and stub the live probe.

    Returns the tasks-db path so a caller can assert a dead-status
    write-back after the seam runs.
    """
    db = str(tmp_path / "tasks.sqlite")
    _seed(db, "T-prior", prior_status, _fingerprint())
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    _patch_context(monkeypatch)
    _stub_probe(monkeypatch, live_status)
    return db


async def test_seam_reuses_live_running_when_not_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_polling=False reuses an in-flight prior (no ainvoke).

    The caller receives their OWN freshly-minted task id (never the
    prior tenant's ``T-prior``); the prior remote id rides
    ``source_task_id`` for the server-side live-status probe.
    """
    db = str(tmp_path / "tasks.sqlite")
    _seed(db, "T-prior", "submitted", _fingerprint())
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    _patch_context(monkeypatch)
    _stub_probe(monkeypatch, "RUNNING")

    result = await ada.submit_analyst_via_subgraph(
        _no_submit_agent(),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert result["task_id"] != "T-prior"
    assert result["source_task_id"] == "T-prior"
    assert result["plan"] is None


async def test_hit_returns_caller_owned_task_id_with_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified-live hit hands the caller a fresh, caller-owned row.

    The reuse result must surface a fresh task id distinct from the
    prior tenant's ``T-prior``, keep the prior id under
    ``source_task_id``, and persist a caller-owned task row whose
    ``source_task_id`` column points back at the prior remote task.
    """
    db = _seed_and_probe(
        tmp_path,
        monkeypatch,
        prior_status="submitted",
        live_status="RUNNING",
    )

    result = await ada.submit_analyst_via_subgraph(
        _no_submit_agent(),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert result["task_id"] != "T-prior"
    assert result["source_task_id"] == "T-prior"
    row = TaskManager(db).get_task(result["task_id"])
    assert row is not None
    assert row["source_task_id"] == "T-prior"


async def test_seam_reuse_chain_probes_root_remote_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second-generation dedup hit probes the ROOT remote id.

    Seeds the row a prior dedup hit would have written: a caller-owned
    local ``T-local`` whose ``source_task_id`` points at the original
    remote ``R-root``. The next reuse must probe ``R-root`` (the live
    remote task), never ``T-local`` (a caller-owned id with no remote
    task), and the freshly recorded row must again point at ``R-root``
    so the dedup chain stays flat instead of breaking on the third
    identical submission.
    """
    db = str(tmp_path / "tasks.sqlite")
    TaskManager(db).record(
        Submission(
            task_id="T-local",
            status="submitted",
            output_dir="/obs/prior",
            input_fingerprint=_fingerprint(),
            source_task_id="R-root",
        )
    )
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    _patch_context(monkeypatch)

    probed: list[str] = []

    async def capturing_probe(task_id: str) -> str | None:
        probed.append(task_id)
        return "RUNNING"

    monkeypatch.setattr(analyst_task_ops, "probe_live_status", capturing_probe)

    result = await ada.submit_analyst_via_subgraph(
        _no_submit_agent(),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert probed == ["R-root"]
    assert result["source_task_id"] == "R-root"
    recorded = TaskManager(db).get_task(result["task_id"])
    assert recorded is not None
    assert recorded["source_task_id"] == "R-root"


async def test_seam_resubmits_running_prior_when_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_polling=True needs a terminal task, so a RUNNING prior resubmits."""
    db = str(tmp_path / "tasks.sqlite")
    _seed(db, "T-prior", "submitted", _fingerprint())
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    _patch_context(monkeypatch)
    _stub_probe(monkeypatch, "RUNNING")

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-fresh"),
        object(),
        object(),
        _request(),
        is_polling=True,
    )

    assert result["task_id"] == "T-fresh"


async def test_seam_resubmits_and_writes_back_dead_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FAILED live prior is never reused: resubmit and write it dead.

    The seam shares ``verify_live_status`` with the top-level path, but
    only the top-level had a dead-prior test; this pins the seam's own
    resubmit-plus-write-back so the local row self-heals at SQL.
    """
    db = _seed_and_probe(
        tmp_path,
        monkeypatch,
        prior_status="submitted",
        live_status="FAILED",
    )

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-fresh"),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert result["task_id"] == "T-fresh"
    dead = TaskManager(db).get_task("T-prior")
    assert dead is not None
    assert dead["status"] == "failed"


async def test_seam_resubmits_when_live_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed live probe (``None``) is fail-safe: resubmit, never reuse.

    Only the helper level had a None-probe test; this pins the seam
    caller's fail-safe so a probe transport error cannot reuse an
    unverified prior task.
    """
    _seed_and_probe(
        tmp_path,
        monkeypatch,
        prior_status="submitted",
        live_status=None,
    )

    result = await ada.submit_analyst_via_subgraph(
        fake_submitting_agent("T-after-probe-fail"),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert result["task_id"] == "T-after-probe-fail"
