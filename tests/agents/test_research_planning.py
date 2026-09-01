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
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research.contracts import (
    MAX_RESEARCH_GOAL_CHARS,
    MAX_RESEARCH_GOAL_CONTEXT_CHARS,
    ResearchGoal,
)
from mcp_server_phytomni.agents.research.document_evidence import (
    DocumentEvidenceDigest,
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
)
from mcp_server_phytomni.agents.research.input_contracts import SourceSpan
from mcp_server_phytomni.agents.research.input_preparation import (
    PreparedResearchAuthority,
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


def _canonical_figure_goals() -> tuple[ResearchGoal, ...]:
    """Owner-approved HTTP fan-out fixture (Figure 2, 4, 3, 1, 5)."""
    return (
        ResearchGoal(
            goal=(
                "Replicate Figure 2: Perform comparative analysis of m5C "
                "methylation levels and gene expression in WT and emf1 "
                "mutants, including LC-MS/MS quantification of m5C, "
                "m5C-RIP-seq peak calling, and correlation analysis with "
                "RNA-seq expression data."
            ),
            context=(
                "The authors performed dot blot and LC-MS/MS assays to "
                "quantify m5C levels in WT and emf1 seedlings "
                "(Figure 2A-B). m5C-RIP-seq was conducted on 7-day-old "
                "seedlings with three biological replicates, identifying "
                "6572 peaks in WT and 6778 peaks in emf1 (Figure S2D). "
                "They observed a 50% increase in m5C abundance in emf1 "
                "mutants. RNA-seq revealed 3526 up-regulated and 3377 "
                "down-regulated genes in emf1. The analysis showed that "
                "831 down-regulated genes had increased m5C modification "
                "(Figure 2I-J), with m5C peaks predominantly in coding "
                "regions (Figure S2G-H)."
            ),
        ),
        ResearchGoal(
            goal=(
                "Replicate Figure 4: Analyze the inverse relationship "
                "between m5C and H3K27me3 distributions using ChIP-seq "
                "and RIP-seq data integration, including Circos plot "
                "generation and gene ontology enrichment."
            ),
            context=(
                "Genome-wide ChIP-seq data of H3K27me3 (from prior "
                "studies) and m5C RIP-seq data were compared. The "
                "analysis revealed that m5C peaks were enriched in "
                "regions lacking H3K27me3 (Figure 4B), with a "
                "significant negative correlation (R = -0.42, "
                "p = 1.1e-11, Figure S3H). Photosynthesis genes "
                "(e.g., LHCB5, CAO) showed m5C enrichment in emf1 "
                "mutants, while starch genes (SUS1, SUS3) lacked m5C "
                "but had reduced H3K27me3 in emf1 (Figure 4C-G). GO "
                "analysis of down-regulated m5C-modified genes "
                "highlighted photosynthesis and chloroplast "
                "development (Figure S5C-E)."
            ),
        ),
        ResearchGoal(
            goal=(
                "Replicate Figure 3: Investigate EMF1's role in "
                "repressing starch synthesis genes via H3K27me3, "
                "including ChIP-qPCR validation of H3K27me3 levels "
                "and RT-qPCR of SUS1/SUS3 expression."
            ),
            context=(
                "emf1 mutants showed 7.3-fold increased starch content "
                "at 7 DAG (Figure 3A). RNA-seq revealed 73% of starch "
                "synthesis genes were up-regulated in emf1 "
                "(Figure 3B). ChIP-seq and qPCR demonstrated reduced "
                "H3K27me3 at SUS1 and SUS3 loci in emf1 "
                "(Figure 3G-H), correlating with increased transcript "
                "levels (Figure 3D-E). The authors concluded EMF1 "
                "maintains repression of starch genes via H3K27me3 "
                "(EMF1-PcG-H3K27me3 module)."
            ),
        ),
        ResearchGoal(
            goal=(
                "Replicate Figure 1: Characterize photosynthetic "
                "defects in emf1 mutants through chlorophyll "
                "quantification, TEM analysis, and differential "
                "expression analysis of photosynthesis genes."
            ),
            context=(
                "emf1 mutants exhibited pale-green leaves and 50% "
                "lower chlorophyll content (Figure 1B). TEM revealed "
                "defective chloroplast structure with starch "
                "accumulation (Figure 1C). RNA-seq identified 2219 "
                "EMF1-no-K27 genes, 775 of which were m5C-modified "
                "(Figure S7A). Down-regulated photosynthesis genes "
                "(e.g., LHCB5, CAO) showed increased m5C in emf1 "
                "(Figure 1D-G). GO analysis enriched for "
                "photosynthesis and chloroplast development "
                "(Figure 1E)."
            ),
        ),
        ResearchGoal(
            goal=(
                "Replicate Figure 5: Validate TRM4B's role in m5C "
                "deposition and its regulation by EMF1 through "
                "ChIP-qPCR of H3K4me3 and RT-qPCR of TRM4B "
                "expression."
            ),
            context=(
                "TRM4B expression was up-regulated 2.5-fold in emf1 "
                "(Figure 4G). ChIP-qPCR showed increased H3K4me3 at "
                "TRM4B loci in emf1 (Figure S6M), correlating with "
                "ULT1 binding (Figure S6L). TRM4B overexpression in "
                "emf1 rescued m5C levels on photosynthesis genes "
                "(Figure 4F). The authors concluded EMF1 represses "
                "TRM4B via H3K4me3 (EMF1-TRM4B-m5C module)."
            ),
        ),
    )


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


async def test_planner_accepts_multiline_effective_query() -> None:
    """A parsed multiline query can reach planning after path removal."""
    prepared = replace(
        _prepared(),
        effective_query=(
            "Compare the supplied datasets.\n"
            "Prioritize reproducible drought-response signals."
        ),
    )
    provider = _GoalProvider((ResearchGoal(goal="Compare datasets"),))

    plan = await build_research_plan(
        _request(prepared=prepared),
        provider,
    )

    assert provider.calls == 1
    assert plan.children[0].goal_description == "Compare datasets"


async def test_provider_order_is_plan_identity() -> None:
    """Extractor order is child identity; a permutation is a new plan."""
    extracted = _canonical_figure_goals()
    alphabetical = tuple(
        sorted(extracted, key=lambda goal: (goal.goal, goal.context or ""))
    )
    assert extracted != alphabetical

    first = await build_research_plan(_request(), _GoalProvider(extracted))
    second = await build_research_plan(_request(), _GoalProvider(alphabetical))

    assert [child.task_name for child in first.children] == [
        "research_goal_0",
        "research_goal_1",
        "research_goal_2",
        "research_goal_3",
        "research_goal_4",
    ]
    assert first.children[0].output_dir.endswith("/part-001")
    assert first.children[0].goal_description.startswith("Replicate Figure 2:")
    assert first.children[3].goal_description.startswith("Replicate Figure 1:")
    assert first.children[3].output_dir.endswith("/part-004")
    assert first.digest != second.digest
    assert second.children[0].goal_description.startswith(
        "Replicate Figure 1:"
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
        replace(_prepared(), effective_query="query\x00payload"),
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


async def test_planner_accepts_empty_effective_query_with_evidence() -> None:
    """Blank query is not a synthetic goal; evidence still reaches the
    provider."""
    prepared = replace(_prepared(), effective_query="")
    provider = _GoalProvider(_canonical_figure_goals()[:2])

    plan = await build_research_plan(_request(prepared=prepared), provider)

    assert provider.calls == 1
    assert provider.received is not None
    assert len(plan.children) == 2
    assert plan.children[0].goal_description.startswith("Replicate Figure 2:")


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
        ({"goal": "x" * (MAX_RESEARCH_GOAL_CHARS + 1)},),
        (
            {
                "goal": "x",
                "context": "y" * (MAX_RESEARCH_GOAL_CONTEXT_CHARS + 1),
            },
        ),
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


async def test_bound_goal_subsets_child_data_list() -> None:
    """Cited inventory ids subset one child; unbound goals keep parent."""
    parent = {
        "obs://bucket/a.csv": "table-a",
        "obs://bucket/b.csv": "table-b",
    }
    prepared = replace(
        _prepared(parent),
        authorities=(
            PreparedResearchAuthority(
                dataset_id="dataset_001",
                exact_reference="obs://bucket/a.csv",
                compound_suffix=".csv",
                authority=cast(Any, SimpleNamespace()),
            ),
        ),
    )
    plan = await build_research_plan(
        _request(prepared=prepared),
        _GoalProvider(
            (
                ResearchGoal(
                    goal="Analyze first table",
                    dataset_ids=("dataset_001",),
                ),
                ResearchGoal(goal="Analyze both tables"),
            )
        ),
    )

    assert len(plan.children[0].data_list) == 1
    assert dict(plan.children[0].data_list) == {
        "obs://bucket/a.csv": "table-a",
    }
    assert len(plan.children[1].data_list) == 2
    assert dict(plan.children[1].data_list) == parent
    assert (
        plan.children[0].dispatch_fingerprint
        != plan.children[1].dispatch_fingerprint
    )


async def test_bound_child_fingerprint_isolates_data_list() -> None:
    """Same child identity, only bound data_list changes fingerprint."""
    parent = {
        "obs://bucket/a.csv": "table-a",
        "obs://bucket/b.csv": "table-b",
    }
    prepared = replace(
        _prepared(parent),
        authorities=(
            PreparedResearchAuthority(
                dataset_id="dataset_001",
                exact_reference="obs://bucket/a.csv",
                compound_suffix=".csv",
                authority=cast(Any, SimpleNamespace()),
            ),
        ),
    )
    request = _request(prepared=prepared)
    goal_one = "Analyze first table"
    goal_two = "Analyze both tables"
    bound = await build_research_plan(
        request,
        _GoalProvider(
            (
                ResearchGoal(
                    goal=goal_one,
                    dataset_ids=("dataset_001",),
                ),
                ResearchGoal(goal=goal_two),
            )
        ),
    )
    unbound = await build_research_plan(
        request,
        _GoalProvider(
            (
                ResearchGoal(goal=goal_one, dataset_ids=None),
                ResearchGoal(goal=goal_two),
            )
        ),
    )
    first_bound = bound.children[0]
    first_unbound = unbound.children[0]

    assert len(first_bound.data_list) == 1
    assert len(first_unbound.data_list) == 2
    assert (
        first_bound.dispatch_fingerprint != first_unbound.dispatch_fingerprint
    )
    assert first_bound.task_name == first_unbound.task_name
    assert first_bound.output_dir == first_unbound.output_dir
    assert first_bound.thread_id == first_unbound.thread_id
    assert first_bound.goal_description == first_unbound.goal_description


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
