# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the ``_stream_graph_agent`` graph astream primitive.

Drives ``_stream_graph_agent`` with a fake compiled-graph ``astream``
(no real LangGraph, no real LLM) and pins the deduped stage-event
sequence: ``RunStarted`` -> whitelisted, deduped ``StepStarted``
frames -> terminal ``TextMessage``/``Custom`` projection ->
``RunFinished``.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Mapping, Tuple

import httpx
import pytest

from mcp_server_phytomni.mcp import app as mcp_app
from mcp_server_phytomni.mcp.app import _stream_graph_agent

pytestmark = pytest.mark.agent


@pytest.fixture(autouse=True)
def _fail_fast_on_network_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Convert any un-mocked outbound call into an instant named failure.

    ``_stream_graph_agent`` is driven with a fake ``astream`` here, so
    no real graph or network call should ever fire. The autouse
    ``block_external_http`` fixture (repo root ``conftest.py``) covers
    only ``socket.create_connection`` (sync) and ``httpx.*.request``,
    so THREE async paths could still reach a real socket and hang a
    test: ``httpx.*.send`` (the low-level send under ``request``),
    the event loop's ``create_connection``, and DNS via
    ``getaddrinfo``. Patch all three to raise so a future regression
    that lets the primitive reach outward surfaces as a named error
    instead of a hang, mirroring
    ``tests/agents/test_brief_gene_preamble_workflow.py``.
    """

    def _blocked_http(_self: Any, request: Any, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError(
            "offline stream test escaped to a live HTTP call "
            f"({request.method} {request.url}); a mock is missing"
        )

    def _blocked_connect(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(
            "offline stream test escaped to a raw async socket "
            "(loop.create_connection); a mock is missing"
        )

    def _blocked_dns(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(
            "offline stream test escaped to DNS resolution "
            "(loop.getaddrinfo); a mock is missing"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_http)
    monkeypatch.setattr(httpx.Client, "send", _blocked_http)
    monkeypatch.setattr(
        asyncio.base_events.BaseEventLoop,
        "create_connection",
        _blocked_connect,
    )
    monkeypatch.setattr(
        asyncio.base_events.BaseEventLoop, "getaddrinfo", _blocked_dns
    )


class FakeKnowledgeStreamApp:
    """Fake compiled graph yielding canned ``(mode, chunk)`` tuples.

    Mirrors the real LangGraph ``Pregel.astream`` yield shape for a
    list ``stream_mode`` (``(mode, payload)`` per
    ``langgraph.pregel.main``): two ``updates`` chunks map to the
    ``KnowledgeAgent`` whitelist (``retrieve_node`` ->
    ``retrieving``, ``generate_post_node`` -> ``generating``), one
    ``updates`` chunk repeats ``retrieve_node`` to prove dedup, one
    ``updates`` chunk names an off-whitelist worker node that must
    produce no ``StepStarted``, and a final ``values`` chunk carries
    the graph's terminal state.
    """

    async def astream(
        self, _state: Mapping[str, Any], stream_mode: list[str]
    ) -> AsyncIterator[Tuple[str, Dict[str, Any]]]:
        """Yield canned stage updates then one terminal values chunk."""
        assert stream_mode == ["updates", "values"]
        yield ("updates", {"retrieve_node": {}})
        yield ("updates", {"retrieve_node": {}})  # repeat: must not re-emit
        yield ("updates", {"retrieve_worker_node": {}})  # off-whitelist
        yield ("updates", {"generate_post_node": {}})
        yield (
            "values",
            {
                "final_response": {
                    "choices": [
                        {"message": {"content": "answer", "doc_list": []}}
                    ]
                }
            },
        )


async def test_graph_stream_dedups_stage_events() -> None:
    """RunStarted -> deduped, whitelisted StepStarted(s) -> RunFinished.

    Pins three things at once: the dedup (a repeated ``retrieve_node``
    update yields only one ``retrieving`` StepStarted), the whitelist
    (the off-whitelist ``retrieve_worker_node`` yields none), and the
    envelope ordering (``RunStarted`` first, ``RunFinished`` last).
    """
    events = [
        e
        async for e in _stream_graph_agent(
            FakeKnowledgeStreamApp(),
            {},
            "KnowledgeAgent",
            "KnowledgeAgent",
            run_id="r",
            dialogue_id="d",
        )
    ]

    assert events[0].type == "RunStarted"
    assert events[-1].type == "RunFinished"
    step_names = [
        e.data["step_name"] for e in events if e.type == "StepStarted"
    ]
    assert step_names == ["retrieving", "generating"]


async def test_graph_stream_carries_run_and_dialogue_ids() -> None:
    """RunStarted/RunFinished carry the caller's run_id and dialogue_id."""
    events = [
        e
        async for e in _stream_graph_agent(
            FakeKnowledgeStreamApp(),
            {},
            "KnowledgeAgent",
            "KnowledgeAgent",
            run_id="run-42",
            dialogue_id="dlg-7",
        )
    ]

    run_started_event = events[0]
    assert run_started_event.data["run_id"] == "run-42"
    assert run_started_event.data["dialogue_id"] == "dlg-7"
    run_finished_event = events[-1]
    assert run_finished_event.data["run_id"] == "run-42"


async def test_graph_stream_off_whitelist_agent_emits_no_step_started() -> (
    None
):
    """An agent name absent from the phase-map whitelist yields no steps.

    ``phase_for`` returns ``None`` for every node under an unmapped
    agent, so the stage loop must fold away entirely while the
    envelope (``RunStarted`` .. ``RunFinished``) still emits.
    """

    class FakeChatLikeApp:
        """Fake app streaming the same nodes under an unmapped agent."""

        async def astream(
            self, _state: Mapping[str, Any], stream_mode: list[str]
        ) -> AsyncIterator[Tuple[str, Dict[str, Any]]]:
            """Yield the same node names, now under an unmapped agent."""
            assert stream_mode == ["updates", "values"]
            yield ("updates", {"retrieve_node": {}})
            yield ("updates", {"generate_post_node": {}})
            yield ("values", {"final_response": {}})

    events = [
        e
        async for e in _stream_graph_agent(
            FakeChatLikeApp(),
            {},
            "ChatAgent",  # not in streaming_phases._PHASE_MAP
            "ChatAgent",
            run_id="r",
            dialogue_id=None,
        )
    ]

    types = [e.type for e in events]
    assert types == ["RunStarted", "RunFinished"]


async def test_terminal_events_carry_answer_and_custom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal projection emits one-shot TextMessage + Custom frames.

    ``tool_name="KnowledgeAgent"`` is a cited tool, so the real
    enrichment path (``_maybe_enrich_cited`` -> ``enrich_cited_doc_list``
    -> ``bi_query``) would otherwise fire a live BI call; stub it so the
    test stays offline while still exercising the envelope projection.
    """

    async def _no_enrich(_tool_name: str, _raw: Any) -> None:
        """Skip bibliographic enrichment in the offline test."""

    monkeypatch.setattr(mcp_app, "_maybe_enrich_cited", _no_enrich)

    class FakeApp:
        """Fake compiled graph yielding one stage update, one terminal."""

        async def astream(
            self, _state: Mapping[str, Any], stream_mode: list[str]
        ) -> AsyncIterator[Tuple[str, Dict[str, Any]]]:
            """Yield one stage update then a terminal ``values`` chunk."""
            assert stream_mode == ["updates", "values"]
            yield ("updates", {"retrieve_node": {}})
            yield (
                "values",
                {
                    "final_response": {
                        "choices": [
                            {
                                "message": {
                                    "content": "Rice [1].",
                                    "doc_list": [
                                        {"file_id": "f1", "title": "T1"}
                                    ],
                                    "follow_up_questions": ["next?"],
                                }
                            }
                        ]
                    }
                },
            )

    events = [
        e
        async for e in _stream_graph_agent(
            FakeApp(),
            {},
            "KnowledgeAgent",
            "KnowledgeAgent",
            run_id="r",
            dialogue_id="d",
        )
    ]
    types = [e.type for e in events]
    assert "TextMessageStart" in types
    tmc = [e for e in events if e.type == "TextMessageContent"]
    assert len(tmc) == 1  # one-shot, not sliced (spec D4)
    assert "Rice" in tmc[0].data["delta"]
    customs = {
        e.data["name"]: e.data["value"] for e in events if e.type == "Custom"
    }
    assert "phyto.references" in customs
    assert customs["phyto.follow_up"] == ["next?"]
    # ordering: RunFinished is last
    assert types[-1] == "RunFinished"
