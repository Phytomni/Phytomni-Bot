# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Format chat and citation-bearing MCP responses."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...agents.shared.citation_enrichment import CITATION_BIBLIO_FIELDS
from ...agents.shared.citation_metadata import (
    CITATION_STATUS_KEY,
    CITATION_STATUS_LOOKUP_FAILED,
    CITATION_STATUS_MISSING,
    canonical_doi_urls,
    normalize_doi,
)
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
_RETRIEVAL_FILE_SUFFIXES = (
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".rtf",
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".msg",
    ".eml",
)
_AUTHOR_SUFFIXES = frozenset(("Jr", "Sr", "II", "III", "IV"))
_MARKDOWN_CONTROL_PATTERN = re.compile(r"([\\`*_\[\]{}()#+!|])")
_DISPLAY_LINE_BREAK_PATTERN = re.compile(r"[\t\n\r\f\v\u2028\u2029]+")


@dataclass(frozen=True, slots=True)
class _CitationNormalization:
    """Detailed cited projection used by the canonical result formatter."""

    answer: str
    references: tuple[Mapping[str, Any], ...]
    metadata_degraded: bool


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

    The answer text stays as markdown with inline HTML superscript citation
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
    normalized = _normalize_citations_detailed(answer, doc_list)
    if normalized.metadata_degraded:
        metadata["citation_metadata_degraded"] = True
    return FormattedToolResult(
        answer=normalized.answer,
        follow_up_questions=follow_up_questions(message),
        metadata=metadata,
        references=normalized.references,
    )


def normalize_citations(
    answer: str,
    doc_list: Sequence[Mapping[str, Any]],
) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    """Deduplicate cited documents and rewrite citation indices."""
    normalized = _normalize_citations_detailed(answer, doc_list)
    return normalized.answer, normalized.references


def _normalize_citations_detailed(
    answer: str,
    doc_list: Sequence[Mapping[str, Any]],
) -> _CitationNormalization:
    """Return normalized citations plus selected metadata status."""
    citation_order = citation_order_for(answer)
    selected_docs: list[Mapping[str, Any]] = []
    old_to_new: dict[int, int] = {}
    seen_keys: dict[str, int] = {}
    metadata_degraded = False

    for old_index in citation_order:
        if old_index < 1 or old_index > len(doc_list):
            continue
        doc = doc_list[old_index - 1]
        doc_key = document_key(doc, old_index)
        if doc_key in seen_keys:
            old_to_new[old_index] = seen_keys[doc_key]
            continue
        if doc.get(CITATION_STATUS_KEY) in {
            CITATION_STATUS_MISSING,
            CITATION_STATUS_LOOKUP_FAILED,
        }:
            metadata_degraded = True
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
        return f"<sup>{','.join(new_numbers)}</sup>" if new_numbers else ""

    return _CitationNormalization(
        answer=_CITATION_PATTERN.sub(replace_citation, answer),
        references=tuple(selected_docs),
        metadata_degraded=metadata_degraded,
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
    title = clean_retrieval_title(doc.get("title"))
    payload: dict[str, Any] = {"file_id": file_id, "title": title}
    for key in CITATION_BIBLIO_FIELDS:
        value = doc.get(key)
        if value is not None:
            payload[key] = value
    payload["formatted_citation"] = format_nature_citation(doc, title)
    if _preferred_doi(doc) is None or _is_title_only_status(doc):
        payload["doi_missing"] = True
    return payload


def clean_retrieval_title(value: object) -> str:
    """Remove only an approved final retrieval file suffix."""
    title = str(value or "").strip()
    lowered = title.lower()
    for suffix in _RETRIEVAL_FILE_SUFFIXES:
        if lowered.endswith(suffix):
            cleaned = title[: -len(suffix)].rstrip()
            return cleaned or title
    return title


def format_authors(value: object) -> str:
    """Conservatively format semicolon-delimited author metadata."""
    authors = _formatted_author_tokens(value)
    return _join_authors(authors)


def _formatted_author_tokens(value: object) -> list[str]:
    """Parse raw author separators before any display escaping."""
    if not isinstance(value, str) or not value.strip():
        return []
    return [
        formatted
        for token in _single_line_text(value).split(";")
        if (formatted := _format_author_token(token.strip()))
    ]


def _join_authors(authors: list[str]) -> str:
    """Join already formatted author tokens conservatively."""
    if not authors:
        return ""
    if len(authors) > 5:
        return f"{authors[0]} et al."
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return " & ".join(authors)
    return f"{', '.join(authors[:-1])} & {authors[-1]}"


def _escaped_authors(value: object) -> str:
    """Escape parsed author tokens without escaping owned separators."""
    return _join_authors(
        [
            _markdown_escape(author)
            for author in _formatted_author_tokens(value)
        ]
    )


def format_nature_citation(doc: Mapping[str, Any], title: str) -> str:
    """Assemble one escaped Nature-style display citation."""
    if _is_title_only_status(doc):
        return _markdown_escape(title)

    authors = _escaped_authors(doc.get("au"))
    citation_title = _markdown_escape(str(doc.get("ti") or title))
    source = _markdown_escape_optional(doc.get("so"))
    volume = _markdown_escape_optional(doc.get("vl"))
    locator = _page_or_article_number(doc)
    year = _markdown_escape_optional(doc.get("py"))
    doi = _preferred_doi(doc)

    fragments: list[str] = []
    if authors:
        fragments.append(_sentence(authors))
    if citation_title:
        fragments.append(_sentence(citation_title))

    publication: list[str] = []
    if source:
        publication.append(f"*{source}*")
    if volume:
        publication.append(f"**{volume + ',' if locator else volume}**")
    if locator:
        publication.append(locator)
    if year:
        publication.append(f"({year})")
    if publication:
        fragments.append(_sentence(" ".join(publication)))

    if doi is not None:
        visible, target = canonical_doi_urls(doi)
        fragments.append(f"[{visible}]({target})")
    return " ".join(fragments)


def _format_author_token(token: str) -> str:
    """Format one standard author token or preserve it unchanged."""
    if not token:
        return ""
    parts = [part.strip() for part in token.split(",")]
    if len(parts) not in {2, 3}:
        return token
    surname, initials = parts[:2]
    suffix = parts[2] if len(parts) == 3 else None
    if not surname or not initials:
        return token
    if suffix is not None and suffix not in _AUTHOR_SUFFIXES:
        return token
    compact_initials = initials.replace(".", "").replace(" ", "")
    groups = compact_initials.split("-")
    if not groups or any(
        not group or not group.isalpha() or not group.isupper()
        for group in groups
    ):
        return token
    formatted_groups = [
        " ".join(f"{char}." for char in group) for group in groups
    ]
    formatted_initials = "-".join(formatted_groups)
    result = f"{surname}, {formatted_initials}"
    return f"{result} {suffix}" if suffix is not None else result


def _page_or_article_number(doc: Mapping[str, Any]) -> str:
    """Return escaped pages, one endpoint, or an article number."""
    beginning = _markdown_escape_optional(doc.get("bp"))
    ending = _markdown_escape_optional(doc.get("ep"))
    if beginning and ending:
        return beginning if beginning == ending else f"{beginning}–{ending}"
    if beginning or ending:
        return beginning or ending
    return _markdown_escape_optional(doc.get("ar"))


def _preferred_doi(doc: Mapping[str, Any]) -> str | None:
    """Prefer DI and use only an approved resolver URL from DL."""
    raw_di = doc.get("di")
    if raw_di is not None:
        if not isinstance(raw_di, str):
            return None
        if raw_di.strip():
            return normalize_doi(raw_di)
    return normalize_doi(doc.get("dl"), require_resolver_url=True)


def _is_title_only_status(doc: Mapping[str, Any]) -> bool:
    """Return whether lookup status requires title-only degradation."""
    return doc.get(CITATION_STATUS_KEY) in {
        CITATION_STATUS_MISSING,
        CITATION_STATUS_LOOKUP_FAILED,
    }


def _markdown_escape_optional(value: object) -> str:
    """Escape one optional source value as display text."""
    return _markdown_escape(str(value)) if value is not None else ""


def _markdown_escape(value: str) -> str:
    """Escape HTML and Markdown controls before adding owned markup."""
    escaped = html.escape(_single_line_text(value), quote=False)
    return _MARKDOWN_CONTROL_PATTERN.sub(r"\\\1", escaped)


def _single_line_text(value: str) -> str:
    """Flatten source-controlled line breaks before display formatting."""
    return _DISPLAY_LINE_BREAK_PATTERN.sub(" ", value)


def _sentence(value: str) -> str:
    """Add one terminal period without duplicating existing punctuation."""
    return value if value.endswith((".", "?", "!")) else f"{value}."


__all__ = [
    "clean_retrieval_title",
    "format_authors",
    "format_cited_message_result",
    "format_message_result",
    "format_nature_citation",
    "reference_payload",
    "normalize_citations",
]
