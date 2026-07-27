# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bounded conversation operations for the Review agent.

The Review graph owns the complete report checkpoint.  This module exposes a
small projection of that checkpoint so follow-ups and local section edits do
not put the full report back into a prompt or shared conversation context.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, cast

from ...common.responses import message_content
from ...runtime.conversation_context.models import (
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_TEXT_CHARS,
    ArtifactRefV1,
    ContextDelta,
    ContextProjection,
    PerAgentMemory,
)
from ...runtime.langgraph_runner import build_runnable_config
from .helpers import _renumber_citations

_MAX_SOURCE_IDS = MAX_CONTEXT_ITEMS
_MAX_HEADINGS = MAX_CONTEXT_ITEMS
_MAX_CLAIMS = MAX_CONTEXT_ITEMS
_MAX_GAPS = MAX_CONTEXT_ITEMS
_MAX_SECTION_CHARS = MAX_CONTEXT_TEXT_CHARS
_MAX_CLAIM_CHARS = 512
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
_REPORT_HEADING_PATTERN = re.compile(r"(?m)^(#{1,6})[ \t]+([^\n]+?)[ \t]*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class ReviewConversationOperation(StrEnum):
    """Private operation selected for one Review context turn."""

    NEW_REVIEW = "new_review"
    FOLLOW_UP = "follow_up"
    LOCAL_REVISION = "local_revision"
    SCOPE_CHANGE = "scope_change"


class ReviewClarificationError(ValueError):
    """Raised when a Review turn cannot produce a valid bounded result."""


@dataclass(frozen=True, slots=True)
class ReviewSection:
    """One bounded report section retained for local reassembly."""

    section_id: str
    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class RevisedSection:
    """The result of revising one validated section."""

    section_id: str
    text: str
    heading: str = ""


@dataclass(frozen=True, slots=True)
class ReviewReportSectionSpan:
    """Private offsets for one section in the original report text."""

    section_id: str
    heading: str
    body_start: int
    body_end: int


@dataclass(frozen=True, slots=True)
class ReviewReportDocument:
    """Original Review text plus spans used for byte-preserving edits."""

    text: str
    sections: tuple[ReviewReportSectionSpan, ...]

    def replace(self, revised: RevisedSection) -> str:
        """Replace one section body without rendering any other report bytes."""
        target = _slug(revised.heading or revised.section_id)
        for section in self.sections:
            if section.section_id != target:
                continue
            body = self.text[section.body_start : section.body_end]
            leading = body[: len(body) - len(body.lstrip())]
            trailing = body[len(body.rstrip()) :]
            return (
                self.text[: section.body_start]
                + leading
                + revised.text
                + trailing
                + self.text[section.body_end :]
            )
        raise ReviewClarificationError(
            f"The requested Review section {target!r} is unavailable."
        )


@dataclass(frozen=True, slots=True)
class ReviewCheckpointSnapshot:
    """Bounded semantic data recovered from a Review graph checkpoint."""

    research_question: str
    source_ids: tuple[str, ...] = ()
    outline_headings: tuple[str, ...] = ()
    key_claims: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()
    sections: tuple[ReviewSection, ...] = ()
    report_artifact_id: str | None = None
    report_revision: int = 0

    def section(self, section_id: str) -> ReviewSection | None:
        """Return one section by stable id or case-insensitive heading."""
        normalized = _slug(section_id)
        for item in self.sections:
            if (
                item.section_id == normalized
                or _slug(item.heading) == normalized
            ):
                return item
        return None


@dataclass(frozen=True, slots=True)
class _PreparedReviewTurn:
    projection: ContextProjection
    operation: ReviewConversationOperation
    snapshot: ReviewCheckpointSnapshot | None
    section: ReviewSection | None


ChatSeam = Callable[[str], Awaitable[Mapping[str, Any] | str | None]]


def _bounded_text(value: object, limit: int = MAX_CONTEXT_TEXT_CHARS) -> str:
    """Return bounded text without carrying arbitrary checkpoint values."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _bounded_items(
    values: object,
    *,
    limit: int,
    item_limit: int = MAX_CONTEXT_TEXT_CHARS,
) -> tuple[str, ...]:
    """Keep an ordered, deduplicated sequence of bounded strings."""
    if isinstance(values, str) or not isinstance(values, Sequence):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _bounded_text(value, item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _slug(value: str) -> str:
    """Make a stable, human-readable section identifier."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _candidate_thread_id(stable_thread_id: str, turn_id: str) -> str:
    """Derive a deterministic isolated checkpoint thread for one turn."""
    digest = hashlib.sha256(
        f"review-candidate-v1:{stable_thread_id}:{turn_id}".encode()
    ).hexdigest()[:32]
    return f"{stable_thread_id}:candidate:{digest}"


def _state_values(state: object) -> Mapping[str, Any]:
    """Unwrap LangGraph snapshots and checkpoint tuples to channel values."""
    current: object = state
    if not isinstance(current, Mapping):
        values = getattr(current, "values", None)
        if callable(values):
            values = None
        if values is None:
            values = getattr(current, "checkpoint", None)
        if values is not None:
            current = values
    if not isinstance(current, Mapping):
        return {}
    for key in ("channel_values", "values", "state"):
        nested = current.get(key)
        if isinstance(nested, Mapping):
            return nested
    return current


def _source_ids(state: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract opaque source identifiers and discard document bodies."""
    values: list[str] = []
    seen: set[str] = set()
    for key in ("all_raw_doc_list", "add_doc_list", "source_ids"):
        raw = state.get(key)
        if isinstance(raw, str) or not isinstance(raw, Sequence):
            continue
        for item in raw:
            candidate: object = item
            if isinstance(item, Mapping):
                candidate = (
                    item.get("source_id")
                    or item.get("doc_id")
                    or item.get("sourceId")
                    or item.get("id")
                )
            if not isinstance(candidate, (str, int)):
                continue
            value = str(candidate).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            values.append(value)
            if len(values) >= _MAX_SOURCE_IDS:
                return tuple(values)
    return tuple(values)


def _report_document_from_text(report: str) -> ReviewReportDocument:
    """Index Markdown headings while retaining the source text verbatim."""
    headings = list(_REPORT_HEADING_PATTERN.finditer(report))
    spans: list[ReviewReportSectionSpan] = []
    used_ids: set[str] = set()
    for index, heading in enumerate(headings):
        heading_text = heading.group(2).strip()
        section_id = _slug(heading_text) or f"section-{index + 1}"
        if section_id in used_ids:
            section_id = f"{section_id}-{index + 1}"
        used_ids.add(section_id)
        spans.append(
            ReviewReportSectionSpan(
                section_id=section_id,
                heading=heading_text,
                body_start=heading.end(),
                body_end=(
                    headings[index + 1].start()
                    if index + 1 < len(headings)
                    else len(report)
                ),
            )
        )
    return ReviewReportDocument(text=report, sections=tuple(spans))


def extract_review_report_document(
    state: object,
) -> ReviewReportDocument | None:
    """Extract the private report source without admitting it to projections."""
    values = _state_values(state)
    candidate: object = values.get("summary_content")
    if not isinstance(candidate, str) or not candidate:
        candidate = values.get("report_text")
    if not isinstance(candidate, str) or not candidate:
        final_response = values.get("final_response")
        if isinstance(final_response, Mapping):
            candidate = message_content(final_response)
    if not isinstance(candidate, str) or not candidate:
        return None
    return _report_document_from_text(candidate)


def _sections_from_report_document(
    document: ReviewReportDocument,
    dimensions: Sequence[str],
) -> tuple[ReviewSection, ...]:
    """Project bounded section text from the private report source."""
    spans = list(document.sections)
    if dimensions:
        by_id = {span.section_id: span for span in spans}
        selected = [
            by_id[_slug(dimension)]
            for dimension in dimensions
            if _slug(dimension) in by_id
        ]
        if selected:
            spans = selected
    sections = [
        ReviewSection(
            section_id=span.section_id,
            heading=span.heading,
            text=_bounded_text(
                document.text[span.body_start : span.body_end],
                _MAX_SECTION_CHARS,
            ),
        )
        for span in spans[:_MAX_HEADINGS]
    ]
    return tuple(item for item in sections if item.text or item.heading)


def _section_values(state: Mapping[str, Any]) -> tuple[ReviewSection, ...]:
    """Extract section text from revised reports without the full summary."""
    dimensions = _bounded_items(
        state.get("research_dimensions"),
        limit=_MAX_HEADINGS,
        item_limit=256,
    )
    document = extract_review_report_document(state)
    if document is not None and document.sections:
        document_sections = _sections_from_report_document(
            document, dimensions
        )
        if document_sections:
            return document_sections
    raw_reports = state.get("revised_reports")
    report_rows = (
        []
        if isinstance(raw_reports, str)
        or not isinstance(raw_reports, Sequence)
        else list(raw_reports)
    )
    if not report_rows:
        raw_drafts = state.get("draft_contents")
        if isinstance(raw_drafts, Sequence) and not isinstance(
            raw_drafts, str
        ):
            report_rows = list(raw_drafts)

    sections: list[ReviewSection] = []
    used_ids: set[str] = set()
    count = max(len(dimensions), len(report_rows))
    for index in range(count):
        row = report_rows[index] if index < len(report_rows) else {}
        if isinstance(row, Mapping):
            heading_value = (
                row.get("subtopic") or row.get("heading") or row.get("title")
            )
            text_value = (
                row.get("revised_report")
                or row.get("text")
                or row.get("content")
            )
        else:
            heading_value = None
            text_value = row
        heading = _bounded_text(
            heading_value
            or (dimensions[index] if index < len(dimensions) else ""),
            256,
        )
        text = _bounded_text(text_value, _MAX_SECTION_CHARS)
        if not heading and not text:
            continue
        heading = heading or f"Section {index + 1}"
        section_id = _slug(heading) or f"section-{index + 1}"
        if section_id in used_ids:
            section_id = f"{section_id}-{index + 1}"
        used_ids.add(section_id)
        sections.append(ReviewSection(section_id, heading, text))
    return tuple(sections[:_MAX_HEADINGS])


def _claim_summary(text: str) -> str:
    """Reduce one section to a short claim, never a report-sized field."""
    for fragment in _SENTENCE_SPLIT.split(text):
        candidate = re.sub(r"^\s*[#>*-]+\s*", "", fragment).strip()
        if candidate:
            return candidate[:_MAX_CLAIM_CHARS]
    return ""


def _explicit_claims(state: Mapping[str, Any]) -> tuple[str, ...]:
    """Read claim summaries from structured checkpoint fields only."""
    for key in ("key_claim_summaries", "claim_summaries", "key_claims"):
        raw = state.get(key)
        if isinstance(raw, Mapping):
            raw = list(raw.values())
        if isinstance(raw, Sequence) and not isinstance(raw, str):
            claims: list[str] = []
            for item in raw:
                if isinstance(item, Mapping):
                    item = (
                        item.get("summary")
                        or item.get("claim_summary")
                        or item.get("text")
                    )
                text = _bounded_text(item, _MAX_CLAIM_CHARS)
                if text:
                    claims.append(text)
            if claims:
                return tuple(claims[:_MAX_CLAIMS])
    return ()


def _feedback_metadata(
    state: Mapping[str, Any],
    field_names: tuple[str, ...],
) -> tuple[str, ...]:
    """Read short structured claim/gap fields from review feedback rows."""
    raw_rows = state.get("review_contents") or state.get("review_feedback")
    if isinstance(raw_rows, (Mapping, str)):
        raw_rows = [raw_rows]
    if not isinstance(raw_rows, Sequence):
        return ()
    values: list[str] = []
    for row in raw_rows[:_MAX_CLAIMS]:
        payload: Mapping[str, Any] | None = None
        if isinstance(row, Mapping):
            payload = row
        elif isinstance(row, str):
            try:
                parsed = json.loads(row[:_MAX_SECTION_CHARS])
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, Mapping):
                payload = parsed
        if payload is None:
            continue
        for field_name in field_names:
            candidate = payload.get(field_name)
            if isinstance(candidate, Sequence) and not isinstance(
                candidate, str
            ):
                values.extend(
                    item
                    for item in candidate
                    if isinstance(item, str) and item.strip()
                )
            elif isinstance(candidate, str) and candidate.strip():
                values.append(candidate)
    return _bounded_items(
        values,
        limit=_MAX_CLAIMS,
        item_limit=_MAX_CLAIM_CHARS,
    )


def _artifact_id(state: Mapping[str, Any]) -> str | None:
    """Read only an opaque report artifact identifier."""
    candidate: object = state.get("report_artifact_id")
    if candidate is None:
        report = state.get("report_artifact")
        if isinstance(report, Mapping):
            candidate = report.get("artifact_id")
    if candidate is None:
        candidate = state.get("artifact_id")
    if not isinstance(candidate, str):
        return None
    value = candidate.strip()
    if not value or "/" in value or "\\" in value:
        return None
    return value[:128]


def _report_revision(state: Mapping[str, Any]) -> int:
    """Read a non-negative artifact revision from the checkpoint."""
    candidate = state.get("report_revision")
    if candidate is None:
        report = state.get("report_artifact")
        if isinstance(report, Mapping):
            candidate = report.get("revision")
    if candidate is None:
        return 0
    if isinstance(candidate, bool):
        return 0
    try:
        revision = int(candidate)
    except (TypeError, ValueError):
        return 0
    return max(0, revision)


def extract_review_checkpoint(
    state: object,
) -> ReviewCheckpointSnapshot | None:
    """Extract a bounded Review snapshot from a graph checkpoint."""
    values = _state_values(state)
    question = _bounded_text(
        values.get("original_user_query") or values.get("user_query"),
        1024,
    )
    sections = _section_values(values)
    headings = _bounded_items(
        values.get("research_dimensions")
        or [item.heading for item in sections],
        limit=_MAX_HEADINGS,
        item_limit=256,
    )
    claims = _explicit_claims(values)
    if not claims:
        claims = _feedback_metadata(
            values,
            ("key_claim_summary", "claim_summary", "key_claims"),
        )
    if not claims:
        claims = tuple(
            summary
            for summary in (_claim_summary(item.text) for item in sections)
            if summary
        )[:_MAX_CLAIMS]
    gaps = _bounded_items(
        values.get("evidence_gaps")
        or values.get("unresolved_evidence_gaps")
        or values.get("open_questions"),
        limit=_MAX_GAPS,
        item_limit=_MAX_CLAIM_CHARS,
    )
    if not gaps:
        gaps = _feedback_metadata(
            values,
            ("evidence_gaps", "unresolved_evidence_gaps", "open_questions"),
        )
    if (
        not question
        and not sections
        and not headings
        and not _source_ids(values)
    ):
        return None
    return ReviewCheckpointSnapshot(
        research_question=question,
        source_ids=_source_ids(values),
        outline_headings=headings,
        key_claims=claims,
        evidence_gaps=gaps,
        sections=sections,
        report_artifact_id=_artifact_id(values),
        report_revision=_report_revision(values),
    )


async def load_review_checkpoint(
    agent: Any,
    thread_id: str,
) -> ReviewCheckpointSnapshot | None:
    """Load Review's private checkpoint through its derived thread ID."""
    state = await _load_review_checkpoint_state(agent, thread_id)
    return extract_review_checkpoint(state)


async def _load_review_checkpoint_state(
    agent: Any,
    thread_id: str,
) -> object:
    """Read one checkpoint object for both metadata and private report bytes."""
    config = build_runnable_config(thread_id)
    app = getattr(agent, "app", None)
    getter = cast(
        Callable[[Any], Awaitable[Any]] | None,
        getattr(app, "aget_state", None),
    )
    if callable(getter):
        snapshot = await getter(config)
        if extract_review_checkpoint(snapshot) is not None:
            return snapshot
    checkpointer = getattr(agent, "checkpointer", None)
    getter = cast(
        Callable[[Any], Awaitable[Any]] | None,
        getattr(checkpointer, "aget", None),
    )
    if callable(getter):
        checkpoint = await getter(config)
        return checkpoint
    return {}


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
        return ReviewConversationOperation.NEW_REVIEW
    if _REVISION_WORDS.search(text) and _section_reference(text, snapshot):
        return ReviewConversationOperation.LOCAL_REVISION
    if _SCOPE_WORDS.search(text):
        return ReviewConversationOperation.SCOPE_CHANGE
    return ReviewConversationOperation.FOLLOW_UP


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
    if operation not in {
        ReviewConversationOperation.NEW_REVIEW,
        ReviewConversationOperation.SCOPE_CHANGE,
    }:
        return None
    revision = snapshot.report_revision if snapshot is not None else 0
    return ReviewCheckpointSnapshot(
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
    """Find Review's existing ordered references through known result wrappers."""
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


def _reference_metadata(
    value: object,
) -> list[dict[str, Any]]:
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
    if operation is ReviewConversationOperation.FOLLOW_UP:
        return _answer_from_result(result)[:_MAX_SUMMARY_CHARS]
    if operation is ReviewConversationOperation.LOCAL_REVISION:
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


class ReviewConversationAdapter:
    """Prepare bounded Review turns and produce bounded context deltas."""

    def __init__(self) -> None:
        self._prepared: _PreparedReviewTurn | None = None
        self._active_snapshot: ReviewCheckpointSnapshot | None = None
        self._staged_snapshot: ReviewCheckpointSnapshot | None = None
        self._last_revised_section: RevisedSection | None = None
        self._captured_result: dict[str, Any] = {}
        self._report_revision = 0
        self._settled = False
        self._operation_successful = False
        self._report_document: ReviewReportDocument | None = None
        self._agent: Any | None = None
        self._thread_id: str | None = None
        self._stable_thread_id: str | None = None
        self._execution_thread_id: str | None = None
        self._candidate_thread_id: str | None = None
        self._turn_id: str | None = None
        self._candidate_discarded = False
        self._pending_report_text: str | None = None
        self._ordered_doc_list: list[dict[str, Any]] = []

    def prepare(
        self,
        projection: ContextProjection,
        *,
        snapshot: ReviewCheckpointSnapshot | Mapping[str, Any] | None = None,
        allow_unresolved_section: bool = False,
        report_document: ReviewReportDocument | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Classify and project one Review turn."""
        self._stable_thread_id = projection.agent_thread_id
        self._thread_id = self._stable_thread_id
        self._turn_id = turn_id
        self._report_document = None
        self._ordered_doc_list = []
        if snapshot is not None and not isinstance(
            snapshot, ReviewCheckpointSnapshot
        ):
            self._ordered_doc_list = _reference_metadata(snapshot)
            report_document = extract_review_report_document(snapshot)
            snapshot = extract_review_checkpoint(snapshot)
        elif report_document is None and self._agent is None:
            self._report_document = None
        if report_document is not None:
            self._report_document = report_document
        operation = classify_review_operation(
            projection.current_query,
            projection=projection,
            snapshot=snapshot,
        )
        if (
            operation
            in {
                ReviewConversationOperation.NEW_REVIEW,
                ReviewConversationOperation.SCOPE_CHANGE,
            }
            and not self._turn_id
        ):
            raise ReviewClarificationError(
                "A valid turn id is required for a new Review graph turn."
            )
        section: ReviewSection | None = None
        section_id = _section_reference(projection.current_query, snapshot)
        if operation is ReviewConversationOperation.LOCAL_REVISION:
            if (
                snapshot is None and not allow_unresolved_section
            ) or section_id is None:
                raise ReviewClarificationError(
                    "Please name an active Review section to revise."
                )
            if snapshot is not None:
                section = snapshot.section(section_id)
            if snapshot is not None and section is None:
                raise ReviewClarificationError(
                    f"The requested Review section {section_id!r} is unknown."
                )
        self._prepared = _PreparedReviewTurn(
            projection=projection,
            operation=operation,
            snapshot=snapshot,
            section=section,
        )
        self._active_snapshot = snapshot
        self._staged_snapshot = _staged_scope_snapshot(
            projection, snapshot, operation
        )
        self._last_revised_section = None
        self._captured_result = {}
        self._report_revision = snapshot.report_revision if snapshot else 0
        self._settled = False
        self._operation_successful = (
            operation is not ReviewConversationOperation.LOCAL_REVISION
        )
        if operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            assert self._turn_id is not None
            self._candidate_thread_id = _candidate_thread_id(
                self._stable_thread_id, self._turn_id
            )
            self._execution_thread_id = self._candidate_thread_id
        else:
            self._candidate_thread_id = None
            self._execution_thread_id = self._stable_thread_id
        self._candidate_discarded = False
        self._pending_report_text = None
        prompt_context = _prompt_context(snapshot, section=section)
        return {
            "user_query": projection.current_query,
            "locale": projection.locale,
            "thread_id": projection.agent_thread_id,
            "operation": operation,
            "review_context": prompt_context,
            "prompt_context": prompt_context,
            "evidence_summary": _evidence_summary(snapshot),
            "section_id": section.section_id if section else section_id,
            "section_text": section.text if section else "",
            "report_artifact_id": (
                snapshot.report_artifact_id if snapshot else None
            ),
            "report_revision": self._report_revision,
        }

    async def prepare_from_agent(
        self,
        projection: ContextProjection,
        agent: Any,
        thread_id: str,
        *,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Load the private checkpoint, then prepare against its snapshot."""
        del thread_id
        stable_thread_id = projection.agent_thread_id
        self._agent = agent
        self._stable_thread_id = stable_thread_id
        self._thread_id = stable_thread_id
        self._turn_id = turn_id
        state = await _load_review_checkpoint_state(agent, stable_thread_id)
        snapshot = extract_review_checkpoint(state)
        document = extract_review_report_document(state)
        if snapshot is None and (
            document is not None or _projection_has_active_review(projection)
        ):
            raise ReviewClarificationError(
                "The active Review checkpoint is unavailable."
            )
        prepared = self.prepare(
            projection,
            snapshot=snapshot,
            report_document=document,
            turn_id=self._turn_id,
        )
        ordered = _reference_metadata(state)
        if ordered:
            self._ordered_doc_list = ordered
        return prepared

    @property
    def operation(self) -> ReviewConversationOperation | None:
        """Return the prepared private operation, if any."""
        return self._prepared.operation if self._prepared else None

    @property
    def snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return the bounded checkpoint snapshot used for this turn."""
        return self._prepared.snapshot if self._prepared else None

    @property
    def active_snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return the last committed snapshot, excluding staged scope changes."""
        return self._active_snapshot

    @property
    def staged_snapshot(self) -> ReviewCheckpointSnapshot | None:
        """Return a candidate snapshot pending successful settlement."""
        return self._staged_snapshot

    @property
    def report_revision(self) -> int:
        """Return the last settled report artifact revision."""
        return self._report_revision

    @property
    def stable_thread_id(self) -> str | None:
        """Return the active Review checkpoint thread."""
        return self._stable_thread_id

    @property
    def candidate_thread_id(self) -> str | None:
        """Return the isolated checkpoint thread pending acknowledgement."""
        return self._candidate_thread_id

    @property
    def execution_thread_id(self) -> str | None:
        """Return the thread on which this turn may execute its graph."""
        return self._execution_thread_id

    @property
    def settlement_ready(self) -> bool:
        """Return whether this turn produced a valid candidate for settlement."""
        if self._prepared is not None and self._prepared.operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            return (
                self._operation_successful
                and self.candidate_thread_id is not None
                and self._agent is not None
            )
        return self._operation_successful

    def settlement_metadata(self) -> dict[str, Any] | None:
        """Return bounded private metadata persisted with one staged turn."""
        operation = self.operation
        if not self.settlement_ready or operation is None:
            return None
        if not self.stable_thread_id or not self._turn_id:
            return None
        candidate = self.candidate_thread_id
        if (
            operation
            in {
                ReviewConversationOperation.NEW_REVIEW,
                ReviewConversationOperation.SCOPE_CHANGE,
            }
            and not candidate
        ):
            return None
        return {
            "version": 1,
            "operation": operation.value,
            "stable_thread_id": self.stable_thread_id[:512],
            "candidate_thread_id": candidate[:512] if candidate else None,
            "turn_id": self._turn_id[:64],
            "report_revision": self._report_revision,
            "settlement_state": "pending",
        }

    async def validate_settlement_candidate(self) -> bool:
        """Verify the current private candidate before context staging."""
        if not self.settlement_ready or self._agent is None:
            return False
        operation = self.operation
        if operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            if self.candidate_thread_id is None:
                self.mark_failed()
                return False
            state = await _load_review_checkpoint_state(
                self._agent, self.candidate_thread_id
            )
            values = _state_values(state)
            snapshot = extract_review_checkpoint(state)
            document = extract_review_report_document(state)
            if not values or snapshot is None or document is None:
                self.mark_failed()
                return False
            self._staged_snapshot = snapshot
            self._report_document = document
            self._pending_report_text = document.text
            return True
        if self.stable_thread_id is None:
            self.mark_failed()
            return False
        state = await _load_review_checkpoint_state(
            self._agent, self.stable_thread_id
        )
        snapshot = extract_review_checkpoint(state)
        if snapshot is None:
            self.mark_failed()
            return False
        self._active_snapshot = snapshot
        return operation in {
            ReviewConversationOperation.FOLLOW_UP,
            ReviewConversationOperation.LOCAL_REVISION,
        }

    async def restore_settlement(
        self,
        metadata: Mapping[str, Any],
        agent: Any,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        """Reconstruct a pending turn from durable metadata after restart."""
        if metadata.get("version") != 1:
            raise ReviewClarificationError(
                "Review settlement metadata version is unsupported."
            )
        operation_value = metadata.get("operation")
        try:
            operation = ReviewConversationOperation(str(operation_value))
        except ValueError as exc:
            raise ReviewClarificationError(
                "Review settlement metadata is invalid."
            ) from exc
        stable = metadata.get("stable_thread_id")
        turn_id = metadata.get("turn_id")
        candidate = metadata.get("candidate_thread_id")
        if not isinstance(stable, str) or not stable:
            raise ReviewClarificationError(
                "Review settlement has no stable checkpoint thread."
            )
        if not isinstance(turn_id, str) or not turn_id:
            raise ReviewClarificationError("Review settlement has no turn id.")
        if operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        } and (not isinstance(candidate, str) or not candidate):
            raise ReviewClarificationError(
                "Review settlement has no candidate checkpoint thread."
            )
        self._agent = agent
        self._stable_thread_id = stable[:512]
        self._thread_id = self._stable_thread_id
        self._candidate_thread_id = (
            candidate[:512] if isinstance(candidate, str) else None
        )
        self._execution_thread_id = (
            self._candidate_thread_id or self._stable_thread_id
        )
        self._turn_id = turn_id[:64]
        try:
            self._report_revision = max(
                0, int(metadata.get("report_revision", 0))
            )
        except (TypeError, ValueError) as exc:
            raise ReviewClarificationError(
                "Review settlement revision metadata is invalid."
            ) from exc
        self._settled = False
        self._candidate_discarded = False
        self._operation_successful = True
        state = await _load_review_checkpoint_state(
            agent, self._stable_thread_id
        )
        snapshot = extract_review_checkpoint(state)
        if snapshot is None and operation in {
            ReviewConversationOperation.FOLLOW_UP,
            ReviewConversationOperation.LOCAL_REVISION,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            raise ReviewClarificationError(
                "The active Review checkpoint is unavailable."
            )
        self._active_snapshot = snapshot
        self._report_document = extract_review_report_document(state)
        projection = ContextProjection.model_construct(
            current_query="settlement",
            intent_kind="follow_up",
            task_summary="",
            relevant_recent_turns=[],
            relevant_user_turns=[],
            relevant_assistant_summaries=[],
            active_entities=[],
            open_questions=[],
            artifact_refs=[],
            agent_thread_id=self._stable_thread_id,
            locale="en-US",
            token_budget=1,
            context_truncated=False,
        )
        self._prepared = _PreparedReviewTurn(
            projection=projection,
            operation=operation,
            snapshot=snapshot,
            section=None,
        )
        self._staged_snapshot = None
        self._pending_report_text = None
        self._ordered_doc_list = _reference_metadata(state)
        if operation is ReviewConversationOperation.LOCAL_REVISION:
            answer = _answer_from_result(result or {})
            if not _usable_response_text(answer):
                self.mark_failed()
                raise ReviewClarificationError(
                    "Review section revision returned empty or invalid content."
                )
            self._pending_report_text = answer
            self._report_document = _report_document_from_text(answer)

    def mark_failed(self) -> None:
        """Discard any candidate produced by a failed or incomplete turn."""
        self._operation_successful = False
        self._staged_snapshot = None
        self._pending_report_text = None

    def capture_result(self, result: Mapping[str, Any]) -> None:
        """Capture only bounded metadata from a completed Review result."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before capture_result")
        if not self._operation_successful:
            return
        ordered = _reference_metadata(result)
        if ordered:
            self._ordered_doc_list = ordered
        answer = _answer_from_result(result)
        captured_answer = (
            _bounded_text(answer, _MAX_SUMMARY_CHARS)
            if self._prepared.operation
            is ReviewConversationOperation.FOLLOW_UP
            else ""
        )
        document = extract_review_report_document(result.get("phytomni_state"))
        checkpoint = extract_review_checkpoint(result.get("phytomni_state"))
        if not _usable_response_text(answer):
            self.mark_failed()
            return
        self._captured_result = {
            "result": {
                "formatted": {
                    "answer": captured_answer,
                    "follow_up_questions": _follow_up_questions(result),
                }
            }
        }
        if document is not None and document.text:
            self._report_document = document
            self._pending_report_text = document.text
        if checkpoint is not None:
            self._staged_snapshot = checkpoint
        if self._last_revised_section is not None:
            base = self._active_snapshot or self._prepared.snapshot
            if base is not None:
                self._staged_snapshot = _replace_section(
                    base, self._last_revised_section
                )
        if (
            self._prepared.operation
            is ReviewConversationOperation.LOCAL_REVISION
        ):
            if not _answer_from_result(result):
                self.mark_failed()
            elif self._report_document is not None:
                self._pending_report_text = (
                    self._report_document.replace(self._last_revised_section)
                    if self._last_revised_section is not None
                    else None
                )

    def settle(self, success: bool) -> int:
        """Advance the report revision only after successful settlement."""
        if not success or not self._operation_successful:
            self._staged_snapshot = None
            return self._report_revision
        if not self._settled:
            self._report_revision += 1
            if self._staged_snapshot is not None:
                self._active_snapshot = replace(
                    self._staged_snapshot,
                    report_revision=self._report_revision,
                )
            self._settled = True
        return self._report_revision

    async def settle_async(self, success: bool) -> int:
        """Settle the candidate and persist private report state when available."""
        prepared = self._prepared
        if prepared is None:
            raise RuntimeError("prepare must run before settle_async")
        if not success or not self._operation_successful:
            return self.settle(False)
        if self._settled:
            return self._report_revision
        next_revision = self._report_revision + 1
        if prepared.operation in {
            ReviewConversationOperation.NEW_REVIEW,
            ReviewConversationOperation.SCOPE_CHANGE,
        }:
            if self.candidate_thread_id is None:
                raise RuntimeError(
                    "Review graph settlement requires a turn-scoped candidate"
                )
            await self._promote_candidate(next_revision)
        else:
            await self._update_stable_checkpoint(next_revision)
        return self.settle(True)

    async def _update_stable_checkpoint(self, revision: int) -> None:
        """Persist a bounded follow-up or local revision on the active thread."""
        app = getattr(self._agent, "app", None)
        updater = cast(
            Callable[..., Awaitable[Any]] | None,
            getattr(app, "aupdate_state", None),
        )
        if not callable(updater) or self.stable_thread_id is None:
            if self._agent is not None:
                raise RuntimeError(
                    "Review stable checkpoint cannot be updated"
                )
            return
        values: dict[str, Any] = {"report_revision": revision}
        if self._pending_report_text is not None:
            values["summary_content"] = self._pending_report_text
        await updater(
            build_runnable_config(self.stable_thread_id), values=values
        )

    async def _promote_candidate(self, revision: int) -> None:
        """Copy an isolated graph result to the stable thread after acknowledgement."""
        if self._agent is None or self.candidate_thread_id is None:
            raise RuntimeError("Review candidate checkpoint is unavailable")
        if self.stable_thread_id is None:
            raise RuntimeError(
                "Review stable checkpoint thread is unavailable"
            )
        candidate = await _load_review_checkpoint_state(
            self._agent, self.candidate_thread_id
        )
        candidate_values = _state_values(candidate)
        if (
            not candidate_values
            or extract_review_checkpoint(candidate) is None
        ):
            raise RuntimeError("Review candidate checkpoint is not promotable")
        app = getattr(self._agent, "app", None)
        updater = cast(
            Callable[..., Awaitable[Any]] | None,
            getattr(app, "aupdate_state", None),
        )
        if not callable(updater):
            raise ReviewClarificationError(
                "Review stable checkpoint cannot be promoted."
            )
        values = dict(candidate_values)
        values["report_revision"] = revision
        if self._pending_report_text is not None:
            values["summary_content"] = self._pending_report_text
        await updater(
            build_runnable_config(self.stable_thread_id), values=values
        )

    async def discard_pending_candidate(self) -> None:
        """Delete an unacknowledged candidate without touching active state."""
        if (
            self._candidate_discarded
            or self._agent is None
            or self.candidate_thread_id is None
        ):
            return
        app = getattr(self._agent, "app", None)
        deleter = getattr(app, "adelete_thread", None)
        if not callable(deleter):
            checkpointer = getattr(self._agent, "checkpointer", None)
            deleter = getattr(checkpointer, "adelete_thread", None)
        if callable(deleter):
            result = deleter(self.candidate_thread_id)
            if inspect.isawaitable(result):
                await result
        self._candidate_discarded = True

    def follow_up_prompt(self) -> str:
        """Build the bounded prompt for an existing-claim follow-up."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before follow_up_prompt")
        context = _prompt_context(self._prepared.snapshot)
        return (
            "Answer the current Review follow-up using only the bounded context. "
            "Do not reconstruct or quote the complete prior report.\n\n"
            f"{context}\n\n[current question]\n"
            f"{self._prepared.projection.current_query}"
        )[:_MAX_PROMPT_CHARS]

    async def follow_up(
        self,
        chat: ChatSeam,
    ) -> dict[str, Any]:
        """Answer a follow-up through Review's existing chat seam."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before follow_up")
        response = await chat(self.follow_up_prompt())
        answer = _response_text(response)
        if not _usable_response_text(answer):
            self.mark_failed()
            raise ReviewClarificationError(
                "Review follow-up returned empty or invalid content."
            )
        return self._answer_result(
            answer, ReviewConversationOperation.FOLLOW_UP
        )

    async def local_revision(
        self,
        chat: ChatSeam,
    ) -> dict[str, Any]:
        """Revise only the validated section and reassemble the report."""
        if self._prepared is None or self._prepared.section is None:
            raise ReviewClarificationError(
                "Please name an active Review section to revise."
            )
        section = self._prepared.section
        revised = await revise_section(
            section_id=section.section_id,
            section_text=section.text,
            instruction=self._prepared.projection.current_query,
            evidence_summary=_evidence_summary(self._prepared.snapshot),
            chat=chat,
        )
        self._last_revised_section = revised
        report_source: ReviewReportDocument | Sequence[ReviewSection] = (
            self._report_document
            if self._report_document is not None
            else (
                self._prepared.snapshot.sections
                if self._prepared.snapshot is not None
                else (section,)
            )
        )
        report = reassemble_report(report_source, revised)
        self._pending_report_text = report
        self._operation_successful = bool(report.strip())
        return self._answer_result(
            report, ReviewConversationOperation.LOCAL_REVISION
        )

    def delta(self, result: Mapping[str, Any] | None = None) -> ContextDelta:
        """Convert a successful Review result into bounded semantic metadata."""
        if self._prepared is None:
            raise RuntimeError("prepare must run before delta")
        result = result or self._captured_result
        snapshot = (
            self._staged_snapshot
            if self._prepared.operation
            in {
                ReviewConversationOperation.NEW_REVIEW,
                ReviewConversationOperation.SCOPE_CHANGE,
            }
            else self._prepared.snapshot
        )
        summary = _review_summary(
            self._prepared.operation,
            self._prepared.projection,
            snapshot,
            result,
        )
        artifact_upserts: list[ArtifactRefV1] = []
        artifact_id = (
            snapshot.report_artifact_id if snapshot is not None else None
        )
        authorized = {
            item.artifact_id: item
            for item in self._prepared.projection.artifact_refs
        }
        if artifact_id in authorized:
            artifact_upserts.append(authorized[artifact_id])
        return ContextDelta(
            summary_update=summary[:MAX_CONTEXT_TEXT_CHARS],
            open_question_updates=_follow_up_questions(result),
            artifact_upserts=artifact_upserts,
            agent_memory_update=PerAgentMemory(
                agent_id="ReviewAgent",
                thread_id=self._prepared.projection.agent_thread_id,
                summary=summary[:MAX_CONTEXT_TEXT_CHARS],
                checkpoint_ref=(
                    artifact_id if artifact_id in authorized else None
                ),
            ),
        )

    def _answer_result(
        self,
        content: str,
        operation: ReviewConversationOperation,
    ) -> dict[str, Any]:
        """Shape a cited-compatible answer without exposing private state."""
        return {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "doc_list": [
                            dict(item) for item in self._ordered_doc_list
                        ],
                        "total": 10000,
                        "follow_up_questions": [],
                    }
                }
            ],
            "phytomni_state": {
                "review_operation": operation.value,
                "report_artifact_id": (
                    self._prepared.snapshot.report_artifact_id
                    if self._prepared and self._prepared.snapshot
                    else None
                ),
                "report_revision": self._report_revision,
            },
        }


async def revise_section(
    *,
    section_id: str,
    section_text: str,
    instruction: str,
    evidence_summary: str,
    chat: ChatSeam | None = None,
) -> RevisedSection:
    """Use the focused Review chat seam to revise exactly one section."""
    prompt = (
        "Revise only the requested Review section. Return only the revised "
        "section text, without a heading or commentary.\n\n"
        f"[section id]\n{_bounded_text(section_id, 128)}\n\n"
        f"[section text]\n{_bounded_text(section_text)}\n\n"
        f"[instruction]\n{_bounded_text(instruction)}\n\n"
        f"[bounded evidence summary]\n{_bounded_text(evidence_summary)}"
    )[:_MAX_PROMPT_CHARS]
    if chat is None:
        raise ReviewClarificationError(
            "Review section revision could not produce content."
        )
    response: object = chat(prompt)
    if inspect.isawaitable(response):
        response = await response
    text = _response_text(response)
    if not _usable_response_text(text):
        raise ReviewClarificationError(
            "Review section revision returned empty or invalid content."
        )
    return RevisedSection(
        section_id=section_id,
        text=_bounded_text(text),
        heading=section_id,
    )


def review_clarification_result(message: str) -> dict[str, Any]:
    """Shape a clarification into the unchanged chat-completion envelope."""
    return {
        "choices": [
            {
                "message": {
                    "content": _bounded_text(message, _MAX_SUMMARY_CHARS),
                    "doc_list": [],
                    "total": 10000,
                    "follow_up_questions": [],
                }
            }
        ]
    }


def _section_parts(
    sections: Sequence[ReviewSection | Mapping[str, str]],
) -> tuple[ReviewSection, ...]:
    """Normalize section mappings accepted by the reassembly helper."""
    normalized: list[ReviewSection] = []
    for index, item in enumerate(sections, start=1):
        if isinstance(item, ReviewSection):
            normalized.append(item)
            continue
        heading = _bounded_text(item.get("heading") or item.get("title"), 256)
        text = _bounded_text(item.get("text") or item.get("content"))
        section_id = (
            _slug(item.get("section_id") or heading) or f"section-{index}"
        )
        normalized.append(ReviewSection(section_id, heading, text))
    return tuple(normalized)


def _coerce_revised_section(
    revised_section: RevisedSection | Mapping[str, str],
) -> RevisedSection:
    """Normalize the focused revision result once for both report forms."""
    if isinstance(revised_section, RevisedSection):
        return revised_section
    return RevisedSection(
        section_id=_slug(revised_section.get("section_id", "")),
        text=_bounded_text(revised_section.get("text")),
        heading=_bounded_text(revised_section.get("heading"), 256),
    )


def _replace_markdown_section(
    report: str,
    revised: RevisedSection,
) -> str:
    """Replace one Markdown heading body while preserving other bytes."""
    target = _slug(revised.heading or revised.section_id)
    headings = list(
        re.finditer(r"(?m)^(#{1,6})[ \t]+([^\n]+?)[ \t]*$", report)
    )
    for index, heading in enumerate(headings):
        if _slug(heading.group(2)) != target:
            continue
        body_start = heading.end()
        body_end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(report)
        )
        body = report[body_start:body_end]
        leading = body[: len(body) - len(body.lstrip())]
        trailing = body[len(body.rstrip()) :]
        return (
            report[:body_start]
            + leading
            + revised.text
            + trailing
            + report[body_end:]
        )
    return report


def reassemble_report(
    sections: (
        Sequence[ReviewSection | Mapping[str, str]]
        | ReviewReportDocument
        | str
    ),
    revised_section: RevisedSection | Mapping[str, str],
) -> str:
    """Reassemble a report while replacing only one section's text."""
    revised = _coerce_revised_section(revised_section)
    if isinstance(sections, ReviewReportDocument):
        return sections.replace(revised)
    if isinstance(sections, str):
        return _replace_markdown_section(sections, revised)
    parts = _section_parts(sections)
    rendered: list[str] = []
    for section in parts:
        text = (
            revised.text
            if section.section_id == _slug(revised.section_id)
            else section.text
        )
        heading = section.heading or revised.heading
        rendered.append(f"## {heading}\n{text}" if heading else text)
    return "\n\n".join(rendered)


__all__ = [
    "ReviewCheckpointSnapshot",
    "ReviewClarificationError",
    "ReviewConversationAdapter",
    "ReviewConversationOperation",
    "ReviewReportDocument",
    "ReviewReportSectionSpan",
    "ReviewSection",
    "RevisedSection",
    "classify_review_operation",
    "extract_review_checkpoint",
    "extract_review_report_document",
    "load_review_checkpoint",
    "reassemble_report",
    "review_clarification_result",
    "revise_section",
]
