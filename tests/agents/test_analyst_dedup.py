# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline tests for the analyst duplicate-submission dedup path.

Covers the input-fingerprint contract on ``retrieve_plan_submit``:
identical inputs reuse a prior non-failed task without invoking
``agent.arun``; a prior failed row falls through to a fresh
submission; and a different fingerprint never collides with a prior
row's cached result.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from mcp_server_phytomni.agents.analyst import planning as analyst_planning
from mcp_server_phytomni.agents.analyst.agent import retrieve_plan_submit
from mcp_server_phytomni.runtime.task_dedup import analyst_task_fingerprint
from mcp_server_phytomni.runtime.task_manager import (
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.agent


def _seed_task(db_path: str, submission: Submission) -> None:
    """Persist one ``Submission`` row via ``TaskManager.record``."""
    TaskManager(db_path).record(submission)


def _patch_db_path(monkeypatch: pytest.MonkeyPatch, db_path: str) -> None:
    """Point ``retrieve_plan_submit`` at a tmp_path tasks SQLite."""
    monkeypatch.setattr(
        analyst_planning,
        "resolve_tasks_db_path",
        lambda: db_path,
    )


def _forbid_submit_agent(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """Replace ``_build_submit_agent`` with a sentinel that fails the test.

    Returns the call-count dict so the caller can assert zero invocations.
    """
    calls = {"build": 0}

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Trip the assertion if the dedup short-circuit was bypassed."""
        del args, kwargs
        calls["build"] += 1
        raise AssertionError(
            "_build_submit_agent must not run on a dedup hit",
        )

    monkeypatch.setattr(analyst_planning, "_build_submit_agent", fail)
    return calls


def _patch_submit_agent(
    monkeypatch: pytest.MonkeyPatch,
    arun_return: Dict[str, Any],
) -> Dict[str, int]:
    """Replace ``_build_submit_agent`` with a counting fake agent.

    The fake returns ``arun_return`` from its ``arun`` coroutine and
    exposes the same 4-tuple ``_build_submit_agent`` produces. The
    agent itself is a ``SimpleNamespace`` carrying just the awaited
    ``arun`` callable, so there is no one-method class to trip
    pylint's too-few-public-methods rule.
    """
    calls = {"build": 0, "arun": 0}

    async def fake_arun(**kwargs: Any) -> Dict[str, Any]:
        """Tally invocation and return the scripted payload."""
        del kwargs
        calls["arun"] += 1
        return dict(arun_return)

    def fake_build(*args: Any, **kwargs: Any):
        """Return the fake agent alongside the usual 4-tuple."""
        del args, kwargs
        calls["build"] += 1
        return (
            SimpleNamespace(arun=fake_arun),
            "/out/from-fake-build",
            "small",
            "thread-1",
        )

    monkeypatch.setattr(analyst_planning, "_build_submit_agent", fake_build)
    return calls


def _stub_probe(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """Force ``retrieve_plan_submit``'s live probe to a scripted status."""

    async def fake_probe(task_id: str) -> str:
        del task_id
        return status

    monkeypatch.setattr(analyst_planning, "probe_live_status", fake_probe)


async def test_retrieve_plan_submit_reuses_in_flight_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A submitted prior row short-circuits the fresh agent.arun."""
    db = str(tmp_path / "tasks.sqlite")
    _patch_db_path(monkeypatch, db)
    forbid = _forbid_submit_agent(monkeypatch)
    _stub_probe(monkeypatch, "RUNNING")

    goal = "Identify SNP signatures in chromosome 1"
    data_list = {"/obs/snp.vcf": "snp calls"}
    fingerprint = analyst_task_fingerprint(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=None,
    )
    _seed_task(
        db,
        Submission(
            task_id="prior-running",
            status="submitted",
            output_dir="/out/prior",
            input_fingerprint=fingerprint,
        ),
    )

    result = await retrieve_plan_submit(
        goal_description=goal,
        data_list=data_list,
        compute_resource="large",
        meta_meta={"caller": "pytest"},
    )

    # A reuse hit hands the caller a fresh, caller-owned task id (never
    # the prior tenant's), with the prior remote id kept under
    # source_task_id for the server-side live-status probe.
    assert result["task_id"] != "prior-running"
    assert result["source_task_id"] == "prior-running"
    assert result["output_dir"] == "/out/prior"
    assert result["input_fingerprint"] == fingerprint
    # compute_resource echoes back the caller's tier even though the
    # fingerprint ignores it.
    assert result["compute_resource"] == "large"
    assert result["meta_meta"] == {"caller": "pytest"}
    # No dedup sentinel: the submit chokepoint now mints a caller-owned
    # run and records the caller's own task row, so the passthrough flag
    # is gone entirely.
    assert "dedup_hit" not in result
    assert forbid["build"] == 0


async def test_retrieve_plan_submit_reuses_succeeded_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A succeeded prior row hands back the finished output directly."""
    db = str(tmp_path / "tasks.sqlite")
    _patch_db_path(monkeypatch, db)
    forbid = _forbid_submit_agent(monkeypatch)
    _stub_probe(monkeypatch, "SUCCEEDED")

    goal = "Annotate orthologs across wheat genotypes"
    data_list = {"/obs/orthologs.tsv": "ortholog table"}
    fingerprint = analyst_task_fingerprint(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=[],
    )
    _seed_task(
        db,
        Submission(
            task_id="prior-done",
            status="succeeded",
            output_dir="/out/done",
            analysis_id="analysis-42",
            input_fingerprint=fingerprint,
        ),
    )

    result = await retrieve_plan_submit(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=[],
    )

    # Succeeded-prior reuse is also caller-owned: a fresh task id with
    # the prior remote id kept under source_task_id.
    assert result["task_id"] != "prior-done"
    assert result["source_task_id"] == "prior-done"
    assert result["output_dir"] == "/out/done"
    assert "dedup_hit" not in result
    assert forbid["build"] == 0


async def test_retrieve_plan_submit_resubmits_when_only_prior_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed-only prior row falls through to a fresh agent.arun."""
    db = str(tmp_path / "tasks.sqlite")
    _patch_db_path(monkeypatch, db)
    arun_payload = {
        "task_id": "fresh-task-1",
        "output_dir": "/out/fresh",
        "job_name": "job-1",
        "compute_resource": "small",
    }
    counter = _patch_submit_agent(monkeypatch, arun_payload)

    goal = "Compute heritability for height across the panel"
    data_list = {"/obs/panel.csv": "panel"}
    fingerprint = analyst_task_fingerprint(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=None,
    )
    _seed_task(
        db,
        Submission(
            task_id="prior-failed",
            status="failed",
            output_dir="/out/dead",
            input_fingerprint=fingerprint,
        ),
    )

    result = await retrieve_plan_submit(
        goal_description=goal,
        data_list=data_list,
    )

    assert counter["build"] == 1
    assert counter["arun"] == 1
    assert result["task_id"] == "fresh-task-1"
    assert result["input_fingerprint"] == fingerprint
    # A fresh submission is NOT a passthrough; the chokepoint should
    # mint a brand new run id, so the sentinel must be absent (not
    # just falsy — present-but-None would still trigger the skip).
    assert "dedup_hit" not in result


async def test_retrieve_plan_submit_misses_on_different_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior row for a different question never collides."""
    db = str(tmp_path / "tasks.sqlite")
    _patch_db_path(monkeypatch, db)
    arun_payload: Dict[str, Any] = {
        "task_id": "fresh-task-2",
        "output_dir": "/out/fresh-2",
    }
    counter = _patch_submit_agent(monkeypatch, arun_payload)

    other_fingerprint = analyst_task_fingerprint(
        goal_description="Different question entirely",
        data_list={"/obs/other.fa": "other"},
        obs_file_list=None,
    )
    _seed_task(
        db,
        Submission(
            task_id="prior-other",
            status="submitted",
            output_dir="/out/other",
            input_fingerprint=other_fingerprint,
        ),
    )

    result = await retrieve_plan_submit(
        goal_description="Run differential expression for the leaf set",
        data_list={"/obs/leaf.csv": "leaf"},
    )

    assert counter["arun"] == 1
    assert result["task_id"] == "fresh-task-2"
    assert result["input_fingerprint"] != other_fingerprint
    # A miss falls through to a real submission, so the passthrough
    # sentinel must be absent on the wrapper's return.
    assert "dedup_hit" not in result


async def test_retrieve_plan_submit_resubmits_when_live_probe_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 'submitted' row whose live status is FAILED must resubmit fresh.

    Pins the staleness fix: the local column says reusable but the live
    probe says dead, so the wrapper falls through to a fresh agent.arun.
    """
    db = str(tmp_path / "tasks.sqlite")
    _patch_db_path(monkeypatch, db)
    counter = _patch_submit_agent(
        monkeypatch, {"task_id": "fresh-live", "output_dir": "/out/live"}
    )
    _stub_probe(monkeypatch, "FAILED")

    goal = "Stale-row reuse must be rejected by the live probe"
    data_list = {"/obs/x.csv": "x"}
    fingerprint = analyst_task_fingerprint(
        goal_description=goal, data_list=data_list, obs_file_list=None
    )
    _seed_task(
        db,
        Submission(
            task_id="prior-stale",
            status="submitted",
            output_dir="/out/stale",
            input_fingerprint=fingerprint,
        ),
    )

    result = await retrieve_plan_submit(
        goal_description=goal, data_list=data_list
    )

    assert counter["arun"] == 1
    assert result["task_id"] == "fresh-live"
    assert "dedup_hit" not in result
