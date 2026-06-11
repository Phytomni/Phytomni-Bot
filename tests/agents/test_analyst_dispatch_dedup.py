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

from mcp_server_phytomni.graphs import analyst_dispatch_adapters as ada
from mcp_server_phytomni.runtime import task_dedup
from mcp_server_phytomni.runtime.task_manager import Submission, TaskManager

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
    monkeypatch.setattr(
        ada,
        "prepare_analyst_dispatch_context",
        lambda config, sensitive, request: SimpleNamespace(
            analysis_type="evolution_analysis",
            target_id="AT1G01010",
            output_dir="/obs/out",
            thread_id="thread-1",
        ),
    )


def _submitting_agent(task_id: str) -> SimpleNamespace:
    """An analyst_agent whose app.ainvoke returns a scripted final state."""

    async def ainvoke(state: Any, config: Any) -> dict[str, Any]:
        del state, config
        return {
            "task_id": task_id,
            "output_dir": "/obs/out",
            "plan": "p",
            "tool_usages": "t",
            "task_status": "SUBMITTED",
        }

    return SimpleNamespace(app=SimpleNamespace(ainvoke=ainvoke))


async def test_seam_miss_submits_and_writes_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A miss runs ainvoke and persists a queryable fingerprint row."""
    db = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db)
    _patch_context(monkeypatch)

    result = await ada.submit_analyst_via_subgraph(
        _submitting_agent("T-new"),
        object(),
        object(),
        _request(),
        is_polling=False,
    )

    assert result["task_id"] == "T-new"
    found = TaskManager(db).get_task_by_fingerprint(_fingerprint())
    assert found is not None
    assert found["task_id"] == "T-new"


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


def _stub_probe(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """Force the live probe used by the seam to a scripted status."""

    async def fake_probe(task_id: str) -> str:
        del task_id
        return status

    monkeypatch.setattr(ada, "probe_live_status", fake_probe)


async def test_seam_reuses_live_running_when_not_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_polling=False reuses an in-flight prior (no ainvoke)."""
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

    assert result["task_id"] == "T-prior"
    assert result["plan"] is None


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
        _submitting_agent("T-fresh"),
        object(),
        object(),
        _request(),
        is_polling=True,
    )

    assert result["task_id"] == "T-fresh"
