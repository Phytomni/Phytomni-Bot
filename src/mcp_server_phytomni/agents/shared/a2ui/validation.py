# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared validation for public A2UI surface payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .schemas import (
    A2uiDownlinkValue,
    ChoiceProps,
    ConfirmProps,
    FormProps,
)

type A2uiPropsModel = (
    type[ConfirmProps] | type[FormProps] | type[ChoiceProps]
)

_PROPS_MODELS: dict[str, A2uiPropsModel] = {
    "confirm": ConfirmProps,
    "form": FormProps,
    "choice": ChoiceProps,
}


class A2uiSurfaceValidationError(ValueError):
    """Raised when a public A2UI surface is not a supported v1.0 value."""


def validate_a2ui_surface(value: Mapping[str, Any]) -> A2uiDownlinkValue:
    """Validate the common downlink and widget-specific props."""
    try:
        surface = A2uiDownlinkValue.model_validate(value)
        if surface.catalog_version != "v1.0":
            raise A2uiSurfaceValidationError(
                "unsupported a2ui catalog version"
            )
        if not surface.surface_id.strip():
            raise A2uiSurfaceValidationError("blank a2ui surface id")
        _PROPS_MODELS[surface.widget].model_validate(surface.props)
    except ValidationError as exc:
        raise A2uiSurfaceValidationError("invalid a2ui surface") from exc
    return surface
