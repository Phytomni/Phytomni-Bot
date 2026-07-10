# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review-agent A2UI projection and action translation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .author import author_a2ui_surface_offline
from .schemas import (
    REVIEW_BODY_MAX_CHARS,
    REVIEW_CONFIRM_TITLE,
    A2uiActionEnvelope,
)
from .translate import action_to_resume_payload

# Re-export for callers that import from review.py directly.
__all__ = [
    "REVIEW_BODY_MAX_CHARS",
    "REVIEW_CONFIRM_TITLE",
    "attach_review_a2ui",
    "project_review_confirm",
    "review_action_to_resume",
    "summary_text_from_interrupt_draft",
]


def summary_text_from_interrupt_draft(draft: Any) -> str:
    """Extract human-readable summary text from an interrupt draft value."""
    if isinstance(draft, str):
        return draft
    if isinstance(draft, Mapping):
        nested = draft.get("draft")
        if isinstance(nested, str):
            return nested
        summary = draft.get("summary")
        if isinstance(summary, str):
            return summary
    return str(draft)


def project_review_confirm(summary: str) -> dict[str, Any]:
    """Build a Review downlink via the shared offline author path."""
    return author_a2ui_surface_offline({"text": summary, "agent": "review"})


def attach_review_a2ui(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of interrupt with draft.a2ui projected when possible."""
    out = dict(interrupt)
    draft = out.get("draft")
    summary = summary_text_from_interrupt_draft(draft)
    surface = author_a2ui_surface_offline({"text": summary, "agent": "review"})
    if isinstance(draft, Mapping):
        nested = dict(draft)
        nested["a2ui"] = surface
        out["draft"] = nested
    else:
        out["draft"] = {"draft": summary, "a2ui": surface}
    return out


def review_action_to_resume(
    envelope: A2uiActionEnvelope,
) -> dict[str, Any]:
    """Translate any Review A2UI action into a resume payload."""
    flat = action_to_resume_payload(envelope)
    out: dict[str, Any] = {
        "widget": flat.get("widget"),
        "surface_id": flat.get("surface_id"),
        "action_id": flat.get("action_id"),
        "edits": None,
    }
    if flat.get("cancelled") is True:
        out["cancelled"] = True
        out["approved"] = False
        return out
    if envelope.widget == "confirm":
        out["approved"] = bool(flat["accepted"])
        return out
    if envelope.widget == "form":
        out["fields"] = flat["fields"]
        out["approved"] = True
        return out
    out["selected"] = flat["selected"]
    out["approved"] = True
    return out
