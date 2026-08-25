# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite, domain-validated public narrative for Gene Network runs.

These producers deliberately accept only the domain selections needed to
validate the public milestone.  Prompt text, tool inputs/results, and provider
records therefore cannot cross this boundary by construction.
"""

from __future__ import annotations

import re

from ...runtime.execution_event_sink import (
    emit_decision_note,
    emit_reasoning_summary,
)
from ..shared.species_catalog import supported_species_codes

_TRAIT_ONTOLOGY_ID = re.compile(r"TO:\d{7}\Z")
_WORKFLOW = "gene_network_analysis"


def publish_gene_network_target_validation(
    to_id: str,
    species_code: str,
) -> None:
    """Publish a fixed summary after validating the declared domain target."""
    if not _TRAIT_ONTOLOGY_ID.fullmatch(to_id):
        raise ValueError("invalid trait target")
    if species_code not in supported_species_codes():
        raise ValueError("invalid species code")
    emit_reasoning_summary(
        "Validated the trait target and species for network analysis.",
        summary_key="gene_network.target_validated",
        idempotency_key="gene-network:target-validated",
    )


def publish_gene_network_workflow_selection(analysis_type: str) -> None:
    """Publish a fixed note for the one declared Gene Network workflow."""
    if analysis_type != _WORKFLOW:
        raise ValueError("unsupported workflow")
    emit_decision_note(
        "Selected the declared gene-network analysis workflow.",
        summary_key="gene_network.workflow_selected",
        idempotency_key="gene-network:workflow-selected",
    )


__all__ = [
    "publish_gene_network_target_validation",
    "publish_gene_network_workflow_selection",
]
