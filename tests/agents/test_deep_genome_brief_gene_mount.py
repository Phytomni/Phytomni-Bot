# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the brief_gene subgraph mount adapter on DeepGenomeAgents.

Covers ``make_brief_gene_mount_node`` IO projection from
BriefGeneOutput's flat-string annotation surface into deep_genome's
nested ``gene_annotation`` + ``knowledge_context`` shape, the
``user_query`` synthesis from ``gene_id``, the ``is_follow_up=False``
opt-out passed to brief_gene, and the required-branch failure raised when
brief_gene cannot produce the mandatory profile.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast
from unittest.mock import AsyncMock

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome.brief_gene_mount import (
    RequiredBriefGeneError,
    make_brief_gene_mount_node,
)

from ._subgraph_branch_fakes import failing_async_object

pytestmark = pytest.mark.agent


class _FakeBriefGeneState(TypedDict, total=False):
    """Minimal state shape for the offline brief_gene-subgraph stub."""

    user_query: str
    is_follow_up: bool
    gene_id: str
    species_code: str
    go_string: str
    kegg_string: str
    interpro_string: str
    description_string: str
    retrieved_docs: list[dict[str, Any]]
    final_response: dict[str, Any]
    literature_degraded: list[dict[str, Any]]
    # M11 — preamble fan-out fields the brief_gene mount projects to
    # deep_genome state.
    gene_structure_string: str
    orthologs_data: dict[str, Any]
    paralogs_data: dict[str, Any]
    interaction_data: dict[str, Any]
    section1_markdown: str
    section2_markdown: str
    section3_markdown: str
    section4_markdown: str
    introduction_report: str


def _build_fake_brief_gene_app(
    output: dict[str, Any] | None = None,
) -> CompiledStateGraph:
    """Compile a one-node ``StateGraph`` to stand in for BriefGeneAgent.

    ``find_subgraph_pregel`` recognises ``CompiledStateGraph`` by
    isinstance, so a SimpleNamespace cannot satisfy the xray
    expansion. Compiling a trivial ``StateGraph`` keeps the test
    fully offline while still presenting a real compiled subgraph
    for the mount factory closure to hold. The optional ``output``
    map stages the canned BriefGeneOutput keys the stub returns.
    """

    canned = output or {
        "gene_id": "AT1G01010",
        "go_string": "GO:0003700",
        "kegg_string": "ath:AT1G01010",
        "interpro_string": "IPR036093",
        "description_string": "transcription factor",
        "retrieved_docs": [{"title": "doc1"}, {"title": "doc2"}],
        "final_response": {"choices": [{"message": {"content": "ans"}}]},
    }

    async def _stub(state: _FakeBriefGeneState) -> dict[str, Any]:
        del state
        return dict(canned)

    workflow: StateGraph = StateGraph(_FakeBriefGeneState)
    workflow.add_node("stub", _stub)
    workflow.add_edge(START, "stub")
    workflow.add_edge("stub", END)
    return workflow.compile()


def _deep_genome_state(gene_id: str = "AT1G01010") -> dict[str, Any]:
    """Minimal deep_genome state for the mount node input."""
    return {"gene_id": gene_id, "species_code": "ath"}


# ---------------------------------------------------------------------------
# Happy-path IO projection: BriefGeneOutput → deep_genome state delta.
# ---------------------------------------------------------------------------


async def test_brief_gene_mount_projects_gene_annotation_dict_shape() -> None:
    """Mount writes the nested ``gene_annotation`` shape deep_genome reads.

    deep_genome's downstream report nodes consume
    ``gene_annotation.{gene_string, description, go, interpro,
    mapman}``; the mount projection MUST preserve these key names
    even though brief_gene exposes the data under flat-string keys
    on BriefGeneOutput (``go_string`` / ``kegg_string`` /
    ``interpro_string`` / ``description_string``). The
    ``kegg_string`` → ``mapman`` cross-naming is the historical
    artefact of brief_gene calling the same BI table's projection
    ``kegg`` while deep_genome calls it ``mapman``.
    """
    fake_app = _build_fake_brief_gene_app()
    mount = make_brief_gene_mount_node(fake_app)
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    annotation = delta["gene_annotation"]
    assert annotation["gene_string"] == "AT1G01010"
    assert annotation["description"] == "transcription factor"
    assert annotation["go"] == "GO:0003700"
    assert annotation["interpro"] == "IPR036093"
    assert annotation["mapman"] == "ath:AT1G01010"


async def test_brief_gene_mount_writes_literature_under_knowledge() -> None:
    """Mount writes ``knowledge_context.literature`` from retrieved_docs."""
    fake_app = _build_fake_brief_gene_app()
    mount = make_brief_gene_mount_node(fake_app)
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    literature = delta["knowledge_context"]["literature"]
    assert literature == [{"title": "doc1"}, {"title": "doc2"}]


async def test_brief_gene_mount_consumes_preamble_verbatim() -> None:
    """Mount consumes brief_gene's preamble verbatim, swapping only the title.

    deep_genome uses the brief_gene preamble (everything from
    ``## Gene Profiles`` down) byte-identically; only the H1 title is
    rewritten from "Brief Gene Analysis" to "Deep Genome Analysis". The
    dead section / introduction / homology projections are no longer
    emitted — the preamble carries that content.
    """
    content = (
        "# Brief Gene Analysis of AT1G01010\n\n"
        "Intro paragraph.\n\n"
        "## Gene Profiles\n\n### Basic Genomic Information\n\nbody"
    )
    canned = {
        "gene_id": "AT1G01010",
        "final_response": {"choices": [{"message": {"content": content}}]},
    }
    fake_app = _build_fake_brief_gene_app(output=canned)
    mount = make_brief_gene_mount_node(fake_app)

    delta = await mount(cast(Any, _deep_genome_state()))

    assert delta["preamble"] == (
        "# Deep Genome Analysis of AT1G01010\n\n"
        "Intro paragraph.\n\n"
        "## Gene Profiles\n\n### Basic Genomic Information\n\nbody"
    )
    for dead in (
        "section1_markdown",
        "introduction_report",
        "orthologs_data",
        "paralogs_data",
        "interaction_data",
    ):
        assert dead not in delta


async def test_brief_gene_mount_writes_experiment_branch_counter() -> None:
    """Mount writes ``experiment_completed_branches: 1``.

    M11 — the legacy ``part1_node`` LLM aggregator that previously
    wrote this +1 (after waiting for 4 preamble branches) is
    deleted; the mount substitutes for it and contributes the +1
    that experiment_node's 2-source barrier needs (the other +1
    comes from synthesize_node on the analyst side).
    """
    fake_app = _build_fake_brief_gene_app()
    mount = make_brief_gene_mount_node(fake_app)
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    assert delta["experiment_completed_branches"] == 1
    # M5-era part1_completed_branches no longer projected
    assert "part1_completed_branches" not in delta
    # M5 brief_response prefix path removed
    assert "brief_response" not in delta


async def test_brief_gene_mount_runs_post_projection_persistence_hook() -> (
    None
):
    """The owner hook receives the projected delta after a successful mount."""
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def persist(
        projected: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        seen.append((projected, state))
        return projected

    mount = make_brief_gene_mount_node(
        _build_fake_brief_gene_app(),
        persist,
    )
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    assert seen == [(delta, state)]


# ---------------------------------------------------------------------------
# Input synthesis: gene_id → BriefGeneInput user_query.
# ---------------------------------------------------------------------------


async def test_brief_gene_mount_synthesises_user_query_from_gene_id() -> None:
    """Mount feeds brief_gene a synthetic ``user_query=gene_id``.

    Captures the BriefGeneInput observed by the fake app to verify
    the mount node synthesises ``user_query=gene_id`` (so
    brief_gene's ``query_judge_node`` BI-resolves back to the same
    canonical gene_id) and pins ``is_follow_up=False`` so brief_gene
    skips its trailing follow_up_questions LLM hop.
    """
    seen: list[dict[str, Any]] = []

    async def _capture_stub(state: _FakeBriefGeneState) -> dict[str, Any]:
        seen.append(dict(state))
        return {
            "gene_id": "AT1G01010",
            "go_string": "",
            "kegg_string": "",
            "interpro_string": "",
            "description_string": "",
            "retrieved_docs": [],
        }

    workflow: StateGraph = StateGraph(_FakeBriefGeneState)
    workflow.add_node("capture", _capture_stub)
    workflow.add_edge(START, "capture")
    workflow.add_edge("capture", END)
    fake_app = workflow.compile()
    mount = make_brief_gene_mount_node(fake_app)

    await mount(cast(Any, _deep_genome_state(gene_id="AT3G18780")))

    assert len(seen) == 1
    captured = seen[0]
    assert captured["user_query"] == "AT3G18780"
    assert captured["is_follow_up"] is False


# ---------------------------------------------------------------------------
# Required branch: a profile failure aborts before downstream work starts.
# ---------------------------------------------------------------------------


async def test_brief_gene_mount_raises_required_error_on_failure() -> None:
    """Mount raises a fixed public error instead of a degraded fallback.

    If brief_gene fails (network, BI outage, model error), the
    mount node MUST fail the required branch. The fixed public message
    is stable and does not copy an upstream exception into graph state.
    """

    broken_app = failing_async_object(
        "ainvoke", RuntimeError("brief_gene exploded")
    )
    mount = make_brief_gene_mount_node(cast(CompiledStateGraph, broken_app))
    state = _deep_genome_state(gene_id="AT1G01010")

    with pytest.raises(
        RequiredBriefGeneError, match="^brief gene profile failed$"
    ):
        await mount(cast(Any, state))


async def test_brief_gene_failure_does_not_invoke_downstream_submit() -> None:
    """A failed required mount prevents the downstream submit node."""
    submits = AsyncMock()

    async def _remote_submit(_state: Any) -> dict[str, Any]:
        await submits()
        return {}

    broken_app = failing_async_object(
        "ainvoke", RuntimeError("brief_gene exploded")
    )

    workflow: StateGraph = StateGraph(_FakeBriefGeneState)
    workflow.add_node(
        "brief", make_brief_gene_mount_node(cast(Any, broken_app))
    )
    workflow.add_node("remote", cast(Any, _remote_submit))
    workflow.add_edge(START, "brief")
    workflow.add_edge("brief", "remote")
    workflow.add_edge("remote", END)
    app = workflow.compile()

    with pytest.raises(RequiredBriefGeneError):
        await app.ainvoke(cast(Any, _deep_genome_state()))
    submits.assert_not_awaited()


async def test_brief_gene_mount_success_emits_no_failures() -> None:
    """A successful mount writes no ``failures`` and no banner.

    Guards against a false-positive degraded signal: the happy path must
    leave the ``failures`` channel untouched so a healthy run never
    surfaces ``degraded: true``.
    """
    fake_app = _build_fake_brief_gene_app()
    mount = make_brief_gene_mount_node(fake_app)

    delta = await mount(cast(Any, _deep_genome_state()))

    assert "failures" not in delta or delta["failures"] == []
    assert "Gene profile unavailable" not in delta["preamble"]


async def test_mount_success_rolls_up_literature_degraded() -> None:
    """The mount carries brief_gene's literature_degraded onto deep_genome.

    A successful brief_gene mount can still surface recoverable
    per-symbol literature degradations on its ``literature_degraded``
    channel; the mount must roll those onto deep_genome state so the
    final report node can persist the status-independent degraded signal.
    """
    canned = {
        "gene_id": "AT1G01010",
        "go_string": "",
        "kegg_string": "",
        "interpro_string": "",
        "description_string": "",
        "retrieved_docs": [],
        "literature_degraded": [{"task_label": "OsCAB1", "message": "boom"}],
    }
    mount = make_brief_gene_mount_node(
        _build_fake_brief_gene_app(output=canned)
    )

    delta = await mount(cast(Any, _deep_genome_state()))

    assert delta["literature_degraded"] == [
        {"task_label": "OsCAB1", "message": "boom"}
    ]


# ---------------------------------------------------------------------------
# Empty brief_gene_output: missing keys default rather than crash.
# ---------------------------------------------------------------------------


async def test_brief_gene_mount_handles_partial_brief_gene_output() -> None:
    """Mount tolerates a brief_gene output missing optional keys.

    Real BriefGeneOutput always populates every field, but a
    degraded brief_gene run (e.g. BI returned no annotation rows) may
    write empty strings for the annotation fields. The mount must
    pass these through without crashing or coercing to ``None``.
    """
    fake_app = _build_fake_brief_gene_app(
        output={
            "gene_id": "AT1G01010",
            "go_string": "",
            "kegg_string": "",
            "interpro_string": "",
            "description_string": "",
            "retrieved_docs": [],
        }
    )
    mount = make_brief_gene_mount_node(fake_app)
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    annotation = delta["gene_annotation"]
    assert annotation["gene_string"] == "AT1G01010"
    assert all(
        annotation[key] == ""
        for key in ("description", "go", "interpro", "mapman")
    )
    assert delta["knowledge_context"]["literature"] == []
    assert delta["experiment_completed_branches"] == 1
