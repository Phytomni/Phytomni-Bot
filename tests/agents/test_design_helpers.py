# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helper-method tests for agents/design/agent.

Pin the compute-resource tier mapping from DigitalDesignConfig
(``protein_design_analysis`` and ``protein_structure_analysis`` use
``medium``; everything else uses the config default) and the
``_analysis_prompt_parts`` guard that raises ``ValueError`` on an
unknown analysis type before any prompt lookup.
"""

# The helper probes below intentionally target private design decisions; each
# carries a symbol-scoped protected-access directive.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import mcp_server_phytomni.agents.design.agent as design_agent_module
from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
    _design_submission_outcome,
    _DispatchOptions,
    _project_design_submission_updates,
)
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisRequest,
    RemoteAnalysisSubmissionError,
)
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime.request_context import (
    current_accepted_task_ids,
    request_context,
)
from tests.support.analysis_states import (
    analysis_node_state,
    assert_partial_submission_outcome,
    install_analysis_prompt_parts,
    install_async_failure,
    mixed_submission_state,
)

pytestmark = pytest.mark.agent


def _build_agent() -> DigitalDesignAgents:
    """Construct a design agent wired to a stub analyst.

    Uses ``SimpleNamespace`` for the analyst stand-in (same pattern as
    ``_analyst_fakes.py``) instead of a one-method stub class.
    """
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return DigitalDesignAgents(
        digital_design_config=DigitalDesignConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )


def test_get_compute_resource_protein_design_returns_medium() -> None:
    """Protein design reads the medium tier from DigitalDesignConfig."""
    agent = _build_agent()

    assert (
        getattr(agent, "_get_compute_resource")("protein_design_analysis")
        == "medium"
    )


def test_get_compute_resource_unknown_falls_back_to_small() -> None:
    """Unmapped analysis types use DigitalDesignConfig.COMPUTE_RESOURCE."""
    agent = _build_agent()

    get_resource = getattr(agent, "_get_compute_resource")
    assert get_resource("promoter_design_analysis") == "small"
    assert get_resource("does-not-exist") == "small"


def test_analysis_prompt_parts_rejects_unknown_type() -> None:
    """Unknown analysis types fail loud before any prompt lookup.

    The guard sits ahead of ``get_prompt`` and ``get_data_list``
    so a misconfigured caller never reaches the species metadata
    layer with a typo in the analysis-type slug.
    """
    agent = _build_agent()

    with pytest.raises(ValueError, match="does-not-exist"):
        getattr(agent, "_analysis_prompt_parts")(
            analysis_type="does-not-exist",
            species_code="ath",
            gene_id="AT1G01010",
        )


@pytest.mark.parametrize(
    "case",
    [
        (
            "protein_design_analysis",
            "protein goal for AT1G01010",
            "protein meta",
            {"/obs/protein.fasta": "protein"},
            "medium",
        ),
        (
            "promoter_design_analysis",
            "promoter goal for AT1G01010",
            "promoter meta",
            {"/obs/promoter.txt": "promoter"},
            "small",
        ),
    ],
)
async def test_dispatch_builds_typed_remote_analysis_request(
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, str, dict[str, str], str],
) -> None:
    """Capture the exact typed request for both Design analysis paths."""
    analysis_type, goal, meta, data_list, compute_resource = case
    agent = _build_agent()
    monkeypatch.setattr(
        agent,
        "_analysis_prompt_parts",
        lambda *_args: (goal, meta, data_list),
    )
    monkeypatch.setattr(
        agent,
        "_get_compute_resource",
        lambda _analysis_type: compute_resource,
    )
    captured: dict[str, object] = {}

    async def fake_submit(
        _analyst: AnalystAgent,
        _config: DigitalDesignConfig,
        _sensitive: SensitiveConfig,
        request: RemoteAnalysisRequest,
        *,
        is_polling: bool,
    ) -> dict[str, str]:
        captured["request"] = request
        captured["is_polling"] = is_polling
        return {"task_id": f"{analysis_type}-task"}

    monkeypatch.setattr(
        design_agent_module,
        "submit_remote_analysis",
        AsyncMock(side_effect=fake_submit),
        raising=False,
    )
    dispatch = getattr(agent, "_dispatch_and_wait_analysis")
    result = await dispatch(
        analysis_type,
        "ath",
        "AT1G01010",
        _DispatchOptions(output_dir="/obs/design-out"),
    )

    request = captured["request"]
    assert isinstance(request, RemoteAnalysisRequest)
    assert request.analysis_type == analysis_type
    assert request.target_id == "AT1G01010"
    assert request.goal_description == goal
    assert request.meta == meta
    assert request.data_list == data_list
    assert request.output_dir == "/obs/design-out"
    assert request.compute_resource == compute_resource
    assert captured["is_polling"] is False
    assert result["task_id"] == f"{analysis_type}-task"


async def test_arun_surfaces_partial_submission_warning_and_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial Design fan-out returns only accepted IDs plus a warning."""
    agent = _build_agent()
    monkeypatch.setattr(
        design_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "design_task_result": [
                    {"task_id": "design-1", "output_dir": "out"}
                ],
                "error": None,
                "failures": [],
                "phytomni_state": {
                    "submission_rejections": [
                        {"goal": "AT1G01010", "code": "upstream_rejected"}
                    ]
                },
            }
        ),
    )

    with request_context("user-1", "request-1"):
        result = await agent.arun("ath", "AT1G01010")
        assert current_accepted_task_ids() == ("design-1",)

    assert result["submission_warnings"] == [
        {
            "code": "partial_submission",
            "retryable": False,
            "rejected_count": 1,
        }
    ]


async def test_arun_rejects_when_all_design_submissions_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design must not return a running-shaped result with zero IDs."""
    agent = _build_agent()
    monkeypatch.setattr(
        design_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "design_task_result": [],
                "error": None,
                "failures": [],
                "phytomni_state": {
                    "submission_rejections": [
                        {"goal": "AT1G01010", "code": "upstream_timeout"}
                    ]
                },
            }
        ),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="no remote task was accepted",
    ):
        await agent.arun("ath", "AT1G01010")


def test_design_outcome_skips_projector_doomed_task_ids() -> None:
    """Synthetic rejected-* ids must not count as accepted submissions."""
    updates = _project_design_submission_updates(
        {
            "design_task_result": [
                {
                    "analysis_type": "protein_design_analysis",
                    "_submission_rejected": {
                        "goal": "AT1G01010",
                        "code": "upstream_rejected",
                    },
                }
            ]
        }
    )
    outcome = _design_submission_outcome(
        {
            **updates,
            "phytomni_state": {
                "submission_rejections": updates.get("submission_rejections")
            },
        }
    )
    assert outcome.kind == "rejected"
    assert not outcome.task_ids
    doomed = updates["design_task_result"][0]
    assert doomed["accepted"] is False
    assert str(doomed["task_id"]).startswith("rejected-")


async def test_arun_rejects_projector_doomed_task_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native MCP must still raise when only unpollable rejected ids exist."""
    agent = _build_agent()
    monkeypatch.setattr(
        design_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "design_task_result": [
                    {
                        "task_id": "rejected-protein_design_analysis",
                        "accepted": False,
                        "status": "failed",
                        "analysis_type": "protein_design_analysis",
                        "error_code": "upstream_rejected",
                    }
                ],
                "error": None,
                "failures": [],
                "phytomni_state": {
                    "submission_rejections": [
                        {"goal": "AT1G01010", "code": "upstream_rejected"}
                    ]
                },
            }
        ),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="no remote task was accepted",
    ):
        await agent.arun("ath", "AT1G01010")


async def test_arun_rejected_returns_when_run_is_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reserved umbrella can persist doomed children instead of raising."""
    agent = _build_agent()
    monkeypatch.setattr(
        design_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "design_task_result": [],
                "error": None,
                "failures": [],
                "phytomni_state": {
                    "submission_rejections": [
                        {
                            "goal": "protein_structure_analysis",
                            "code": "input_rejected",
                        }
                    ]
                },
            }
        ),
    )

    with request_context("alice", "req-rejected", "run-reserved"):
        result = await agent.arun("ath", "AT1G01010")
        assert current_accepted_task_ids() == ()

    assert result["task_ids"] == []
    rejections = result["phytomni_state"]["submission_rejections"]
    assert rejections[0]["code"] == "input_rejected"


async def test_arun_rejects_unpersistable_design_a2a_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A2A pending work cannot escape as zero-task native running work."""
    agent = _build_agent()
    pending = {"a2a_pending": [{"task_id": "peer-task"}]}
    monkeypatch.setattr(
        design_agent_module,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "design_task_result": [{"task_id": "local-task"}],
                "error": None,
                "failures": [],
                "phytomni_state": pending,
            }
        ),
    )

    with pytest.raises(RemoteAnalysisSubmissionError) as caught:
        await agent.arun("ath", "AT1G01010")
    assert "no remote task was accepted" in str(caught.value)


async def test_design_dispatch_propagates_missing_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local task-id invariant must not become an upstream warning."""
    agent = _build_agent()
    install_async_failure(
        monkeypatch,
        agent,
        "_dispatch_and_wait_analysis",
        RemoteAnalysisSubmissionError("missing task id"),
    )
    state = cast(
        Any,
        analysis_node_state(
            species_code="ath",
            gene_id="AT1G01010",
            analysis_type="protein_design_analysis",
            output_dir="/obs/out",
            interop_mode="off",
            interop_targets=[],
        ),
    )

    with pytest.raises(RemoteAnalysisSubmissionError, match="missing task"):
        await agent.run_design_node(state)


async def test_design_dispatch_propagates_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected fan-out failures must not become submission warnings."""
    agent = _build_agent()
    install_analysis_prompt_parts(monkeypatch, agent)
    monkeypatch.setattr(
        agent,
        "_dispatch_and_wait_analysis",
        AsyncMock(side_effect=AssertionError("dispatch invariant")),
    )

    with pytest.raises(AssertionError, match="dispatch invariant"):
        await agent.run_design_node(
            cast(
                Any,
                analysis_node_state(
                    species_code="ath",
                    gene_id="AT1G01010",
                    analysis_type="protein_design_analysis",
                    output_dir="/obs/out",
                    interop_mode="off",
                    interop_targets=[],
                ),
            )
        )


def test_design_mixed_a2a_pending_is_not_full_submission() -> None:
    """A local acceptance plus an unresolved A2A pause is partial."""
    outcome = getattr(design_agent_module, "_design_submission_outcome")(
        mixed_submission_state(
            {"design_task_result": [{"task_id": "local-task"}]}
        )
    )

    assert_partial_submission_outcome(outcome)
