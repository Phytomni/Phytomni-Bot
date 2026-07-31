# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bounded conversation operations for the Review agent.

The Review graph owns the complete report checkpoint.  This module exposes a
small projection of that checkpoint so follow-ups and local section edits do
not put the full report back into a prompt or shared conversation context.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from typing import Any

from ...runtime.conversation_context.models import (
    MAX_CONTEXT_TEXT_CHARS,
    ContextProjection,
)
from .conversation_checkpoint import (
    _bounded_text,
    _slug,
)
from .conversation_checkpoint import (
    _candidate_thread_id as _checkpoint_candidate_thread_id,
)
from .conversation_checkpoint import (
    _required_thread_id as _checkpoint_required_thread_id,
)
from .conversation_checkpoint import (
    _required_turn_id as _checkpoint_required_turn_id,
)
from .conversation_checkpoint import (
    extract_review_checkpoint as _extract_review_checkpoint,
)
from .conversation_checkpoint import (
    extract_review_report_document as _extract_review_report_document,
)
from .conversation_checkpoint import (
    load_review_checkpoint as _load_review_checkpoint,
)
from .conversation_turn import (
    _response_text,
    _usable_response_text,
)
from .conversation_turn import (
    classify_review_operation as _classify_review_operation,
)

_MAX_PROMPT_CHARS = MAX_CONTEXT_TEXT_CHARS
_MAX_SUMMARY_CHARS = 1024

ChatSeam = Callable[[str], Awaitable[Mapping[str, Any] | str | None]]


def _candidate_thread_id(stable_thread_id: str, turn_id: str) -> str:
    """Preserve the legacy private helper import for context services."""
    return _checkpoint_candidate_thread_id(stable_thread_id, turn_id)


def _required_turn_id(value: object) -> str:
    """Preserve the legacy private turn-id validator import."""
    return _checkpoint_required_turn_id(value)


def _required_thread_id(value: object, label: str) -> str:
    """Preserve the legacy private thread-id validator import."""
    return _checkpoint_required_thread_id(value, label)


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
        """Replace one section body without rendering other report bytes."""
        target = _slug(revised.heading or revised.section_id)
        for section in self.sections:
            if section.section_id != target:
                continue
            body = self.text[slice(section.body_start, section.body_end)]
            leading = body[: len(body) - len(body.lstrip())]
            trailing = body[slice(len(body.rstrip()), None)]
            return (
                self.text[: section.body_start]
                + leading
                + revised.text
                + trailing
                + self.text[slice(section.body_end, None)]
            )
        raise ReviewClarificationError(
            f"The requested Review section {target!r} is unavailable."
        )


@dataclass(frozen=True, slots=True)
class _ReviewCheckpointSnapshotFields:
    """Stable checkpoint fields shared by the public snapshot type."""

    research_question: str
    source_ids: tuple[str, ...] = ()
    outline_headings: tuple[str, ...] = ()
    key_claims: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()
    sections: tuple[ReviewSection, ...] = ()
    report_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewCheckpointSnapshot(_ReviewCheckpointSnapshotFields):
    """Bounded semantic data recovered from a Review graph checkpoint."""

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


def _restore_checkpoint_snapshot_metadata() -> None:
    """Keep direct class metadata compatible with the original dataclass."""
    annotations = {
        **_ReviewCheckpointSnapshotFields.__annotations__,
        **ReviewCheckpointSnapshot.__annotations__,
    }
    ReviewCheckpointSnapshot.__annotations__ = annotations
    setattr(ReviewCheckpointSnapshot, "__slots__", tuple(annotations))


_restore_checkpoint_snapshot_metadata()


@dataclass(frozen=True, slots=True)
class _RestoredReviewSettlement:
    """Validated durable identity needed to reconstruct one Review turn."""

    operation: ReviewConversationOperation
    stable_thread_id: str
    turn_id: str
    candidate_thread_id: str | None
    report_revision: int


@dataclass(frozen=True, slots=True)
class _RestoredReviewCheckpoint:
    """Checkpoint values loaded during restart reconstruction."""

    state: object
    snapshot: ReviewCheckpointSnapshot | None
    document: ReviewReportDocument | None


def extract_review_report_document(
    state: object,
) -> ReviewReportDocument | None:
    """Extract the private report source through the checkpoint helper."""
    return _extract_review_report_document(state)


def extract_review_checkpoint(
    state: object,
) -> ReviewCheckpointSnapshot | None:
    """Extract a bounded Review snapshot through the checkpoint helper."""
    return _extract_review_checkpoint(state)


async def load_review_checkpoint(
    agent: Any,
    thread_id: str,
) -> ReviewCheckpointSnapshot | None:
    """Load Review's private checkpoint through the checkpoint helper."""
    return await _load_review_checkpoint(agent, thread_id)


def classify_review_operation(
    query: str | ContextProjection,
    *,
    projection: ContextProjection | None = None,
    snapshot: ReviewCheckpointSnapshot | None = None,
    active_review: bool | None = None,
    has_active_review: bool | None = None,
) -> ReviewConversationOperation:
    """Classify one Review turn without invoking retrieval or generation."""
    return _classify_review_operation(
        query,
        projection=projection,
        snapshot=snapshot,
        active_review=active_review,
        has_active_review=has_active_review,
    )


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
        trailing = body[slice(len(body.rstrip()), None)]
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


_conversation_adapter_module = import_module(
    ".conversation_adapter", __package__
)
ReviewConversationAdapter = (
    _conversation_adapter_module.ReviewConversationAdapter
)
_PreparedReviewTurn = getattr(
    _conversation_adapter_module, "_PreparedReviewTurn"
)
_ReviewAdapterProperties = getattr(
    _conversation_adapter_module, "_ReviewAdapterProperties"
)
_ReviewAdapterState = getattr(
    _conversation_adapter_module, "_ReviewAdapterState"
)
_ReviewCheckpointState = getattr(
    _conversation_adapter_module, "_ReviewCheckpointState"
)
_ReviewResultState = getattr(
    _conversation_adapter_module, "_ReviewResultState"
)
ReviewConversationAdapter.__module__ = __name__


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
