# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helper-method tests for agents/design/agent.

Pin the compute-resource tier mapping (``protein_design_analysis``
gets ``"medium"``; everything else gets ``"small"``) and the
``_analysis_prompt_parts`` guard that raises ``ValueError`` on an
unknown analysis type before any prompt lookup.
"""

# The helper probes below intentionally target private design decisions; each
# carries a symbol-scoped protected-access directive.

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

import mcp_server_phytomni.agents.design.agent as design_agent_module
from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.design.agent import (
    DigitalDesignAgents,
    DigitalDesignConfig,
    _DispatchOptions,
)
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisRequest,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent() -> DigitalDesignAgents:
    """Construct a design agent wired to a stub analyst.

    Uses ``SimpleNamespace`` for the analyst stand-in (same pattern as
    ``_analyst_fakes.py``) so pylint's R0903 too-few-public-methods
    rule does not trip on a single-method stub class.
    """
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    return DigitalDesignAgents(
        digital_design_config=DigitalDesignConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )


def test_get_compute_resource_protein_design_returns_medium() -> None:
    """Protein design is the only documented medium-tier analysis."""
    # pylint: disable=protected-access
    agent = _build_agent()

    assert agent._get_compute_resource("protein_design_analysis") == "medium"


def test_get_compute_resource_unknown_falls_back_to_small() -> None:
    """Every other analysis type stays on the small tier by default."""
    # pylint: disable=protected-access
    agent = _build_agent()

    assert agent._get_compute_resource("promoter_design_analysis") == "small"
    assert agent._get_compute_resource("does-not-exist") == "small"


def test_analysis_prompt_parts_rejects_unknown_type() -> None:
    """Unknown analysis types fail loud before any prompt lookup.

    The guard sits ahead of ``get_prompt`` and ``get_data_list``
    so a misconfigured caller never reaches the species metadata
    layer with a typo in the analysis-type slug.
    """
    # pylint: disable=protected-access
    agent = _build_agent()

    with pytest.raises(ValueError, match="does-not-exist"):
        agent._analysis_prompt_parts(
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
