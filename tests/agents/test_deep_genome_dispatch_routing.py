# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Routing tests for the deep_genome producer-wrapper reroute.

Pins ``DeepGenomeDispatchMixin._submit_analysis_task`` routing:
``evolution_analysis`` tasks call ``evolution_analysis_for_gene``;
``protein_structure_analysis`` / ``promoter_analysis`` tasks call the
matching design module wrappers. Non-transferred analysis types route
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
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    AnalysisDispatchContext,
)
from mcp_server_phytomni.config.defaults import DeepGenomeConfig

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
            "task_id": "subgraph-id",
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
    """Patch the 3 module-level producer wrappers on the dispatch module."""
    mocks = {
        "evolution_analysis_for_gene": AsyncMock(
            return_value={
                "task_id": "evo-id",
                "output_dir": "/obs/evo",
                "task_status": "SUCCEEDED",
            }
        ),
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


async def test_evolution_analysis_routes_to_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``evolution_analysis`` routes to evolution_analysis_for_gene."""
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("evolution_analysis")
        )
    )

    assert result["task_id"] == "evo-id"
    wrappers["evolution_analysis_for_gene"].assert_awaited_once()
    subgraph_mock.assert_not_awaited()


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

    assert result["task_id"] == "struct-id"
    wrappers["protein_structure_for_gene"].assert_awaited_once()
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

    assert result["task_id"] == "prom-id"
    wrappers["promoter_design_for_gene"].assert_awaited_once()
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

    assert result["task_id"] == "subgraph-id"
    subgraph_mock.assert_awaited_once()
    for wrapper in wrappers.values():
        wrapper.assert_not_awaited()


async def test_subgraph_helper_called_with_is_polling_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deep_genome dispatch passes ``is_polling=True`` to the helper.

    Pins the polling semantics that match the historical
    ``analyst_agent.arun(is_polling=True, ...)`` call site; the
    explicit kwarg forwarding is the contract.
    """
    mixin = _build_mixin_instance()
    _install_wrapper_mocks(monkeypatch)
    subgraph_mock = _install_shared_helper_mock(monkeypatch)

    await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
        mixin, _context("haplotypes_analysis")
    )

    assert subgraph_mock.await_args is not None
    assert subgraph_mock.await_args.kwargs["is_polling"] is True


def test_transferred_types_dropped_from_prompt_maps() -> None:
    """Producer-owned types leave the prompt / meta / data maps.

    evolution_analysis / protein_structure_analysis / promoter_analysis
    submit through the evolution and design producer wrappers (the
    routing tests above), so deep_genome no longer builds their goal,
    meta, or data-list prompts and the three maps must not list them.
    Their output-file features stay because deep_genome still downloads
    and summarizes the producer's results by analysis type.
    """
    transferred = {
        "evolution_analysis",
        "protein_structure_analysis",
        "promoter_analysis",
    }
    assert transferred.isdisjoint(dispatch_module.ANALYSIS_GOAL_TEMPLATE_MAP)
    assert transferred.isdisjoint(dispatch_module.ANALYSIS_META_TEMPLATE_MAP)
    assert transferred.isdisjoint(dispatch_module.ANALYSIS_DATA_LIST_MAP)
    assert transferred <= set(dispatch_module.ANALYSIS_TARGET_FILE_FEATURE_MAP)
