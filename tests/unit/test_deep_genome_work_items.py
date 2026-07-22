# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the immutable DeepGenome work-item plan."""

from __future__ import annotations

from dataclasses import asdict, fields
from itertools import pairwise
from typing import Any, cast

import pytest
from langgraph.graph import END, START, StateGraph

from mcp_server_phytomni.agents.deep_genome.agent import DeepGenomeState
from mcp_server_phytomni.agents.deep_genome.work_items import (
    WorkItemSpec,
    build_logical_analysis_tasks,
    build_work_item_plan,
    section_keys,
)

pytestmark = pytest.mark.unit


def test_work_item_plan_has_eleven_sections_and_twelve_jobs() -> None:
    """Concrete jobs remain stable while design shares one report section."""
    items = build_work_item_plan("osa", "Os01g0100100", "LOC_Os01g01010")

    assert len(items) == 12
    assert len(section_keys(items)) == 11
    assert [item.work_item_key for item in items][-2:] == [
        "protein_design",
        "promoter_design",
    ]
    assert all(a.display_order < b.display_order for a, b in pairwise(items))


def test_expression_items_use_resolved_gene_without_mutating_other_items() -> (
    None
):
    """Only expression jobs use the species-specific resolved identifier."""
    items = build_work_item_plan("osa", "Os01g0100100", "LOC_Os01g01010")
    by_key = {item.work_item_key: item for item in items}

    assert by_key["gene_expression_tissues"].target_gene == "LOC_Os01g01010"
    assert by_key["single_cell_analysis"].target_gene == "Os01g0100100"


def test_work_item_spec_is_frozen_and_has_the_public_shape() -> None:
    """The model is immutable and exposes the seven contract fields."""
    assert fields(WorkItemSpec)
    assert tuple(field.name for field in fields(WorkItemSpec)) == (
        "section_key",
        "work_item_key",
        "analysis_type",
        "display_order",
        "compute_resource",
        "target_gene",
        "species_code",
    )

    item = build_work_item_plan("ath", "AT1G01010", "AT1G01010")[0]
    with pytest.raises(AttributeError):
        setattr(item, "target_gene", "other")


def test_work_item_keys_and_sections_are_deterministic() -> None:
    """The concrete-key universe and report-section order are explicit."""
    items = build_work_item_plan("ath", "AT1G01010", "AT1G01010")

    assert [item.work_item_key for item in items] == [
        "evolution_analysis",
        "gene_expression_tissues",
        "gene_expression_cultivars",
        "gene_expression_treatments",
        "gene_expression_genotypes",
        "single_cell_analysis",
        "promoter_analysis",
        "smep_analysis",
        "smoc_analysis",
        "protein_structure_analysis",
        "protein_design",
        "promoter_design",
    ]
    assert section_keys(items) == (
        "evolution_analysis",
        "gene_expression_tissues",
        "gene_expression_cultivars",
        "gene_expression_treatments",
        "gene_expression_genotypes",
        "single_cell_analysis",
        "promoter_analysis",
        "smep_analysis",
        "smoc_analysis",
        "protein_structure_analysis",
        "digital_design",
    )


def test_logical_task_projection_keeps_design_as_one_branch() -> None:
    """The legacy branch projection preserves concrete target semantics."""
    items = build_work_item_plan("osa", "Os01g0100100", "LOC_Os01g01010")

    tasks = build_logical_analysis_tasks(
        items,
        species_code="osa",
        gene_id="Os01g0100100",
    )

    assert len(tasks) == 11
    assert tasks[-1] == {
        "target_gene": "Os01g0100100",
        "species_code": "osa",
        "analysis_type": "digital_design",
        "compute": "medium",
        "func_name": "digital_design",
    }
    expression = next(
        task
        for task in tasks
        if task["analysis_type"] == "gene_expression_tissues"
    )
    assert expression["target_gene"] == "LOC_Os01g01010"


def test_deep_genome_state_graph_retains_all_work_items() -> None:
    """The declared state schema preserves every serialized concrete job."""
    items = build_work_item_plan("osa", "Os01g0100100", "LOC_Os01g01010")

    def prepare_node(_state: DeepGenomeState) -> dict[str, Any]:
        """Return the concrete plan through a real StateGraph channel."""
        return {"work_items": [asdict(item) for item in items]}

    workflow = StateGraph(DeepGenomeState)
    workflow.add_node("prepare", cast(Any, prepare_node))
    workflow.add_edge(START, "prepare")
    workflow.add_edge("prepare", END)
    result = workflow.compile().invoke(
        cast(
            DeepGenomeState,
            {"species_code": "osa", "gene_id": "Os01g0100100"},
        )
    )

    assert len(result["work_items"]) == 12
    assert result["work_items"][-2]["work_item_key"] == "protein_design"
    assert result["work_items"][-1]["work_item_key"] == "promoter_design"
