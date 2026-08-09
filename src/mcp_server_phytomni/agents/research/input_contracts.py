# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure domain contracts for Research pasted dataset input."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import (
    Any,
    Literal,
    NamedTuple,
    NotRequired,
    Protocol,
    TypedDict,
    Unpack,
)

from ...runtime.research_failure_codes import (
    ResearchFailureCode,
    is_research_failure_code,
)

__all__ = [
    "EvidenceSourceKind",
    "ParsedResearchInput",
    "PastedDatasetCandidate",
    "ResearchConfidence",
    "ResearchCoordinatorDependencies",
    "ResearchCoordinatorRequest",
    "ResearchErrorCode",
    "ResearchFailureStage",
    "ResearchInputFailure",
    "ResearchInteropMode",
    "SourceSpan",
    "TokenEstimator",
    "research_input_failure",
]

EvidenceSourceKind = Literal[
    "query", "pdf_page", "document_section", "user_hint", "dataset_meta"
]
ResearchInteropMode = Literal["off", "auto", "required"]
ResearchConfidence = Literal["high", "medium", "low"]


class TokenEstimator(Protocol):
    """Estimate the token cost of one exact serialized provider request."""

    def estimate(self, serialized_request: bytes) -> int:
        """Return a non-negative token estimate without provider I/O."""
        raise NotImplementedError

    @property
    def contract_name(self) -> str:
        """Identify the pure local estimation contract."""
        return "research_token_estimator"


ResearchErrorCode = ResearchFailureCode
ResearchFailureStage = Literal[
    "request_validation",
    "input_resolution",
    "planning",
    "execution",
    "report_assembly",
]


class _ResearchInputFailureArguments(TypedDict):
    """Required keyword-only construction values for one domain failure."""

    code: ResearchErrorCode
    safe_message: str
    http_status_hint: int
    stage: ResearchFailureStage
    retryable: bool
    last_stage: NotRequired[str | None]


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """One recognized query range in original-query code points."""

    start: int
    end: int
    grammar: Literal["trailing_json", "fenced_json", "standalone_tab", "query"]


class ResearchInputFailureError(ValueError):
    """Stable, transport-independent failure for Research input resolution."""

    def __init__(
        self, **arguments: Unpack[_ResearchInputFailureArguments]
    ) -> None:
        code = arguments["code"]
        if not is_research_failure_code(code):
            raise ValueError("unknown Research input failure code")
        super().__init__(arguments["safe_message"])
        self.code = code
        self.safe_message = arguments["safe_message"]
        self.http_status_hint = arguments["http_status_hint"]
        self.stage = arguments["stage"]
        self.retryable = arguments["retryable"]
        self.last_stage = arguments.get("last_stage") or self.stage


ResearchInputFailure = ResearchInputFailureError


def research_input_failure(
    code: ResearchErrorCode,
    safe_message: str,
    *,
    http_status_hint: int = 400,
    retryable: bool = False,
    stage: ResearchFailureStage = "input_resolution",
    **metadata: str | None,
) -> ResearchInputFailure:
    """Create one stable non-disclosing Research input failure."""
    failure = ResearchInputFailure(
        code=code,
        safe_message=safe_message,
        http_status_hint=http_status_hint,
        stage=stage,
        retryable=retryable,
    )
    last_stage = metadata.get("last_stage")
    if last_stage:
        failure.last_stage = last_stage
    return failure


class ResearchCoordinatorDependencies(NamedTuple):
    """Injected side-effect ports for one Research preparation run.

    The coordinator deliberately knows only the order and failure boundary of
    these callbacks.  Storage, document conversion, resolver execution, and
    the native dispatch validator remain independently replaceable ports.
    """

    build_inventory: Callable[[Any], Awaitable[Any]] | None = None
    extract_evidence: Callable[[Any], Awaitable[Any]] | None = None
    resolve_descriptions: Callable[[Any], Awaitable[Any]] | None = None
    revalidate_inventory: Callable[[Any], Awaitable[Any]] | None = None
    validate_native: Callable[[Any], Any] | None = None
    persist_planning: Callable[..., Any] | None = None
    join_prepared: Callable[[Any, Any], Any] | None = None
    submit_children: Callable[..., Awaitable[Any]] | None = None


class ResearchCoordinatorRequest(NamedTuple):
    """Immutable inputs and dependency ports for coordinator execution."""

    run_id: str
    inventory_request: Any
    evidence: Any = None
    resolution: Any = None
    dependencies: ResearchCoordinatorDependencies = (
        ResearchCoordinatorDependencies()
    )
    effective_query: str = ""
    evidence_digest: str = ""
    work_digest: str = ""
    policy_fingerprint: str = ""


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
