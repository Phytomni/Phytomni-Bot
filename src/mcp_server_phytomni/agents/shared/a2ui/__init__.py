# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared A2UI schemas, builders, heuristics, and action translation."""

from __future__ import annotations

from .build import build_a2ui_value, build_submitted_value, mint_surface_id
from .rules import should_emit_confirm
from .schemas import (
    A2UI_CATALOG_VERSION,
    A2UI_CUSTOM_NAME,
    A2uiActionEnvelope,
    A2uiDownlinkValue,
    A2uiWidget,
    ChoiceOption,
    ChoicePayload,
    ChoiceProps,
    ConfirmPayload,
    ConfirmProps,
    FormField,
    FormPayload,
    FormProps,
)
from .translate import action_to_resume_payload

__all__ = [
    "A2UI_CATALOG_VERSION",
    "A2UI_CUSTOM_NAME",
    "A2uiActionEnvelope",
    "A2uiDownlinkValue",
    "A2uiWidget",
    "ChoiceOption",
    "ChoicePayload",
    "ChoiceProps",
    "ConfirmPayload",
    "ConfirmProps",
    "FormField",
    "FormPayload",
    "FormProps",
    "action_to_resume_payload",
    "build_a2ui_value",
    "build_submitted_value",
    "mint_surface_id",
    "should_emit_confirm",
]
