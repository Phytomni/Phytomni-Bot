# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Builders for phyto.a2ui downlink values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from ....storage.path_policy import IdFactory
from .schemas import (
    A2UI_CATALOG_VERSION,
    ChoiceProps,
    ConfirmProps,
    FormProps,
)

A2uiProps = ConfirmProps | FormProps | ChoiceProps


def mint_surface_id() -> str:
    """Return a fresh surface identifier for an A2UI downlink."""
    return IdFactory().new_id("sfc")


def build_a2ui_value(
    *,
    surface_id: str,
    widget: Literal["confirm", "form", "choice"],
    props: A2uiProps,
) -> dict[str, Any]:
    """Build a phyto.a2ui downlink dict from typed widget props."""
    return {
        "catalog_version": A2UI_CATALOG_VERSION,
        "surface_id": surface_id,
        "widget": widget,
        "props": props.model_dump(exclude_none=True, by_alias=True),
    }


def build_submitted_value(
    prior: Mapping[str, Any],
    *,
    accepted: bool | None = None,
) -> dict[str, Any]:
    """Clone a prior downlink value with status=submitted."""
    props = dict(prior.get("props") or {})
    props["status"] = "submitted"
    if accepted is not None:
        props["accepted"] = accepted
    return {
        "catalog_version": prior.get("catalog_version", A2UI_CATALOG_VERSION),
        "surface_id": prior["surface_id"],
        "widget": prior["widget"],
        "props": props,
    }
