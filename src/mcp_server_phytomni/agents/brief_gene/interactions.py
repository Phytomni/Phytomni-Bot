# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helpers for homology + protein interaction list projection.

``_homology_gene_lists`` splits a homology BI response into
cross-species orthologs vs same-species paralogs by ``species_code``;
``_interaction_gene_list`` projects a protein-interaction response into
a compact list excluding the query gene's self-interaction. Both are
migrated from ``deep_genome/dispatch.py`` for the brief_gene-owned
homology data path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _homology_gene_lists(
    homology_response: Dict[str, Any],
    species_code: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split homology BI response into orthologs + paralogs lists.

    Args:
        homology_response: BI response dict with ``data`` list of
            rows containing ``homology_species`` etc.
        species_code: Query gene's species code; entries with matching
            ``homology_species`` go to paralogs (same-species
            homologs), others go to orthologs (cross-species).

    Returns:
        ``(orthologs_list, paralogs_list)`` — each a list of dict
        entries from the BI ``data`` field, partitioned by the
        homology_species match.
    """
    entries = homology_response.get("data") or []
    orthologs: List[Dict[str, Any]] = []
    paralogs: List[Dict[str, Any]] = []
    for entry in entries:
        if entry.get("homology_species") == species_code:
            paralogs.append(entry)
        else:
            orthologs.append(entry)
    return orthologs, paralogs


def _interaction_gene_list(
    interaction_response: Dict[str, Any],
    gene_id: str,
    species_code: str,
) -> List[Dict[str, Any]]:
    """Project protein interaction BI response into compact list.

    Filters out self-loops where ``interact_gene_id`` equals the
    query ``gene_id``. BI's
    ``protein_interaction_col WHERE query_gene_id = X OR
    interact_gene_id = X`` query returns both directions; this
    helper keeps only rows where the query gene is the source side
    (i.e. ``interact_gene_id`` is a distinct partner).

    Args:
        interaction_response: BI response dict with ``data`` list.
        gene_id: Query gene id; rows with ``interact_gene_id ==
            gene_id`` are dropped as self-loops.
        species_code: Query gene's species code (reserved for
            future cross-species filtering; currently unused).

    Returns:
        List of interaction dicts excluding the self-loop entry.
    """
    del species_code  # reserved for future cross-species filtering
    entries = interaction_response.get("data") or []
    return [
        entry for entry in entries if entry.get("interact_gene_id") != gene_id
    ]
