# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Fixed thin A2UI form/choice templates for Chat heuristics."""

from __future__ import annotations

from .schemas import ChoiceOption, ChoiceProps, FormField, FormProps


def build_form_template_props() -> FormProps:
    """Return the fixed single-field form template."""
    return FormProps(
        title="Form",
        fields=[
            FormField(
                name="value",
                label="Value",
                type="text",
                required=True,
            ),
        ],
    )


def build_choice_template_props() -> ChoiceProps:
    """Return the fixed two-option choice template."""
    return ChoiceProps(
        title="Choice",
        options=[
            ChoiceOption(id="a", label="Option A"),
            ChoiceOption(id="b", label="Option B"),
        ],
        multiple=False,
    )
