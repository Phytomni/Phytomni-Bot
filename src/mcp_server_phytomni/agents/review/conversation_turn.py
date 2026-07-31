# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Intent, prompt, and bounded result helpers for Review turns."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...common.responses import message_content
from ...runtime.conversation_context.models import (
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_TEXT_CHARS,
    ContextProjection,
)
from .conversation_checkpoint import _bounded_items, _slug, _state_values
from .conversation_types import review_classes
from .helpers import _renumber_citations

if TYPE_CHECKING:
    from .conversation import (
        ReviewCheckpointSnapshot,
        ReviewConversationOperation,
        ReviewReportDocument,
        ReviewSection,
        RevisedSection,
    )

_MAX_PROMPT_CHARS = MAX_CONTEXT_TEXT_CHARS
_MAX_SUMMARY_CHARS = 1024
_INVALID_RESPONSE_TEXT = frozenset(
    {
        "no answer generated",
        "no answer generated.",
        "no summary generated",
        "no summary generated.",
        "n/a",
        "none",
        "null",
    }
)
_REVISION_WORDS = re.compile(
    r"\b(?:revise|revision|rewrite|rewritten|edit|edited|update|updated|"
    r"correct|shorten|expand|tighten|improve|change)\w*\b",
    re.IGNORECASE,
)
_SCOPE_WORDS = re.compile(
    r"\b(?:different|another|instead|broaden|narrow|focus on|"
    r"change (?:the )?scope|new source(?:s| set)?|source set|"
    r"new research question|start (?:a )?new review|now investigate)\b",
    re.IGNORECASE,
)
_SECTION_PATTERN = re.compile(
    r"\b(?:section|subsection|part)\s*[:#-]?\s*"
    r"(?:\d+|[A-Za-z][A-Za-z0-9 _-]{1,80})",
    re.IGNORECASE,
)


def _section_reference(
    query: str,
    snapshot: ReviewCheckpointSnapshot | None,
) -> str | None:
    """Resolve a section name or ordinal from the current instruction."""
    lowered = query.lower()
    if snapshot is not None:
        for index, section in enumerate(snapshot.sections, start=1):
            if re.search(rf"\bsection\s*{index}\b", lowered):
                return section.section_id
            if section.heading and section.heading.lower() in lowered:
                return section.section_id
    match = _SECTION_PATTERN.search(query)
    if match is None:
        return None
    token = match.group(0)
    token = re.sub(
        r"^(?:section|subsection|part)\s*[:#-]?\s*",
        "",
        token,
        flags=re.IGNORECASE,
    )
    return _slug(token)


def _projection_has_active_review(projection: ContextProjection) -> bool:
    """Use only bounded semantic fields to detect a prior Review turn."""
    context_text = " ".join(
        (
            projection.task_summary,
            *projection.relevant_assistant_summaries,
        )
    )
    return bool(
        re.search(r"\breview\b|\bresearch\b", context_text, re.IGNORECASE)
        or any(
            entity.entity_id.startswith("review:")
            for entity in projection.active_entities
        )
    )


def classify_review_operation(
    query: str | ContextProjection,
    *,
    projection: ContextProjection | None = None,
    snapshot: ReviewCheckpointSnapshot | None = None,
    active_review: bool | None = None,
    has_active_review: bool | None = None,
) -> ReviewConversationOperation:
    """Classify one Review turn without invoking retrieval or generation."""
    operation_type = review_classes()[2]
    if isinstance(query, ContextProjection):
        projection = query
        text = query.current_query
    else:
        text = query
    active = active_review
    if active is None:
        active = has_active_review
    if active is None:
        active = snapshot is not None or (
            projection is not None
            and _projection_has_active_review(projection)
        )
    if not active:
        return operation_type.NEW_REVIEW
    if _REVISION_WORDS.search(text) and _section_reference(text, snapshot):
        return operation_type.LOCAL_REVISION
    if _SCOPE_WORDS.search(text):
        return operation_type.SCOPE_CHANGE
    return operation_type.FOLLOW_UP


def _prompt_context(
    snapshot: ReviewCheckpointSnapshot | None,
    *,
    section: ReviewSection | None = None,
) -> str:
    """Render only bounded Review metadata for a generation prompt."""
    if snapshot is None:
        return "[review context]\nNo active Review checkpoint."
    fragments = [
        f"[research question]\n{snapshot.research_question}",
        "[source ids]\n" + (", ".join(snapshot.source_ids) or "none"),
        "[outline headings]\n"
        + (
            "\n".join(f"- {item}" for item in snapshot.outline_headings)
            or "none"
        ),
        "[key claims]\n"
        + ("\n".join(f"- {item}" for item in snapshot.key_claims) or "none"),
        "[unresolved evidence gaps]\n"
        + (
            "\n".join(f"- {item}" for item in snapshot.evidence_gaps) or "none"
        ),
    ]
    if section is not None:
        fragments.append(
            f"[requested section: {section.heading}]\n{section.text}"
        )
    return "\n\n".join(fragments)[:_MAX_PROMPT_CHARS]


def _evidence_summary(snapshot: ReviewCheckpointSnapshot | None) -> str:
    """Return a bounded claim/gap summary for local section revision."""
    if snapshot is None:
        return "No bounded evidence summary is available."
    parts = [*snapshot.key_claims, *snapshot.evidence_gaps]
    return "\n".join(f"- {item}" for item in parts)[:_MAX_PROMPT_CHARS]


def _staged_scope_snapshot(
    projection: ContextProjection,
    snapshot: ReviewCheckpointSnapshot | None,
    operation: ReviewConversationOperation,
) -> ReviewCheckpointSnapshot | None:
    """Stage a new research focus without replacing the active checkpoint."""
    classes = review_classes()
    snapshot_type, operation_type = classes[0], classes[2]
    if operation not in {
        operation_type.NEW_REVIEW,
        operation_type.SCOPE_CHANGE,
    }:
        return None
    revision = snapshot.report_revision if snapshot is not None else 0
    return snapshot_type(
        research_question=projection.current_query[:1024],
        source_ids=(),
        outline_headings=(),
        key_claims=(),
        evidence_gaps=(),
        sections=(),
        report_artifact_id=(
            snapshot.report_artifact_id if snapshot is not None else None
        ),
        report_revision=revision,
    )


def _replace_section(
    snapshot: ReviewCheckpointSnapshot,
    revised: RevisedSection,
) -> ReviewCheckpointSnapshot:
    """Build a staged snapshot with one section changed."""
    sections = tuple(
        (
            replace(
                section,
                text=revised.text,
                heading=revised.heading or section.heading,
            )
            if section.section_id == _slug(revised.section_id)
            else section
        )
        for section in snapshot.sections
    )
    return replace(snapshot, sections=sections)


def _answer_from_result(result: Mapping[str, Any]) -> str:
    """Read one answer from either a native or agent.run envelope."""
    nested = result.get("result")
    if isinstance(nested, Mapping):
        formatted = nested.get("formatted")
        if isinstance(formatted, Mapping):
            answer = formatted.get("answer")
            if isinstance(answer, str):
                return answer.strip()
        raw = nested.get("raw")
        if isinstance(raw, Mapping):
            answer = message_content(raw)
            if answer:
                return answer.strip()
    answer = message_content(result)
    return answer.strip()


def _response_text(response: object) -> str:
    """Read bounded text from a chat response or direct test seam."""
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, Mapping):
        return message_content(response).strip()
    return ""


def _usable_response_text(value: str) -> bool:
    """Reject empty and known placeholder model responses."""
    normalized = value.strip().casefold()
    return bool(normalized) and normalized not in _INVALID_RESPONSE_TEXT


def _usable_report_document(document: ReviewReportDocument | None) -> bool:
    """Require non-placeholder report bytes before restart or promotion."""
    return document is not None and _usable_response_text(document.text)


def _follow_up_questions(result: Mapping[str, Any]) -> list[str]:
    """Extract a bounded follow-up list without copying report state."""
    nested = result.get("result")
    if isinstance(nested, Mapping):
        formatted = nested.get("formatted")
        if isinstance(formatted, Mapping):
            raw_questions = formatted.get("follow_up_questions")
            if isinstance(raw_questions, Sequence) and not isinstance(
                raw_questions, str
            ):
                return list(
                    _bounded_items(
                        raw_questions,
                        limit=MAX_CONTEXT_ITEMS,
                        item_limit=512,
                    )
                )
    return []


def _bounded_doc_list(value: object) -> list[dict[str, Any]]:
    """Copy the existing ordered Review references without expanding scope."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)][
        :MAX_CONTEXT_ITEMS
    ]


def _ordered_doc_list(
    value: object, *, depth: int = 0
) -> list[dict[str, Any]]:
    """Find Review's ordered references through known result wrappers."""
    if depth > 4 or not isinstance(value, Mapping):
        return []
    for key in ("ordered_doc_list", "doc_list", "references"):
        documents = _bounded_doc_list(value.get(key))
        if documents:
            return documents
    for key in (
        "choices",
        "message",
        "final_response",
        "phytomni_state",
        "result",
        "raw",
        "formatted",
        "channel_values",
        "values",
        "state",
    ):
        nested = value.get(key)
        if isinstance(nested, Sequence) and not isinstance(
            nested, (str, bytes)
        ):
            for item in nested:
                documents = _ordered_doc_list(item, depth=depth + 1)
                if documents:
                    return documents
        else:
            documents = _ordered_doc_list(nested, depth=depth + 1)
            if documents:
                return documents
    return []


def _raw_doc_list(value: object) -> list[dict[str, Any]]:
    """Collect bounded raw documents only for citation-helper lookup."""
    if not isinstance(value, Mapping):
        return []
    documents: list[dict[str, Any]] = []
    for key in ("all_raw_doc_list", "add_doc_list"):
        documents.extend(_bounded_doc_list(value.get(key)))
    return documents[:MAX_CONTEXT_ITEMS]


def _reference_metadata(value: object) -> list[dict[str, Any]]:
    """Recover ordered references and raw inputs from one private result."""
    state_values = _state_values(value)
    if not isinstance(state_values, Mapping):
        return []
    ordered = _ordered_doc_list(value)
    raw = _raw_doc_list(value)
    if state_values is not value:
        ordered = ordered or _ordered_doc_list(state_values)
        raw = raw or _raw_doc_list(state_values)
    if not ordered:
        report = state_values.get("summary_content")
        if isinstance(report, str) and raw:
            _formatted, ordered = _renumber_citations(report, raw)
    return ordered


def _review_summary(
    operation: ReviewConversationOperation,
    projection: ContextProjection,
    snapshot: ReviewCheckpointSnapshot | None,
    result: Mapping[str, Any],
) -> str:
    """Build a compact context summary while excluding full report text."""
    operation_type = review_classes()[2]
    if operation is operation_type.FOLLOW_UP:
        return _answer_from_result(result)[:_MAX_SUMMARY_CHARS]
    if operation is operation_type.LOCAL_REVISION:
        return "Review section revision completed."
    question = (
        snapshot.research_question
        if snapshot is not None
        else projection.current_query
    )
    headings = ", ".join(snapshot.outline_headings) if snapshot else ""
    return (
        f"Review completed for {question[:512]}. Sections: {headings[:480]}"[
            :_MAX_SUMMARY_CHARS
        ]
    )
