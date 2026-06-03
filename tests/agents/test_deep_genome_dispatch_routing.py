# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for DeepGenome dispatch routing and pure helpers.

Pins the conditional routing in DeepGenomeDispatchMixin (the use_analyst /
use_data branches that pick which downstream node runs) and the two
module-level pure helpers ``_homology_gene_lists`` / ``_interaction_gene_list``
that split BI rows into ortholog / paralog / interaction groupings.
"""

from __future__ import annotations

from typing import Any, Dict, List, cast

import pytest
from langgraph.constants import END

from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeState
from mcp_server_phytomni.agents.deep_genome.dispatch import (
    DeepGenomeDispatchMixin,
    _homology_gene_lists,
    _interaction_gene_list,
)

pytestmark = pytest.mark.unit


class _RouteProbe(DeepGenomeDispatchMixin):
    """Test-only subclass exposing protected routing helpers publicly.

    The mixin's ``_route_*`` methods are protected because production
    code only calls them from the LangGraph workflow builder on the same
    class. Tests exercise them through this subclass so the calls stay
    inside the class hierarchy (no pylint W0212 protected-access escape).
    """

    def route_start(self, state: DeepGenomeState):
        """Public proxy for ``_route_start``."""
        return self._route_start(state)

    def route_after_knowledge(self, state: DeepGenomeState):
        """Public proxy for ``_route_after_knowledge``."""
        return self._route_after_knowledge(state)

    def route_after_gene_summary(self, state: DeepGenomeState):
        """Public proxy for ``_route_after_gene_summary``."""
        return self._route_after_gene_summary(state)

    def route_after_part1(self, state: DeepGenomeState):
        """Public proxy for ``_route_after_part1``."""
        return self._route_after_part1(state)

    def route_after_synthesize(self, state: DeepGenomeState):
        """Public proxy for ``_route_after_synthesize``."""
        return self._route_after_synthesize(state)

    def route_analyst_tasks(self, state: DeepGenomeState):
        """Public proxy for ``_route_analyst_tasks``."""
        return self._route_analyst_tasks(state)

    def route_after_analyst(self, state: DeepGenomeState):
        """Public proxy for ``_route_after_analyst``."""
        return self._route_after_analyst(state)


def _state(**config_params: Any) -> DeepGenomeState:
    """Build a DeepGenomeState mapping with overridable config_params."""
    return cast(DeepGenomeState, {"config_params": dict(config_params)})


def test_homology_gene_lists_splits_orthologs_and_paralogs() -> None:
    """``_homology_gene_lists`` separates rows by species_code match."""
    response: Dict[str, List[Dict[str, str]]] = {
        "data": [
            {"homology_species": "osa", "homology_gene_id": "Os02g1"},
            {"homology_species": "ath", "homology_gene_id": "AT1G2"},
            {"homology_species": "osa", "homology_gene_id": "Os03g3"},
        ]
    }

    orthologs, paralogs = _homology_gene_lists(response, "osa")

    assert orthologs == [("ath", "AT1G2")]
    assert paralogs == [("osa", "Os02g1"), ("osa", "Os03g3")]


def test_homology_gene_lists_deduplicates_repeated_pairs() -> None:
    """Repeated (species, gene) pairs are collapsed via set membership."""
    response = {
        "data": [
            {"homology_species": "ath", "homology_gene_id": "AT1G2"},
            {"homology_species": "ath", "homology_gene_id": "AT1G2"},
        ]
    }

    orthologs, paralogs = _homology_gene_lists(response, "osa")

    assert orthologs == [("ath", "AT1G2")]
    assert paralogs == []


def test_interaction_gene_list_picks_partner_for_each_match() -> None:
    """A query-side match yields the interact partner and vice versa."""
    response = {
        "data": [
            {"query_gene_id": "Os01g1", "interact_gene_id": "Os02g2"},
            {"query_gene_id": "Os03g3", "interact_gene_id": "Os01g1"},
            {"query_gene_id": "Os04g4", "interact_gene_id": "Os05g5"},
        ]
    }

    interactions = _interaction_gene_list(response, "Os01g1", "osa")

    assert interactions == [("osa", "Os02g2"), ("osa", "Os03g3")]


@pytest.mark.parametrize(
    ("use_analyst", "use_data", "expected"),
    [
        (
            True,
            True,
            ["knowledge_node", "data_node", "prepare_tasks_node"],
        ),
        (True, False, ["knowledge_node", "prepare_tasks_node"]),
        (False, True, ["knowledge_node", "data_node"]),
        (False, False, ["knowledge_node"]),
    ],
)
def test_route_start_dispatches_per_config_flags(
    use_analyst: bool, use_data: bool, expected: List[str]
) -> None:
    """All four (use_analyst, use_data) combos pick the right initial nodes."""
    state = _state(use_analyst_agent=use_analyst, use_data_agent=use_data)

    result = _RouteProbe().route_start(state)

    assert result == expected


@pytest.mark.parametrize(
    ("use_data", "expected"),
    [(True, "gene_annotation_node"), (False, "gene_summary_node")],
)
def test_route_after_knowledge_chooses_annotation_or_summary(
    use_data: bool, expected: str
) -> None:
    """use_data picks between gene_annotation and gene_summary."""
    state = _state(use_data_agent=use_data)

    result = _RouteProbe().route_after_knowledge(state)

    assert result == expected


@pytest.mark.parametrize(
    ("use_analyst", "expected"),
    [(True, "experiment_node"), (False, "introduction_node")],
)
def test_route_after_gene_summary_chooses_experiment_or_introduction(
    use_analyst: bool, expected: str
) -> None:
    """use_analyst picks between experiment_node and introduction_node."""
    state = _state(use_analyst_agent=use_analyst)

    result = _RouteProbe().route_after_gene_summary(state)

    assert result == expected


@pytest.mark.parametrize(
    ("use_analyst", "expected"),
    [(True, "experiment_node"), (False, "introduction_node")],
)
def test_route_after_part1_chooses_experiment_or_introduction(
    use_analyst: bool, expected: str
) -> None:
    """The post-part1 router mirrors post-gene-summary semantics."""
    state = _state(use_analyst_agent=use_analyst)

    result = _RouteProbe().route_after_part1(state)

    assert result == expected


@pytest.mark.parametrize(
    ("use_data", "expected"),
    [(True, "experiment_node"), (False, "introduction_node")],
)
def test_route_after_synthesize_chooses_experiment_or_introduction(
    use_data: bool, expected: str
) -> None:
    """use_data picks between experiment_node and introduction_node."""
    state = _state(use_data_agent=use_data)

    result = _RouteProbe().route_after_synthesize(state)

    assert result == expected


def test_route_analyst_tasks_emits_send_per_task() -> None:
    """``_route_analyst_tasks`` produces one Send object per analysis task."""
    state = cast(
        DeepGenomeState,
        {
            "analysis_tasks": [
                {"analysis_type": "homology", "gene_id": "g1"},
                {"analysis_type": "interaction", "gene_id": "g2"},
            ]
        },
    )

    sends = _RouteProbe().route_analyst_tasks(state)

    assert len(sends) == 2
    assert sends[0].node == "analyst_node"
    assert sends[0].arg == {
        "task_index": 0,
        "task_submit_sleep": 0,
        "analysis_type": "homology",
        "gene_id": "g1",
    }
    assert sends[1].arg["task_index"] == 1


def test_route_after_analyst_returns_end_sentinel() -> None:
    """The post-analyst router unconditionally returns the END sentinel.

    Pins the barrier contract: every Send instance into ``analyst_node``
    must terminate before ``synthesize_node`` runs, and the END sentinel
    is the routing signal that lets LangGraph collect the fan-out.
    """
    result = _RouteProbe().route_after_analyst(_state())

    assert result is END
