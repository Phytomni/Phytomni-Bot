# pylint: disable=duplicate-code
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for analyst/planning.py reuse and locale forwarding."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst import planning as analyst_planning
from mcp_server_phytomni.agents.analyst import task_ops
from mcp_server_phytomni.agents.analyst.planning import retrieve_plan_submit

pytestmark = pytest.mark.agent


def _patch_prior(
    monkeypatch: pytest.MonkeyPatch,
    prior: dict[str, Any] | None,
) -> None:
    """Stub the fingerprint lookup to a scripted prior row."""

    def fake_task_manager(db_path: str) -> SimpleNamespace:
        del db_path

        def lookup(_fingerprint: str) -> dict[str, Any] | None:
            return prior

        return SimpleNamespace(get_task_by_fingerprint=lookup)

    monkeypatch.setattr(analyst_planning, "TaskManager", fake_task_manager)
    monkeypatch.setattr(
        analyst_planning, "resolve_tasks_db_path", lambda: ":memory:"
    )


def _patch_submit(
    monkeypatch: pytest.MonkeyPatch,
    arun_return: dict[str, Any],
) -> dict[str, Any]:
    """Replace the submit-agent builder and capture arun kwargs."""
    captured: dict[str, Any] = {}

    async def fake_arun(**kwargs: Any) -> dict[str, Any]:
        captured["arun_kwargs"] = kwargs
        return dict(arun_return)

    def fake_build(*_args: Any, **_kwargs: Any) -> tuple[Any, str, str, str]:
        return (
            SimpleNamespace(arun=fake_arun),
            "/out/fresh",
            "small",
            "thread-x",
        )

    monkeypatch.setattr(analyst_planning, "_build_submit_agent", fake_build)
    return captured


async def test_reuse_keeps_truthy_meta_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied meta_meta rides through the reuse payload."""
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-meta",
            "output_dir": "/out/prior-meta",
            "status": "succeeded",
        },
    )

    async def fake_probe(task_id: str) -> str:
        del task_id
        return "SUCCEEDED"

    monkeypatch.setattr(task_ops, "probe_live_status", fake_probe)

    result = await retrieve_plan_submit(
        goal_description="reuse with meta",
        data_list={"/obs/a.tsv": "a"},
        meta_meta={"note": "keep"},
    )

    assert result["meta_meta"] == {"note": "keep"}
    assert result["source_task_id"] == "prior-meta"
    assert result["job_name"] == ""


async def test_fresh_submit_forwards_locale_and_obs_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locale and obs_file_list reach arun on a fingerprint miss."""
    _patch_prior(monkeypatch, None)
    captured = _patch_submit(
        monkeypatch, {"task_id": "fresh-locale", "output_dir": "/out/new"}
    )

    result = await retrieve_plan_submit(
        goal_description="fresh locale",
        data_list={"/obs/b.tsv": "b"},
        obs_file_list=["obs://docs/paper.pdf"],
        locale="zh-CN",
        meta_meta="caller-meta",
    )

    assert captured["arun_kwargs"]["obs_file_list"] == ["obs://docs/paper.pdf"]
    assert captured["arun_kwargs"]["locale"] == "zh-CN"
    assert result["meta_meta"] == "caller-meta"
    assert result["input_fingerprint"]
    assert result["task_id"] == "fresh-locale"


async def test_reuse_skips_shared_default_output_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior row in the shared dump must fall through to a fresh submit."""
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-dump",
            "output_dir": (
                "/obs/phytomni/agent_data/test/output/children/part-001"
            ),
            "status": "succeeded",
        },
    )
    captured = _patch_submit(
        monkeypatch, {"task_id": "fresh-dump", "output_dir": "/out/new"}
    )

    result = await retrieve_plan_submit(
        goal_description="skip dump",
        data_list={"/obs/a.tsv": "a"},
    )

    assert result["task_id"] == "fresh-dump"
    assert "source_task_id" not in result
    assert captured["arun_kwargs"]["goal_description"] == "skip dump"


async def test_reuse_skips_when_live_probe_is_dead(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead remote probe forces a fresh submit."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-dead",
            "output_dir": "/out/dead",
            "status": "submitted",
        },
    )

    async def fake_probe(task_id: str) -> str:
        del task_id
        return "FAILED"

    monkeypatch.setattr(task_ops, "probe_live_status", fake_probe)
    _patch_submit(
        monkeypatch, {"task_id": "fresh-dead", "output_dir": "/out/new"}
    )

    result = await retrieve_plan_submit(
        goal_description="dead prior",
        data_list={},
    )

    assert result["task_id"] == "fresh-dead"
    assert "meta_meta" not in result


async def test_reuse_uses_source_task_id_for_live_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reuse row probes the recorded remote id, not the caller id."""
    probed: list[str] = []
    _patch_prior(
        monkeypatch,
        {
            "task_id": "caller-owned",
            "source_task_id": "remote-prior",
            "output_dir": "/out/src",
            "status": "running",
        },
    )

    async def fake_probe(task_id: str) -> str:
        probed.append(task_id)
        return "RUNNING"

    monkeypatch.setattr(task_ops, "probe_live_status", fake_probe)

    result = await retrieve_plan_submit(
        goal_description="probe source",
        data_list={},
        compute_resource="medium",
    )

    assert probed == ["remote-prior"]
    assert result["source_task_id"] == "remote-prior"
    assert result["compute_resource"] == "medium"
