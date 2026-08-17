# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic Review retrieve-query composition and domain permit.

Keeps gene-module retrieval inside the plant or query-term domain so
bare PPI headings cannot pull biomedical drug-target papers into a
review dimension. Callers stay free of citation lookup and network I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

__all__ = [
    "compose_review_retrieve_query",
    "extract_review_query_terms",
    "review_document_permitted",
]

_GENE_TOKEN = re.compile(
    r"(?:Os|At|Zm|Sl|Ta|Gm|Sb|Md)[A-Za-z0-9]+(?:-\d+)?"
    r"|LOC\d+"
    r"|[A-Z]{2,}[0-9]+[A-Z0-9]*"
)
_WORD_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_CROP_OR_TRAIT_TERMS = frozenset(
    {
        "arabidopsis",
        "barley",
        "crop",
        "cuticle",
        "drought",
        "maize",
        "oryza",
        "plant",
        "qtl",
        "rice",
        "sorghum",
        "soybean",
        "tomato",
        "upland",
        "wax",
        "wheat",
    }
)
_PLANT_DOMAIN_PHRASES = frozenset(
    {
        "arabidopsis",
        "barley",
        "breeding",
        "crop",
        "cuticle",
        "drought",
        "leaf",
        "maize",
        "oryza",
        "plant",
        "qtl",
        "rice",
        "root",
        "sorghum",
        "soybean",
        "tomato",
        "upland",
        "wax",
        "wheat",
    }
)
_DENYLIST_PHRASES = (
    "chemogenomic",
    "drug target",
    "drug-target",
    "ebv",
    "epstein-barr",
    "fullerene",
    "gpcr",
    "gpcrs",
    "nuclear receptor",
    "pharmacogenomic",
    "c60",
)


def extract_review_query_terms(user_query: str) -> frozenset[str]:
    """Return lowercase gene symbols and crop words from the user query."""
    terms: set[str] = {
        match.group(0).lower() for match in _GENE_TOKEN.finditer(user_query)
    }
    for word in _WORD_TOKEN.findall(user_query):
        lowered = word.lower()
        if lowered in _CROP_OR_TRAIT_TERMS:
            terms.add(lowered)
    return frozenset(terms)


def compose_review_retrieve_query(
    user_query: str,
    heading: str,
    search_query: str | None = None,
) -> str:
    """Build a retrieve query that stays scoped to the original topic.

    Prefers a planner ``search_query`` when present. Otherwise joins the
    user query and dimension heading. Gene and crop tokens from the user
    query are appended when the chosen string omitted them, so a heading
    such as ``PPI Mechanisms`` cannot go out as a bare biomedical search.
    """
    preferred = (search_query or "").strip()
    composed = preferred or " ".join(
        part for part in (user_query.strip(), heading.strip()) if part
    )
    missing = [
        term
        for term in sorted(extract_review_query_terms(user_query))
        if term not in composed.lower()
    ]
    if missing:
        composed = f"{composed} {' '.join(missing)}".strip()
    return composed


def review_document_permitted(
    doc: Mapping[str, object],
    query_terms: Iterable[str],
) -> bool:
    """Return whether one retrieved document may enter Review drafting.

    Query-term hits always keep the document. A denylist hit without a
    plant-domain token or query term drops it. Everything else stays, so
    ambiguous methods notes are not discarded.
    """
    haystack = _document_haystack(doc)
    normalized_terms = {
        str(term).strip().lower() for term in query_terms if str(term).strip()
    }
    if any(_phrase_in_text(haystack, term) for term in normalized_terms):
        return True
    has_plant = any(
        _phrase_in_text(haystack, phrase) for phrase in _PLANT_DOMAIN_PHRASES
    )
    has_denied = any(
        _phrase_in_text(haystack, phrase) for phrase in _DENYLIST_PHRASES
    )
    return has_plant or not has_denied


def _phrase_in_text(haystack: str, phrase: str) -> bool:
    """Match a multi-word phrase or a whole short token."""
    if " " in phrase or "-" in phrase:
        return phrase in haystack
    return re.search(rf"\b{re.escape(phrase)}\b", haystack) is not None


def _document_haystack(doc: Mapping[str, object]) -> str:
    """Join the title and body fields used for the domain check."""
    parts = (
        doc.get("title"),
        doc.get("subtitle"),
        doc.get("content"),
        doc.get("big_content"),
    )
    return " ".join(str(part) for part in parts if part).lower()
