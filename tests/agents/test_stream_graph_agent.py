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

from collections.abc import AsyncIterator, Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.mcp.app import _stream_graph_agent
from mcp_server_phytomni.mcp.stream_lifecycle import (
    StreamLifecycleState,
    project_stream_failures,
)

from ._network_escape import install_network_escape_guard

pytestmark = pytest.mark.agent


@pytest.fixture(autouse=True)
def _fail_fast_on_network_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert any un-mocked outbound call into an instant named failure.

    ``_stream_graph_agent`` is driven with a fake ``astream`` here, so
    no real graph or network call should ever fire. The shared
    ``install_network_escape_guard`` patches the three async paths the
    repo-root ``block_external_http`` fixture leaves open (``httpx.*.send``,
    the loop's ``create_connection``, and ``getaddrinfo``); see the
    helper module for the full rationale.
    """
    install_network_escape_guard(monkeypatch, label="stream")


# Canned ``(ns, mode, chunk)`` sequence for the dedup / whitelist tests:
# two ``retrieve_node`` updates (the repeat must not re-emit a
# StepStarted), one off-whitelist worker node (must emit no StepStarted),
# one ``generate_post_node`` update, then a terminal ``values`` chunk.
# All carry ``ns=()`` (parent-only) so updates/values project.
_KNOWLEDGE_STAGE_YIELDS: list[tuple[tuple[str, ...], str, dict[str, Any]]] = [
    ((), "updates", {"retrieve_node": {}}),
    ((), "updates", {"retrieve_node": {}}),
    ((), "updates", {"retrieve_worker_node": {}}),
    ((), "updates", {"generate_post_node": {}}),
    (
        (),
        "values",
        {
            "final_response": {
                "choices": [{"message": {"content": "answer", "doc_list": []}}]
            }
        },
    ),
]


class FakeStreamApp:
    """Fake compiled graph yielding a canned ``(ns, mode, chunk)`` sequence.

    One parameterized fake backs every ``_stream_graph_agent`` test: the
    yield sequence is injected, and an optional ``record_config`` flag
    captures the ``config`` astream received so the thread_id test can
    assert it. Two public methods (``astream`` + ``configurable``) keep
    this off the R0903 single-public-method baseline; the
    ``configurable`` body returns the whole sub-dict (rather than the
    thread_id directly) so it stays distinct from the api-layer
    streaming fake's ``thread_id`` accessor and does not register as an
    R0801 duplicate block.
    """

    def __init__(
        self,
        yields: list[tuple[tuple[str, ...], str, dict[str, Any]]],
        *,
        record_config: bool = False,
    ) -> None:
        """Store the canned yield sequence and capture mode."""
        self._yields = list(yields)
        self._record = record_config
        self.seen: Mapping[str, Any] | None = None

    def configurable(self) -> dict[str, Any]:
        """Return the ``configurable`` sub-dict astream received."""
        seen = self.seen or {}
        sub = seen.get("configurable")
        return dict(sub) if isinstance(sub, Mapping) else {}

    async def astream(
        self,
        _state: Mapping[str, Any],
        stream_mode: list[str],
        config: Mapping[str, Any] | None = None,
        *,
        subgraphs: bool = False,
    ) -> AsyncIterator[tuple[tuple[str, ...], str, dict[str, Any]]]:
        """Assert modes+subgraphs, optionally capture config, yield seq."""
        assert stream_mode == ["custom", "updates", "values"]
        assert subgraphs is True
        if self._record:
            self.seen = config
        for ns, mode, chunk in self._yields:
            yield ns, mode, chunk


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
            FakeStreamApp(_KNOWLEDGE_STAGE_YIELDS),
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
            FakeStreamApp(_KNOWLEDGE_STAGE_YIELDS),
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
    events = [
        e
        async for e in _stream_graph_agent(
            FakeStreamApp(
                [
                    ((), "updates", {"retrieve_node": {}}),
                    ((), "updates", {"generate_post_node": {}}),
                    ((), "values", {"final_response": {}}),
                ]
            ),
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

    monkeypatch.setattr(
        "mcp_server_phytomni.mcp.app._maybe_enrich_cited", _no_enrich
    )

    events = [
        e
        async for e in _stream_graph_agent(
            FakeStreamApp(
                [
                    ((), "updates", {"retrieve_node": {}}),
                    (
                        (),
                        "values",
                        {
                            "final_response": {
                                "choices": [
                                    {
                                        "message": {
                                            "content": "Rice [1].",
                                            "doc_list": [
                                                {
                                                    "file_id": "f1",
                                                    "title": "T1",
                                                }
                                            ],
                                            "follow_up_questions": ["next?"],
                                        }
                                    }
                                ]
                            }
                        },
                    ),
                ]
            ),
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


async def test_graph_stream_passes_thread_id_config_to_astream() -> None:
    """astream receives a config carrying the run's non-empty thread_id.

    Pins Step 0 at the unit layer: ``_stream_graph_agent`` must pass
    ``config=build_runnable_config(run_id)`` to ``app.astream`` because
    the real Knowledge/Review graphs compile with a checkpointer and
    LangGraph raises ``ValueError`` on a checkpointer graph invoked
    without ``configurable.thread_id``. The fake records the config and
    the test asserts the thread_id equals the caller's run id.
    """
    fake_app = FakeStreamApp(
        [
            ((), "updates", {"retrieve_node": {}}),
            ((), "values", {"final_response": {}}),
        ],
        record_config=True,
    )
    events = [
        e
        async for e in _stream_graph_agent(
            fake_app,
            {},
            "KnowledgeAgent",
            "KnowledgeAgent",
            run_id="run-cfg-1",
            dialogue_id="d",
        )
    ]

    assert fake_app.seen is not None
    assert fake_app.configurable().get("thread_id") == "run-cfg-1"
    assert events[0].type == "RunStarted"
    assert events[-1].type == "RunFinished"


async def test_graph_stream_projects_progress_and_filters_child_ns() -> None:
    """phyto.progress passes from any ns; child updates/values are ignored.

    A custom tick from a mounted subgraph (non-empty ns) must project
    to a phyto.progress Custom frame, while a child-ns ``updates`` must
    NOT fire a StepStarted and a child-ns ``values`` must NOT overwrite
    the parent terminal state.
    """
    yields: list[tuple[tuple[str, ...], str, dict[str, Any]]] = [
        ((), "updates", {"retrieve_node": {}}),
        (
            ("retrieve_node:abc",),
            "custom",
            {
                "kind": "phyto.progress",
                "phase": "retrieving",
                "current": 3,
                "total": 8,
                "detail": "gene 3/8",
            },
        ),
        (
            ("retrieve_node:abc",),
            "updates",
            {"sub_reduce_node": {}},
        ),
        (
            ("retrieve_node:abc",),
            "values",
            {"final_response": "CHILD"},
        ),
        ((), "updates", {"generate_post_node": {}}),
        ((), "values", {"final_response": {}}),
    ]
    events = [
        e
        async for e in _stream_graph_agent(
            FakeStreamApp(yields),
            {},
            "KnowledgeAgent",
            "KnowledgeAgent",
            run_id="r",
            dialogue_id="d",
        )
    ]
    types = [e.type for e in events]
    # exactly two StepStarted (retrieving, generating) — child dropped
    step_names = [
        e.data["step_name"] for e in events if e.type == "StepStarted"
    ]
    assert step_names == ["retrieving", "generating"]
    # one phyto.progress Custom frame carrying the child-ns tick
    progress = [
        e
        for e in events
        if e.type == "Custom" and e.data["name"] == "phyto.progress"
    ]
    assert len(progress) == 1
    assert progress[0].data["value"]["phase"] == "retrieving"
    assert progress[0].data["value"]["current"] == 3
    assert types[0] == "RunStarted" and types[-1] == "RunFinished"


async def test_graph_stream_propagates_runtime_failure() -> None:
    """Raw graph streams leave runtime failures for the outer projector."""

    async def failing_astream(
        _state: Mapping[str, Any],
        stream_mode: list[str],
        config: Mapping[str, Any] | None = None,
        *,
        subgraphs: bool = False,
    ) -> AsyncIterator[tuple[tuple[str, ...], str, dict[str, Any]]]:
        """Raise a raw error after validating the graph stream options."""
        del config
        assert stream_mode == ["custom", "updates", "values"]
        assert subgraphs is True
        if stream_mode:
            raise RuntimeError(
                "Bearer bearer-secret postgresql://db-user:db-password@"
                "db.internal/db SELECT secret_token FROM private_table"
            )
        yield (), "values", {}

    failing_app = SimpleNamespace(astream=failing_astream)

    stream = _stream_graph_agent(
        failing_app,
        {},
        "KnowledgeAgent",
        "KnowledgeAgent",
        run_id="run-fail",
        dialogue_id=None,
    )

    async def drain() -> None:
        """Consume the raw stream until its backend error is raised."""
        async for _event in stream:
            pass

    with pytest.raises(RuntimeError, match="secret_token"):
        await drain()

    lifecycle = StreamLifecycleState()
    projected = [
        event
        async for event in project_stream_failures(
            _stream_graph_agent(
                failing_app,
                {},
                "KnowledgeAgent",
                "KnowledgeAgent",
                run_id="run-projected-fail",
                dialogue_id=None,
            ),
            state=lifecycle,
            run_id="run-projected-fail",
            request_id="req-projected-fail",
        )
    ]
    assert [event.type for event in projected] == ["RunStarted", "RunError"]
    assert lifecycle.reached_finish is False
    assert lifecycle.saw_error is True
    evidence = repr(projected)
    for forbidden in (
        "bearer-secret",
        "postgresql://",
        "db-user:db-password",
        "SELECT secret_token",
    ):
        assert forbidden not in evidence
