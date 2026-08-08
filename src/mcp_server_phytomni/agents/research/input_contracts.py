# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure domain contracts for Research pasted dataset input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict, Unpack

__all__ = [
    "EvidenceSourceKind",
    "ParsedResearchInput",
    "PastedDatasetCandidate",
    "ResearchErrorCode",
    "ResearchFailureStage",
    "ResearchInputFailure",
    "SourceSpan",
    "research_input_failure",
]

EvidenceSourceKind = Literal[
    "query", "pdf_page", "document_section", "user_hint", "dataset_meta"
]

ResearchErrorCode = Literal[
    "research_idempotency_key_required",
    "research_idempotency_conflict",
    "research_data_block_invalid",
    "research_dataset_path_invalid",
    "research_dataset_not_found",
    "research_dataset_duplicate",
    "research_dataset_format_unsupported",
    "research_input_limit_exceeded",
    "research_document_extraction_failed",
    "research_input_resolution_failed",
    "research_input_resolution_unavailable",
    "research_run_tracking_failed",
    "research_input_protocol_unavailable",
    "research_cancel_conflict",
]
ResearchFailureStage = Literal[
    "request_validation",
    "input_resolution",
    "planning",
    "execution",
    "report_assembly",
]
_RESEARCH_ERROR_CODES = frozenset(
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
        "research_input_resolution_failed",
        "research_input_resolution_unavailable",
        "research_run_tracking_failed",
        "research_input_protocol_unavailable",
        "research_cancel_conflict",
    }
)


class _ResearchInputFailureArguments(TypedDict):
    """Required keyword-only construction values for one domain failure."""

    code: ResearchErrorCode
    safe_message: str
    http_status_hint: int
    stage: ResearchFailureStage
    retryable: bool


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """One recognized query range in original-query code points."""

    start: int
    end: int
    grammar: Literal["trailing_json", "fenced_json", "standalone_tab", "query"]


class ResearchInputFailureError(Exception):
    """Stable, transport-independent failure for Research input resolution."""

    def __init__(
        self, **arguments: Unpack[_ResearchInputFailureArguments]
    ) -> None:
        code = arguments["code"]
        if code not in _RESEARCH_ERROR_CODES:
            raise ValueError("unknown Research input failure code")
        super().__init__(arguments["safe_message"])
        self.code = code
        self.safe_message = arguments["safe_message"]
        self.http_status_hint = arguments["http_status_hint"]
        self.stage = arguments["stage"]
        self.retryable = arguments["retryable"]


ResearchInputFailure = ResearchInputFailureError


def research_input_failure(
    code: ResearchErrorCode,
    safe_message: str,
    *,
    http_status_hint: int = 400,
    retryable: bool = False,
) -> ResearchInputFailure:
    """Create one stable non-disclosing Research input failure."""
    return ResearchInputFailure(
        code=code,
        safe_message=safe_message,
        http_status_hint=http_status_hint,
        stage="input_resolution",
        retryable=retryable,
    )


@dataclass(frozen=True, slots=True)
class _PastedDatasetReference:
    """Shared reference identity and source span for parsed candidates."""

    exact_reference: str
    comparison_key: str
    user_hint: str | None
    source_start: int
    source_end: int


@dataclass(frozen=True, slots=True)
class PastedDatasetCandidate(_PastedDatasetReference):
    """Exact user reference, comparison identity, and source span."""

    ordinal: int


@dataclass(frozen=True, slots=True)
class ParsedResearchInput:
    """Pure parse result retaining original-query provenance for later I/O."""

    original_query_digest: str
    original_query_length: int
    effective_query: str
    effective_to_original: tuple[int, ...]
    removed_spans: tuple[SourceSpan, ...]
    candidates: tuple[PastedDatasetCandidate, ...]
