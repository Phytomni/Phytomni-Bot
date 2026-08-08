# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure planning contracts for the Research input coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import pytest

from mcp_server_phytomni.agents.research.contracts import ResearchGoal
from mcp_server_phytomni.agents.research.document_evidence import (
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
)
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchInput,
)
from mcp_server_phytomni.agents.research.planning import (
    ResearchPlanningRequest,
    build_research_plan,
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
        content_digest="content-digest",
        text="Drought response in rice.",
        dataset_ids=("dataset_001",),
    )
    return ExtractedResearchEvidence(
        units=(unit,),
        document_digests=(),
        coverage_digest="coverage-digest",
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
        first.children[0].data_list["new"] = "mutation"  # type: ignore[index]


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

    assert isinstance(plan.children[0].data_list, MappingProxyType)
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
