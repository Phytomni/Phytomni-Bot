# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Immutable DeepGenome logical-section and concrete-work-item plans.

The DeepGenome graph still routes its historical eleven logical analysis
branches.  This module is the single source of truth for the concrete jobs
behind those branches, including the two independent Digital Design jobs.
It deliberately contains no submission or polling code.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...config.defaults import DeepGenomeConfig, resolve_compute_resource


@dataclass(frozen=True)
class WorkItemSpec:
    """Describe one concrete optional analysis job."""

    section_key: str
    work_item_key: str
    analysis_type: str
    display_order: int
    compute_resource: str
    target_gene: str
    species_code: str


# The tuple order is the report order.  Keeping this data declarative avoids
# a second hard-coded task list in the graph dispatch node.  Design's two
# concrete jobs intentionally share the one logical ``digital_design``
# section and retain their producer-specific analysis type.
_WORK_ITEM_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("evolution_analysis", "evolution_analysis", "original"),
    ("gene_expression_tissues", "gene_expression_tissues", "resolved"),
    ("gene_expression_cultivars", "gene_expression_cultivars", "resolved"),
    ("gene_expression_treatments", "gene_expression_treatments", "resolved"),
    ("gene_expression_genotypes", "gene_expression_genotypes", "resolved"),
    ("single_cell_analysis", "single_cell_analysis", "original"),
    ("promoter_analysis", "promoter_analysis", "original"),
    ("smep_analysis", "smep_analysis", "original"),
    ("smoc_analysis", "smoc_analysis", "original"),
    ("protein_structure_analysis", "protein_structure_analysis", "original"),
    ("digital_design", "protein_design", "original"),
    ("digital_design", "promoter_design", "original"),
)


def build_work_item_plan(
    species_code: str,
    gene_id: str,
    resolved_gene_id: str,
) -> tuple[WorkItemSpec, ...]:
    """Build the stable concrete-job plan for one DeepGenome request.

    Expression jobs use ``resolved_gene_id`` because their platform data
    tables require the species-specific identifier.  Every other job keeps
    the caller's ``gene_id``.  The input strings are copied into immutable
    records; this function performs no remote calls or task submissions.

    Args:
        species_code: Three-letter species code used by analysis platforms.
        gene_id: Caller-supplied target gene identifier.
        resolved_gene_id: Species-specific identifier for expression jobs.

    Returns:
        Twelve concrete work-item specifications in deterministic report
        order.  The two design jobs share one logical section.
    """
    plan: list[WorkItemSpec] = []
    for display_order, (
        section_key,
        work_item_key,
        target_kind,
    ) in enumerate(_WORK_ITEM_DEFINITIONS):
        target_gene = (
            resolved_gene_id if target_kind == "resolved" else gene_id
        )
        analysis_type = (
            "protein_design_analysis"
            if work_item_key == "protein_design"
            else (
                "promoter_design_analysis"
                if work_item_key == "promoter_design"
                else work_item_key
            )
        )
        plan.append(
            WorkItemSpec(
                section_key=section_key,
                work_item_key=work_item_key,
                analysis_type=analysis_type,
                display_order=display_order,
                compute_resource=resolve_compute_resource(
                    DeepGenomeConfig, analysis_type
                ),
                target_gene=target_gene,
                species_code=species_code,
            )
        )
    return tuple(plan)


def section_keys(items: Iterable[WorkItemSpec]) -> tuple[str, ...]:
    """Return unique logical section keys in stable work-item order."""
    seen: set[str] = set()
    keys: list[str] = []
    for item in items:
        if item.section_key not in seen:
            seen.add(item.section_key)
            keys.append(item.section_key)
    return tuple(keys)


def build_logical_analysis_tasks(
    items: Iterable[WorkItemSpec],
    species_code: str,
    gene_id: str,
) -> list[dict[str, str]]:
    """Project concrete jobs onto the legacy logical branch contract.

    The graph still launches one branch per logical section. Digital Design
    owns two concrete jobs but keeps one branch, so its target remains the
    caller-supplied gene id while every other section uses its representative
    concrete item.
    """
    concrete_items = tuple(items)
    logical_tasks: list[dict[str, str]] = []
    for section_key in section_keys(concrete_items):
        section_items = [
            item for item in concrete_items if item.section_key == section_key
        ]
        representative = section_items[0]
        analysis_type = (
            "digital_design"
            if section_key == "digital_design"
            else representative.analysis_type
        )
        logical_tasks.append(
            {
                "target_gene": (
                    gene_id
                    if section_key == "digital_design"
                    else representative.target_gene
                ),
                "species_code": species_code,
                "analysis_type": analysis_type,
                "compute": representative.compute_resource,
                "func_name": analysis_type,
            }
        )
    return logical_tasks


__all__ = [
    "WorkItemSpec",
    "build_logical_analysis_tasks",
    "build_work_item_plan",
    "section_keys",
]
