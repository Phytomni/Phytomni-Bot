# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Per-call failure tests for ``DeepResearchAgent._feedback_rag``.

Topology C wires the supplementary-retrieval ``asyncio.gather`` so each
failed ``self.ka.arun`` call surfaces as a ``FailureRecord`` on the
returned ``failures`` list without spawning a new graph node. These
tests pin the per-call accumulation, the per-query ``task_label`` form
(``add_query:<subtopic_idx>:<query_idx>``), and the empty-failures
contract when no add_query calls are issued.
"""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.review.agent import DeepResearchAgent
from mcp_server_phytomni.agents.review.report import ReviewReportMixin
from mcp_server_phytomni.config.defaults import ReviewConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.agent


def _build_agent() -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` for ``_feedback_rag`` probes.

    Pins ``USE_KNOWLEDGE_SUBGRAPH`` off so the agent constructor does
    not eagerly assemble the knowledge subgraph; the tests target
    ``_feedback_rag`` directly without invoking the compiled LangGraph
    workflow.
    """
    config = ReviewConfig().model_copy(
        update={
            "USE_KNOWLEDGE_SUBGRAPH": False,
        }
    )
    return DeepResearchAgent(
        review_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


async def _audit_passthrough(
    self: Any,
    *,
    content_to_check: str,
    raw_doc_list: List[Dict[str, Any]],
    add_doc_list: List[Dict[str, Any]],
) -> str:
    """Return ``content_to_check`` unchanged for deterministic asserts.

    The production audit path runs another chat call against
    ``self._chat`` to rewrite citation markers, which is outside this
    test's scope.
    """
    del self, raw_doc_list, add_doc_list
    return content_to_check


async def test_feedback_rag_records_failure_for_single_failed_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed add_query call yields exactly one FailureRecord.

    Stubs ``KnowledgeAgent.arun`` so the second call
    (``query_idx == 1``) of three raises; the other two return empty
    results. Verifies the ``task_label`` carries the subtopic and
    query indices, ``kind`` is ``"execute"``, and the message round-
    trips ``str(exc)``.
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
    ) -> List[Dict[str, Any]]:
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
    result = await agent._feedback_rag(
        subtopic_idx=4,
        draft_content="draft-orig",
        review_content=review_content,
        raw_doc_list=[],
    )

    assert "failures" in result
    failures = result["failures"]
    assert len(failures) == 1
    rec = failures[0]
    assert rec["task_label"] == "add_query:4:1"
    assert rec["kind"] == "execute"
    assert rec["message"] == "boom"
    assert rec["traceback_digest"]


async def test_feedback_rag_records_failures_for_all_failed_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three add_query calls fail → three FailureRecord entries.

    Verifies per-entry ``task_label`` increments ``query_idx`` 0/1/2;
    ``add_doc_list`` stays empty because the formatter filters every
    Exception result; ``revised_content`` falls through to
    ``draft_content`` because the empty supplementary block bypasses
    the feedback chat.
    """
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
    ) -> List[Dict[str, Any]]:
        del self, is_generate, is_follow_up
        raise RuntimeError(call_messages[user_query])

    monkeypatch.setattr(KnowledgeAgent, "arun", fake_arun)

    agent = _build_agent()
    review_content = (
        '{"has_critical_gaps": true, '
        '"search_queries": ["q-a", "q-b", "q-c"]}'
    )
    result = await agent._feedback_rag(
        subtopic_idx=0,
        draft_content="draft-orig",
        review_content=review_content,
        raw_doc_list=[],
    )

    failures = result["failures"]
    assert len(failures) == 3
    for idx, expected_msg in enumerate(
        ["first failure", "second failure", "third failure"]
    ):
        rec = failures[idx]
        assert rec["task_label"] == f"add_query:0:{idx}"
        assert rec["kind"] == "execute"
        assert rec["message"] == expected_msg
        assert rec["traceback_digest"]
    assert result["add_doc_list"] == []
    # All add_query calls failed → supplementary block is empty →
    # feedback chat is skipped → audit-passthrough echoes the draft.
    assert result["revised_content"] == "draft-orig"


@pytest.mark.parametrize(
    "review_content",
    [
        '{"has_critical_gaps": false, "search_queries": ["q-a"]}',
        '{"has_critical_gaps": true, "search_queries": []}',
    ],
    ids=["no_gaps", "empty_search_queries"],
)
async def test_feedback_rag_failures_empty_when_no_add_queries(
    monkeypatch: pytest.MonkeyPatch,
    review_content: str,
) -> None:
    """The ``failures`` key is present and empty when no add_query runs.

    Both no-add_queries paths — ``has_critical_gaps=false`` and
    ``search_queries=[]`` — short-circuit before the gather. The key
    MUST still be present (the worker's ``result.get("failures", [])``
    forward expects a list-typed delta on the universal failures
    channel so the ``operator.add`` reducer never sees ``None``).
    """
    monkeypatch.setattr(
        ReviewReportMixin, "_audit_citations", _audit_passthrough
    )

    arun_calls: List[str] = []

    async def fake_arun(
        self: Any,
        *,
        user_query: str,
        is_generate: bool,
        is_follow_up: bool,
    ) -> List[Dict[str, Any]]:
        del self, is_generate, is_follow_up
        arun_calls.append(user_query)
        return []

    monkeypatch.setattr(KnowledgeAgent, "arun", fake_arun)

    agent = _build_agent()
    result = await agent._feedback_rag(
        subtopic_idx=2,
        draft_content="draft-orig",
        review_content=review_content,
        raw_doc_list=[],
    )

    assert "failures" in result
    assert result["failures"] == []
    assert not arun_calls
