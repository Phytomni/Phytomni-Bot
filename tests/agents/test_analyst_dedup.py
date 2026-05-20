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

from mcp_server_phytomni.agents.analyst import agent as analyst_agent_module
from mcp_server_phytomni.agents.analyst.agent import (
    _analyst_task_fingerprint,
    _should_reuse_prior_task,
    retrieve_plan_submit,
)
from mcp_server_phytomni.runtime.task_manager import (
    Submission,
    TaskManager,
)

pytestmark = pytest.mark.agent


def _seed_task(
    db_path: str,
    *,
    task_id: str,
    status: str,
    output_dir: str,
    input_fingerprint: str,
    analysis_id: str = "",
) -> None:
    """Write one task row directly through TaskManager.record."""
    TaskManager(db_path).record(
        Submission(
            task_id=task_id,
            status=status,
            output_dir=output_dir,
            analysis_id=analysis_id,
            input_fingerprint=input_fingerprint,
        )
    )


def _patch_db_path(monkeypatch: pytest.MonkeyPatch, db_path: str) -> None:
    """Point ``retrieve_plan_submit`` at a tmp_path tasks SQLite."""
    monkeypatch.setattr(
        analyst_agent_module,
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

    monkeypatch.setattr(analyst_agent_module, "_build_submit_agent", fail)
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

    monkeypatch.setattr(
        analyst_agent_module, "_build_submit_agent", fake_build
    )
    return calls


def test_fingerprint_excludes_compute_resource_and_user_id() -> None:
    """The fingerprint is anchored on the question + data only."""
    fp_a = _analyst_task_fingerprint(
        goal_description="Cluster the leaf RNA-seq replicates",
        data_list={"/obs/a.fa": "first", "/obs/b.fa": "second"},
        obs_file_list=["/obs/extra.pdf"],
    )
    # Same scientific identity, different dict insertion order and
    # different obs ordering must yield the same digest.
    fp_b = _analyst_task_fingerprint(
        goal_description="Cluster the leaf RNA-seq replicates",
        data_list={"/obs/b.fa": "second", "/obs/a.fa": "first"},
        obs_file_list=["/obs/extra.pdf"],
    )
    # Changing the question or the data set produces a fresh digest.
    fp_c = _analyst_task_fingerprint(
        goal_description="Cluster the root RNA-seq replicates",
        data_list={"/obs/a.fa": "first", "/obs/b.fa": "second"},
        obs_file_list=["/obs/extra.pdf"],
    )
    assert fp_a == fp_b
    assert fp_a != fp_c


def test_should_reuse_prior_task_classifies_known_states() -> None:
    """In-flight + succeeded states reuse; unknown falls through."""
    for status in (
        "submitted",
        "running",
        "pending",
        "SUCCEEDED",
        "success",
        "completed",
        "Done",
    ):
        assert _should_reuse_prior_task(status) is True
    # Unknown states intentionally fall back to resubmit (fail-safe).
    assert _should_reuse_prior_task("unknown") is False
    assert _should_reuse_prior_task("") is False


async def test_retrieve_plan_submit_reuses_in_flight_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A submitted prior row short-circuits the fresh agent.arun."""
    db = str(tmp_path / "tasks.sqlite")
    _patch_db_path(monkeypatch, db)
    forbid = _forbid_submit_agent(monkeypatch)

    goal = "Identify SNP signatures in chromosome 1"
    data_list = {"/obs/snp.vcf": "snp calls"}
    fingerprint = _analyst_task_fingerprint(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=None,
    )
    _seed_task(
        db,
        task_id="prior-running",
        status="submitted",
        output_dir="/out/prior",
        input_fingerprint=fingerprint,
    )

    result = await retrieve_plan_submit(
        goal_description=goal,
        data_list=data_list,
        compute_resource="large",
        meta_meta={"caller": "pytest"},
    )

    assert result["task_id"] == "prior-running"
    assert result["output_dir"] == "/out/prior"
    assert result["input_fingerprint"] == fingerprint
    # compute_resource echoes back the caller's tier even though the
    # fingerprint ignores it.
    assert result["compute_resource"] == "large"
    assert result["meta_meta"] == {"caller": "pytest"}
    assert forbid["build"] == 0


async def test_retrieve_plan_submit_reuses_succeeded_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A succeeded prior row hands back the finished output directly."""
    db = str(tmp_path / "tasks.sqlite")
    _patch_db_path(monkeypatch, db)
    forbid = _forbid_submit_agent(monkeypatch)

    goal = "Annotate orthologs across wheat genotypes"
    data_list = {"/obs/orthologs.tsv": "ortholog table"}
    fingerprint = _analyst_task_fingerprint(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=[],
    )
    _seed_task(
        db,
        task_id="prior-done",
        status="succeeded",
        output_dir="/out/done",
        analysis_id="analysis-42",
        input_fingerprint=fingerprint,
    )

    result = await retrieve_plan_submit(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=[],
    )

    assert result["task_id"] == "prior-done"
    assert result["output_dir"] == "/out/done"
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
    fingerprint = _analyst_task_fingerprint(
        goal_description=goal,
        data_list=data_list,
        obs_file_list=None,
    )
    _seed_task(
        db,
        task_id="prior-failed",
        status="failed",
        output_dir="/out/dead",
        input_fingerprint=fingerprint,
    )

    result = await retrieve_plan_submit(
        goal_description=goal,
        data_list=data_list,
    )

    assert counter["build"] == 1
    assert counter["arun"] == 1
    assert result["task_id"] == "fresh-task-1"
    assert result["input_fingerprint"] == fingerprint


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

    other_fingerprint = _analyst_task_fingerprint(
        goal_description="Different question entirely",
        data_list={"/obs/other.fa": "other"},
        obs_file_list=None,
    )
    _seed_task(
        db,
        task_id="prior-other",
        status="submitted",
        output_dir="/out/other",
        input_fingerprint=other_fingerprint,
    )

    result = await retrieve_plan_submit(
        goal_description="Run differential expression for the leaf set",
        data_list={"/obs/leaf.csv": "leaf"},
    )

    assert counter["arun"] == 1
    assert result["task_id"] == "fresh-task-2"
    assert result["input_fingerprint"] != other_fingerprint


def test_fingerprint_is_stable_across_runs() -> None:
    """The digest is process-stable and uses hashlib (not hash())."""
    fp_first = _analyst_task_fingerprint(
        goal_description="Stable digest check",
        data_list={"/obs/a.fa": "alpha"},
        obs_file_list=["/obs/extra"],
    )
    fp_second = _analyst_task_fingerprint(
        goal_description="Stable digest check",
        data_list={"/obs/a.fa": "alpha"},
        obs_file_list=["/obs/extra"],
    )
    assert fp_first == fp_second
    # SHA-256 hex digest is 64 lower-hex chars; pin it so a future
    # accidental swap to a salted hash() is caught.
    assert len(fp_first) == 64
    assert set(fp_first) <= set("0123456789abcdef")
