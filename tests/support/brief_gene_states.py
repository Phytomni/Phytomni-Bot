# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared BriefGene state fragments for independent agent tests."""

from __future__ import annotations

__all__ = [
    "brief_gene_identity_fields",
    "empty_brief_gene_annotation_fields",
]


def brief_gene_identity_fields() -> dict[str, object]:
    """Return the canonical found-gene identity fields."""
    return {
        "is_follow_up": False,
        "gene_found": True,
        "gene_id": "AT1G01010",
        "query_id_version": "tair10",
        "gene_id_version": "tair10",
        "species_code": "ath",
        "species_latin_name": "Arabidopsis thaliana",
        "species_english_name": "thale cress",
    }


def empty_brief_gene_annotation_fields() -> dict[str, object]:
    """Return optional annotation fields for a degraded profile."""
    return {
        "go_string": "",
        "kegg_string": "",
        "interpro_string": "",
        "description_string": "",
        "retrieved_docs": [],
    }
