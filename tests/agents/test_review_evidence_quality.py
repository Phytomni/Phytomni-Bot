# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic Review evidence-quality contract tests."""

from __future__ import annotations

from mcp_server_phytomni.agents.review.evidence_quality import (
    compose_review_retrieval_query,
    select_review_evidence,
)

PLANT_SCRNA_QUERY = (
    "How does single-cell RNA sequencing (scRNA-seq) reveal the "
    "heterogeneous responses of different cell types within plant organs "
    "to biotic/abiotic stresses?"
)


def _doc(
    chunk_id: str,
    title: str,
    content: str,
    **metadata: object,
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "title": title,
        "content": content,
        **metadata,
    }


def test_compose_primary_query_preserves_original_scope_and_dimension() -> (
    None
):
    query = compose_review_retrieval_query(
        original_query=PLANT_SCRNA_QUERY,
        dimension="Cell type-specific transcriptional reprogramming",
    )

    assert PLANT_SCRNA_QUERY in query
    assert "Cell type-specific transcriptional reprogramming" in query


def test_compose_supplementary_query_preserves_all_three_scopes() -> None:
    query = compose_review_retrieval_query(
        original_query=PLANT_SCRNA_QUERY,
        dimension="Abiotic stress responses in crop roots",
        supplementary_query="guard-cell drought transcriptome",
    )

    assert PLANT_SCRNA_QUERY in query
    assert "Abiotic stress responses in crop roots" in query
    assert "guard-cell drought transcriptome" in query


def test_selector_rejects_off_domain_docs_and_deduplicates_publications() -> (
    None
):
    docs = [
        _doc(
            "mouse-1",
            "Single-cell sequencing of mouse embryonic stem cells",
            "Murine cell-state heterogeneity during differentiation.",
        ),
        _doc(
            "root-weak",
            "Plant root single-cell responses to drought",
            "Cell-type-specific transcriptomes reveal abiotic stress "
            "responses in Arabidopsis root organs.",
            doi="10.1000/plant-root",
            score=0.4,
        ),
        _doc(
            "root-best",
            "Single-cell RNA-seq reveals plant root drought responses",
            "Arabidopsis cell types undergo heterogeneous transcriptional "
            "reprogramming during abiotic stress.",
            doi="https://doi.org/10.1000/PLANT-ROOT",
            score=0.9,
        ),
        _doc(
            "guard-1",
            "Plant guard-cell transcriptomics under pathogen stress",
            "Single-cell analysis resolves cell-type-specific biotic "
            "responses in leaf tissue.",
        ),
        _doc(
            "yeast-1",
            "Chromatin remodeling in budding yeast",
            "Single-cell variation in Saccharomyces cerevisiae.",
        ),
    ]

    selected = select_review_evidence(
        original_query=PLANT_SCRNA_QUERY,
        dimension="Cell-type-specific responses to biotic and abiotic stress",
        documents=docs,
    )

    assert [doc["chunk_id"] for doc in selected] == [
        "root-best",
        "guard-1",
    ]


def test_selector_deduplicates_the_same_publication_across_dimensions() -> (
    None
):
    seen: set[str] = set()
    first = select_review_evidence(
        original_query=PLANT_SCRNA_QUERY,
        dimension="Plant root drought responses",
        documents=[
            _doc(
                "root-1",
                "Plant root single-cell responses to drought",
                "Arabidopsis root cell types respond to abiotic stress.",
                doi="10.1000/root",
            )
        ],
        seen_identities=seen,
    )
    second = select_review_evidence(
        original_query=PLANT_SCRNA_QUERY,
        dimension="Cell-type-specific stress mechanisms",
        documents=[
            _doc(
                "root-2",
                "Plant root single-cell responses to drought",
                "The same Arabidopsis study returned from another chunk.",
                doi="https://doi.org/10.1000/ROOT",
            )
        ],
        seen_identities=seen,
    )

    assert [doc["chunk_id"] for doc in first] == ["root-1"]
    assert second == []


def test_selector_does_not_invent_plant_scope_for_a_mouse_question() -> None:
    query = (
        "How does single-cell RNA sequencing reveal heterogeneity in mouse "
        "embryonic stem cells?"
    )
    selected = select_review_evidence(
        original_query=query,
        dimension="Embryonic stem-cell state transitions",
        documents=[
            _doc(
                "mouse-1",
                "Single-cell RNA sequencing of mouse embryonic stem cells",
                "Murine stem-cell heterogeneity and transcriptional states.",
            )
        ],
    )

    assert [doc["chunk_id"] for doc in selected] == ["mouse-1"]
