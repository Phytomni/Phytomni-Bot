# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure goal and child-plan construction for Research input resolution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import ValidationError

from ...runtime.locale import SupportedLocale
from ...storage.path_policy import PathPolicyError, safe_path_segment
from .contracts import ResearchGoal, ResearchGoalBatch
from .document_evidence import ExtractedResearchEvidence
from .input_contracts import ResearchInputFailure, research_input_failure
from .input_preparation import PreparedResearchInput

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
    """Stable goal identity fields for one child plan."""

    ordinal: int
    task_name: str
    goal_description: str
    context: str


@dataclass(frozen=True, slots=True)
class _ResearchChildPayload:
    """Native and local runtime fields for one child plan."""

    data_list: MappingProxyType[str, str]
    output_dir: str
    thread_id: str


@dataclass(frozen=True, slots=True)
class _ResearchChildInterop:
    """Interop controls and dispatch identity for one child plan."""

    interop_mode: str
    interop_targets: tuple[str, ...]
    dispatch_fingerprint: str


@dataclass(frozen=True, slots=True, init=False)
class ResearchChildPlan:
    """One deterministic child dispatch payload before outbox persistence."""

    _identity: _ResearchChildIdentity
    _payload: _ResearchChildPayload
    _interop: _ResearchChildInterop

    def __init__(
        self,
        *args: Any,
        **values: Any,
    ) -> None:
        """Build one plan while retaining the public flat field contract."""
        names = (
            "ordinal",
            "task_name",
            "goal_description",
            "context",
            "data_list",
            "output_dir",
            "thread_id",
            "interop_mode",
            "interop_targets",
            "dispatch_fingerprint",
        )
        if args:
            if values or len(args) != len(names):
                raise TypeError("ResearchChildPlan arguments are invalid")
            values = dict(zip(names, args, strict=True))
        object.__setattr__(
            self,
            "_identity",
            _ResearchChildIdentity(
                values["ordinal"],
                values["task_name"],
                values["goal_description"],
                values["context"],
            ),
        )
        object.__setattr__(
            self,
            "_payload",
            _ResearchChildPayload(
                values["data_list"],
                values["output_dir"],
                values["thread_id"],
            ),
        )
        object.__setattr__(
            self,
            "_interop",
            _ResearchChildInterop(
                values["interop_mode"],
                values["interop_targets"],
                values["dispatch_fingerprint"],
            ),
        )

    @property
    def ordinal(self) -> int:
        """Return the zero-based child ordinal."""
        return self._identity.ordinal

    @property
    def task_name(self) -> str:
        """Return the deterministic Analyst task name."""
        return self._identity.task_name

    @property
    def goal_description(self) -> str:
        """Return the bounded goal prompt part."""
        return self._identity.goal_description

    @property
    def context(self) -> str:
        """Return the bounded context prompt part."""
        return self._identity.context

    @property
    def data_list(self) -> MappingProxyType[str, str]:
        """Return the immutable final native dataset map."""
        return self._payload.data_list

    @property
    def output_dir(self) -> str:
        """Return the deterministic child output key."""
        return self._payload.output_dir

    @property
    def thread_id(self) -> str:
        """Return the deterministic child graph thread ID."""
        return self._payload.thread_id

    @property
    def interop_mode(self) -> str:
        """Return the requested interop mode."""
        return self._interop.interop_mode

    @property
    def interop_targets(self) -> tuple[str, ...]:
        """Return the ordered interop target IDs."""
        return self._interop.interop_targets

    @property
    def dispatch_fingerprint(self) -> str:
        """Return the deterministic child dispatch fingerprint."""
        return self._interop.dispatch_fingerprint


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
    if _invalid_bounded_text(
        prepared.effective_query, _MAX_EFFECTIVE_QUERY_CHARS
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
        if _invalid_bounded_text(description, _MAX_DATA_DESCRIPTION_CHARS):
            raise _planning_failure()


def _validate_evidence(evidence: ExtractedResearchEvidence) -> None:
    """Validate evidence identity without persisting plaintext."""
    if not isinstance(evidence, ExtractedResearchEvidence):
        raise _planning_failure()
    if not isinstance(evidence.units, tuple) or not evidence.units:
        raise _planning_failure()
    if not isinstance(evidence.document_digests, tuple):
        raise _planning_failure()
    if (
        not isinstance(evidence.coverage_digest, str)
        or not evidence.coverage_digest
    ):
        raise _planning_failure()
    identifiers: list[str] = []
    for unit in evidence.units:
        if not isinstance(unit.evidence_id, str) or not unit.evidence_id:
            raise _planning_failure()
        if not isinstance(unit.text, str) or not unit.text:
            raise _planning_failure()
        if not isinstance(unit.dataset_ids, tuple):
            raise _planning_failure()
        if any(
            not isinstance(dataset_id, str) or not dataset_id
            for dataset_id in unit.dataset_ids
        ):
            raise _planning_failure()
        identifiers.append(unit.evidence_id)
    if len(identifiers) != len(set(identifiers)):
        raise _planning_failure()


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
        data_list = MappingProxyType(dict(data_snapshot))
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


def _invalid_bounded_text(value: object, limit: int) -> bool:
    """Return whether a value is not a bounded nonblank string."""
    return (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
    )


def _digest(value: object) -> str:
    """Return a process-independent SHA-256 over canonical JSON."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
