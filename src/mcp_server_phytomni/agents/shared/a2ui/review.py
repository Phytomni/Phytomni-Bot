# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review-agent A2UI projection and action translation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from .build import build_a2ui_value, mint_surface_id
from .schemas import A2uiActionEnvelope, ConfirmProps
from .translate import action_to_resume_payload

REVIEW_CONFIRM_TITLE: Final = "Review approval"
REVIEW_BODY_MAX_CHARS: Final = 500


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
    """Build a confirm downlink for a Review approval pause."""
    body = summary[:REVIEW_BODY_MAX_CHARS]
    return build_a2ui_value(
        surface_id=mint_surface_id(),
        widget="confirm",
        props=ConfirmProps(title=REVIEW_CONFIRM_TITLE, body=body),
    )


def attach_review_a2ui(interrupt: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of interrupt with draft.a2ui projected when possible."""
    out = dict(interrupt)
    draft = out.get("draft")
    summary = summary_text_from_interrupt_draft(draft)
    surface = project_review_confirm(summary)
    if isinstance(draft, Mapping):
        nested = dict(draft)
        nested["a2ui"] = surface
        out["draft"] = nested
    else:
        out["draft"] = {"draft": summary, "a2ui": surface}
    return out


def review_confirm_action_to_resume(
    envelope: A2uiActionEnvelope,
) -> dict[str, Any]:
    """Translate a confirm action into Review ``{approved, edits}``."""
    if envelope.widget != "confirm":
        raise ValueError("Review A2UI resume requires widget=confirm")
    flat = action_to_resume_payload(envelope)
    return {"approved": bool(flat["accepted"]), "edits": None}
