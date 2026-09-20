# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Canonical status groups shared by execution work-unit adapters."""

from __future__ import annotations

from typing import Final

from .execution_journal_v2 import WorkUnitStatus

TERMINAL_WORK_UNIT_STATUSES: Final = frozenset(
    {
        WorkUnitStatus.SUCCEEDED,
        WorkUnitStatus.PARTIAL,
        WorkUnitStatus.FAILED,
        WorkUnitStatus.CANCELLED,
        WorkUnitStatus.TIMED_OUT,
    }
)
TERMINAL_WORK_UNIT_VALUES: Final = tuple(
    status.value
    for status in (
        WorkUnitStatus.SUCCEEDED,
        WorkUnitStatus.PARTIAL,
        WorkUnitStatus.FAILED,
        WorkUnitStatus.CANCELLED,
        WorkUnitStatus.TIMED_OUT,
    )
)
