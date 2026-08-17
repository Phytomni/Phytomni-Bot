# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for Review retrieve-query composition and domain permit.

Pins the first-knife contract: dimension headings must not become bare
PPI searches, and biomedical off-topic papers must stay out of a
gene-module review unless they also match the query terms or a plant
token.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.review.evidence_filter import (
    compose_review_retrieve_query,
    extract_review_query_terms,
    review_document_permitted,
)

pytestmark = pytest.mark.unit

_ZOS7_QUERY = (
    "ZOS7-MYB60-CER1 regulatory pathway in drought resistance "
    "of upland rice"
)


def test_extract_review_query_terms_keeps_gene_and_crop_tokens() -> None:
    """Gene symbols and crop words from the user query become terms."""
    terms = extract_review_query_terms(_ZOS7_QUERY)

    assert "zos7" in terms
    assert "myb60" in terms
    assert "cer1" in terms
    assert "rice" in terms
    assert "drought" in terms


def test_compose_appends_gene_tokens_to_ppi_heading() -> None:
    """A bare PPI heading cannot be the retrieve query by itself."""
    composed = compose_review_retrieve_query(_ZOS7_QUERY, "PPI Mechanisms")

    lowered = composed.lower()
    assert "zos7" in lowered
    assert "myb60" in lowered
    assert "cer1" in lowered
    assert composed.strip().lower() not in {"ppi", "ppi mechanisms"}


def test_compose_prefers_planner_search_query_and_still_scopes_genes() -> None:
    """Planner search strings stay, but missing gene tokens are appended."""
    composed = compose_review_retrieve_query(
        _ZOS7_QUERY,
        "Regulatory logic",
        search_query="OsMYB60 OsCER1 promoter binding in rice",
    )

    lowered = composed.lower()
    assert "osmyb60" in lowered
    assert "oscer1" in lowered
    assert "zos7" in lowered


def test_permit_keeps_document_matching_query_gene() -> None:
    """A hit on a query gene token keeps the document."""
    terms = extract_review_query_terms(_ZOS7_QUERY)
    doc = {
        "title": "Mutation of OsMYB60 reduces rice resilience to drought",
        "content": "Yeast one-hybrid and ChIP assays.",
    }

    assert review_document_permitted(doc, terms) is True


def test_permit_drops_drug_target_network_paper() -> None:
    """Human drug-target integration papers are off-domain."""
    terms = extract_review_query_terms(_ZOS7_QUERY)
    doc = {
        "title": (
            "Effects of protein interaction data integration, "
            "representation and reliability on the use of network "
            "properties for drug target prediction"
        ),
        "content": "Chemogenomic analysis of human enzymes and GPCRs.",
    }

    assert review_document_permitted(doc, terms) is False


def test_permit_drops_c60_bait_paper() -> None:
    """Fullerene C60 bait papers are off-domain."""
    terms = extract_review_query_terms(_ZOS7_QUERY)
    doc = {
        "title": "Baiting proteins with C60",
        "content": "Computational docking of fullerene C60.",
    }

    assert review_document_permitted(doc, terms) is False


def test_permit_drops_epstein_barr_perturbation_paper() -> None:
    """EBV reactivation papers are off-domain."""
    terms = extract_review_query_terms(_ZOS7_QUERY)
    doc = {
        "title": (
            "Small molecule perturbation of the CAND1-Cullin1-ubiquitin "
            "cycle stabilizes p53 and triggers Epstein-Barr virus "
            "reactivation"
        ),
        "content": "Viral latency in human B cells.",
    }

    assert review_document_permitted(doc, terms) is False


def test_permit_keeps_rice_wax_paper() -> None:
    """Rice cuticular-wax papers stay in domain."""
    terms = extract_review_query_terms(_ZOS7_QUERY)
    doc = {
        "title": (
            "Rice OsGL1-6 is involved in leaf cuticular wax "
            "accumulation and drought resistance"
        ),
        "content": "Antisense-RNA plants reduced wax synthesis.",
    }

    assert review_document_permitted(doc, terms) is True


def test_permit_keeps_network_title_when_body_is_upland_rice() -> None:
    """A generic network title stays if the body matches the query."""
    terms = extract_review_query_terms(_ZOS7_QUERY)
    doc = {
        "title": "Protein-protein interaction network analysis",
        "content": "ZOS7 binds OsMYB60 in Shanlan upland rice leaves.",
    }

    assert review_document_permitted(doc, terms) is True


def test_permit_keeps_ambiguous_document_without_denylist() -> None:
    """Ambiguous documents default to keep when no denylist hits."""
    terms = extract_review_query_terms(_ZOS7_QUERY)
    doc = {
        "title": "Transcription factor binding assays",
        "content": "A general methods note with no species listed.",
    }

    assert review_document_permitted(doc, terms) is True
