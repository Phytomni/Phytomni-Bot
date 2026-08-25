# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite registry and discovery contract for public execution operations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Final

from ..public_agent_catalog import PUBLIC_AGENT_CATALOG
from .execution_event_limits import DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS

__all__ = [
    "AGENT_OPERATION_COVERAGE",
    "OPERATION_PRESENTER_CATALOG",
    "OPERATION_PRESENTER_REGISTRY",
    "OperationPresenterCapability",
    "OperationPresenterRegistry",
    "PresentedOperation",
    "serialize_operation_record_capability",
]

_TARGET_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROVIDER_STATES: Final = frozenset(
    {"submitted", "queued", "running", "consolidating", "terminal"}
)


@dataclass(frozen=True, slots=True)
class OperationPresenterCapability:
    """Stable label plus finite public detail, counter, and target schemas."""

    operation_key: str
    label_key: str
    fallback_label: str
    allowed_detail_fields: Mapping[str, str]
    counter_units: tuple[str, ...] = ()
    target_kinds: tuple[str, ...] = ()
    semantic_kind: str = "operation"

    def to_public_dict(self) -> dict[str, object]:
        """Return a fresh JSON-compatible presenter descriptor."""
        return {
            "operation_key": self.operation_key,
            "label_key": self.label_key,
            "fallback_label": self.fallback_label,
            "semantic_kind": self.semantic_kind,
            "allowed_detail_fields": dict(self.allowed_detail_fields),
            "counter_units": list(self.counter_units),
            "target_kinds": list(self.target_kinds),
        }


@dataclass(frozen=True, slots=True)
class PresentedOperation:
    """Sanitized presenter output safe for a public operation record."""

    operation_key: str
    label_key: str
    fallback_label: str
    semantic_kind: str
    detail: dict[str, int | bool | str]
    progress: dict[str, int | str] | None
    target: dict[str, str] | None


_UNKNOWN_PRESENTER: Final = OperationPresenterCapability(
    "operation.unknown",
    "execution.operation.generic",
    "Internal operation",
    {},
)

_TOOL_PRESENTER_LABELS: Final = {
    "analyst": "Submit analysis",
    "brief_gene": "Run gene summary",
    "chat": "Generate response",
    "data": "Run data analysis",
    "deep_genome": "Run genome analysis",
    "design": "Submit design analysis",
    "knowledge": "Run knowledge analysis",
    "network": "Submit network analysis",
    "research": "Submit research analysis",
    "review": "Run literature review",
}

_TOOL_PRESENTERS: Final = tuple(
    OperationPresenterCapability(
        f"tool.{slug}",
        f"execution.operation.tool.{slug}",
        label,
        {},
        semantic_kind="tool",
    )
    for slug, label in _TOOL_PRESENTER_LABELS.items()
)

OPERATION_PRESENTER_CATALOG = (
    OperationPresenterCapability(
        "analyst.prepare_analysis",
        "execution.trace.analyst.prepareAnalysis",
        "Prepare analysis",
        {},
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "analyst.run_workflow",
        "execution.trace.analyst.runWorkflow",
        "Run analysis workflow",
        {"step_count": "integer"},
        ("steps",),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "analyst.collect_outputs",
        "execution.trace.analyst.collectOutputs",
        "Collect analysis outputs",
        {"result_count": "integer"},
        ("results",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "artifact.package",
        "execution.operation.artifact.package",
        "Package results",
        {"artifact_count": "integer"},
        ("artifacts",),
        ("artifact", "download"),
    ),
    OperationPresenterCapability(
        "data.query",
        "execution.operation.data.query",
        "Query data",
        {"result_count": "integer"},
        ("rows", "results"),
    ),
    OperationPresenterCapability(
        "deep_genome.workflow",
        "execution.operation.deepGenome.workflow",
        "Run genome workflow",
        {},
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "deep_genome.prepare_plan",
        "execution.trace.deepGenome.preparePlan",
        "Prepare genome analysis plan",
        {},
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "deep_genome.gather_context",
        "execution.trace.deepGenome.gatherContext",
        "Gather genome context",
        {"result_count": "integer"},
        ("results",),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "deep_genome.run_analysis_branches",
        "execution.trace.deepGenome.runAnalysisBranches",
        "Run genome analysis branches",
        {"branch_count": "integer"},
        ("branches",),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "deep_genome.experiment_protocol",
        "execution.trace.deepGenome.experimentProtocol",
        "Build experiment protocol",
        {},
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "deep_genome.synthesize_results",
        "execution.trace.deepGenome.synthesizeResults",
        "Synthesize genome results",
        {"result_count": "integer"},
        ("results",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "design.validate_target",
        "execution.trace.design.validateTarget",
        "Validate design target",
        {"target_validated": "boolean"},
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "design.run_branches",
        "execution.trace.design.runBranches",
        "Run design branches",
        {"branch_count": "integer"},
        ("branches",),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "design.consolidate_candidates",
        "execution.trace.design.consolidateCandidates",
        "Consolidate design candidates",
        {"candidate_count": "integer"},
        ("candidates",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "design.package_outputs",
        "execution.trace.design.packageOutputs",
        "Package design outputs",
        {"artifact_count": "integer"},
        ("artifacts",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "gene_network.infer_network",
        "execution.trace.geneNetwork.inferNetwork",
        "Infer regulatory network",
        {"gene_count": "integer", "interaction_count": "integer"},
        ("genes", "interactions"),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "gene_network.prepare_inputs",
        "execution.trace.geneNetwork.prepareInputs",
        "Prepare analysis inputs",
        {"input_count": "integer"},
        ("inputs",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "gene_network.rank_regulators",
        "execution.trace.geneNetwork.rankRegulators",
        "Rank candidate regulators",
        {"regulator_count": "integer"},
        ("regulators",),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "gene_network.synthesize_results",
        "execution.trace.geneNetwork.synthesizeResults",
        "Synthesize network results",
        {"result_count": "integer"},
        ("results",),
        ("artifact", "report", "download"),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "gene_network.validate_target",
        "execution.trace.geneNetwork.validateTarget",
        "Validate trait target",
        {"target_validated": "boolean"},
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "knowledge.search",
        "execution.operation.knowledge.search",
        "Search knowledge",
        {"repository_count": "integer", "result_count": "integer"},
        ("repositories", "results"),
    ),
    OperationPresenterCapability(
        "model.generate",
        "execution.operation.model.generate",
        "Generate response",
        {},
    ),
    OperationPresenterCapability(
        "remote.analysis",
        "execution.operation.remote.analysis",
        "Run analysis",
        {"provider_state": "provider_state"},
        target_kinds=("trace",),
    ),
    OperationPresenterCapability(
        "remote.reconcile",
        "execution.operation.remote.reconcile",
        "Collect analysis",
        {"provider_state": "provider_state", "result_count": "integer"},
        ("results",),
        ("artifact", "download"),
    ),
    OperationPresenterCapability(
        "remote.submit",
        "execution.operation.remote.submit",
        "Submit analysis",
        {"provider_state": "provider_state"},
    ),
    OperationPresenterCapability(
        "research.decompose_objectives",
        "execution.trace.research.decomposeObjectives",
        "Define research objectives",
        {"objective_count": "integer"},
        ("objectives",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "research.dispatch_work",
        "execution.trace.research.dispatchWork",
        "Run research tasks",
        {"task_count": "integer"},
        ("tasks",),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "research.collect_evidence",
        "execution.trace.research.collectEvidence",
        "Collect research evidence",
        {"result_count": "integer"},
        ("results",),
        semantic_kind="tool",
    ),
    OperationPresenterCapability(
        "research.synthesize_results",
        "execution.trace.research.synthesizeResults",
        "Synthesize research results",
        {"result_count": "integer"},
        ("results",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "research.package_outputs",
        "execution.trace.research.packageOutputs",
        "Package research outputs",
        {"artifact_count": "integer"},
        ("artifacts",),
        semantic_kind="phase",
    ),
    OperationPresenterCapability(
        "review.citation_check",
        "execution.operation.review.citationCheck",
        "Check citations",
        {"ordinal": "integer", "total": "integer"},
        ("batches",),
    ),
    OperationPresenterCapability(
        "review.draft_dimension",
        "execution.operation.review.draftDimension",
        "Draft section",
        {"ordinal": "integer", "total": "integer"},
        ("dimensions",),
    ),
    OperationPresenterCapability(
        "review.final_synthesis",
        "execution.operation.review.finalSynthesis",
        "Synthesize report",
        {"total": "integer"},
        ("dimensions",),
    ),
    OperationPresenterCapability(
        "review.retrieve_dimension",
        "execution.operation.review.retrieveDimension",
        "Retrieve evidence",
        {"ordinal": "integer", "total": "integer"},
        ("dimensions",),
    ),
    *_TOOL_PRESENTERS,
)


class OperationPresenterRegistry:
    """Fail-closed lookup and sanitizer for public operation facts."""

    def __init__(
        self,
        presenters: tuple[OperationPresenterCapability, ...],
    ) -> None:
        self._presenters = {
            presenter.operation_key: presenter for presenter in presenters
        }

    def resolve(self, operation_key: str) -> OperationPresenterCapability:
        """Return a registered presenter or the payload-free fallback."""
        return self._presenters.get(operation_key, _UNKNOWN_PRESENTER)

    def present(
        self,
        operation_key: str,
        *,
        detail: Mapping[str, Any] | None = None,
        progress: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
    ) -> PresentedOperation:
        """Filter caller-owned values through the presenter's finite schema."""
        presenter = self.resolve(operation_key)
        return PresentedOperation(
            operation_key=presenter.operation_key,
            label_key=presenter.label_key,
            fallback_label=presenter.fallback_label,
            semantic_kind=presenter.semantic_kind,
            detail=_sanitize_detail(presenter, detail),
            progress=_sanitize_progress(presenter, progress),
            target=_sanitize_target(presenter, target),
        )


def _sanitize_detail(
    presenter: OperationPresenterCapability,
    detail: Mapping[str, Any] | None,
) -> dict[str, int | bool | str]:
    if detail is None:
        return {}
    sanitized: dict[str, int | bool | str] = {}
    for key, field_type in presenter.allowed_detail_fields.items():
        value = detail.get(key)
        if field_type == "integer":
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= 1_000_000
            ):
                sanitized[key] = value
        elif field_type == "boolean":
            if isinstance(value, bool):
                sanitized[key] = value
        elif (
            field_type == "provider_state"
            and isinstance(value, str)
            and value in _PROVIDER_STATES
        ):
            sanitized[key] = value
    DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS.validate_detail_field_count(
        len(sanitized)
    )
    return sanitized


def _sanitize_progress(
    presenter: OperationPresenterCapability,
    progress: Mapping[str, Any] | None,
) -> dict[str, int | str] | None:
    if progress is None:
        return None
    completed = progress.get("completed")
    total = progress.get("total")
    unit = progress.get("unit")
    if (
        not isinstance(completed, int)
        or isinstance(completed, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or completed < 0
        or total < 1
        or completed > total
        or not isinstance(unit, str)
        or unit not in presenter.counter_units
    ):
        return None
    return {"completed": completed, "total": total, "unit": unit}


def _sanitize_target(
    presenter: OperationPresenterCapability,
    target: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    if target is None:
        return None
    kind = target.get("kind")
    target_id = target.get("id")
    if (
        not isinstance(kind, str)
        or kind not in presenter.target_kinds
        or not isinstance(target_id, str)
        or _TARGET_ID_PATTERN.fullmatch(target_id) is None
    ):
        return None
    return {"kind": kind, "id": target_id}


OPERATION_PRESENTER_REGISTRY: Final = OperationPresenterRegistry(
    OPERATION_PRESENTER_CATALOG
)

AGENT_OPERATION_COVERAGE: Final = {
    item.slug: item.trace_operations for item in PUBLIC_AGENT_CATALOG
}


def serialize_operation_record_capability() -> dict[str, object]:
    """Return the bounded grouped-operation discovery descriptor."""
    return {
        "major_version": 1,
        "grouping_key": "work_unit_id",
        "attempt_history": True,
        "unknown_presenter": _UNKNOWN_PRESENTER.to_public_dict(),
        "execution_log_artifact_role": "execution_log",
        "liveness_clocks": [
            "last_execution_fact_at",
            "last_provider_contact_at",
            "last_stream_contact_at",
        ],
        "limits": asdict(DEFAULT_EXECUTION_TRACE_DETAIL_LIMITS),
        "agent_coverage": {
            agent: list(operations)
            for agent, operations in AGENT_OPERATION_COVERAGE.items()
        },
        "presenters": [
            presenter.to_public_dict()
            for presenter in OPERATION_PRESENTER_CATALOG
        ],
    }
