# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared bounded progress projection for execution operation views."""

from __future__ import annotations

from .execution_journal_v2 import (
    ProgressPublicPayload,
    PublicOperationProgressV2,
)


def operation_progress(
    payload: object,
    counter_units: tuple[str, ...],
) -> PublicOperationProgressV2 | None:
    """Project typed progress with a deterministic single-unit fallback."""
    if not isinstance(payload, ProgressPublicPayload):
        return None
    if payload.completed is None or payload.total is None:
        return None
    unit = payload.unit
    if unit is None and len(counter_units) == 1:
        unit = counter_units[0]
    if unit is None:
        return None
    return PublicOperationProgressV2(
        completed=payload.completed,
        total=payload.total,
        unit=unit,
    )
