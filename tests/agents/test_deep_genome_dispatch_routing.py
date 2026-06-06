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

pytestmark = pytest.mark.agent


def _build_mixin_instance(
    use_evo: bool = False,
    use_design: bool = False,
    use_analyst: bool = False,
) -> Any:
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
            "USE_ANALYST_SUBGRAPH": use_analyst,
        }
    )
    return SimpleNamespace(
        deep_genome_config=config,
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
        species="Arabidopsis thaliana",
        gene_id="AT1G01010",
        output_dir="/obs/run/out",
    )


def _install_shared_helper_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AsyncMock, AsyncMock]:
    """Patch the two shared dispatch helpers on the dispatch module.

    The non-transferred (12 remaining) analysis types route through
    ``submit_analyst_via_subgraph`` (USE_ANALYST_SUBGRAPH=True) or
    ``submit_analyst_analysis`` (legacy fallback) since the cluster
    #9 sunset removed deep_genome's inline ``analyst_agent.arun``
    call. Tests patch both so each branch can be asserted in
    isolation.
    """
    subgraph_mock = AsyncMock(
        return_value={
            "task_id": "subgraph-id",
            "output_dir": "/obs/subgraph",
            "task_status": "SUCCEEDED",
        }
    )
    legacy_mock = AsyncMock(
        return_value={
            "task_id": "legacy-id",
            "output_dir": "/obs/legacy",
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        dispatch_module, "submit_analyst_via_subgraph", subgraph_mock
    )
    monkeypatch.setattr(
        dispatch_module, "submit_analyst_analysis", legacy_mock
    )
    return subgraph_mock, legacy_mock


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
    mixin = _build_mixin_instance(use_evo=True)
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock, legacy_mock = _install_shared_helper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("evolution_analysis")
        )
    )

    assert result["task_id"] == "evo-id"
    wrappers["evolution_analysis_for_gene"].assert_awaited_once()
    subgraph_mock.assert_not_awaited()
    legacy_mock.assert_not_awaited()


async def test_protein_structure_routes_to_wrapper_when_design_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``USE_DESIGN_SUBGRAPH=True`` routes structure to wrapper."""
    mixin = _build_mixin_instance(use_design=True)
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock, legacy_mock = _install_shared_helper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("protein_structure_analysis")
        )
    )

    assert result["task_id"] == "struct-id"
    wrappers["protein_structure_for_gene"].assert_awaited_once()
    subgraph_mock.assert_not_awaited()
    legacy_mock.assert_not_awaited()


async def test_promoter_routes_to_wrapper_when_design_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``USE_DESIGN_SUBGRAPH=True`` routes promoter to wrapper."""
    mixin = _build_mixin_instance(use_design=True)
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock, legacy_mock = _install_shared_helper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("promoter_analysis")
        )
    )

    assert result["task_id"] == "prom-id"
    wrappers["promoter_design_for_gene"].assert_awaited_once()
    subgraph_mock.assert_not_awaited()
    legacy_mock.assert_not_awaited()


async def test_evolution_analysis_stays_legacy_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off keeps evolution_analysis on the shared legacy path."""
    mixin = _build_mixin_instance()
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock, legacy_mock = _install_shared_helper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("evolution_analysis")
        )
    )

    assert result["task_id"] == "legacy-id"
    legacy_mock.assert_awaited_once()
    subgraph_mock.assert_not_awaited()
    wrappers["evolution_analysis_for_gene"].assert_not_awaited()


async def test_non_transferred_type_routes_legacy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``haplotypes_analysis`` defaults to ``submit_analyst_analysis``.

    Without ``USE_ANALYST_SUBGRAPH=True`` the non-transferred 12
    types stay on the shared legacy helper that fans out
    ``analyst.arun``, mirroring the pre-Phase-6 production default.
    """
    mixin = _build_mixin_instance(use_evo=True, use_design=True)
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock, legacy_mock = _install_shared_helper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("haplotypes_analysis")
        )
    )

    assert result["task_id"] == "legacy-id"
    legacy_mock.assert_awaited_once()
    subgraph_mock.assert_not_awaited()
    for wrapper in wrappers.values():
        wrapper.assert_not_awaited()


async def test_non_transferred_type_routes_subgraph_when_analyst_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``USE_ANALYST_SUBGRAPH=True`` routes non-transferred types to subgraph.

    Pins cluster #9 sunset: the 12 remaining analysis types share
    the same ``submit_analyst_via_subgraph`` chokepoint as design /
    network / research / environment / evolution once their config's
    ``USE_ANALYST_SUBGRAPH`` flag is on.
    """
    mixin = _build_mixin_instance(use_analyst=True)
    wrappers = _install_wrapper_mocks(monkeypatch)
    subgraph_mock, legacy_mock = _install_shared_helper_mocks(monkeypatch)

    result = (
        await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
            mixin, _context("haplotypes_analysis")
        )
    )

    assert result["task_id"] == "subgraph-id"
    subgraph_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()
    for wrapper in wrappers.values():
        wrapper.assert_not_awaited()


async def test_subgraph_helper_called_with_is_polling_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deep_genome dispatch passes ``is_polling=True`` to both helpers.

    Pins the polling semantics that match the historical
    ``analyst_agent.arun(is_polling=True, ...)`` call site; the legacy
    fallback defaults to ``is_polling=False`` so the explicit kwarg
    forwarding is the contract.
    """
    mixin = _build_mixin_instance(use_analyst=True)
    _install_wrapper_mocks(monkeypatch)
    subgraph_mock, _ = _install_shared_helper_mocks(monkeypatch)

    await dispatch_module.DeepGenomeDispatchMixin._submit_analysis_task(
        mixin, _context("haplotypes_analysis")
    )

    assert subgraph_mock.await_args is not None
    assert subgraph_mock.await_args.kwargs["is_polling"] is True
