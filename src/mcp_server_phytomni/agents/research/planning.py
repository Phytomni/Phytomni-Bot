# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure goal and child-plan construction for Research input resolution."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from pydantic import ValidationError

from ...runtime.locale import SupportedLocale
from ...storage.path_policy import PathPolicyError, safe_path_segment
from .contracts import ResearchGoal, ResearchGoalBatch
from .document_evidence import (
    DocumentEvidenceDigest,
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
    research_evidence_coverage_digest,
)
from .input_contracts import (
    ResearchInputFailure,
    SourceSpan,
    research_input_failure,
)
from .input_preparation import PreparedResearchInput
from .resolver_policy import canonical_json_bytes

__all__ = [
    "ResearchChildPlan",
    "ResearchGoalProvider",
    "ResearchPlan",
    "ResearchPlanningRequest",
    "build_research_plan",
    "research_planning_failure",
    "research_child_output_dir",
    "research_child_thread_id",
]

_MAX_RUN_ID_CHARS = 256
_MAX_COMPUTE_RESOURCE_CHARS = 128
_MAX_INTEROP_TARGETS = 64
_MAX_INTEROP_TARGET_CHARS = 256
_MAX_DATA_DESCRIPTION_CHARS = 4096
_MAX_EFFECTIVE_QUERY_CHARS = 131_072
_PLANNING_SCHEMA_VERSION = 1
_INTEROP_MODES = frozenset({"off", "auto", "required"})
_EVIDENCE_SOURCE_KINDS = frozenset(
    {"query", "pdf_page", "document_section", "user_hint", "dataset_meta"}
)
_SOURCE_SPAN_GRAMMARS = frozenset(
    {"trailing_json", "fenced_json", "standalone_tab", "query"}
)
_DIGEST_LENGTH = 64


class _ImmutableDataMap(Mapping[str, str]):
    """A deepcopy/asdict-compatible immutable child dataset map."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        """Copy mapping values into a private immutable-view backing map."""
        self._values = dict(values or {})

    def __getitem__(self, key: str) -> str:
        """Return one dataset description by opaque reference."""
        return self._values[key]

    def __iter__(self):
        """Iterate over opaque dataset references."""
        return iter(self._values)

    def __len__(self) -> int:
        """Return the number of dataset descriptions."""
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, object]) -> dict[str, str]:
        """Project to a plain dict for dataclasses.asdict serialization."""
        copied = dict(self._values)
        memo[id(self)] = copied
        return copied


@dataclass(frozen=True, slots=True)
class ResearchPlanningRequest:
    """Immutable inputs for one side-effect-free Research plan."""

    run_id: str
    prepared: PreparedResearchInput
    evidence: ExtractedResearchEvidence
    locale: SupportedLocale | None
    compute_resource: str
    interop_mode: str
    interop_targets: tuple[str, ...]


class ResearchGoalProvider(Protocol):
    """Provider boundary for extracting goals from retained evidence."""

    async def extract(
        self,
        evidence: ExtractedResearchEvidence,
        locale: SupportedLocale | None,
    ) -> tuple[ResearchGoal, ...]:
        """Return bounded goals without performing planning side effects."""
        raise NotImplementedError

    @property
    def contract_name(self) -> str:
        """Identify the pure Research goal-provider contract."""
        return "research_goal_provider"


@dataclass(frozen=True, slots=True)
class _ResearchChildIdentity:
    """First flat fields shared by the public child-plan dataclass."""

    ordinal: int
    task_name: str
    goal_description: str
    context: str


@dataclass(frozen=True, slots=True)
class ResearchChildPlan(_ResearchChildIdentity):
    """One deterministic child dispatch payload before outbox persistence."""

    data_list: Mapping[str, str]
    output_dir: str
    thread_id: str
    interop_mode: str
    interop_targets: tuple[str, ...]
    dispatch_fingerprint: str

    def __post_init__(self) -> None:
        """Freeze the dataset map without breaking dataclass serialization."""
        if not isinstance(self.data_list, _ImmutableDataMap):
            object.__setattr__(
                self, "data_list", _ImmutableDataMap(self.data_list)
            )


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    """The complete ordered Research plan and its stable digest."""

    goals: tuple[ResearchGoal, ...]
    children: tuple[ResearchChildPlan, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class _ResearchChildDraft:
    """Transient identity bundle used while hashing one child."""

    ordinal: int
    task_name: str
    goal_description: str
    context: str
    output_dir: str
    thread_id: str
    data_snapshot: tuple[tuple[str, str], ...]


async def build_research_plan(
    request: ResearchPlanningRequest,
    goal_provider: ResearchGoalProvider,
) -> ResearchPlan:
    """Build one ordered, nonempty plan without durable or remote I/O.

    ``request.evidence`` is passed to the injected provider as-is.  The
    planner only validates the resulting bounded goals and derives immutable
    child identities from the parent run ID and ordinal.  It deliberately
    does not create an output directory, write an outbox, submit an Analyst
    task, or touch document storage.
    """
    _validate_request(request)
    # Protocols are not runtime-checkable.  Keep the structural check
    # explicit so a malformed coordinator dependency fails before awaiting
    # user code.
    if not callable(getattr(goal_provider, "extract", None)):
        raise _planning_failure()
    data_snapshot = tuple(request.prepared.data_list.items())
    try:
        raw_goals = await goal_provider.extract(
            request.evidence, request.locale
        )
    except ResearchInputFailure:
        raise
    except Exception as error:
        raise _planning_failure() from error
    if tuple(request.prepared.data_list.items()) != data_snapshot:
        raise _planning_failure()
    goals = _validated_goals(raw_goals)
    children = _children(request, goals, data_snapshot)
    if not children:
        raise _planning_failure()
    digest = _plan_digest(request, goals, children)
    return ResearchPlan(goals=goals, children=children, digest=digest)


def research_child_output_dir(run_id: str, ordinal: int) -> str:
    """Return a stable child output key derived only from run and ordinal."""
    safe_run_id = _safe_run_id(run_id)
    if not isinstance(ordinal, int) or isinstance(ordinal, bool):
        raise _planning_failure()
    if ordinal < 0 or ordinal >= 20:
        raise _planning_failure()
    return f"research/{safe_run_id}/children/part-{ordinal + 1:03d}"


def research_child_thread_id(run_id: str, ordinal: int) -> str:
    """Return a stable LangGraph thread ID for one Research child."""
    safe_run_id = _safe_run_id(run_id)
    if not isinstance(ordinal, int) or isinstance(ordinal, bool):
        raise _planning_failure()
    if ordinal < 0 or ordinal >= 20:
        raise _planning_failure()
    return f"thread-{ordinal}-{safe_run_id}"


def _validate_request(request: ResearchPlanningRequest) -> None:
    """Reject malformed or mutable coordinator inputs before provider I/O."""
    if not isinstance(request, ResearchPlanningRequest):
        raise _planning_failure()
    _safe_run_id(request.run_id)
    if request.locale not in (None, "en-US", "zh-CN"):
        raise _planning_failure()
    if _invalid_bounded_text(
        request.compute_resource, _MAX_COMPUTE_RESOURCE_CHARS
    ):
        raise _planning_failure()
    if request.interop_mode not in _INTEROP_MODES:
        raise _planning_failure()
    if not isinstance(request.interop_targets, tuple):
        raise _planning_failure()
    if len(request.interop_targets) > _MAX_INTEROP_TARGETS:
        raise _planning_failure()
    if any(
        _invalid_bounded_text(target, _MAX_INTEROP_TARGET_CHARS)
        for target in request.interop_targets
    ) or len(set(request.interop_targets)) != len(request.interop_targets):
        raise _planning_failure()
    _validate_prepared(request.prepared)
    _validate_evidence(request.evidence)


def _validate_prepared(prepared: PreparedResearchInput) -> None:
    """Validate the final native projection and its immutable data map."""
    if not isinstance(prepared, PreparedResearchInput):
        raise _planning_failure()
    if not isinstance(prepared.effective_query, str):
        raise _planning_failure()
    if prepared.effective_query.strip() and _invalid_bounded_text(
        prepared.effective_query,
        _MAX_EFFECTIVE_QUERY_CHARS,
        allowed_control_chars="\t\n\r",
    ):
        raise _planning_failure()
    if not isinstance(prepared.obs_file_list, tuple) or any(
        _invalid_bounded_text(item, _MAX_EFFECTIVE_QUERY_CHARS)
        for item in prepared.obs_file_list
    ):
        raise _planning_failure()
    if not isinstance(prepared.data_list, MappingProxyType):
        raise _planning_failure()
    if len(prepared.data_list) > 256:
        raise _planning_failure()
    for reference, description in prepared.data_list.items():
        if _invalid_bounded_text(reference, _MAX_EFFECTIVE_QUERY_CHARS):
            raise _planning_failure()
        if _invalid_data_description(description):
            raise _planning_failure()


def _validate_evidence(evidence: ExtractedResearchEvidence) -> None:
    """Validate every evidence identity and its exact coverage boundary."""
    if not isinstance(evidence, ExtractedResearchEvidence):
        raise _planning_failure()
    if not isinstance(evidence.units, tuple) or not evidence.units:
        raise _planning_failure()
    if not isinstance(evidence.document_digests, tuple):
        raise _planning_failure()
    if not _valid_digest(evidence.coverage_digest):
        raise _planning_failure()
    units_by_id = _validated_evidence_units(evidence.units)
    covered_document_ids = _validated_document_coverage(
        evidence.document_digests, units_by_id
    )

    document_unit_ids = {
        unit.evidence_id
        for unit in evidence.units
        if unit.source_kind in {"pdf_page", "document_section"}
    }
    if document_unit_ids != set(covered_document_ids):
        raise _planning_failure()
    if (
        research_evidence_coverage_digest(
            evidence.units, evidence.document_digests
        )
        != evidence.coverage_digest
    ):
        raise _planning_failure()


def _validated_evidence_units(
    units: tuple[object, ...],
) -> dict[str, ResearchEvidenceUnit]:
    """Validate transient units and index their opaque identities."""
    units_by_id: dict[str, ResearchEvidenceUnit] = {}
    for candidate in units:
        if not isinstance(candidate, ResearchEvidenceUnit):
            raise _planning_failure()
        _validate_evidence_unit(candidate, units_by_id)
        units_by_id[candidate.evidence_id] = candidate
    return units_by_id


def _validate_evidence_unit(
    unit: ResearchEvidenceUnit,
    units_by_id: dict[str, ResearchEvidenceUnit],
) -> None:
    """Validate one unit's source identity, text digest, and memberships."""
    if not isinstance(unit.evidence_id, str):
        raise _planning_failure()
    if not unit.evidence_id.strip() or unit.evidence_id in units_by_id:
        raise _planning_failure()
    if unit.source_kind not in _EVIDENCE_SOURCE_KINDS:
        raise _planning_failure()
    if not _valid_non_negative_ordinal(unit.source_ordinal):
        raise _planning_failure()
    if not isinstance(unit.text, str) or not unit.text.strip():
        raise _planning_failure()
    if not _valid_digest(unit.content_digest):
        raise _planning_failure()
    if unit.content_digest != _sha256_text(unit.text):
        raise _planning_failure()
    if not isinstance(unit.dataset_ids, tuple):
        raise _planning_failure()
    if any(
        not isinstance(dataset_id, str) or not dataset_id.strip()
        for dataset_id in unit.dataset_ids
    ):
        raise _planning_failure()
    if len(set(unit.dataset_ids)) != len(unit.dataset_ids):
        raise _planning_failure()
    if _invalid_source_span(unit.source_span):
        raise _planning_failure()


def _validated_document_coverage(
    documents: tuple[object, ...],
    units_by_id: dict[str, ResearchEvidenceUnit],
) -> list[str]:
    """Validate document records and return their unique covered IDs."""
    covered_document_ids: list[str] = []
    document_ids: set[str] = set()
    for candidate in documents:
        if not isinstance(candidate, DocumentEvidenceDigest):
            raise _planning_failure()
        _validate_document_digest(candidate, document_ids)
        document_ids.add(candidate.document_id)
        _append_document_evidence_ids(
            candidate.evidence_ids, covered_document_ids, units_by_id
        )
    return covered_document_ids


def _validate_document_digest(
    document: DocumentEvidenceDigest, document_ids: set[str]
) -> None:
    """Validate one document's digest and record identity."""
    if not isinstance(document.document_id, str):
        raise _planning_failure()
    if (
        not document.document_id.strip()
        or document.document_id in document_ids
    ):
        raise _planning_failure()
    if not _valid_digest(document.content_digest):
        raise _planning_failure()
    if (
        not isinstance(document.evidence_ids, tuple)
        or not document.evidence_ids
    ):
        raise _planning_failure()


def _append_document_evidence_ids(
    evidence_ids: tuple[object, ...],
    covered_document_ids: list[str],
    units_by_id: dict[str, ResearchEvidenceUnit],
) -> None:
    """Check document membership and append each covered unit exactly once."""
    for evidence_id in evidence_ids:
        if not isinstance(evidence_id, str):
            raise _planning_failure()
        if not evidence_id.strip() or evidence_id in covered_document_ids:
            raise _planning_failure()
        matched_unit = units_by_id.get(evidence_id)
        if matched_unit is None:
            raise _planning_failure()
        if matched_unit.source_kind not in {"pdf_page", "document_section"}:
            raise _planning_failure()
        covered_document_ids.append(evidence_id)


def _validated_goals(raw_goals: object) -> tuple[ResearchGoal, ...]:
    """Normalize provider output through the existing bounded goal model."""
    if isinstance(raw_goals, tuple):
        values: object = list(raw_goals)
    elif isinstance(raw_goals, list):
        values = raw_goals
    else:
        raise _planning_failure()
    try:
        batch = ResearchGoalBatch.model_validate(values)
    except ValidationError as error:
        raise _planning_failure() from error
    goals = tuple(batch.root)
    descriptions = tuple(goal.goal for goal in goals)
    if len(descriptions) != len(set(descriptions)):
        raise _planning_failure()
    return goals


def _valid_digest(value: object) -> bool:
    """Require the lower-case hexadecimal SHA-256 contract."""
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_LENGTH
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_non_negative_ordinal(value: object) -> bool:
    """Require a real non-negative integer ordinal, not a boolean."""
    return (
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
    )


def _invalid_source_span(span: object) -> bool:
    """Reject malformed source ranges and unknown parser grammars."""
    if span is None:
        return False
    if not isinstance(span, SourceSpan):
        return True
    return (
        not isinstance(span.start, int)
        or isinstance(span.start, bool)
        or not isinstance(span.end, int)
        or isinstance(span.end, bool)
        or span.start < 0
        or span.end <= span.start
        or span.grammar not in _SOURCE_SPAN_GRAMMARS
    )


def _sha256_text(text: str) -> str:
    """Hash transient evidence text without retaining it in metadata."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _children(
    request: ResearchPlanningRequest,
    goals: tuple[ResearchGoal, ...],
    data_snapshot: tuple[tuple[str, str], ...],
) -> tuple[ResearchChildPlan, ...]:
    """Derive immutable child records in provider order."""
    children: list[ResearchChildPlan] = []
    for ordinal, goal in enumerate(goals):
        task_name = f"research_goal_{ordinal}"
        context = goal.context or ""
        output_dir = research_child_output_dir(request.run_id, ordinal)
        thread_id = research_child_thread_id(request.run_id, ordinal)
        data_list = _ImmutableDataMap(dict(data_snapshot))
        fingerprint = _dispatch_fingerprint(
            request,
            _ResearchChildDraft(
                ordinal,
                task_name,
                goal.goal,
                context,
                output_dir,
                thread_id,
                data_snapshot,
            ),
        )
        children.append(
            ResearchChildPlan(
                ordinal=ordinal,
                task_name=task_name,
                goal_description=goal.goal,
                context=context,
                data_list=data_list,
                output_dir=output_dir,
                thread_id=thread_id,
                interop_mode=request.interop_mode,
                interop_targets=request.interop_targets,
                dispatch_fingerprint=fingerprint,
            )
        )
    return tuple(children)


def _dispatch_fingerprint(
    request: ResearchPlanningRequest,
    child: _ResearchChildDraft,
) -> str:
    """Hash every semantic input that affects one child dispatch."""
    return _digest(
        {
            "compute_resource": request.compute_resource,
            "data_list": child.data_snapshot,
            "effective_query": request.prepared.effective_query,
            "evidence_digest": request.evidence.coverage_digest,
            "goal_description": child.goal_description,
            "context": child.context,
            "interop_mode": request.interop_mode,
            "interop_targets": request.interop_targets,
            "ordinal": child.ordinal,
            "output_dir": child.output_dir,
            "prepared_fingerprint": request.prepared.execution_fingerprint,
            "run_id": request.run_id,
            "schema_version": _PLANNING_SCHEMA_VERSION,
            "task_name": child.task_name,
            "thread_id": child.thread_id,
        }
    )


def _plan_digest(
    request: ResearchPlanningRequest,
    goals: tuple[ResearchGoal, ...],
    children: tuple[ResearchChildPlan, ...],
) -> str:
    """Hash the full ordered plan and its parent bindings."""
    return _digest(
        {
            "children": [
                {
                    "context": child.context,
                    "data_list": tuple(child.data_list.items()),
                    "dispatch_fingerprint": child.dispatch_fingerprint,
                    "goal_description": child.goal_description,
                    "interop_mode": child.interop_mode,
                    "interop_targets": child.interop_targets,
                    "ordinal": child.ordinal,
                    "output_dir": child.output_dir,
                    "task_name": child.task_name,
                    "thread_id": child.thread_id,
                }
                for child in children
            ],
            "compute_resource": request.compute_resource,
            "evidence_digest": request.evidence.coverage_digest,
            "goals": [goal.model_dump(mode="json") for goal in goals],
            "interop_mode": request.interop_mode,
            "interop_targets": request.interop_targets,
            "prepared_fingerprint": request.prepared.execution_fingerprint,
            "run_id": request.run_id,
            "schema_version": _PLANNING_SCHEMA_VERSION,
        }
    )


def _safe_run_id(run_id: object) -> str:
    """Validate and path-normalize the parent identity without randomness."""
    if _invalid_bounded_text(run_id, _MAX_RUN_ID_CHARS):
        raise _planning_failure()
    try:
        return safe_path_segment(run_id, "run")
    except PathPolicyError as error:
        raise _planning_failure() from error


def _invalid_bounded_text(
    value: object,
    limit: int,
    *,
    allowed_control_chars: str = "",
) -> bool:
    """Return whether a value is not a bounded nonblank string."""
    return (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(
            ord(char) < 32 and char not in allowed_control_chars
            for char in value
        )
    )


def _invalid_data_description(value: object) -> bool:
    """Allow an empty hint only for the remote file-inspection path."""
    return (
        not isinstance(value, str)
        or len(value) > _MAX_DATA_DESCRIPTION_CHARS
        or any(ord(char) < 32 for char in value)
    )


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def research_planning_failure() -> ResearchInputFailure:
    """Build the stable private planning failure contract."""
    return research_input_failure(
        "research_input_resolution_failed",
        "Research input resolution failed.",
        http_status_hint=422,
        retryable=False,
        stage="planning",
    )


_planning_failure = research_planning_failure
