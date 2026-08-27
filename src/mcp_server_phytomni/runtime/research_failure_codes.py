# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Canonical, storage-neutral Research terminal-failure code contract."""

from __future__ import annotations

from typing import Literal, TypeGuard

__all__ = [
    "RESEARCH_FAILURE_CODES",
    "ResearchFailureCode",
    "is_research_failure_code",
]

ResearchFailureCode = Literal[
    "research_idempotency_key_required",
    "research_idempotency_conflict",
    "research_data_block_invalid",
    "research_dataset_path_invalid",
    "research_dataset_not_found",
    "research_dataset_duplicate",
    "research_dataset_format_unsupported",
    "research_input_limit_exceeded",
    "research_document_extraction_failed",
    "research_goal_extraction_failed",
    "research_input_resolution_failed",
    "research_input_resolution_unavailable",
    "research_run_tracking_failed",
    "research_input_protocol_unavailable",
    "research_cancel_conflict",
]

RESEARCH_FAILURE_CODES = frozenset(
    {
        "research_idempotency_key_required",
        "research_idempotency_conflict",
        "research_data_block_invalid",
        "research_dataset_path_invalid",
        "research_dataset_not_found",
        "research_dataset_duplicate",
        "research_dataset_format_unsupported",
        "research_input_limit_exceeded",
        "research_document_extraction_failed",
        "research_goal_extraction_failed",
        "research_input_resolution_failed",
        "research_input_resolution_unavailable",
        "research_run_tracking_failed",
        "research_input_protocol_unavailable",
        "research_cancel_conflict",
    }
)


def is_research_failure_code(value: object) -> TypeGuard[ResearchFailureCode]:
    """Return whether one untrusted value is a public Research failure code."""
    return isinstance(value, str) and value in RESEARCH_FAILURE_CODES
