# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Stable public DeepGenome report and progress field identities.

This module is intentionally neutral: it imports no server runtime, client,
storage, network, or formatting code. Consumers keep their own containers and
value-validation rules while sharing these immutable public field identities.
"""

from enum import StrEnum
from typing import Final


class _DeepGenomeReportField(StrEnum):
    INTERMEDIATE_REPORT = "intermediate_report"
    FINAL_REPORT = "final_report"
    REPORT_STAGE = "report_stage"
    REPORT_COMPLETENESS = "report_completeness"
    REPORT_REVISION = "report_revision"
    REPORT_UPDATED_AT = "report_updated_at"
    PROGRESS = "progress"
    DEGRADED = "degraded"
    DEGRADED_REASON = "degraded_reason"
    FAILURES = "failures"


class _DeepGenomeProgressField(StrEnum):
    PLANNING_COMPLETE = "planning_complete"
    BRIEF_GENE_STATUS = "brief_gene_status"
    TOTAL = "total"
    PLANNED = "planned"
    SUBMITTED = "submitted"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


DEEP_GENOME_REPORT_FIELDS: Final[tuple[str, ...]] = tuple(
    field.value for field in _DeepGenomeReportField
)
DEEP_GENOME_PROGRESS_FIELDS: Final[tuple[str, ...]] = tuple(
    field.value for field in _DeepGenomeProgressField
)


def sanitize_nonnegative_int(value: object) -> int:
    """Return a JSON-safe non-negative integer or the public default."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


__all__ = [
    "DEEP_GENOME_PROGRESS_FIELDS",
    "DEEP_GENOME_REPORT_FIELDS",
    "sanitize_nonnegative_int",
]
