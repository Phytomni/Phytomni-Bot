# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for analyst/planning.py orchestration contract.

Pins the return-shape invariants of ``retrieve_plan_submit`` that the
dedup-flow tests exercise end-to-end but do not explicitly assert:
``job_name=""`` and ``compute_resource`` default on the reuse branch,
``meta_meta`` key-presence semantics, ``obs_file_list=None``→``[]`` arun
forwarding, and wrapper-owned ``input_fingerprint`` overriding any
arun-returned key of the same name.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.analyst import planning as analyst_planning
from mcp_server_phytomni.agents.analyst import task_ops as analyst_task_ops
from mcp_server_phytomni.agents.analyst.planning import retrieve_plan_submit

pytestmark = pytest.mark.agent


def _patch_prior(
    monkeypatch: pytest.MonkeyPatch,
    prior: dict[str, Any] | None,
) -> None:
    """Stub ``TaskManager(...).get_task_by_fingerprint`` to return ``prior``.

    ``retrieve_plan_submit`` constructs a fresh ``TaskManager`` per call,
    so the patch replaces the constructor with a factory returning a
    ``SimpleNamespace`` whose ``get_task_by_fingerprint`` ignores its
    argument and yields the scripted row.
    """

    def fake_task_manager(db_path: str) -> SimpleNamespace:
        """Return a stub manager with a scripted dedup lookup."""
        del db_path

        def lookup(fingerprint: str) -> dict[str, Any] | None:
            """Yield the scripted row regardless of the queried key."""
            del fingerprint
            return prior

        return SimpleNamespace(get_task_by_fingerprint=lookup)

    monkeypatch.setattr(analyst_planning, "TaskManager", fake_task_manager)
    monkeypatch.setattr(
        analyst_planning, "resolve_tasks_db_path", lambda: ":memory:"
    )


def _patch_submit_agent_capturing(
    monkeypatch: pytest.MonkeyPatch,
    arun_return: dict[str, Any],
) -> dict[str, Any]:
    """Replace ``_build_submit_agent`` and capture the ``arun`` kwargs.

    The returned dict accumulates ``arun_kwargs`` on the first call so
    tests can assert what the wrapper actually forwarded to the agent
    (e.g. ``obs_file_list``, ``is_polling``).
    """
    captured: dict[str, Any] = {}

    async def fake_arun(**kwargs: Any) -> dict[str, Any]:
        """Record forwarded kwargs and return the scripted payload."""
        captured["arun_kwargs"] = kwargs
        return dict(arun_return)

    def fake_build(*args: Any, **kwargs: Any):
        """Yield the fake agent plus the usual 4-tuple."""
        del args, kwargs
        return (
            SimpleNamespace(arun=fake_arun),
            "/out/fresh",
            "small",
            "thread-x",
        )

    monkeypatch.setattr(analyst_planning, "_build_submit_agent", fake_build)
    return captured


def _stub_probe(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """Force ``retrieve_plan_submit``'s live probe to a scripted status."""

    async def fake_probe(task_id: str) -> str:
        del task_id
        return status

    monkeypatch.setattr(analyst_task_ops, "probe_live_status", fake_probe)


async def test_dedup_hit_uses_small_compute_resource_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse falls back to AnalystConfig.COMPUTE_RESOURCE when omitted."""
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-1",
            "output_dir": "/out/prior",
            "status": "submitted",
        },
    )
    _stub_probe(monkeypatch, "RUNNING")

    result = await retrieve_plan_submit(
        goal_description="Detect outliers in the heritability scan",
        data_list={"/obs/scan.tsv": "scan"},
    )

    assert result["compute_resource"] == "small"
    # The reuse branch always returns an empty job_name literal so the
    # HTTP layer can't accidentally surface a stale submission label.
    assert result["job_name"] == ""
    # The reuse hit is caller-owned: a fresh task id with the prior
    # remote id kept under source_task_id, and no dedup sentinel.
    assert result["task_id"] != "prior-1"
    assert result["source_task_id"] == "prior-1"
    assert "dedup_hit" not in result


async def test_dedup_hit_omits_meta_meta_when_caller_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``meta_meta`` kwarg MUST mean no ``meta_meta`` key in the reuse dict.

    The chokepoint distinguishes "caller passed empty meta" from "caller
    didn't pass meta at all" by key presence — surface the contract.
    """
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-2",
            "output_dir": "/out/prior-2",
            "status": "succeeded",
        },
    )
    _stub_probe(monkeypatch, "SUCCEEDED")

    result = await retrieve_plan_submit(
        goal_description="A",
        data_list={},
    )

    assert "meta_meta" not in result


async def test_dedup_hit_drops_empty_meta_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty string ``meta_meta`` is treated as absent (falsy check)."""
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-3",
            "output_dir": "/out/prior-3",
            "status": "running",
        },
    )
    _stub_probe(monkeypatch, "RUNNING")

    result = await retrieve_plan_submit(
        goal_description="A",
        data_list={},
        meta_meta="",
    )

    assert "meta_meta" not in result


async def test_dedup_hit_treats_blank_prior_status_as_no_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior row whose ``status`` is None falls through to fresh submit.

    ``retrieve_plan_submit`` coerces ``prior["status"] or ""`` before
    asking ``should_reuse_prior_task``; an empty status is unknown and
    must fail safe to a resubmit.
    """
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-blank",
            "output_dir": "/out/prior-blank",
            "status": None,
        },
    )
    captured = _patch_submit_agent_capturing(
        monkeypatch,
        {"task_id": "fresh-blank", "output_dir": "/out/fresh-blank"},
    )

    result = await retrieve_plan_submit(
        goal_description="B",
        data_list={},
    )

    assert result["task_id"] == "fresh-blank"
    assert "dedup_hit" not in result
    assert captured["arun_kwargs"]["obs_file_list"] == []


async def test_fresh_submit_forwards_obs_file_list_default_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``obs_file_list=None`` (default) MUST be coerced to ``[]`` for arun.

    The retrieval graph expects a list-typed obs argument; ``None`` would
    fan out as a TypeError inside the LangGraph node.
    """
    _patch_prior(monkeypatch, None)
    captured = _patch_submit_agent_capturing(
        monkeypatch,
        {"task_id": "fresh-1", "output_dir": "/out/fresh-1"},
    )

    result = await retrieve_plan_submit(
        goal_description="C",
        data_list={"/obs/c.csv": "c"},
    )

    assert captured["arun_kwargs"]["query"] == "C"
    assert captured["arun_kwargs"]["goal_description"] == "C"
    assert captured["arun_kwargs"]["preset_data_list"] == {"/obs/c.csv": "c"}
    assert captured["arun_kwargs"]["obs_file_list"] == []
    assert captured["arun_kwargs"]["is_polling"] is False
    assert captured["arun_kwargs"]["is_auto_select"] is True
    assert result["task_id"] == "fresh-1"
    assert "input_fingerprint" in result


async def test_fresh_submit_overrides_arun_input_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper-owned fingerprint MUST win over any arun-returned key.

    The agent layer should never set ``input_fingerprint`` itself — the
    wrapper is the single source of truth for the dedup key. Pin the
    overwrite so a future agent change does not silently corrupt it.
    """
    _patch_prior(monkeypatch, None)
    _patch_submit_agent_capturing(
        monkeypatch,
        {
            "task_id": "fresh-2",
            "output_dir": "/out/fresh-2",
            "input_fingerprint": "AGENT-SHOULD-NOT-WIN",
        },
    )

    result = await retrieve_plan_submit(
        goal_description="D",
        data_list={},
    )

    assert result["input_fingerprint"] != "AGENT-SHOULD-NOT-WIN"
    # SHA-256 hex digest length pin (mirrors the dedup test invariant).
    assert len(result["input_fingerprint"]) == 64


async def test_dedup_hit_skips_shared_default_output_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior row that wrote into the shared test dump must resubmit.

    Reusing that prefix would harvest leftover objects from every other
    job that landed in ``AnalystConfig.OUTPUT_DIR``.
    """
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
    _stub_probe(monkeypatch, "SUCCEEDED")
    captured = _patch_submit_agent_capturing(
        monkeypatch,
        {"task_id": "fresh-isolated", "output_dir": "/obs/run-scoped"},
    )

    result = await retrieve_plan_submit(
        goal_description="Count the rows in the uploaded table",
        data_list={"/obs/lines.tsv": "table"},
    )

    assert result["task_id"] == "fresh-isolated"
    assert result["output_dir"] == "/obs/run-scoped"
    assert "source_task_id" not in result
    assert captured["arun_kwargs"]["goal_description"] == (
        "Count the rows in the uploaded table"
    )


async def test_dedup_hit_skips_legacy_fingerprint_output_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior row on the old shared dump must resubmit into a job dir."""
    fingerprint = "c" * 64
    _patch_prior(
        monkeypatch,
        {
            "task_id": "prior-legacy-shared",
            "output_dir": (
                f"/obs/phytomni/agent_data/shared/{fingerprint}/"
                "output/children/part-001"
            ),
            "status": "succeeded",
        },
    )
    _stub_probe(monkeypatch, "SUCCEEDED")
    captured = _patch_submit_agent_capturing(
        monkeypatch,
        {
            "task_id": "fresh-job",
            "output_dir": (
                f"/obs/phytomni/agent_data/shared/{fingerprint}/"
                "jobs/run-new/output/children/part-001"
            ),
        },
    )

    result = await retrieve_plan_submit(
        goal_description="Shared public catalog query",
        data_list={"/obs/public/ref.tsv": "catalog"},
    )

    assert result["task_id"] == "fresh-job"
    assert "/jobs/" in result["output_dir"]
    assert "source_task_id" not in result
    assert captured["arun_kwargs"]["goal_description"] == (
        "Shared public catalog query"
    )
