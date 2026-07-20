# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dispatch tests for the evolution analyst-subgraph submission.

Asserts ``submit_evolution_task_node`` always routes through
``submit_analyst_via_subgraph``. The branch is pinned without
constructing a real ``AnalystAgent``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.evolution import graph as evolution_graph
from mcp_server_phytomni.agents.evolution.graph import (
    submit_evolution_task_node,
)
from mcp_server_phytomni.agents.evolution.state import EvolutionState
from mcp_server_phytomni.agents.shared.analysis_requests import (
    AnalystAnalysisRequest,
    build_analyst_analysis_request,
)

pytestmark = pytest.mark.agent


def _state_with_taxids() -> EvolutionState:
    """Build an EvolutionState ready for ``submit_evolution_task_node``.

    The resolve step has populated ``target_taxids``; the submit node
    only reads taxids / species_code / gene_id plus the ``kwargs`` /
    ``batch`` overrides so the surrounding state stays minimal.
    """
    return {
        "query": "Evolution analysis for AT1G01010",
        "species_code": "ath",
        "gene_id": "AT1G01010",
        "target_taxids": "3702,3711",
        "kwargs": {"user_id": "user-test"},
        "batch": True,
        "enable_auto_select": False,
    }


def _install_submit_deps(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Patch prompt / data deps and a sentinel ``agent.submit`` guard.

    The returned mock pins that the removed free-function submit path
    is never awaited now that dispatch always routes via subgraph.
    """

    async def fake_submit(**kwargs: Any) -> dict[str, Any]:
        return {"task_id": "free-fn-evo-task", "submit_kwargs": kwargs}

    submit_mock = AsyncMock(side_effect=fake_submit)
    monkeypatch.setattr(evolution_graph.agent, "submit", submit_mock)
    monkeypatch.setattr(
        evolution_graph.agent,
        "get_prompt",
        lambda *_a, **_kw: "prompt-stub",
    )
    monkeypatch.setattr(
        evolution_graph.agent,
        "get_data_list",
        lambda *_a, **_kw: ["obs://data/evo-1"],
    )
    return submit_mock


async def test_submit_evolution_task_uses_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submission delegates to ``submit_analyst_via_subgraph``."""
    free_fn_mock = _install_submit_deps(monkeypatch)
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "subgraph-evo-task",
            "output_dir": "obs://run/evo-out",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        evolution_graph, "submit_analyst_via_subgraph", subgraph_mock
    )
    monkeypatch.setattr(
        evolution_graph,
        "_build_submit_agent",
        lambda *_a, **_kw: ("analyst-agent-stub", "", "small", "thread-x"),
    )

    result = await submit_evolution_task_node(_state_with_taxids())

    assert result["evolution_agents_task"]["task_id"] == "subgraph-evo-task"
    subgraph_mock.assert_awaited_once()
    free_fn_mock.assert_not_awaited()


async def test_submit_evolution_subgraph_request_carries_target_and_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Submission uses ``gene_id`` as ``target_id`` and pins polling.

    The subgraph helper builds the dispatch request from the resolved
    gene_id and threads ``is_polling=False`` so the analyst graph runs
    fire-and-poll-elsewhere.
    """
    _install_submit_deps(monkeypatch)
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-evo-task"})
    monkeypatch.setattr(
        evolution_graph, "submit_analyst_via_subgraph", subgraph_mock
    )
    monkeypatch.setattr(
        evolution_graph,
        "_build_submit_agent",
        lambda *_a, **_kw: ("analyst-agent-stub", "", "small", "thread-x"),
    )

    await submit_evolution_task_node(_state_with_taxids())

    call_args = subgraph_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
    assert request["analysis_type"] == "evolution_analysis"
    assert request["target_id"] == "AT1G01010"
    assert request["prompt_parts"][0] == "prompt-stub"
    assert (
        request["output_dir"] == evolution_graph.DEEP_GENOME_CONFIG.OUTPUT_DIR
    )
    assert request["compute_resource"] == "medium"
    assert request["prompt_parts"][1] == "prompt-stub"
    assert request["prompt_parts"][2] == ["obs://data/evo-1"]
    assert call_args.kwargs["is_polling"] is False


def test_analysis_request_builder_keeps_evolution_fields_domain_neutral() -> (
    None
):
    """The same builder preserves an evolution target without branching."""
    request = build_analyst_analysis_request(
        analysis_type="evolution_analysis",
        target_id="AT1G01010",
        output_dir="obs://run/evolution-out",
        prompt_parts=("goal", "meta", {"obs://data/evo-1": "evolution"}),
        compute_resource="medium",
    )

    assert isinstance(request, AnalystAnalysisRequest)
    assert request.to_payload()["target_id"] == "AT1G01010"
    assert request.to_payload()["prompt_parts"] == (
        "goal",
        "meta",
        {"obs://data/evo-1": "evolution"},
    )
