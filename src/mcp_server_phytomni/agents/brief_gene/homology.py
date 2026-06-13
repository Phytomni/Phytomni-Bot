# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""brief_gene homology + protein interaction BI fetch node.

Hosts ``_run_fetch_homology_interactions_node`` which issues two BI
SQL queries (``homology_gene`` + ``protein_interaction_col``) and
projects the responses into ``orthologs_data`` / ``paralogs_data`` /
``interaction_data`` state deltas with derived count summaries for
Basic Information bullet rendering. Migrated from deep_genome's
``_run_data_agent`` as part of the X3b A architecture fusion.
"""

from __future__ import annotations

from typing import Any, Dict

from ..shared.sql import relay_bi_query, sql_literal
from .interactions import _homology_gene_lists, _interaction_gene_list
from .state import BriefGeneAgentState


async def _run_fetch_homology_interactions_node(
    state: BriefGeneAgentState,
) -> Dict[str, Any]:
    """Fetch homology + interactions BI rows for the query gene.

    Issues two BI SQL queries against ``homology_gene`` and
    ``protein_interaction_col`` tables, splits homology into
    orthologs (cross-species) vs paralogs (same-species), and
    derives count summaries for downstream Basic Information
    bullets.

    Args:
        state: Workflow state with ``gene_id`` + ``species_code``.

    Returns:
        State delta with ``orthologs_data`` / ``paralogs_data`` /
        ``interaction_data`` dicts (each ``{"gene_list": [...]}``)
        plus four count fields (``ortholog_count``,
        ``ortholog_species_count``, ``paralog_count``,
        ``interaction_count``).
    """
    gene_id = state.get("gene_id", "")
    if not gene_id:
        # The node runs unconditionally off query_judge so the section
        # fan-in never waits on a branch that may not fire; an
        # unresolved gene (gene_found=False) has no homology to fetch.
        return {
            "orthologs_data": {"gene_list": []},
            "paralogs_data": {"gene_list": []},
            "interaction_data": {"gene_list": []},
            "ortholog_count": 0,
            "ortholog_species_count": 0,
            "paralog_count": 0,
            "interaction_count": 0,
        }
    species_code = state.get("species_code", "")
    gene_literal = sql_literal(gene_id)

    homology_response = await relay_bi_query(
        "SELECT query_gene_id, query_species, homology_gene_id, "
        "homology_species "
        f"FROM homology_gene WHERE query_gene_id = {gene_literal}",
        message="Homology query failed",
    )
    interaction_response = await relay_bi_query(
        "SELECT query_gene_id, query_protein, interact_gene_id, "
        "interact_protein "
        "FROM protein_interaction_col "
        f"WHERE query_gene_id = {gene_literal} OR "
        f"interact_gene_id = {gene_literal}",
        message="Interaction query failed",
    )

    orthologs_list, paralogs_list = _homology_gene_lists(
        homology_response, species_code
    )
    interaction_list = _interaction_gene_list(
        interaction_response, gene_id, species_code
    )

    ortholog_species = {
        entry.get("homology_species", "") for entry in orthologs_list
    }

    return {
        "orthologs_data": {"gene_list": orthologs_list},
        "paralogs_data": {"gene_list": paralogs_list},
        "interaction_data": {"gene_list": interaction_list},
        "ortholog_count": len(orthologs_list),
        "ortholog_species_count": len(ortholog_species),
        "paralog_count": len(paralogs_list),
        "interaction_count": len(interaction_list),
    }
