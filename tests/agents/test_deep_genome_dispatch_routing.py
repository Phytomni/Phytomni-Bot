# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Flag-branch tests for the deep_genome producer-wrapper reroute.

Pins ``DeepGenomeDispatchMixin._submit_analysis_task`` routing: when
``USE_EVOLUTION_SUBGRAPH`` is True, ``evolution_analysis`` tasks call
``evolution_analysis_for_gene``; when ``USE_DESIGN_SUBGRAPH`` is True,
``protein_structure_analysis`` / ``promoter_analysis`` tasks call the
matching design module wrappers. Flag-off + non-transferred analysis
types stay on the legacy ``analyst_agent.arun`` path.
"""

# pylint: disable=protected-access
# Test file exercises ``_submit_analysis_task`` (the internal
# dispatch chokepoint inside DeepGenomeAgents) directly to assert
# flag routing.

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
from mcp_server_phytomni.storage.path_policy import RunIdentity

pytestmark = pytest.mark.agent


def _build_mixin_instance(use_evo: bool, use_design: bool) -> Any:
    """Construct a minimal stand-in for ``DeepGenomeDispatchMixin``.

    The dispatch mixin only reads ``self.deep_genome_config`` and
    ``self._agents.analyst_agent`` inside ``_submit_analysis_task``;
    a ``SimpleNamespace`` with those two attributes is enough to
    exercise the routing branch without constructing the full
    ``DeepGenomeAgents`` (which would compile a graph and bind a
    ``BriefGeneAgent`` subgraph).
    """
    config = DeepGenomeConfig().model_copy(
        update={
            "USE_EVOLUTION_SUBGRAPH": use_evo,
            "USE_DESIGN_SUBGRAPH": use_design,
        }
    )
    analyst_agent_mock = SimpleNamespace(
        arun=AsyncMock(
            return_value={
                "task_id": "legacy-id",
                "output_dir": "/legacy",
                "task_status": "SUCCEEDED",
            }
        )
    )
    return SimpleNamespace(
        deep_genome_config=config,
        sensitive_config=SimpleNamespace(),
        _agents=SimpleNamespace(analyst_agent=analyst_agent_mock),
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
        species="Arabidopsis thaliana",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
    )


def _run_identity() -> RunIdentity:
    """Build a deterministic run identity for the dispatch helper."""
    return RunIdentity.create(user_id="user-test", scope="evolution_analysis")


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


async def test_evolution_analysis_routes_to_wrapper_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``USE_EVOLUTION_SUBGRAPH=True`` calls evolution_analysis_for_gene."""
    mixin = _build_mixin_instance(use_evo=True, use_design=False)
    wrappers = _install_wrapper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("evolution_analysis"), _run_identity()
        )
    )

    assert result["task_id"] == "evo-id"
    wrappers["evolution_analysis_for_gene"].assert_awaited_once()
    mixin._agents.analyst_agent.arun.assert_not_awaited()


async def test_protein_structure_routes_to_wrapper_when_design_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``USE_DESIGN_SUBGRAPH=True`` routes structure to wrapper."""
    mixin = _build_mixin_instance(use_evo=False, use_design=True)
    wrappers = _install_wrapper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("protein_structure_analysis"), _run_identity()
        )
    )

    assert result["task_id"] == "struct-id"
    wrappers["protein_structure_for_gene"].assert_awaited_once()
    mixin._agents.analyst_agent.arun.assert_not_awaited()


async def test_promoter_routes_to_wrapper_when_design_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``USE_DESIGN_SUBGRAPH=True`` routes promoter to wrapper."""
    mixin = _build_mixin_instance(use_evo=False, use_design=True)
    wrappers = _install_wrapper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("promoter_analysis"), _run_identity()
        )
    )

    assert result["task_id"] == "prom-id"
    wrappers["promoter_design_for_gene"].assert_awaited_once()
    mixin._agents.analyst_agent.arun.assert_not_awaited()


async def test_evolution_analysis_stays_legacy_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off keeps evolution_analysis on the legacy analyst.arun path."""
    mixin = _build_mixin_instance(use_evo=False, use_design=False)
    wrappers = _install_wrapper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("evolution_analysis"), _run_identity()
        )
    )

    assert result["task_id"] == "legacy-id"
    mixin._agents.analyst_agent.arun.assert_awaited_once()
    wrappers["evolution_analysis_for_gene"].assert_not_awaited()


async def test_non_transferred_type_stays_legacy_even_with_flags_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both flags on but ``haplotypes_analysis`` still goes legacy.

    The reroute is strictly opt-in for the 3 transferred analysis
    types. Other 12 analysis types keep the legacy path until cluster
    #9 sunset wires USE_ANALYST_SUBGRAPH at the same chokepoint.
    """
    mixin = _build_mixin_instance(use_evo=True, use_design=True)
    wrappers = _install_wrapper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("haplotypes_analysis"), _run_identity()
        )
    )

    assert result["task_id"] == "legacy-id"
    mixin._agents.analyst_agent.arun.assert_awaited_once()
    wrappers["evolution_analysis_for_gene"].assert_not_awaited()
    wrappers["protein_structure_for_gene"].assert_not_awaited()
    wrappers["promoter_design_for_gene"].assert_not_awaited()
