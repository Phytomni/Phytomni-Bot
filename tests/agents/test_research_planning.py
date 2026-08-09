# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure planning contracts for the Research input coordinator."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from types import MappingProxyType
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research.contracts import ResearchGoal
from mcp_server_phytomni.agents.research.document_evidence import (
    DocumentEvidenceDigest,
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
)
from mcp_server_phytomni.agents.research.input_contracts import SourceSpan
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from mcp_server_phytomni.agents.research.planning import (
    ResearchPlanningRequest,
    build_research_plan,
    research_child_output_dir,
    research_child_thread_id,
)

pytestmark = pytest.mark.agent


def _prepared(
    data_list: MappingProxyType[str, str] | dict[str, str] | None = None,
) -> PreparedResearchInput:
    """Build a minimal final native Research projection."""
    return PreparedResearchInput(
        effective_query="Investigate drought response.",
        obs_file_list=(),
        data_list=MappingProxyType(
            {"obs://bucket/drought.csv": "expression matrix"}
            if data_list is None
            else data_list
        ),
        inventory_digest="inventory-digest",
        evidence_digest="evidence-digest",
        execution_fingerprint="execution-fingerprint",
        authority_ids=("authority-1",),
    )


def _evidence() -> ExtractedResearchEvidence:
    """Build one opaque extracted evidence object."""
    unit = ResearchEvidenceUnit(
        evidence_id="query_span_001",
        source_kind="query",
        source_ordinal=0,
        source_span=None,
        content_digest=_sha256("Drought response in rice."),
        text="Drought response in rice.",
        dataset_ids=("dataset_001",),
    )
    coverage = _coverage_digest((unit,), ())
    return ExtractedResearchEvidence(
        units=(unit,),
        document_digests=(),
        coverage_digest=coverage,
    )


def _sha256(value: str) -> str:
    """Return the digest used by the evidence extractor contract."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _coverage_digest(
    units: tuple[ResearchEvidenceUnit, ...],
    documents: tuple[DocumentEvidenceDigest, ...],
) -> str:
    """Build the canonical coverage digest for test evidence."""
    payload = {
        "units": [
            {
                "evidence_id": unit.evidence_id,
                "source_kind": unit.source_kind,
                "source_ordinal": unit.source_ordinal,
                "source_span": (
                    None
                    if unit.source_span is None
                    else {
                        "start": unit.source_span.start,
                        "end": unit.source_span.end,
                        "grammar": unit.source_span.grammar,
                    }
                ),
                "content_digest": unit.content_digest,
                "dataset_ids": unit.dataset_ids,
            }
            for unit in units
        ],
        "documents": [
            {
                "document_id": document.document_id,
                "content_digest": document.content_digest,
                "evidence_ids": document.evidence_ids,
            }
            for document in documents
        ],
    }
    return _sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )


@dataclass
class _GoalProvider:
    """Record the pure provider call and return configured goals."""

    goals: tuple[Any, ...]
    calls: int = 0
    received: ExtractedResearchEvidence | None = None

    @property
    def contract_name(self) -> str:
        """Identify the test goal-provider contract."""
        return "test_goal_provider"

    async def extract(
        self,
        evidence: ExtractedResearchEvidence,
        locale: str | None,
    ) -> tuple[ResearchGoal, ...]:
        """Return goals without performing any external side effect."""
        del locale
        self.calls += 1
        self.received = evidence
        return self.goals


def _request(
    *,
    run_id: str = "run-123",
    prepared: PreparedResearchInput | None = None,
) -> ResearchPlanningRequest:
    """Build a deterministic planning request."""
    return ResearchPlanningRequest(
        run_id=run_id,
        prepared=prepared or _prepared(),
        evidence=_evidence(),
        locale="en-US",
        compute_resource="medium",
        interop_mode="auto",
        interop_targets=("target-a",),
    )


async def test_plan_is_ordered_deterministic_and_side_effect_free() -> None:
    """Planning uses extracted evidence and computes stable child identity."""
    request = _request()
    provider = _GoalProvider(
        goals=(
            ResearchGoal(goal="Map drought genes", context="rice"),
            ResearchGoal(goal="Prioritize candidates", context="field"),
        )
    )

    first = await build_research_plan(request, provider)
    second = await build_research_plan(
        _request(), _GoalProvider(provider.goals)
    )

    assert provider.calls == 1
    assert provider.received is request.evidence
    assert first == second
    assert first.digest
    assert [child.ordinal for child in first.children] == [0, 1]
    assert [child.task_name for child in first.children] == [
        "research_goal_0",
        "research_goal_1",
    ]
    assert first.children[0].output_dir.endswith("/run-123/children/part-001")
    assert first.children[0].thread_id == "thread-0-run-123"
    assert first.children[0].data_list == _prepared().data_list
    with pytest.raises(TypeError):
        cast(Any, first.children[0].data_list)["new"] = "mutation"
    assert [item.name for item in fields(first.children[0])] == [
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
    ]
    assert asdict(first.children[0])["goal_description"] == (
        "Map drought genes"
    )


async def test_provider_order_that_is_not_canonical_fails_closed() -> None:
    """A provider cannot silently change child identity by changing order."""
    goals = (
        ResearchGoal(goal="Prioritize candidates", context="field"),
        ResearchGoal(goal="Map drought genes", context="rice"),
    )

    with pytest.raises(Exception) as caught:
        await build_research_plan(_request(), _GoalProvider(goals))

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )


@pytest.mark.parametrize("ordinal", [-1, 20, True, "1"])
def test_child_identifiers_reject_invalid_ordinals(ordinal: object) -> None:
    """Only bounded integer child ordinals can derive durable identities."""
    with pytest.raises(Exception) as caught:
        research_child_output_dir("run-123", cast(Any, ordinal))

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    with pytest.raises(Exception):
        research_child_thread_id("run-123", cast(Any, ordinal))


def test_child_identifiers_project_stable_public_paths() -> None:
    """A bounded run/ordinal pair projects deterministic child identities."""
    assert research_child_output_dir("run-123", 0) == (
        "research/run-123/children/part-001"
    )
    assert research_child_thread_id("run-123", 19) == "thread-19-run-123"


async def test_planner_projects_provider_failures_without_child_work() -> None:
    """Unexpected goal-provider errors become the stable planning failure."""

    class BrokenProvider:
        """Raise an implementation detail that must not escape planning."""

        @property
        def contract_name(self) -> str:
            """Identify the intentionally broken provider fixture."""
            return "broken"

        async def extract(self, _evidence: object, _locale: object) -> object:
            """Raise a provider detail for the planner to classify."""
            raise RuntimeError("provider detail")

    with pytest.raises(Exception) as caught:
        await build_research_plan(_request(), cast(Any, BrokenProvider()))

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"locale": "fr-FR"},
        {"compute_resource": ""},
        {"interop_mode": "unsupported"},
        {"interop_targets": cast(Any, ["target-a"])},
        {"interop_targets": ("target-a", "target-a")},
    ],
)
async def test_planner_rejects_invalid_public_execution_controls(
    changes: dict[str, object],
) -> None:
    """Malformed locale, resource, and interop controls fail before I/O."""
    provider = _GoalProvider((ResearchGoal(goal="Analyze"),))

    with pytest.raises(Exception) as caught:
        await build_research_plan(
            replace(_request(), **cast(Any, changes)), provider
        )

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    assert not provider.calls


@pytest.mark.parametrize(
    "prepared",
    [
        cast(Any, object()),
        replace(_prepared(), effective_query=""),
        replace(_prepared(), obs_file_list=cast(Any, ["obs://asset"])),
        replace(_prepared(), data_list=cast(Any, {"obs://asset": "data"})),
    ],
)
async def test_planner_rejects_invalid_final_native_projection(
    prepared: PreparedResearchInput,
) -> None:
    """Only an immutable bounded final projection can reach goal planning."""
    provider = _GoalProvider((ResearchGoal(goal="Analyze"),))

    with pytest.raises(Exception) as caught:
        await build_research_plan(_request(prepared=prepared), provider)

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    assert not provider.calls


async def test_empty_goal_result_fails_before_any_child_work() -> None:
    """An empty provider result fails before a plan can be persisted."""
    provider = _GoalProvider(goals=())

    with pytest.raises(Exception) as caught:
        await build_research_plan(_request(), provider)

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )
    assert getattr(caught.value, "stage", None) == "planning"
    assert provider.calls == 1


@pytest.mark.parametrize(
    "goals",
    [
        (ResearchGoal(goal="x"), ResearchGoal(goal="x")),
        ({"goal": "x" * 1001},),
        ({"goal": "x", "context": "y" * 4001},),
    ],
)
async def test_invalid_goal_result_fails_closed(
    goals: tuple[ResearchGoal, ...],
) -> None:
    """Duplicate or unbounded model goals never become child plans."""
    with pytest.raises(Exception) as caught:
        await build_research_plan(_request(), _GoalProvider(goals))

    assert getattr(caught.value, "code", None) == (
        "research_input_resolution_failed"
    )


async def test_planner_rejects_mutable_or_changed_data_list() -> None:
    """The final native data map is copied into an immutable child payload."""
    prepared = _prepared({"obs://bucket/a.csv": "A"})
    plan = await build_research_plan(
        _request(prepared=prepared),
        _GoalProvider((ResearchGoal(goal="Analyze A"),)),
    )

    assert isinstance(plan.children[0].data_list, Mapping)
    assert dict(plan.children[0].data_list) == {"obs://bucket/a.csv": "A"}


async def test_query_only_plan_allows_empty_data_list() -> None:
    """A query-only Research request has no dataset description map."""
    plan = await build_research_plan(
        _request(prepared=_prepared({})),
        _GoalProvider((ResearchGoal(goal="Analyze the paper"),)),
    )

    assert not dict(plan.children[0].data_list)


async def test_planner_rejects_empty_evidence_before_provider() -> None:
    """A forged empty evidence object cannot reach the goal provider."""
    request = _request()
    invalid = ResearchPlanningRequest(
        run_id=request.run_id,
        prepared=request.prepared,
        evidence=ExtractedResearchEvidence((), (), ""),
        locale=request.locale,
        compute_resource=request.compute_resource,
        interop_mode=request.interop_mode,
        interop_targets=request.interop_targets,
    )
    provider = _GoalProvider((ResearchGoal(goal="Analyze"),))

    with pytest.raises(Exception):
        await build_research_plan(invalid, provider)

    assert not provider.calls


@pytest.mark.parametrize(
    "invalid_unit",
    [
        lambda unit: replace(unit, source_kind="forged"),
        lambda unit: replace(unit, source_ordinal=-1),
        lambda unit: replace(unit, content_digest=""),
        lambda unit: replace(unit, source_span=SourceSpan(-1, 0, "query")),
        lambda unit: replace(unit, evidence_id="query_span_001"),
    ],
)
async def test_planner_rejects_malformed_evidence(
    invalid_unit: Any,
) -> None:
    """Every forged evidence identity fails before the provider is called."""
    evidence = _evidence()
    unit = invalid_unit(evidence.units[0])
    units: tuple[ResearchEvidenceUnit, ...] = (unit,)
    if (
        unit.evidence_id == evidence.units[0].evidence_id
        and unit is not evidence.units[0]
    ):
        units = (evidence.units[0], unit)
    invalid = ExtractedResearchEvidence(
        units=units,
        document_digests=evidence.document_digests,
        coverage_digest=evidence.coverage_digest,
    )
    request = replace(_request(), evidence=invalid)
    provider = _GoalProvider((ResearchGoal(goal="Analyze"),))

    with pytest.raises(Exception):
        await build_research_plan(request, provider)
    assert not provider.calls


async def test_planner_rejects_document_coverage_boundary() -> None:
    """Document records must cover exactly document evidence units."""
    evidence = _evidence()
    invalid = replace(
        evidence,
        document_digests=(
            DocumentEvidenceDigest(
                document_id="document_001",
                content_digest="0" * 64,
                evidence_ids=("missing-evidence",),
            ),
        ),
    )
    with pytest.raises(Exception):
        await build_research_plan(
            replace(_request(), evidence=invalid),
            _GoalProvider((ResearchGoal(goal="Analyze"),)),
        )


async def test_planner_rejects_coverage_digest_mismatch() -> None:
    """The coverage digest must bind the complete identity projection."""
    evidence = replace(_evidence(), coverage_digest="0" * 64)

    with pytest.raises(Exception):
        await build_research_plan(
            replace(_request(), evidence=evidence),
            _GoalProvider((ResearchGoal(goal="Analyze"),)),
        )
