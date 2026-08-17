# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch coverage for DeepGenome dispatch mixin error and poll paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.graph import END

from mcp_server_phytomni.agents.deep_genome import dispatch as dispatch_mod
from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
)
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    AnalysisDispatchContext,
    DeepGenomeDispatchMixin,
)
from mcp_server_phytomni.runtime.deep_genome_store import (
    DeepGenomeTrackingError,
)
from mcp_server_phytomni.storage.path_policy import RunIdentity

pytestmark = pytest.mark.unit


class _DispatchHost(DeepGenomeDispatchMixin):
    """Lightweight host for mixin methods that need a config namespace."""

    def __init__(self) -> None:
        self.deep_genome_config = SimpleNamespace(
            USER_ID="alice",
            DEEPGENOME_OUT="/tmp/deep-out",
            ANALYSIS_URL="https://analysis.example",
            ANALYSIS_REGION="cn-test",
            RETRIABLE_CODES=[429],
            MAX_RETRIES=2,
            BUCKET_NAME="phytomni",
            OBS_SERVER="https://obs.example",
        )
        self.sensitive_config = SimpleNamespace()


def _state(**overrides: Any) -> dict[str, Any]:
    """Return a Send-style analyst state with optional overrides."""
    state: dict[str, Any] = {
        "task_index": 3,
        "target_gene": "AT1G01010",
        "species_code": "ath",
        "analysis_type": "smep_analysis",
    }
    state.update(overrides)
    return state


def _context() -> AnalysisDispatchContext:
    """Return one analysis context used by resolve helpers."""
    return AnalysisDispatchContext(
        analysis_type="smep_analysis",
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/out",
    )


def _identity() -> RunIdentity:
    """Return a throwaway run identity for resolve helpers."""
    return RunIdentity.create("alice", "dispatch-edges")


def test_analysis_request_kwargs_forwards_config() -> None:
    """The mixin request-kwargs helper projects configured platform fields."""
    host = _DispatchHost()
    kwargs = getattr(host, "_analysis_request_kwargs")()

    assert kwargs["analysis_url"] == "https://analysis.example"
    assert kwargs["region"] == "cn-test"
    assert kwargs["retriable_codes"] == [429]
    assert kwargs["max_retries"] == 2


def test_route_after_analyst_ends_send_instance() -> None:
    """Each Send worker terminates so synthesize can act as a barrier."""
    assert getattr(_DispatchHost(), "_route_after_analyst")(_state()) is END


async def test_run_analyst_node_sleeps_then_projects_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A positive submit sleep waits, then success updates both maps."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def fake_dispatch(**_kwargs: Any) -> dict[str, Any]:
        return {
            "task_id": "t-1",
            "output_path": "/obs/out",
            "results_dir": "/local/out",
        }

    def fake_summary(**_kwargs: Any) -> dict[str, str]:
        return {"smep_summary": "ok"}

    monkeypatch.setattr(dispatch_mod.asyncio, "sleep", fake_sleep)
    host = _DispatchHost()
    setattr(host, "_dispatch_and_wait_analysis", fake_dispatch)
    setattr(host, "_generate_sub_summary", fake_summary)

    result = await getattr(host, "_run_analyst_node")(
        _state(task_submit_sleep=2)
    )

    assert slept == [2]
    assert result["analysis_completed_branches"] == 1
    assert result["raw_analyst_data"]["task_3"]["status"] == "success"
    assert result["raw_analyst_data"]["task_3"]["task_id"] == "t-1"
    assert result["analyst_summaries"] == {"smep_summary": "ok"}


async def test_run_analyst_node_records_best_effort_failure() -> None:
    """Ordinary dispatch errors become a failed branch instead of raising."""

    async def boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("dispatch failed")

    host = _DispatchHost()
    setattr(host, "_dispatch_and_wait_analysis", boom)
    result = await getattr(host, "_run_analyst_node")(_state())

    assert result["raw_analyst_data"]["task_3"] == {
        "status": "failed",
        "analysis_type": "smep_analysis",
        "error": "dispatch failed",
    }
    assert result["analysis_completed_branches"] == 1


async def test_run_analyst_node_reraises_tracking_loss() -> None:
    """Tracking-identity loss is not swallowed as a best-effort failure."""

    async def boom(**_kwargs: Any) -> dict[str, Any]:
        raise DeepGenomeTrackingError("tracking lost")

    host = _DispatchHost()
    setattr(host, "_dispatch_and_wait_analysis", boom)

    with pytest.raises(DeepGenomeTrackingError, match="tracking lost"):
        await getattr(host, "_run_analyst_node")(_state())


async def test_finalize_evolution_requires_task_and_output_dir() -> None:
    """Missing evolution tasks or output dirs fail before download."""
    host = _DispatchHost()

    with pytest.raises(RuntimeError, match="no task"):
        await host.finalize_evolution_result(None, _state())

    with pytest.raises(RuntimeError, match="no output directory"):
        await host.finalize_evolution_result(
            {"task_id": "t1", "output_dir": None},
            _state(),
        )


async def test_poll_design_work_item_records_missing_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing design submission is persisted as a failed work item."""
    recorded: list[str] = []

    class _Tracking:
        async def record_work_item_failure(self, work_item_key: str) -> None:
            recorded.append(work_item_key)

    monkeypatch.setattr(
        DeepGenomeDispatchMixin,
        "_transition_sink",
        lambda self, _state: _Tracking(),
    )
    raw, summaries = await getattr(_DispatchHost(), "_poll_design_work_item")(
        "protein_design",
        None,
        _state(),
    )

    assert recorded == ["protein_design"]
    assert summaries == {}
    assert raw["task_3:protein_design"]["status"] == "failed"
    assert raw["task_3:protein_design"]["error"] == (
        "design submission unavailable"
    )


async def test_finalize_design_result_requires_protein_task_and_dir() -> None:
    """The compatibility design path needs a protein task with a directory."""
    host = _DispatchHost()

    with pytest.raises(RuntimeError, match="no protein-design task"):
        await host.finalize_design_result(
            {"task_ids": {"protein_design": "p1"}, "design_task_result": []},
            _state(),
        )

    with pytest.raises(RuntimeError, match="no output directory"):
        await host.finalize_design_result(
            {
                "task_ids": {"protein_design": "p1"},
                "design_task_result": [{"task_id": "p1", "output_dir": 1}],
            },
            _state(),
        )


def test_generate_sub_summary_rejects_unexpected_options() -> None:
    """Unknown sub-summary kwargs stay a TypeError at the mixin boundary."""
    with pytest.raises(TypeError, match="unexpected sub-summary options"):
        getattr(_DispatchHost(), "_generate_sub_summary")(
            "smep_analysis",
            "AT1G01010",
            _state(),
            extra_flag=True,
        )


async def test_resolve_remote_analysis_validates_options_and_outcome() -> None:
    """Remote resolve rejects extra kwargs, failed polls, and missing dirs."""
    host = _DispatchHost()
    submission = RemoteSubmission("caller-1", "poll-1", "/obs/out")

    class _Tracking:
        async def accept_remote_submission(
            self, work_item_key: str, received: RemoteSubmission
        ) -> None:
            del work_item_key, received

    resolve = getattr(host, "_resolve_remote_analysis")
    with pytest.raises(TypeError, match="unexpected remote-analysis options"):
        await resolve(
            submission,
            _context(),
            _identity(),
            tracking=_Tracking(),
            work_item_key="smep_analysis",
            extra=1,
        )

    async def failed_poll(
        *_args: Any, **_kwargs: Any
    ) -> tuple[WorkItemOutcome, str | None]:
        return WorkItemOutcome("failed", None, "remote failed"), None

    setattr(host, "_poll_remote_submission", failed_poll)
    with pytest.raises(RuntimeError, match="remote failed"):
        await resolve(
            submission,
            _context(),
            _identity(),
            tracking=_Tracking(),
            work_item_key="smep_analysis",
        )

    async def blank_poll(
        *_args: Any, **_kwargs: Any
    ) -> tuple[WorkItemOutcome, str | None]:
        return WorkItemOutcome("succeeded", "# ok", None), None

    setattr(host, "_poll_remote_submission", blank_poll)
    with pytest.raises(RuntimeError, match="result resolution failed"):
        await resolve(
            submission,
            _context(),
            _identity(),
            tracking=_Tracking(),
            work_item_key="smep_analysis",
        )


async def test_resolve_direct_analysis_validates_options_and_output() -> None:
    """Direct resolve rejects extra kwargs, agent failures, and blank dirs."""
    host = _DispatchHost()
    resolve = getattr(host, "_resolve_direct_analysis")

    with pytest.raises(TypeError, match="unexpected direct-analysis options"):
        await resolve(
            {"task_id": "t1", "output_dir": "/obs/out"},
            _context(),
            "smep_analysis",
            _identity(),
            extra=1,
        )

    with pytest.raises(RuntimeError, match="AnalystAgent failed"):
        await resolve(
            {
                "task_status": "FAILED_AT_AGENT_LEVEL",
                "error_detail": "boom",
                "output_dir": "/obs/out",
            },
            _context(),
            "smep_analysis",
            _identity(),
        )

    with pytest.raises(RuntimeError, match="no output directory"):
        await resolve(
            {"task_id": "t1", "output_dir": None},
            _context(),
            "smep_analysis",
            _identity(),
        )
