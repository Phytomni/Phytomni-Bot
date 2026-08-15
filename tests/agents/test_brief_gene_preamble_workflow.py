# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""End-to-end fan-in safety tests for the brief_gene preamble graph.

Drives the compiled graph with mocked BI / section LLM calls under an
asyncio timeout to prove the section fan-in resolves on BOTH the
gene-found path (parallel annotation/homology + retrieve) and the
gene-not-found path (homology short-circuits, sections run literature
only) without deadlocking, and that the render writes a full preamble.
"""

from __future__ import annotations

import asyncio
import faulthandler
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.brief_gene.core import (
    BriefGeneAgent,
    initial_brief_gene_state,
)
from mcp_server_phytomni.agents.brief_gene.state import BriefGeneInput
from mcp_server_phytomni.config.defaults import BriefGeneConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._network_escape import install_network_escape_guard
from ._subgraph_branch_fakes import install_chat_subgraph_mocks

pytestmark = pytest.mark.agent

_FOUND_ROW = {
    "message": "ok",
    "data": [
        {
            "gene_id": "Os01g0177400",
            "species_code": "osa",
            "id_type": "rap",
            "symbol": "OsCAB1",
            "chromosome": "1",
            "start": "1",
            "end": "9",
            "strand": "+",
            "description": "stub",
        }
    ],
}
_EMPTY_ROW = {"message": "ok", "data": []}
_CHAT_STUB = {"choices": [{"message": {"content": "stub section/intro"}}]}
_KNOWLEDGE_OUTPUT = {
    "retrieved_docs": [
        {
            "chunk_id": "1",
            "score": 0.9,
            "title": "stub literature",
            "content": "stub literature chunk",
        },
    ],
    "retrieval_outcome": "complete",
    "final_response": {},
}
_ANNOTATION_TABLE_MARKERS = (
    "FROM id_table ",
    "FROM annotation_gene_structure_col ",
    "FROM annotation_gene_ontology ",
    "FROM annotation_gene_mapman ",
    "FROM annotation_gene_interpro ",
    "FROM annotation_gene_description ",
)


async def _stub_knowledge_ainvoke(
    _knowledge_input: Any, *_args: Any, **_kwargs: Any
) -> dict[str, Any]:
    """Return a canned ``KnowledgeOutput``-shaped doc list."""
    return _KNOWLEDGE_OUTPUT


def _install_mocks(
    monkeypatch: pytest.MonkeyPatch, *, bi_response: dict[str, Any]
) -> AsyncMock:
    """Patch every external call the preamble graph makes.

    Covers the BI annotation / homology lookups, the section + intro
    chat completions, AND the mounted knowledge subgraph (the retrieve
    worker's ``knowledge_app.ainvoke``) so no real HTTP / relay / cache
    call can escape into the fan-in under test.
    """
    bi_mock = AsyncMock(return_value=bi_response)
    chat_mock = AsyncMock(return_value=_CHAT_STUB)
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.build_knowledge_app",
        lambda *_args, **_kwargs: SimpleNamespace(
            ainvoke=_stub_knowledge_ainvoke
        ),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api", bi_mock
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.homology.run_bi_api",
        AsyncMock(return_value=_EMPTY_ROW),
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.analytical_sections."
        "phyto_chat",
        chat_mock,
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.introduction.phyto_chat",
        chat_mock,
    )
    return chat_mock


def _install_annotation_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_indices: frozenset[int] = frozenset(),
    empty_indices: frozenset[int] = frozenset(),
) -> None:
    """Keep identity reliable while controlling six annotation outcomes."""

    async def run_bi_api(query_sql: str, **_kwargs: Any) -> dict[str, Any]:
        for index, marker in enumerate(_ANNOTATION_TABLE_MARKERS):
            if marker not in query_sql:
                continue
            if index in failed_indices:
                raise RuntimeError("private annotation failure")
            if index in empty_indices:
                return _EMPTY_ROW
            return _FOUND_ROW
        return _FOUND_ROW

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api",
        run_bi_api,
    )


def _install_knowledge_outcome(
    monkeypatch: pytest.MonkeyPatch,
    responder: Any,
) -> None:
    """Install one bounded Knowledge subgraph responder before agent build."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.build_knowledge_app",
        lambda *_args, **_kwargs: SimpleNamespace(ainvoke=responder),
    )


@pytest.fixture(autouse=True)
def _fail_fast_on_network_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Convert any un-mocked outbound call into an instant named failure.

    The shared ``install_network_escape_guard`` patches the three async
    paths the repo-root ``block_external_http`` fixture leaves open
    (``httpx.*.send``, the loop's ``create_connection``, and
    ``getaddrinfo``); see ``tests/agents/_network_escape.py`` for the
    full rationale.
    """
    install_network_escape_guard(monkeypatch, label="preamble")


@pytest.fixture(autouse=True)
def _dump_stack_if_hung() -> Iterator[None]:
    """Auto-capture the blocked frame if the compiled graph wedges.

    This graph passes on this machine across every dependency resolve
    tried (langgraph 1.2.0 and 1.2.6, anyio 4.13/4.14, default loop and
    uvloop) and 40 cold-cache reruns, yet independent audits reproduce a
    hang their environment alone exhibits. ``faulthandler`` arms a
    watchdog *thread* that dumps every thread's stack after 12s — chosen
    to fire WHILE the ``await`` is still blocked, INSIDE this test's own
    20s ``wait_for`` (the prior 25s fired only after ``wait_for`` had
    already raised ``TimeoutError`` and teardown cancelled it, so it never
    captured anything). The watchdog runs off the event loop, so it fires
    even when a synchronous call wedges the loop (which ``wait_for`` cannot
    then cancel). A fast pass cancels it well before 12s so it never
    bleeds into a neighbouring test.
    """
    faulthandler.dump_traceback_later(12, exit=False)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


async def _invoke_agent_preamble(
    agent: BriefGeneAgent,
    user_query: str,
    *,
    thread_id: str,
) -> dict[str, Any]:
    """Invoke one fully seeded preamble run on a selected graph thread."""
    initial_state = initial_brief_gene_state(user_query)
    initial_state["is_follow_up"] = False
    return await asyncio.wait_for(
        agent.app.ainvoke(
            cast(BriefGeneInput, initial_state),
            config={"configurable": {"thread_id": thread_id}},
        ),
        timeout=20,
    )


async def _invoke_preamble(user_query: str) -> dict[str, Any]:
    """Invoke the compiled graph with the follow-up tail disabled."""
    agent = BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    return await _invoke_agent_preamble(
        agent,
        user_query,
        thread_id="preamble-test",
    )


async def _run_preamble(user_query: str) -> str:
    """Invoke the compiled graph (no follow-up) and return the content."""
    final_state = await _invoke_preamble(user_query)
    return final_state["final_response"]["choices"][0]["message"]["content"]


async def test_preamble_gene_found_fan_in_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gene_found path: parallel fetch + 4-section fan-in reaches render.

    The four section nodes gate on retrieve_reduce while fetch_homology
    runs in parallel off query_judge; this asserts the fan-in resolves
    (no deadlock) and the render writes the full preamble skeleton.
    """
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)

    content = await _run_preamble("Os01g0177400")

    assert content.startswith("# Brief Gene Analysis of")
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content


async def test_preamble_knowledge_exception_keeps_report_copy_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising knowledge subgraph degrades the run without warning copy.

    Drives the FULL compiled graph with a knowledge mount whose
    ``ainvoke`` raises, so every per-symbol ``retrieve_worker_node``
    hits its broad ``except``, records the failed index, and appends an
    internal ``literature_degraded`` record. The compiled graph must still
    reach ``render_node`` without exposing a source warning in the report.
    """
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    # Re-point the knowledge mount built in ``BriefGeneAgent.__init__``
    # at a stand-in whose ``ainvoke`` raises, so every per-symbol
    # retrieve worker hits its broad except (overrides the canned stub
    # installed above; the later setattr wins).
    raising_app = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=RuntimeError("knowledge unavailable"))
    )
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.build_knowledge_app",
        lambda *_args, **_kwargs: raising_app,
    )

    content = await _run_preamble("Os01g0177400")

    assert content.startswith("# Brief Gene Analysis of")
    assert "⚠️" not in content
    # Degraded is status-independent: the full preamble skeleton still
    # renders rather than collapsing to a failure note.
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content


async def test_preamble_valid_literature_no_match_uses_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid empty literature result still renders annotation evidence."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    no_match = {
        **_KNOWLEDGE_OUTPUT,
        "retrieved_docs": [],
        "retrieval_outcome": "no_match",
    }
    _install_knowledge_outcome(
        monkeypatch,
        AsyncMock(return_value=no_match),
    )

    final_state = await _invoke_preamble("Os01g0177400")
    content = final_state["final_response"]["choices"][0]["message"]["content"]

    assert final_state["retrieved_docs"] == []
    assert "### Basic Genomic Information" in content
    assert "Literature retrieval" not in content


@pytest.mark.parametrize(
    "identity_response",
    [
        {"message": "error", "data": []},
        {"message": "ok"},
        {"message": "ok", "data": {}},
        {"message": "ok", "data": ["private identity body"]},
        {"message": "ok", "data": [{}]},
        {"message": "ok", "data": [{"gene_id": "Os01g0177400"}]},
        {"message": "ok", "data": [{"species_code": "osa"}]},
    ],
)
async def test_preamble_rejects_malformed_primary_identity_response(
    monkeypatch: pytest.MonkeyPatch,
    identity_response: dict[str, Any],
) -> None:
    """Identity protocol faults cannot masquerade as a valid no-match."""
    _install_mocks(monkeypatch, bi_response=identity_response)
    knowledge = AsyncMock(return_value=_KNOWLEDGE_OUTPUT)
    _install_knowledge_outcome(monkeypatch, knowledge)

    with pytest.raises(
        McpError, match="^Knowledge retrieval temporarily unavailable"
    ):
        await _invoke_preamble("Os01g0177400")

    knowledge.assert_not_awaited()


async def test_preamble_sanitizes_identity_lookup_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream identity exception becomes one fixed terminal error."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)

    async def fail_identity(_query_sql: str, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("private identity failure")

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api",
        fail_identity,
    )
    knowledge = AsyncMock(return_value=_KNOWLEDGE_OUTPUT)
    _install_knowledge_outcome(monkeypatch, knowledge)

    with pytest.raises(
        McpError, match="^Knowledge retrieval temporarily unavailable"
    ) as exc_info:
        await _invoke_preamble("Os01g0177400")

    assert "private identity failure" not in str(exc_info.value)
    knowledge.assert_not_awaited()


async def test_preamble_propagates_identity_lookup_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity lookup cancellation crosses the graph boundary unchanged."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)

    async def cancel_identity(
        _query_sql: str, **_kwargs: Any
    ) -> dict[str, Any]:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api",
        cancel_identity,
    )
    knowledge = AsyncMock(return_value=_KNOWLEDGE_OUTPUT)
    _install_knowledge_outcome(monkeypatch, knowledge)

    with pytest.raises(asyncio.CancelledError):
        await _invoke_preamble("Os01g0177400")

    knowledge.assert_not_awaited()


@pytest.mark.parametrize("malformed_lookup", ["aliases", "species"])
async def test_preamble_rejects_malformed_secondary_identity_response(
    monkeypatch: pytest.MonkeyPatch,
    malformed_lookup: str,
) -> None:
    """Malformed canonical-id or species payloads stop before retrieval."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    id_lookup_count = 0

    async def run_bi_api(query_sql: str, **_kwargs: Any) -> dict[str, Any]:
        nonlocal id_lookup_count
        if "FROM id2multispecies" in query_sql:
            id_lookup_count += 1
            if malformed_lookup == "aliases" and id_lookup_count == 2:
                return {"message": "error", "data": []}
            return _FOUND_ROW
        if "FROM species" in query_sql and malformed_lookup == "species":
            return {"message": "error", "data": []}
        return _FOUND_ROW

    monkeypatch.setattr(
        "mcp_server_phytomni.agents.brief_gene.core.run_bi_api",
        run_bi_api,
    )
    knowledge = AsyncMock(return_value=_KNOWLEDGE_OUTPUT)
    _install_knowledge_outcome(monkeypatch, knowledge)

    with pytest.raises(
        McpError, match="^Knowledge retrieval temporarily unavailable"
    ):
        await _invoke_preamble("Os01g0177400")

    knowledge.assert_not_awaited()


async def test_preamble_partial_literature_and_annotation_failures_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reliable docs and annotation tables survive independent failures."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    _install_annotation_outcomes(
        monkeypatch,
        failed_indices=frozenset({2, 4}),
    )

    async def partial_knowledge(
        knowledge_input: dict[str, Any], *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        if str(knowledge_input.get("user_query", "")).endswith("OsCAB1"):
            raise RuntimeError("private literature failure")
        return _KNOWLEDGE_OUTPUT

    _install_knowledge_outcome(monkeypatch, partial_knowledge)

    final_state = await _invoke_preamble("Os01g0177400")
    content = final_state["final_response"]["choices"][0]["message"]["content"]

    assert final_state["retrieved_docs"]
    assert final_state["literature_degraded"]
    assert "Literature retrieval" not in content
    assert "private" not in content


async def test_preamble_partial_knowledge_output_marks_internal_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Knowledge partial-with-docs renders normally with an internal marker."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    partial_output = {
        **_KNOWLEDGE_OUTPUT,
        "retrieval_outcome": "partial",
    }
    _install_knowledge_outcome(
        monkeypatch,
        AsyncMock(return_value=partial_output),
    )

    final_state = await _invoke_preamble("Os01g0177400")
    content = final_state["final_response"]["choices"][0]["message"]["content"]

    assert final_state["retrieved_docs"]
    assert len(final_state["literature_degraded"]) == 3
    assert "Literature retrieval" not in content
    assert "partial source" not in content.lower()


async def test_preamble_literature_failure_with_valid_empty_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reliable gene identity permits a sparse profile from valid absences."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    _install_annotation_outcomes(
        monkeypatch,
        empty_indices=frozenset(range(6)),
    )
    _install_knowledge_outcome(
        monkeypatch,
        AsyncMock(side_effect=RuntimeError("private literature failure")),
    )

    final_state = await _invoke_preamble("Os01g0177400")
    content = final_state["final_response"]["choices"][0]["message"]["content"]

    assert final_state["retrieved_docs"] == []
    assert final_state["literature_degraded"]
    assert "### Basic Genomic Information" in content
    assert "Literature retrieval" not in content


async def test_preamble_all_annotation_failures_keep_literature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reliable literature is sufficient when every annotation table fails."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    _install_annotation_outcomes(
        monkeypatch,
        failed_indices=frozenset(range(6)),
    )

    final_state = await _invoke_preamble("Os01g0177400")
    content = final_state["final_response"]["choices"][0]["message"]["content"]

    assert final_state["retrieved_docs"]
    assert "Literature retrieval" not in content


async def test_preamble_fails_when_all_independent_evidence_calls_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Total literature and annotation failure stops before report drafting."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    _install_annotation_outcomes(
        monkeypatch,
        failed_indices=frozenset(range(6)),
    )
    _install_knowledge_outcome(
        monkeypatch,
        AsyncMock(side_effect=RuntimeError("private literature failure")),
    )

    with pytest.raises(
        McpError, match="^Knowledge retrieval temporarily unavailable"
    ):
        await _invoke_preamble("Os01g0177400")


async def test_preamble_propagates_literature_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation exits the compiled graph without producing a report."""
    chat_mock = _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    _install_knowledge_outcome(
        monkeypatch,
        AsyncMock(side_effect=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await _invoke_preamble("Os01g0177400")
    chat_mock.assert_not_awaited()


async def test_preamble_same_thread_successful_refresh_resets_retrieve_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful refresh cannot inherit prior fan-out classifications."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    agent = BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    thread_id = "same-thread-success-refresh"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    first = await _invoke_agent_preamble(
        agent,
        "Os01g0177400",
        thread_id=thread_id,
    )
    first_snapshot = await agent.app.aget_state(config)
    second = await _invoke_agent_preamble(
        agent,
        "Os01g0177400",
        thread_id=thread_id,
    )
    second_snapshot = await agent.app.aget_state(config)

    assert first["retrieved_docs"]
    assert second["retrieved_docs"] == first["retrieved_docs"]
    assert second["literature_degraded"] == []
    assert first_snapshot.values["gene_profile_completed_branches"] == 4
    assert second_snapshot.values["gene_profile_completed_branches"] == 4
    assert sorted(
        index
        for index, _docs in second_snapshot.values["retrieve_indexed_results"]
    ) == [0, 1, 2]
    assert second_snapshot.values["retrieve_failed_indices"] == []
    assert second_snapshot.values["retrieve_cancelled_indices"] == []
    assert second_snapshot.values["literature_degraded"] == []
    assert "retrieve_indexed_results" not in second
    assert "retrieve_failed_indices" not in second
    assert "retrieve_cancelled_indices" not in second


async def test_preamble_same_thread_retry_after_cancellation_resets_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled attempt leaves no private marker on its successful retry."""
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    calls = 0

    async def cancel_once(
        _knowledge_input: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return _KNOWLEDGE_OUTPUT

    _install_knowledge_outcome(monkeypatch, cancel_once)
    agent = BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    thread_id = "same-thread-cancel-retry"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    with pytest.raises(asyncio.CancelledError):
        await _invoke_agent_preamble(
            agent,
            "Os01g0177400",
            thread_id=thread_id,
        )
    recovered = await _invoke_agent_preamble(
        agent,
        "Os01g0177400",
        thread_id=thread_id,
    )
    recovered_snapshot = await agent.app.aget_state(config)

    assert recovered["retrieved_docs"]
    assert recovered["literature_degraded"] == []
    assert sorted(
        index
        for index, _docs in recovered_snapshot.values[
            "retrieve_indexed_results"
        ]
    ) == [0, 1, 2]
    assert recovered_snapshot.values["retrieve_failed_indices"] == []
    assert recovered_snapshot.values["retrieve_cancelled_indices"] == []
    assert recovered_snapshot.values["literature_degraded"] == []
    assert recovered_snapshot.values["gene_profile_completed_branches"] == 4
    assert "retrieve_indexed_results" not in recovered
    assert "retrieve_failed_indices" not in recovered
    assert "retrieve_cancelled_indices" not in recovered


async def test_preamble_gene_not_found_fan_in_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gene_not_found path: homology short-circuits, fan-in still resolves.

    With no resolved gene the homology node returns empty immediately,
    yet it still runs (static edge off query_judge) so the section
    fan-in never waits on a branch that did not fire — the graph must
    still reach the render rather than deadlock.
    """
    _install_mocks(monkeypatch, bi_response=_EMPTY_ROW)

    content = await _run_preamble("an unresolvable free-form query")

    assert content.startswith("# Brief Gene Analysis of")
    assert "## Gene Profiles" in content
    assert "### Basic Genomic Information" in content


async def test_preamble_follow_up_tail_fires_render_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_follow_up=True must not collide on the final_response channel.

    The four sections gate on retrieve_reduce (a deep superstep) while
    fetch_homology settles in a shallow one. A homology->section edge
    would fire the fan-in once per superstep, running every section and
    render twice; with the follow-up tail active a wave-2 render then
    collides with the wave-1 follow_up_post on the no-reducer
    ``final_response`` channel, raising ``InvalidUpdateError``. This
    drives the FULL graph with the follow-up tail — the arun default
    that the is_follow_up=False fan-in tests never exercise — so the
    single-fire fan-in is pinned end to end.
    """
    _install_mocks(monkeypatch, bi_response=_FOUND_ROW)
    install_chat_subgraph_mocks(
        monkeypatch,
        "mcp_server_phytomni.agents.brief_gene.core",
        legacy_response=_CHAT_STUB,
        subgraph_response=_CHAT_STUB,
    )
    agent = BriefGeneAgent(
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )

    final_state = await asyncio.wait_for(
        agent.app.ainvoke(
            {"user_query": "Os01g0177400", "is_follow_up": True},
            config={"configurable": {"thread_id": "preamble-followup"}},
        ),
        timeout=20,
    )

    content = final_state["final_response"]["choices"][0]["message"]["content"]
    assert content.startswith("# Brief Gene Analysis of")
    assert "## Gene Profiles" in content
