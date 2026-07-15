# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Routing tests for the deep_genome producer-wrapper reroute.

Pins ``DeepGenomeDispatchMixin`` routing: ``evolution_analysis`` fans to
the mounted ``evolution_node``; ``protein_structure_analysis`` /
``promoter_analysis`` tasks call the matching design module wrappers
inside ``_submit_analysis_task``. Non-transferred analysis types route
through ``submit_analyst_via_subgraph``.
"""

# pylint: disable=protected-access
# Test file exercises ``_submit_analysis_task`` (the internal
# dispatch chokepoint inside DeepGenomeAgents) directly to assert
# producer-wrapper routing.

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.deep_genome import dispatch as dispatch_module
from mcp_server_phytomni.agents.deep_genome.coordinator import RemoteSubmission
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    AnalysisDispatchContext,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig
from mcp_server_phytomni.graphs import analyst_dispatch_adapters

pytestmark = pytest.mark.agent


def _build_mixin_instance() -> Any:
    """Construct a minimal stand-in for ``DeepGenomeDispatchMixin``.

    The dispatch mixin only reads ``self.deep_genome_config`` and
    ``self._agents.analyst_agent`` inside ``_submit_analysis_task``;
    a ``SimpleNamespace`` with those two attributes is enough to
    exercise the routing branch without constructing the full
    ``DeepGenomeAgents`` (which would compile a graph and bind a
    ``BriefGeneAgent`` subgraph).
    """
    return SimpleNamespace(
        deep_genome_config=DeepGenomeConfig(),
        sensitive_config=SimpleNamespace(),
        _agents=SimpleNamespace(analyst_agent="analyst-stub"),
        _analysis_prompt_parts=lambda _ctx: (
            "goal-stub",
            ["data-stub"],
            "meta-stub",
            "small",
        ),
    )


def _context(analysis_type: str) -> AnalysisDispatchContext:
    """Build a dispatch context for a single-gene single-task submit."""
    return AnalysisDispatchContext(
        analysis_type=analysis_type,
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
    )


def _install_shared_helper_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Patch the shared dispatch helper on the dispatch module.

    The non-transferred (12 remaining) analysis types always route
    through ``submit_analyst_via_subgraph`` since the cluster #9
    sunset removed deep_genome's inline ``analyst_agent.arun`` call.
    """
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "caller-1",
            "source_task_id": "remote-1",
            "output_dir": "/obs/subgraph",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        dispatch_module, "submit_analyst_via_subgraph", subgraph_mock
    )
    return subgraph_mock


def _install_wrapper_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, AsyncMock]:
    """Patch the 2 module-level design producer wrappers on dispatch.

    Evolution is no longer a producer-wrapper branch inside
    ``_submit_analysis_task``; it is routed to the mounted
    ``evolution_node`` instead, so only the two design wrappers remain.
    """
    mocks = {
        "protein_structure_for_gene": AsyncMock(
            return_value={
                "task_id": "struct-id",
                "output_dir": "/obs/struct",
                "task_status": "SUCCEEDED",
            }
        ),
        "promoter_design_for_gene": AsyncMock(
            return_value={
                "task_id": "prom-id",
                "output_dir": "/obs/prom",
                "task_status": "SUCCEEDED",
            }
        ),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(dispatch_module, name, mock)
    return mocks


def test_route_analyst_tasks_sends_evolution_to_evolution_node() -> None:
    """The evolution task fans to evolution_node; others to analyst_node.

    Evolution stays an entry in ``analysis_tasks`` (so the synthesize
    barrier's ``total_expected`` count is unchanged) but routes to the
    dedicated mounted ``evolution_node`` rather than the generic
    ``analyst_node``.
    """
    state: Any = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": "evolution_analysis",
                "target_gene": "g1",
                "species_code": "osa",
            },
            {
                "analysis_type": "single_cell_analysis",
                "target_gene": "g1",
                "species_code": "osa",
            },
        ],
    }

    sends = dispatch_module.DeepGenomeDispatchMixin._route_analyst_tasks(
        object(), state
    )

    targets = {send.node for send in sends}
    assert targets == {"evolution_node", "single_cell_node"}
    evo = next(send for send in sends if send.node == "evolution_node")
    assert evo.arg["analysis_type"] == "evolution_analysis"
    assert evo.arg["task_index"] == 0


def test_route_analyst_tasks_sends_design_to_design_node() -> None:
    """The digital_design task fans to the mounted ``design_node``."""
    state: Any = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": "digital_design",
                "target_gene": "g1",
                "species_code": "osa",
            },
            {
                "analysis_type": "single_cell_analysis",
                "target_gene": "g1",
                "species_code": "osa",
            },
        ],
    }

    sends = dispatch_module.DeepGenomeDispatchMixin._route_analyst_tasks(
        object(), state
    )

    targets = {send.node for send in sends}
    assert targets == {"design_node", "single_cell_node"}
    design = next(send for send in sends if send.node == "design_node")
    assert design.arg["analysis_type"] == "digital_design"
    assert design.arg["task_index"] == 0


def test_route_analyst_tasks_sends_each_generic_to_its_own_node() -> None:
    """Each generic analysis_type fans to its deterministic named node."""
    state: Any = {
        "task_submit_sleep": 0,
        "analysis_tasks": [
            {
                "analysis_type": analysis_type,
                "target_gene": "g1",
                "species_code": "osa",
            }
            for analysis_type in dispatch_module.GENERIC_ANALYSIS_NODE_TYPES
        ],
    }

    sends = dispatch_module.DeepGenomeDispatchMixin._route_analyst_tasks(
        object(), state
    )

    by_type = {send.arg["analysis_type"]: send.node for send in sends}
    assert by_type == {
        analysis_type: dispatch_module._analyst_node_name(analysis_type)
        for analysis_type in dispatch_module.GENERIC_ANALYSIS_NODE_TYPES
    }
    assert "analyst_node" not in {send.node for send in sends}


async def test_protein_structure_routes_to_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``protein_structure_analysis`` routes structure to its wrapper."""
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("protein_structure_analysis")
        )
    )

    assert isinstance(result, dict)
    assert result["task_id"] == "struct-id"
    wrappers["protein_structure_for_gene"].assert_awaited_once_with(
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
    )
    subgraph_mock.assert_not_awaited()


async def test_promoter_routes_to_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``promoter_analysis`` routes promoter to its wrapper."""
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("promoter_analysis")
        )
    )

    assert isinstance(result, dict)
    assert result["task_id"] == "prom-id"
    wrappers["promoter_design_for_gene"].assert_awaited_once_with(
        species_code="ath",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
    )
    subgraph_mock.assert_not_awaited()


async def test_non_transferred_type_routes_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-transferred types route through ``submit_analyst_via_subgraph``.

    Pins cluster #9 sunset: the 12 remaining analysis types share
    the same ``submit_analyst_via_subgraph`` chokepoint as design /
    network / research / environment / evolution.
    """
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("haplotypes_analysis")
        )
    )

    assert isinstance(result, RemoteSubmission)
    assert result.poll_task_id == "remote-1"
    assert result.submitted_task_id == "caller-1"
    subgraph_mock.assert_awaited_once()
    for wrapper in wrappers.values():
        wrapper.assert_not_awaited()


async def test_deep_genome_generic_dispatch_is_submit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepGenome submits generic work without nested polling.

    The submission acknowledgement is normalized immediately so the
    coordinator can poll the effective remote task id later.
    """
    mixin = _build_mixin_instance()
    _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("haplotypes_analysis")
        )
    )

    assert subgraph_mock.await_args is not None
    assert subgraph_mock.await_args.kwargs["is_polling"] is False
    assert isinstance(result, RemoteSubmission)
    assert result.poll_task_id == "remote-1"


async def test_default_analyst_adapter_still_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone adapter default keeps its historical polling mode."""
    captured: list[bool] = []

    monkeypatch.setattr(
        analyst_dispatch_adapters,
        "prepare_analyst_dispatch_context",
        lambda *_args: SimpleNamespace(
            analysis_type="standalone",
            output_dir="/obs/out",
            thread_id="thread-1",
        ),
    )

    async def no_reuse(*_args: Any, **_kwargs: Any) -> None:
        """Keep the adapter on its fresh-submission path."""
        return None

    monkeypatch.setattr(
        analyst_dispatch_adapters, "_reuse_prior_dispatch", no_reuse
    )

    def capture_input(payload: Any, *, is_polling: bool = True) -> dict:
        """Record the adapter's default polling argument."""
        del payload
        captured.append(is_polling)
        return {}

    monkeypatch.setattr(
        analyst_dispatch_adapters,
        "map_send_payload_to_analyst_input",
        capture_input,
    )
    monkeypatch.setattr(
        analyst_dispatch_adapters,
        "record_dispatch_submission",
        lambda *_args, **_kwargs: None,
    )
    agent = SimpleNamespace(
        app=SimpleNamespace(
            ainvoke=AsyncMock(
                return_value={
                    "task_id": "standalone-1",
                    "output_dir": "/obs/out",
                }
            )
        )
    )

    await analyst_dispatch_adapters.submit_analyst_via_subgraph(
        agent,
        SimpleNamespace(USER_ID="alice"),
        SimpleNamespace(),
        {
            "analysis_type": "standalone",
            "target_id": "gene-1",
            "prompt_parts": ("goal", "meta", {}),
            "compute_resource": "small",
        },
    )

    assert captured == [True]


async def test_prepare_tasks_includes_protein_structure() -> None:
    """``_prepare_analysis_tasks`` enumerates a protein-structure task.

    The producer + ``load_protein_structure`` loader already exist; the
    task was simply never added to the analysis list, so the §Protein
    Structure section never rendered. ``species_code="ath"`` takes the
    ``case _`` branch and avoids the BI id-table lookup.
    """
    mixin = _build_mixin_instance()
    state: Any = {"gene_id": "AT1G01010", "species_code": "ath"}

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._prepare_analysis_tasks(
            mixin, state
        )
    )

    types = {task["analysis_type"] for task in result["analysis_tasks"]}
    assert "protein_structure_analysis" in types


def test_transferred_types_dropped_from_prompt_maps() -> None:
    """Producer-owned types leave the goal / meta prompt maps.

    evolution_analysis / protein_structure_analysis / promoter_analysis
    submit through the evolution and design producer wrappers (the
    routing tests above), so deep_genome no longer builds their goal or
    meta prompts and those two maps must not list them. The shared
    ANALYSIS_DATA_LIST_MAP still keeps protein_structure_analysis (its
    data lives under a non-identity key, structure_analysis, that the
    producer reuses via resolve_data_list_key); evolution and promoter
    are identity lookups and need no entry. Output-file features stay
    because deep_genome still downloads and summarizes the producer's
    results by analysis type.
    """
    transferred = {
        "evolution_analysis",
        "protein_structure_analysis",
        "promoter_analysis",
    }
    assert transferred.isdisjoint(dispatch_module.ANALYSIS_GOAL_TEMPLATE_MAP)
    assert transferred.isdisjoint(dispatch_module.ANALYSIS_META_TEMPLATE_MAP)
    # protein_structure_analysis is the one transferred type whose data
    # lives under a DIFFERENT key (structure_analysis), so it stays in
    # the shared ANALYSIS_DATA_LIST_MAP translation table; evolution and
    # promoter are identity lookups and need no entry.
    assert {"evolution_analysis", "promoter_analysis"}.isdisjoint(
        dispatch_module.ANALYSIS_DATA_LIST_MAP
    )
    assert (
        dispatch_module.ANALYSIS_DATA_LIST_MAP["protein_structure_analysis"]
        == "structure_analysis"
    )
    assert transferred <= set(dispatch_module.ANALYSIS_TARGET_FILE_FEATURE_MAP)
