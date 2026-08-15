# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Per-call failure tests for ``DeepResearchAgent._feedback_rag``.

Topology C wires the supplementary-retrieval ``asyncio.gather`` so failed
``self.ka.arun`` calls remain internal evidence decisions without exposing
exception text or ``FailureRecord`` metadata. These tests pin silent
continuation with existing evidence, sanitized failure when no evidence
remains, and cancellation propagation.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.report import (
    ReviewReportMixin,
    _partition_add_query_results,
)
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent() -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` for ``_feedback_rag`` probes.

    The tests target ``_feedback_rag`` directly without invoking the
    compiled LangGraph workflow; the knowledge subgraph compiled at
    construction time is offline-only and never awaited here.
    """
    return DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


async def _audit_passthrough(
    self: Any,
    *,
    content_to_check: str,
    raw_doc_list: list[dict[str, Any]],
    add_doc_list: list[dict[str, Any]],
) -> str:
    """Return ``content_to_check`` unchanged for deterministic asserts.

    The production audit path runs another chat call against
    ``self._chat`` to rewrite citation markers, which is outside this
    test's scope.
    """
    del self, raw_doc_list, add_doc_list
    return content_to_check


async def test_feedback_rag_silently_continues_with_main_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed add_query call does not leak degradation metadata.

    Stubs ``KnowledgeAgent.arun`` so the second call
    (``query_idx == 1``) of three raises; the other two return empty
    results. Existing main evidence lets the report continue normally.
    """
    monkeypatch.setattr(
        ReviewReportMixin, "_audit_citations", _audit_passthrough
    )

    async def fake_arun(
        self: Any,
        *,
        user_query: str,
        is_generate: bool,
        is_follow_up: bool,
    ) -> list[dict[str, Any]]:
        del self, is_generate, is_follow_up
        if user_query == "q-mid":
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(KnowledgeAgent, "arun", fake_arun)

    agent = _build_agent()
    review_content = (
        '{"has_critical_gaps": true, '
        '"search_queries": ["q-first", "q-mid", "q-last"]}'
    )
    result = await getattr(agent, "_feedback_rag")(
        subtopic_idx=4,
        draft_content="draft-orig",
        review_content=review_content,
        raw_doc_list=[{"doc_id": "document 001", "content": "main"}],
    )

    assert result["revised_content"] == "draft-orig"
    assert result["add_doc_list"] == []
    assert "failures" not in result


async def test_feedback_rag_fails_when_all_evidence_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three add_query calls fail with no main evidence."""
    monkeypatch.setattr(
        ReviewReportMixin, "_audit_citations", _audit_passthrough
    )

    call_messages = {
        "q-a": "first failure",
        "q-b": "second failure",
        "q-c": "third failure",
    }

    async def fake_arun(
        self: Any,
        *,
        user_query: str,
        is_generate: bool,
        is_follow_up: bool,
    ) -> list[dict[str, Any]]:
        del self, is_generate, is_follow_up
        raise RuntimeError(call_messages[user_query])

    monkeypatch.setattr(KnowledgeAgent, "arun", fake_arun)

    agent = _build_agent()
    review_content = (
        '{"has_critical_gaps": true, '
        '"search_queries": ["q-a", "q-b", "q-c"]}'
    )
    with pytest.raises(
        McpError, match="Knowledge retrieval temporarily unavailable"
    ):
        await getattr(agent, "_feedback_rag")(
            subtopic_idx=0,
            draft_content="draft-orig",
            review_content=review_content,
            raw_doc_list=[],
        )


async def test_feedback_rag_uses_mixed_supplementary_evidence_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid supplementary result supports silent partial continuation."""
    monkeypatch.setattr(
        ReviewReportMixin, "_audit_citations", _audit_passthrough
    )

    async def fake_arun(
        self: Any,
        *,
        user_query: str,
        is_generate: bool,
        is_follow_up: bool,
    ) -> list[dict[str, Any]]:
        del self, is_generate, is_follow_up
        if user_query == "private-failed-query":
            raise RuntimeError("sensitive endpoint details")
        return [
            {
                "chunk_id": "supplement-1",
                "title": "Supported finding",
                "content": "reliable supplementary evidence",
            }
        ]

    async def fake_chat(self: Any, user_query: str) -> dict[str, Any]:
        del self, user_query
        return {
            "choices": [{"message": {"content": "evidence-backed revision"}}]
        }

    monkeypatch.setattr(KnowledgeAgent, "arun", fake_arun)
    monkeypatch.setattr(DeepResearchAgent, "_chat", fake_chat)

    agent = _build_agent()
    result = await getattr(agent, "_feedback_rag")(
        subtopic_idx=1,
        draft_content="draft-orig",
        review_content=(
            '{"has_critical_gaps": true, "search_queries": '
            '["supported-query", "private-failed-query"]}'
        ),
        raw_doc_list=[],
    )

    assert result["revised_content"] == "evidence-backed revision"
    assert [doc["chunk_id"] for doc in result["add_doc_list"]] == [
        "supplement-1"
    ]
    public_state = json.dumps(result, ensure_ascii=False)
    assert "failures" not in result
    assert "unavailable" not in public_state
    assert "private-failed-query" not in public_state
    assert "sensitive endpoint details" not in public_state


async def test_feedback_rag_propagates_gathered_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation from a supplementary Knowledge call aborts revision."""
    monkeypatch.setattr(
        ReviewReportMixin, "_audit_citations", _audit_passthrough
    )

    async def fake_arun(
        self: Any,
        *,
        user_query: str,
        is_generate: bool,
        is_follow_up: bool,
    ) -> list[dict[str, Any]]:
        del self, is_generate, is_follow_up
        if user_query == "cancel-query":
            raise asyncio.CancelledError()
        return []

    monkeypatch.setattr(KnowledgeAgent, "arun", fake_arun)

    agent = _build_agent()
    with pytest.raises(asyncio.CancelledError):
        await getattr(agent, "_feedback_rag")(
            subtopic_idx=0,
            draft_content="draft-orig",
            review_content=(
                '{"has_critical_gaps": true, "search_queries": '
                '["empty-query", "cancel-query"]}'
            ),
            raw_doc_list=[{"doc_id": "document 001", "content": "main"}],
        )


@pytest.mark.parametrize(
    "review_content",
    [
        '{"has_critical_gaps": false, "search_queries": ["q-a"]}',
        '{"has_critical_gaps": true, "search_queries": []}',
    ],
    ids=["no_gaps", "empty_search_queries"],
)
async def test_feedback_rag_has_no_failure_metadata_when_no_add_queries(
    monkeypatch: pytest.MonkeyPatch,
    review_content: str,
) -> None:
    """No supplementary retrieval creates no failure metadata.

    Both no-add_queries paths — ``has_critical_gaps=false`` and
    ``search_queries=[]`` — short-circuit before the gather.
    """
    monkeypatch.setattr(
        ReviewReportMixin, "_audit_citations", _audit_passthrough
    )

    arun_calls: list[str] = []

    async def fake_arun(
        self: Any,
        *,
        user_query: str,
        is_generate: bool,
        is_follow_up: bool,
    ) -> list[dict[str, Any]]:
        del self, is_generate, is_follow_up
        arun_calls.append(user_query)
        return []

    monkeypatch.setattr(KnowledgeAgent, "arun", fake_arun)

    agent = _build_agent()
    result = await getattr(agent, "_feedback_rag")(
        subtopic_idx=2,
        draft_content="draft-orig",
        review_content=review_content,
        raw_doc_list=[],
    )

    assert "failures" not in result
    assert not arun_calls


def test_partition_counts_failures_without_retaining_exception_text() -> None:
    """Partitioning keeps valid lists and replaces failures with no payload."""
    results: list[Any] = [[], RuntimeError("boom"), []]
    normalized, failure_count = _partition_add_query_results(results)
    assert normalized == [[], None, []]
    assert failure_count == 1


def test_partition_reraises_cancellation_class() -> None:
    """A CancelledError in the results propagates, not degrades.

    ``asyncio.gather(return_exceptions=True)`` can capture a child
    ``CancelledError`` as a ``BaseException`` result; the partitioner must
    re-raise it so cancellation aborts the review instead of being retained.
    """
    results: list[Any] = [RuntimeError("ok-failure"), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        _partition_add_query_results(results)
