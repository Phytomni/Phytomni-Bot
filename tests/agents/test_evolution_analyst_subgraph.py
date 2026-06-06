# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for the evolution analyst-subgraph dispatch.

Asserts ``submit_evolution_task_node`` calls ``agent.submit`` directly
when ``USE_ANALYST_SUBGRAPH=False`` and routes through
``submit_analyst_via_subgraph`` when ``True``. Both branches are
pinned without constructing a real ``AnalystAgent``.
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

pytestmark = pytest.mark.agent


def _state_with_taxids() -> EvolutionState:
    """Build an EvolutionState ready for ``submit_evolution_task_node``.

    The resolve step has populated ``target_taxids``; the submit node
    only reads taxids / species / gene_id plus the ``kwargs`` / ``batch``
    overrides so the surrounding state stays minimal.
    """
    return {
        "query": "Evolution analysis for AT1G01010",
        "species": "Arabidopsis thaliana",
        "gene_id": "AT1G01010",
        "target_taxids": "3702,3711",
        "kwargs": {"user_id": "user-test"},
        "batch": True,
        "enable_auto_select": False,
    }


def _install_legacy_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Patch the prompt / data / submit dependencies for the legacy path."""

    async def fake_submit(**kwargs: Any) -> dict[str, Any]:
        return {"task_id": "legacy-evo-task", "submit_kwargs": kwargs}

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


async def test_submit_evolution_task_uses_legacy_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default flag-off path awaits the legacy ``agent.submit``.

    Pins the production-default routing: ``submit_evolution_task_node``
    must call ``analyst.submit`` directly and the subgraph helper must
    not run. ``USE_ANALYST_SUBGRAPH`` defaults to ``False`` on
    ``DeepGenomeConfig`` (the config evolution shares with deep_genome)
    so the default state of the config object is what production sees.
    """
    monkeypatch.setattr(
        evolution_graph.DEEP_GENOME_CONFIG, "USE_ANALYST_SUBGRAPH", False
    )
    legacy_mock = _install_legacy_submit(monkeypatch)
    subgraph_mock = AsyncMock(return_value={"task_id": "subgraph-evo-task"})
    monkeypatch.setattr(
        evolution_graph, "submit_analyst_via_subgraph", subgraph_mock
    )

    result = await submit_evolution_task_node(_state_with_taxids())

    assert result["evolution_agents_task"]["task_id"] == "legacy-evo-task"
    legacy_mock.assert_awaited_once()
    subgraph_mock.assert_not_awaited()


async def test_submit_evolution_task_uses_subgraph_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path delegates to ``submit_analyst_via_subgraph``."""
    monkeypatch.setattr(
        evolution_graph.DEEP_GENOME_CONFIG, "USE_ANALYST_SUBGRAPH", True
    )
    legacy_mock = _install_legacy_submit(monkeypatch)
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
    legacy_mock.assert_not_awaited()


async def test_submit_evolution_subgraph_request_carries_target_and_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on path uses ``gene_id`` as ``target_id`` and pins polling.

    The subgraph helper builds the dispatch request from the resolved
    gene_id and threads ``is_polling=False`` so the analyst graph runs
    fire-and-poll-elsewhere just like the legacy ``analyst.submit`` path.
    """
    monkeypatch.setattr(
        evolution_graph.DEEP_GENOME_CONFIG, "USE_ANALYST_SUBGRAPH", True
    )
    _install_legacy_submit(monkeypatch)
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
    assert call_args.kwargs["is_polling"] is False
