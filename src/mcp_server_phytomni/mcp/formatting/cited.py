# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Format chat and citation-bearing MCP responses."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...agents.shared.citation_enrichment import CITATION_BIBLIO_FIELDS
from ..universal_failures import (
    project_degraded_metadata,
    project_universal_failure_metadata,
)
from ._shared import (
    first_message,
    follow_up_questions,
    mapping_sequence,
    phytomni_state,
)
from .models import FormattedToolResult

_CITATION_PATTERN = re.compile(r"\[(?:[A-Za-z]+[:\s]*)?(\d+(?:,\s*\d+)*)\]")


def format_message_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format an OpenAI-style response with one assistant message."""
    del arguments
    message = first_message(content)
    return FormattedToolResult(
        answer=str(message.get("content", "")),
        follow_up_questions=follow_up_questions(message),
    )


def format_cited_message_result(
    content: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
) -> FormattedToolResult:
    """Format an OpenAI-style response and normalize cited documents.

    The answer text stays as plain markdown with inline ``[N]`` citation
    markers; deduplicated citation documents flow through the structured
    ``references`` field. ReviewAgent fan-out failures on
    ``phytomni_state.failures`` surface as universal failure keys.
    """
    del arguments
    message = first_message(content)
    answer = str(message.get("content", ""))
    state = phytomni_state(content)
    metadata: dict[str, Any] = (
        project_universal_failure_metadata(state)
        if state.get("failures")
        else {}
    )
    metadata.update(project_degraded_metadata(state))
    doc_list = tuple(mapping_sequence(message.get("doc_list")))
    if not doc_list:
        return FormattedToolResult(
            answer=answer,
            follow_up_questions=follow_up_questions(message),
            metadata=metadata,
        )
    normalized_answer, references = normalize_citations(answer, doc_list)
    return FormattedToolResult(
        answer=normalized_answer,
        follow_up_questions=follow_up_questions(message),
        metadata=metadata,
        references=references,
    )


def normalize_citations(
    answer: str,
    doc_list: Sequence[Mapping[str, Any]],
) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    """Deduplicate cited documents and rewrite citation indices."""
    citation_order = citation_order_for(answer)
    selected_docs: list[Mapping[str, Any]] = []
    old_to_new: dict[int, int] = {}
    seen_keys: dict[str, int] = {}

    for old_index in citation_order:
        if old_index < 1 or old_index > len(doc_list):
            continue
        doc = doc_list[old_index - 1]
        doc_key = document_key(doc, old_index)
        if doc_key in seen_keys:
            old_to_new[old_index] = seen_keys[doc_key]
            continue
        selected_docs.append(reference_payload(doc))
        new_ref = len(selected_docs)
        seen_keys[doc_key] = new_ref
        old_to_new[old_index] = new_ref

    def replace_citation(match: re.Match[str]) -> str:
        new_numbers = [
            str(old_to_new[old_index])
            for old_index in numbers_from_match(match)
            if old_index in old_to_new
        ]
        return f"[{','.join(new_numbers)}]" if new_numbers else ""

    return (
        _CITATION_PATTERN.sub(replace_citation, answer),
        tuple(selected_docs),
    )


def citation_order_for(answer: str) -> tuple[int, ...]:
    """Return cited document indices in first-appearance order."""
    seen_indices: set[int] = set()
    ordered_indices: list[int] = []
    for match in _CITATION_PATTERN.finditer(answer):
        for number in numbers_from_match(match):
            if number not in seen_indices:
                seen_indices.add(number)
                ordered_indices.append(number)
    return tuple(ordered_indices)


def numbers_from_match(match: re.Match[str]) -> tuple[int, ...]:
    """Return numeric citation values from a regex match."""
    numbers: list[int] = []
    for raw_number in match.group(1).split(","):
        try:
            numbers.append(int(raw_number.strip()))
        except ValueError:
            continue
    return tuple(numbers)


def document_key(doc: Mapping[str, Any], index: int) -> str:
    """Return a stable deduplication key for a cited document."""
    return str(doc.get("file_id") or doc.get("title") or index)


def reference_payload(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the reference metadata exposed to clients."""
    file_id = doc.get("file_id")
    title = str(doc.get("title", ""))
    if title.endswith(".pdf"):
        title = title[:-4]
    payload: dict[str, Any] = {"file_id": file_id, "title": title}
    for key in CITATION_BIBLIO_FIELDS:
        value = doc.get(key)
        if value is not None:
            payload[key] = value
    return payload


__all__ = [
    "format_cited_message_result",
    "format_message_result",
    "reference_payload",
    "normalize_citations",
]
