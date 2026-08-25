# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Deterministic evidence selection for Review Agent.

The Knowledge layer validates retrieval payloads. This module owns the narrower
Review concern: deciding which validated documents are relevant enough to enter
the drafting and citation authority set.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, MutableSet, Sequence
from math import isfinite
from typing import Any

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

NO_RELEVANT_EVIDENCE_MESSAGE = "No sufficiently relevant evidence found"

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_SPACE_PATTERN = re.compile(r"\s+")

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "does",
        "different",
        "do",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "or",
        "such",
        "than",
        "that",
        "the",
        "their",
        "these",
        "this",
        "to",
        "under",
        "what",
        "which",
        "within",
        "with",
    }
)

# Query-side markers that make a plant-domain constraint explicit.
_PLANT_SCOPE_TOKENS = frozenset(
    {
        "arabidopsis",
        "barley",
        "botanical",
        "crop",
        "crops",
        "maize",
        "oryza",
        "plant",
        "plants",
        "poplar",
        "rice",
        "seed",
        "seeds",
        "soybean",
        "tomato",
        "wheat",
        "zea",
    }
)

# Document-side vocabulary is intentionally broader than query-side markers so
# a query saying "crops" accepts evidence identified by a species name.
_PLANT_DOCUMENT_TOKENS = _PLANT_SCOPE_TOKENS | frozenset(
    {
        "angiosperm",
        "brassica",
        "chlamydomonas",
        "glycine",
        "medicago",
        "nicotiana",
        "photosynthesis",
        "phytohormone",
        "solanum",
        "sorghum",
        "stomata",
        "stomatal",
        "triticum",
    }
)

_NON_PLANT_ORGANISM_TOKENS = frozenset(
    {
        "drosophila",
        "human",
        "mouse",
        "murine",
        "rat",
        "saccharomyces",
        "yeast",
        "zebrafish",
    }
)

_URL_FIELDS = (
    "source_url",
    "canonical_url",
    "url",
    "file_url",
    "download_url",
    "link",
)
_DOI_FIELDS = ("doi", "DOI", "article_doi")
_TEXT_FIELDS = (
    "title",
    "subtitle",
    "abstract",
    "big_content",
    "content",
    "keywords",
    "species",
    "organism",
)


def compose_review_retrieval_query(
    *,
    original_query: str,
    dimension: str,
    supplementary_query: str | None = None,
) -> str:
    """Compose a retrieval query without dropping the user's scope."""
    parts = [
        f"Original review question: {original_query.strip()}",
        f"Research dimension: {dimension.strip()}",
    ]
    if supplementary_query and supplementary_query.strip():
        parts.append(
            f"Additional evidence query: {supplementary_query.strip()}"
        )
    return "\n".join(parts)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return " ".join(str(item) for item in value)
    return ""


def _document_text(document: Mapping[str, Any]) -> str:
    return " ".join(_text(document.get(field)) for field in _TEXT_FIELDS)


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_PATTERN.findall(text)
        if len(token) > 1 and token.lower() not in _STOP_WORDS
    }


def _concepts(tokens: set[str]) -> set[str]:
    concepts: set[str] = set()
    if "scrna" in tokens or ({"single", "cell"} <= tokens):
        concepts.add("single_cell")
    if (
        "transcriptome" in tokens
        or "transcriptomic" in tokens
        or "transcriptomics" in tokens
        or "sequencing" in tokens
        or ({"rna", "seq"} <= tokens)
    ):
        concepts.add("transcriptomics")
    if tokens & {
        "abiotic",
        "biotic",
        "cold",
        "drought",
        "heat",
        "pathogen",
        "salinity",
        "salt",
        "stress",
        "stresses",
    }:
        concepts.add("stress")
    if tokens & {"heterogeneity", "heterogeneous"} or (
        "cell" in tokens and ({"type", "types"} & tokens)
    ):
        concepts.add("cell_type")
    if tokens & {"response", "responses", "responsive"}:
        concepts.add("response")
    if tokens & {"organ", "organs", "root", "roots", "leaf", "leaves"}:
        concepts.add("organ")
    if tokens & {
        "epigenetic",
        "epigenetics",
        "histone",
        "methylation",
        "methylome",
    }:
        concepts.add("epigenetics")
    if tokens & {"hormone", "hormonal", "phytohormone"}:
        concepts.add("hormone")
    if tokens & {"network", "networks", "regulatory", "regulation"}:
        concepts.add("network")
    return concepts


def _normalized_doi(document: Mapping[str, Any]) -> str:
    for field in _DOI_FIELDS:
        value = _text(document.get(field)).strip().lower()
        match = _DOI_PATTERN.search(value)
        if match:
            return match.group(0).rstrip(".,;)")
    for field in _URL_FIELDS:
        match = _DOI_PATTERN.search(_text(document.get(field)).lower())
        if match:
            return match.group(0).rstrip(".,;)")
    return ""


def _normalized_url(document: Mapping[str, Any]) -> str:
    for field in _URL_FIELDS:
        value = _text(document.get(field)).strip().lower().rstrip("/")
        if value:
            return value
    return ""


def _normalized_title(document: Mapping[str, Any]) -> str:
    title = _text(document.get("title")).strip().lower()
    return _SPACE_PATTERN.sub(" ", re.sub(r"[^a-z0-9]+", " ", title)).strip()


def review_evidence_identity(document: Mapping[str, Any]) -> str:
    """Return a stable publication-level identity for one document."""
    if doi := _normalized_doi(document):
        return f"doi:{doi}"
    if url := _normalized_url(document):
        return f"url:{url}"
    if title := _normalized_title(document):
        return f"title:{title}"
    chunk_id = _text(document.get("chunk_id")).strip()
    return f"chunk:{chunk_id}" if chunk_id else ""


def _provider_score(document: Mapping[str, Any]) -> float:
    value = document.get("score")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        return score if isfinite(score) else 0.0
    return 0.0


def _relevance_score(
    document: Mapping[str, Any],
    query_tokens: set[str],
    query_concepts: set[str],
    *,
    plant_scoped: bool,
) -> tuple[float, bool]:
    title_tokens = _tokens(_text(document.get("title")))
    document_tokens = _tokens(_document_text(document))
    document_concepts = _concepts(document_tokens)

    if plant_scoped:
        has_plant_anchor = bool(document_tokens & _PLANT_DOCUMENT_TOKENS)
        has_only_non_plant_signal = (
            bool(document_tokens & _NON_PLANT_ORGANISM_TOKENS)
            and not has_plant_anchor
        )
        if not has_plant_anchor or has_only_non_plant_signal:
            return (0.0, False)

    topic_terms = query_tokens - _PLANT_SCOPE_TOKENS
    title_overlap = len(title_tokens & topic_terms)
    body_overlap = len(document_tokens & topic_terms)
    concept_overlap = len(document_concepts & query_concepts)
    relevant = (
        body_overlap >= 2
        or concept_overlap >= 2
        or (title_overlap >= 1 and concept_overlap >= 1)
    )
    score = (
        title_overlap * 4.0
        + body_overlap
        + concept_overlap * 5.0
        + _provider_score(document) * 0.01
    )
    return (score, relevant)


def select_review_evidence(
    *,
    original_query: str,
    dimension: str,
    documents: Iterable[Mapping[str, Any]],
    seen_identities: MutableSet[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank, filter, and publication-deduplicate Review evidence."""
    seen = seen_identities if seen_identities is not None else set()
    scope_text = f"{original_query} {dimension}".strip()
    query_tokens = _tokens(scope_text)
    query_concepts = _concepts(query_tokens)
    plant_scoped = bool(query_tokens & _PLANT_SCOPE_TOKENS)

    # Legacy and isolated helper callers without scope retain provider order.
    scoped = bool(original_query.strip())
    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for index, document in enumerate(documents):
        detached = dict(document)
        identity = review_evidence_identity(detached)
        if not identity or identity in seen:
            continue
        if scoped:
            score, relevant = _relevance_score(
                detached,
                query_tokens,
                query_concepts,
                plant_scoped=plant_scoped,
            )
            if not relevant:
                continue
        else:
            score = -float(index)
        ranked.append((score, index, identity, detached))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: list[dict[str, Any]] = []
    selected_in_call: set[str] = set()
    for _, _, identity, document in ranked:
        if identity in selected_in_call or identity in seen:
            continue
        selected_in_call.add(identity)
        seen.add(identity)
        selected.append(document)
    return selected


def no_relevant_evidence_error() -> McpError:
    """Build the fixed public Review quality failure."""
    return McpError(
        ErrorData(code=INTERNAL_ERROR, message=NO_RELEVANT_EVIDENCE_MESSAGE)
    )


__all__ = [
    "NO_RELEVANT_EVIDENCE_MESSAGE",
    "compose_review_retrieval_query",
    "no_relevant_evidence_error",
    "review_evidence_identity",
    "select_review_evidence",
]
