# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Translate Web A2UI action envelopes into resume payloads."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .schemas import (
    A2uiActionEnvelope,
    ChoicePayload,
    ConfirmPayload,
    FormPayload,
)


def action_to_resume_payload(
    envelope: A2uiActionEnvelope,
) -> dict[str, Any]:
    """Validate and flatten an action envelope into a resume dict."""
    base = {
        "surface_id": envelope.surface_id,
        "widget": envelope.widget,
        "action_id": envelope.action_id,
    }
    if (
        envelope.widget in ("form", "choice")
        and envelope.payload.get("cancelled") is True
    ):
        return {**base, "cancelled": True}
    try:
        if envelope.widget == "confirm":
            confirm = ConfirmPayload.model_validate(envelope.payload)
            return {**base, "accepted": confirm.accepted}
        if envelope.widget == "form":
            form = FormPayload.model_validate(envelope.payload)
            return {**base, "fields": form.fields}
        choice = ChoicePayload.model_validate(envelope.payload)
        return {**base, "selected": choice.selected}
    except ValidationError as exc:
        raise ValueError(f"Invalid {envelope.widget} action payload") from exc
