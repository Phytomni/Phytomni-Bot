# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pydantic models for phyto.a2ui downlink values and action envelopes."""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

A2UI_CATALOG_VERSION: Final = "v1.0"
A2UI_CUSTOM_NAME: Final = "phyto.a2ui"

A2uiWidget = Literal["confirm", "form", "choice"]


class ConfirmProps(BaseModel):
    """Props for a confirm surface."""

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str | None = None


class FormField(BaseModel):
    """One editable field on a form surface."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    field_type: str = "text"
    required: bool = False


class FormProps(BaseModel):
    """Props for a form surface."""

    model_config = ConfigDict(extra="forbid")

    title: str
    fields: list[FormField] = Field(default_factory=list)


class ChoiceOption(BaseModel):
    """One selectable option on a choice surface."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class ChoiceProps(BaseModel):
    """Props for a choice surface."""

    model_config = ConfigDict(extra="forbid")

    title: str
    options: list[ChoiceOption] = Field(default_factory=list)
    multiple: bool = False


class A2uiDownlinkValue(BaseModel):
    """Typed downlink value emitted as phyto.a2ui custom content."""

    model_config = ConfigDict(extra="forbid")

    catalog_version: str = A2UI_CATALOG_VERSION
    surface_id: str
    widget: A2uiWidget
    props: dict[str, Any]


class ConfirmPayload(BaseModel):
    """Payload for a confirm action envelope."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool


class FormPayload(BaseModel):
    """Payload for a form action envelope."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]


class ChoicePayload(BaseModel):
    """Payload for a choice action envelope."""

    model_config = ConfigDict(extra="forbid")

    selected: list[str] | str


class A2uiActionEnvelope(BaseModel):
    """Web-originated action envelope for A2UI resume handling."""

    model_config = ConfigDict(extra="forbid")

    surface_id: str
    widget: A2uiWidget
    action_id: str
    run_id: str
    payload: dict[str, Any]
