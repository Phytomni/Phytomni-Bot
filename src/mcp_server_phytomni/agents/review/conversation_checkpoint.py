# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bounded checkpoint extraction for Review conversation turns."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from ...common.responses import message_content
from ...runtime.conversation_context.models import (
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_TEXT_CHARS,
)
from ...runtime.langgraph_runner import build_runnable_config

if TYPE_CHECKING:
    from .conversation import (
        ReviewCheckpointSnapshot,
        ReviewReportDocument,
        ReviewSection,
    )

_MAX_SOURCE_IDS = MAX_CONTEXT_ITEMS
_MAX_HEADINGS = MAX_CONTEXT_ITEMS
_MAX_CLAIMS = MAX_CONTEXT_ITEMS
_MAX_GAPS = MAX_CONTEXT_ITEMS
_MAX_SECTION_CHARS = MAX_CONTEXT_TEXT_CHARS
_MAX_CLAIM_CHARS = 512
_MAX_THREAD_ID_CHARS = 512
_MAX_TURN_ID_CHARS = 64
_REPORT_HEADING_PATTERN = re.compile(r"(?m)^(#{1,6})[ \t]+([^\n]+?)[ \t]*$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _review_classes() -> tuple[type[Any], ...]:
    """Load conversation classes only after the parent module is ready."""
    module = sys.modules[f"{__package__}.conversation"]

    return (
        module.ReviewCheckpointSnapshot,
        module.ReviewClarificationError,
        module.ReviewReportDocument,
        module.ReviewReportSectionSpan,
        module.ReviewSection,
    )


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


def _required_turn_id(value: object) -> str:
    """Require the current envelope identity before any checkpoint access."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TURN_ID_CHARS
        or "/" in value
        or "\\" in value
    ):
        clarification_error = _review_classes()[1]
        raise clarification_error(
            "A valid turn id is required for every Review context turn."
        )
    return value


def _required_thread_id(value: object, label: str) -> str:
    """Validate one bounded private checkpoint identity."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_THREAD_ID_CHARS
        or "/" in value
        or "\\" in value
    ):
        clarification_error = _review_classes()[1]
        raise clarification_error(
            f"Review settlement has no valid {label} checkpoint thread."
        )
    if label == "stable" and not re.fullmatch(r"ctx-[0-9a-f]{64}", value):
        clarification_error = _review_classes()[1]
        raise clarification_error(
            "Review settlement stable checkpoint is outside its namespace."
        )
    return value


async def _invoke_checkpoint_updater(
    updater: Callable[..., Awaitable[Any]],
    thread_id: str,
    values: dict[str, Any],
) -> None:
    """Invoke one validated LangGraph checkpoint updater."""
    await updater(build_runnable_config(thread_id), values=values)


def _invoke_checkpoint_deleter(
    deleter: Callable[[str], Any], thread_id: str
) -> Any:
    """Invoke one validated checkpoint-thread deletion seam."""
    return deleter(thread_id)


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
    _, _, report_document_type, report_span_type, _ = _review_classes()
    headings = list(_REPORT_HEADING_PATTERN.finditer(report))
    spans: list[Any] = []
    used_ids: set[str] = set()
    for index, heading in enumerate(headings):
        heading_text = heading.group(2).strip()
        section_id = _slug(heading_text) or f"section-{index + 1}"
        if section_id in used_ids:
            section_id = f"{section_id}-{index + 1}"
        used_ids.add(section_id)
        spans.append(
            report_span_type(
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
    return report_document_type(text=report, sections=tuple(spans))


def extract_review_report_document(
    state: object,
) -> ReviewReportDocument | None:
    """Extract the private report source, never admitting it to projections."""
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
    section_type = _review_classes()[4]
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
        section_type(
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


def _section_from_report_row(
    row: object,
    index: int,
    dimensions: tuple[str, ...],
    used_ids: set[str],
) -> ReviewSection | None:
    """Convert one legacy report row into a bounded uniquely named section."""
    section_type = _review_classes()[4]
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
        return None
    heading = heading or f"Section {index + 1}"
    section_id = _slug(heading) or f"section-{index + 1}"
    if section_id in used_ids:
        section_id = f"{section_id}-{index + 1}"
    used_ids.add(section_id)
    return section_type(section_id, heading, text)


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
        section = _section_from_report_row(
            row, index, dimensions, used_ids
        )
        if section is None:
            continue
        sections.append(section)
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
    snapshot_type = _review_classes()[0]
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
    return snapshot_type(
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
    """Read one checkpoint object for metadata and private report bytes."""
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
