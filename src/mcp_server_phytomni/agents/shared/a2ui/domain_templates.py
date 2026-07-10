# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Domain A2UI templates for research-common form/choice surfaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .schemas import (
    ChoiceOption,
    ChoiceProps,
    ConfirmProps,
    FormField,
    FormProps,
)

A2uiProps = ConfirmProps | FormProps | ChoiceProps


@dataclass(frozen=True)
class DomainTemplate:
    """One domain template: matcher patterns plus typed widget props."""

    template_id: str
    widget: Literal["confirm", "form", "choice"]
    patterns: tuple[re.Pattern[str], ...]
    props: A2uiProps


def _pat(*raw: str) -> tuple[re.Pattern[str], ...]:
    """Compile casefolded literal patterns for casefold search."""
    return tuple(re.compile(re.escape(s.casefold())) for s in raw)


_DOMAIN_TEMPLATES: tuple[DomainTemplate, ...] = (
    DomainTemplate(
        template_id="gene_id",
        widget="form",
        patterns=_pat("gene id", "基因", "gene_id", "atg"),
        props=FormProps(
            title="Gene ID",
            fields=[
                FormField(
                    name="gene_id",
                    label="Gene ID",
                    type="text",
                    required=True,
                ),
            ],
        ),
    ),
    DomainTemplate(
        template_id="species",
        widget="choice",
        patterns=_pat("species", "物种"),
        props=ChoiceProps(
            title="Species",
            options=[
                ChoiceOption(id="arabidopsis", label="Arabidopsis"),
                ChoiceOption(id="rice", label="Rice"),
                ChoiceOption(id="maize", label="Maize"),
            ],
            multiple=False,
        ),
    ),
    DomainTemplate(
        template_id="yes_no",
        widget="choice",
        patterns=_pat("是否", "yes or no", "yes/no"),
        props=ChoiceProps(
            title="Yes / No",
            options=[
                ChoiceOption(id="yes", label="Yes"),
                ChoiceOption(id="no", label="No"),
            ],
            multiple=False,
        ),
    ),
    DomainTemplate(
        template_id="trait_to_id",
        widget="form",
        patterns=_pat("to_id", "trait ontology", "性状"),
        props=FormProps(
            title="Trait Ontology",
            fields=[
                FormField(
                    name="to_id",
                    label="TO ID",
                    type="text",
                    required=True,
                ),
            ],
        ),
    ),
    DomainTemplate(
        template_id="analysis_tier",
        widget="choice",
        patterns=_pat(
            "small/medium/large",
            "analysis tier",
            "算力",
        ),
        props=ChoiceProps(
            title="Analysis Tier",
            options=[
                ChoiceOption(id="small", label="Small"),
                ChoiceOption(id="medium", label="Medium"),
                ChoiceOption(id="large", label="Large"),
            ],
            multiple=False,
        ),
    ),
    DomainTemplate(
        template_id="free_text",
        widget="form",
        patterns=_pat("请补充", "additional notes", "备注"),
        props=FormProps(
            title="Additional Notes",
            fields=[
                FormField(
                    name="notes",
                    label="Notes",
                    type="text",
                    required=True,
                ),
            ],
        ),
    ),
)


def all_domain_templates() -> tuple[DomainTemplate, ...]:
    """Return the ordered D2 domain template catalog."""
    return _DOMAIN_TEMPLATES


def match_domain_template(text: str) -> DomainTemplate | None:
    """Return the first domain template whose patterns hit ``text``.

    Patterns are searched against ``text.casefold()``. Catalog order is
    the priority when multiple templates match.
    """
    folded = text.casefold()
    for template in _DOMAIN_TEMPLATES:
        if any(pattern.search(folded) for pattern in template.patterns):
            return template
    return None
