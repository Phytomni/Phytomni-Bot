# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared A2UI schemas, builders, heuristics, and action translation."""

from __future__ import annotations

from .author import (
    AuthorContext,
    author_a2ui_surface,
    author_a2ui_surface_offline,
)
from .build import build_a2ui_value, build_submitted_value, mint_surface_id
from .domain_templates import (
    DomainTemplate,
    all_domain_templates,
    match_domain_template,
)
from .loop import (
    A2UI_MAX_ROUNDS,
    clear_a2ui_for_reenter,
    next_a2ui_round,
    should_reenter_a2ui,
)
from .review import (
    REVIEW_BODY_MAX_CHARS,
    REVIEW_CONFIRM_TITLE,
    attach_review_a2ui,
    project_review_confirm,
    review_action_to_resume,
    summary_text_from_interrupt_draft,
)
from .rules import (
    select_chat_a2ui_widget,
    should_emit_choice,
    should_emit_confirm,
    should_emit_form,
)
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
from .templates import build_choice_template_props, build_form_template_props
from .translate import action_to_resume_payload
from .validation import A2uiSurfaceValidationError, validate_a2ui_surface

__all__ = [
    "A2UI_CATALOG_VERSION",
    "A2UI_CUSTOM_NAME",
    "A2UI_MAX_ROUNDS",
    "A2uiActionEnvelope",
    "A2uiDownlinkValue",
    "A2uiSurfaceValidationError",
    "A2uiWidget",
    "AuthorContext",
    "ChoiceOption",
    "ChoicePayload",
    "ChoiceProps",
    "ConfirmPayload",
    "ConfirmProps",
    "DomainTemplate",
    "FormField",
    "FormPayload",
    "FormProps",
    "REVIEW_BODY_MAX_CHARS",
    "REVIEW_CONFIRM_TITLE",
    "action_to_resume_payload",
    "all_domain_templates",
    "attach_review_a2ui",
    "author_a2ui_surface",
    "author_a2ui_surface_offline",
    "build_a2ui_value",
    "build_choice_template_props",
    "build_form_template_props",
    "build_submitted_value",
    "clear_a2ui_for_reenter",
    "match_domain_template",
    "mint_surface_id",
    "next_a2ui_round",
    "project_review_confirm",
    "review_action_to_resume",
    "select_chat_a2ui_widget",
    "should_emit_choice",
    "should_emit_confirm",
    "should_emit_form",
    "should_reenter_a2ui",
    "summary_text_from_interrupt_draft",
    "validate_a2ui_surface",
]
