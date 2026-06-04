# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the brief_gene subgraph mount adapter on DeepGenomeAgents.

Covers ``make_brief_gene_mount_node`` IO projection from
BriefGeneOutput's flat-string annotation surface into deep_genome's
nested ``gene_annotation`` + ``knowledge_context`` shape, the
``user_query`` synthesis from ``gene_id``, the ``is_follow_up=False``
opt-out passed to brief_gene, and the exception fallback that lets
deep_genome advance past the part1 barrier on brief_gene faults.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome.brief_gene_mount import (
    make_brief_gene_mount_node,
)

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


async def test_brief_gene_mount_projects_brief_response_for_report_nodes() -> (
    None
):
    """Mount writes brief_gene's final_response under ``brief_response``.

    ``_run_report_introduction`` and ``_run_report_summary`` read
    ``state["brief_response"]`` via ``message_content`` and prepend the
    extracted text to the content they send to the introduction /
    summary LLM templates. The mount node MUST project
    ``BriefGeneOutput.final_response`` into this state key so the
    report nodes see the brief gene answer brief_gene produced inside
    the mounted subgraph rather than degrading to an empty prefix.
    """
    fake_app = _build_fake_brief_gene_app()
    mount = make_brief_gene_mount_node(fake_app)
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    assert delta["brief_response"] == {
        "choices": [{"message": {"content": "ans"}}]
    }


async def test_brief_gene_mount_brief_response_defaults_to_empty_dict() -> (
    None
):
    """Missing ``final_response`` projects an empty dict rather than None.

    Keeps the DeepGenomeState ``brief_response: Optional[Dict[str, Any]]``
    contract honoured even when brief_gene returns a partial output
    without a final_response (degraded run); ``message_content`` returns
    "" for an empty dict so report nodes degrade gracefully.
    """
    fake_app = _build_fake_brief_gene_app(
        output={"gene_id": "AT1G01010", "retrieved_docs": []}
    )
    mount = make_brief_gene_mount_node(fake_app)
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    assert delta["brief_response"] == {}


async def test_brief_gene_mount_writes_part1_barrier_counter() -> None:
    """Mount writes ``part1_completed_branches: 1`` for the part1 barrier.

    Preserves the existing ``_route_part1_barrier`` topology: the
    barrier reducer accumulates completions across the gene
    annotation site and the data agent site; the mount node stands
    in for the gene annotation site so it MUST emit the +1 increment
    or the barrier will never fire.
    """
    fake_app = _build_fake_brief_gene_app()
    mount = make_brief_gene_mount_node(fake_app)
    state = _deep_genome_state()

    delta = await mount(cast(Any, state))

    assert delta["part1_completed_branches"] == 1


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
# Exception fallback: deep_genome still advances past the part1 barrier.
# ---------------------------------------------------------------------------


async def test_brief_gene_mount_fallback_on_brief_gene_exception() -> None:
    """Mount catches brief_gene failures and emits empty annotation + barrier.

    If brief_gene fails (network, BI outage, model error), the
    mount node MUST NOT propagate the exception — that would wedge
    the part1 barrier waiting forever. Instead the mount logs the
    exception, emits an empty annotation + empty literature delta,
    and still writes the barrier increment so the deep_genome
    workflow advances past the barrier and downstream report nodes
    see a degraded but well-formed state.
    """

    async def _raising_ainvoke(_input: Any) -> Any:
        raise RuntimeError("brief_gene exploded")

    class _BrokenApp:
        ainvoke = staticmethod(_raising_ainvoke)

    mount = make_brief_gene_mount_node(cast(CompiledStateGraph, _BrokenApp()))
    state = _deep_genome_state(gene_id="AT1G01010")

    delta = await mount(cast(Any, state))

    # Empty annotation but well-formed: every key present, all empty
    # strings rather than missing keys. The gene_string falls back to
    # the requested gene_id so downstream prompts still have a token
    # to display.
    annotation = delta["gene_annotation"]
    assert annotation["gene_string"] == "AT1G01010"
    assert annotation["description"] == ""
    assert annotation["go"] == ""
    assert annotation["interpro"] == ""
    assert annotation["mapman"] == ""
    assert delta["knowledge_context"]["literature"] == []
    # Barrier counter still emitted so the workflow advances.
    assert delta["part1_completed_branches"] == 1


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
    assert delta["part1_completed_branches"] == 1
