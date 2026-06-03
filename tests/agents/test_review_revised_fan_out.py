# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for the ``DeepResearchAgent`` revised fan-out.

Flag-off keeps the legacy ``revise_node`` gather; flag-on routes through
``revised_dispatch`` → N × ``revised_worker_node`` →
``revised_reduce_node`` whose workers call ``self._feedback_rag``.
Also covers the original-draft fallback when a worker writes the empty
sentinel, the dual mirror-write of ``revised_contents`` and
``revised_reports``, reduce ordering, and xray expansion.
"""

# pylint: disable=protected-access

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Send

from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.state import DeepResearchState
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent

_AGENT_MODULE = "mcp_server_phytomni.agents.review.agent"


def _build_agent(
    use_chat_subgraph: bool,
) -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` with the chat-subgraph flag set."""
    config = ReviewConfig().model_copy(
        update={
            "USE_CHAT_SUBGRAPH": use_chat_subgraph,
            # Pin USE_KNOWLEDGE_SUBGRAPH False so the retrieve site keeps
            # its legacy ``retrieve_node`` and the test focuses on the
            # revised fan-out wiring under ``USE_CHAT_SUBGRAPH``.
            "USE_KNOWLEDGE_SUBGRAPH": False,
        }
    )
    return DeepResearchAgent(
        review_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Flag-off legacy: ``revise_node`` still gathers via ``_feedback_rag``.
# ---------------------------------------------------------------------------


def test_revised_node_flag_off_graph_keeps_legacy_node() -> None:
    """Flag-off compiled graph has ``revise_node`` and no Send triad."""
    agent = _build_agent(use_chat_subgraph=False)
    node_keys = set(agent.app.get_graph(xray=True).nodes.keys())
    assert "revise_node" in node_keys
    assert "revised_dispatch" not in node_keys
    assert "revised_worker_node" not in node_keys
    assert "revised_reduce_node" not in node_keys


# ---------------------------------------------------------------------------
# Flag-on prepare node returns empty delta.
# ---------------------------------------------------------------------------


async def test_revised_prepare_tasks_node_returns_empty_delta() -> None:
    """``revised_prepare_tasks_node`` acts as a no-op split node."""
    agent = _build_agent(use_chat_subgraph=True)
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["photosynthesis"],
            "draft_contents": ["draft-A"],
            "review_contents": ["{}"],
            "all_raw_doc_list": [],
        },
    )
    result = await agent.revised_prepare_tasks_node(state)
    assert result == {}


# ---------------------------------------------------------------------------
# Flag-on route_revised_tasks returns N Send payloads.
# ---------------------------------------------------------------------------


def test_route_revised_tasks_returns_n_sends() -> None:
    """``route_revised_tasks`` returns one Send per dimension entry."""
    agent = _build_agent(use_chat_subgraph=True)
    dimensions = ["photosynthesis", "chlorophyll", "stomatal"]
    drafts = ["draft-A", "draft-B", "draft-C"]
    reviews = ["{}", '{"has_critical_gaps": true}', "{}"]
    raw_doc_list = [{"id": "doc-1"}, {"id": "doc-2"}]
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": dimensions,
            "draft_contents": drafts,
            "review_contents": reviews,
            "all_raw_doc_list": raw_doc_list,
        },
    )
    sends = agent.route_revised_tasks(state)

    assert len(sends) == 3
    for i, send in enumerate(sends):
        assert isinstance(send, Send)
        assert send.node == "revised_worker_node"
        assert send.arg["task_index"] == i
        assert send.arg["subtopic"] == dimensions[i]
        assert send.arg["draft_content"] == drafts[i]
        assert send.arg["review_content"] == reviews[i]
        # The raw_doc_list is shared across all dimensions (matches the
        # legacy ``revise_node`` pattern which forwarded the whole list
        # to each ``_feedback_rag`` call).
        assert send.arg["raw_doc_list"] == raw_doc_list


# ---------------------------------------------------------------------------
# Flag-on worker success: writes indexed result and supplementary docs.
# ---------------------------------------------------------------------------


async def test_revised_worker_node_success_writes_indexed_result_and_add_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``(task_index, content)`` + ``add_doc_list`` on success.

    Mocks ``_feedback_rag`` to return the shape its production body
    delivers — ``{"revised_content": str, "add_doc_list": list}`` — so
    the worker's delta lifts both fields into the shared state channels
    without re-running the retrieve/audit pipeline.
    """
    fake_feedback_rag = AsyncMock(
        return_value={
            "revised_content": "revised-A",
            "add_doc_list": [{"id": "doc-extra-1"}, {"id": "doc-extra-2"}],
        }
    )
    monkeypatch.setattr(DeepResearchAgent, "_feedback_rag", fake_feedback_rag)
    agent = _build_agent(use_chat_subgraph=True)
    state = cast(
        DeepResearchState,
        {
            "task_index": 2,
            "subtopic": "auxin signalling",
            "draft_content": "draft-A",
            "review_content": '{"has_critical_gaps": true}',
            "raw_doc_list": [{"id": "doc-1"}],
        },
    )
    result = await agent.revised_worker_node(state)

    assert result["revised_indexed_results"] == [(2, "revised-A")]
    assert result["add_doc_list"] == [
        {"id": "doc-extra-1"},
        {"id": "doc-extra-2"},
    ]
    assert "failures" not in result
    assert fake_feedback_rag.await_count == 1


# ---------------------------------------------------------------------------
# Flag-on worker exception: writes "" sentinel AND FailureRecord.
# ---------------------------------------------------------------------------


async def test_revised_worker_node_exception_writes_sentinel_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``""`` sentinel AND FailureRecord dict on exception.

    The empty-string sentinel (not ``"{}"``) signals to
    ``revised_reduce_node`` to substitute the original draft for that
    dimension — matches the legacy ``revise_node`` substitution at
    ``report.py`` lines 159-165. ``add_doc_list`` is also written as
    ``[]`` so the ``operator.add`` reducer never sees a missing key.
    """
    fake_feedback_rag = AsyncMock(
        side_effect=RuntimeError("supplementary retrieval timeout")
    )
    monkeypatch.setattr(DeepResearchAgent, "_feedback_rag", fake_feedback_rag)
    agent = _build_agent(use_chat_subgraph=True)
    state = cast(
        DeepResearchState,
        {
            "task_index": 1,
            "subtopic": "drought tolerance",
            "draft_content": "draft-orig",
            "review_content": '{"has_critical_gaps": true}',
            "raw_doc_list": [],
        },
    )
    result = await agent.revised_worker_node(state)

    assert result["revised_indexed_results"] == [(1, "")]
    # Empty list, NOT missing key — the operator.add reducer must
    # receive a list, not None, for every worker.
    assert result["add_doc_list"] == []
    failures = result.get("failures", [])
    assert len(failures) == 1
    rec = failures[0]
    # FailureRecord is a TypedDict — check structural keys, not isinstance.
    assert rec["kind"] == "execute"
    assert "supplementary retrieval timeout" in rec["message"]
    assert rec["task_label"] == "revised:1"
    assert rec["traceback_digest"] is not None


# ---------------------------------------------------------------------------
# Flag-on reduce: sorts indexed_results by task_index before projecting.
# ---------------------------------------------------------------------------


async def test_revised_reduce_node_sorts_by_task_index() -> None:
    """``revised_reduce_node`` sorts results by task_index.

    Delivers the same ``revised_contents`` and ``revised_reports``
    ordering regardless of the order concurrent workers completed.
    """
    agent = _build_agent(use_chat_subgraph=True)
    # Supply results out-of-order (task 2 arrives first).
    state = cast(
        DeepResearchState,
        {
            "revised_indexed_results": [(2, "C"), (0, "A"), (1, "B")],
            "draft_contents": ["draft-A", "draft-B", "draft-C"],
            "research_dimensions": ["dim-A", "dim-B", "dim-C"],
        },
    )
    result = await agent.revised_reduce_node(state)

    assert result["revised_contents"] == ["A", "B", "C"]
    assert [r["revised_report"] for r in result["revised_reports"]] == [
        "A",
        "B",
        "C",
    ]


# ---------------------------------------------------------------------------
# Flag-on reduce: empty sentinel falls back to the original draft.
# ---------------------------------------------------------------------------


async def test_revised_reduce_node_falls_back_to_original_draft_on_empty() -> (
    None
):
    """An empty-string sentinel triggers the original-draft fallback.

    Mirrors the legacy ``revise_node`` substitution at ``report.py``
    lines 159-165: a failed dimension surfaces its prior draft so the
    summary node still sees a non-empty subsection for that slot.
    """
    agent = _build_agent(use_chat_subgraph=True)
    state = cast(
        DeepResearchState,
        {
            "revised_indexed_results": [
                (0, "revised-A"),
                (1, ""),  # failed dimension
                (2, "revised-C"),
            ],
            "draft_contents": [
                "draft-A",
                "draft-B-original",
                "draft-C",
            ],
            "research_dimensions": ["dim-A", "dim-B", "dim-C"],
        },
    )
    result = await agent.revised_reduce_node(state)

    assert result["revised_contents"] == [
        "revised-A",
        "draft-B-original",
        "revised-C",
    ]


# ---------------------------------------------------------------------------
# Flag-on reduce: mirror-writes ``revised_reports`` in the legacy shape.
# ---------------------------------------------------------------------------


async def test_revised_reduce_node_mirror_writes_revised_reports() -> None:
    """Reduce projects the legacy ``revised_reports`` list of dicts.

    ``summary.py`` reads ``state["revised_reports"][idx]["revised_report"]``
    and ``test_review_graph_io.py`` pins the field on the TypedDict, so
    the new reduce node must keep emitting both ``subtopic`` and
    ``revised_report`` per dimension entry.
    """
    agent = _build_agent(use_chat_subgraph=True)
    state = cast(
        DeepResearchState,
        {
            "revised_indexed_results": [(0, "revised-A"), (1, "revised-B")],
            "draft_contents": ["draft-A", "draft-B"],
            "research_dimensions": ["photosynthesis", "chlorophyll"],
        },
    )
    result = await agent.revised_reduce_node(state)

    assert result["revised_reports"] == [
        {"subtopic": "photosynthesis", "revised_report": "revised-A"},
        {"subtopic": "chlorophyll", "revised_report": "revised-B"},
    ]
    # Mirror invariant: revised_reports[idx]["revised_report"] tracks
    # revised_contents[idx] one-for-one across the dimension list.
    for idx, dim in enumerate(state["research_dimensions"]):
        assert result["revised_reports"][idx]["subtopic"] == dim
        assert (
            result["revised_reports"][idx]["revised_report"]
            == result["revised_contents"][idx]
        )


# ---------------------------------------------------------------------------
# Flag-branch: chat-subgraph lifecycle parity check.
# ---------------------------------------------------------------------------


def test_chat_app_built_only_when_flag_on() -> None:
    """The compiled chat fan-out exists only under ``USE_CHAT_SUBGRAPH``.

    The agent does not stash the chat subgraph on ``self``; instead the
    module-level :data:`CHAT_APP` is wired into each worker. Pin the
    flag-on / flag-off graph shapes so a regression that loses the
    revised Send triad surfaces here rather than only at runtime.
    """
    agent_off = _build_agent(use_chat_subgraph=False)
    off_keys = set(agent_off.app.get_graph(xray=True).nodes.keys())
    assert "revise_node" in off_keys
    assert "revised_dispatch" not in off_keys

    agent_on = _build_agent(use_chat_subgraph=True)
    on_keys = set(agent_on.app.get_graph(xray=True).nodes.keys())
    assert "revised_dispatch" in on_keys
    assert "revise_node" not in on_keys


# ---------------------------------------------------------------------------
# Structural: flag-on graph has Send triad with xray-expanded worker key.
# ---------------------------------------------------------------------------


def test_compiled_graph_flag_on_has_revised_send_triad() -> None:
    """Flag-on graph has ``revised_dispatch`` and the reduce node.

    The legacy ``revise_node`` body called ``self._feedback_rag``, which
    in turn awaits ``self._chat`` and ``self.ka.arun`` — no compiled
    subgraph hangs directly off the worker, so xray should NOT prefix
    the worker key with a child namespace. Pin both the dispatch node
    and the flat worker key so a regression that loses either surfaces
    here.
    """
    agent = _build_agent(use_chat_subgraph=True)
    node_keys = set(agent.app.get_graph(xray=True).nodes.keys())
    assert "revised_dispatch" in node_keys
    assert "revised_worker_node" in node_keys
    assert "revised_reduce_node" in node_keys
    assert "revise_node" not in node_keys


# ---------------------------------------------------------------------------
# Xray: the chat subgraph still expands UNDER ``review_results_worker_node``.
# ---------------------------------------------------------------------------


def test_compiled_graph_xray_expands_chat_under_revised_worker() -> None:
    """Flag-on graph keeps chat-subgraph xray expansion for the upstream site.

    The revised worker calls ``self._feedback_rag`` (not the module
    ``CHAT_APP``), so xray cannot inline the chat subgraph under
    ``revised_worker_node:``. Verify the upstream review_results
    worker continues to expose its xray-expanded chat subgraph so
    the broader render is unaffected by the revised triad insertion.
    """
    agent = _build_agent(use_chat_subgraph=True)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(
        key.startswith("review_results_worker_node:") for key in node_keys
    ), sorted(node_keys)
    assert "revised_dispatch" in node_keys
    assert "revised_worker_node" in node_keys
    assert "revised_reduce_node" in node_keys
    assert "revise_node" not in node_keys
