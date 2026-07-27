# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for bounded Review conversation operations."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

import pytest

from mcp_server_phytomni.agents.review import agent as review_agent
from mcp_server_phytomni.agents.review.conversation import (
    ReviewCheckpointSnapshot,
    ReviewClarificationError,
    ReviewConversationAdapter,
    ReviewConversationOperation,
    RevisedSection,
    classify_review_operation,
    extract_review_checkpoint,
    load_review_checkpoint,
    reassemble_report,
    revise_section,
)
from mcp_server_phytomni.runtime.conversation_context.adapters import (
    review_agent_invocation,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextProjection,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)

pytestmark = pytest.mark.agent

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
_THREAD_ID = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
_FULL_REPORT = "FULL_PRIOR_REPORT_MUST_NOT_CROSS_THE_CONTEXT_BOUNDARY"


def _projection(query: str, *, active: bool = True) -> ContextProjection:
    """Build a bounded Review projection for one conversation turn."""
    return ContextProjection(
        current_query=query,
        task_summary="active review summary" if active else "",
        relevant_recent_turns=[],
        relevant_assistant_summaries=(
            ["bounded review answer"] if active else []
        ),
        active_entities=[],
        open_questions=[],
        artifact_refs=[],
        agent_thread_id=_THREAD_ID,
        locale="en-US",
        token_budget=4096,
        context_truncated=False,
    )


def _checkpoint_state() -> dict[str, Any]:
    """Return a graph checkpoint containing more data than the adapter admits."""
    return {
        "original_user_query": "Review drought tolerance in rice",
        "research_dimensions": ["Background", "Evidence"],
        "revised_reports": [
            {
                "subtopic": "Background",
                "revised_report": "Background claim remains supported.",
            },
            {
                "subtopic": "Evidence",
                "revised_report": "Evidence claim needs a replication study.",
            },
        ],
        "review_contents": [
            '{"key_claim_summary": "Background claim remains supported."}',
            '{"evidence_gaps": ["replication study"]}',
        ],
        "evidence_gaps": ["replication study"],
        "all_raw_doc_list": [
            {"doc_id": "source-1", "content": "raw source body"}
        ],
        "add_doc_list": [{"doc_id": "source-2", "content": "more raw body"}],
        "summary_content": _FULL_REPORT,
        "report_artifact_id": "report-1",
        "report_revision": 4,
    }


def test_classify_review_operation_covers_all_four_intents() -> None:
    """Classification is deterministic before any graph or chat call."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None

    assert (
        classify_review_operation(
            "Review drought tolerance in rice",
            active_review=False,
        )
        is ReviewConversationOperation.NEW_REVIEW
    )
    assert (
        classify_review_operation(
            "What evidence supports that claim?",
            active_review=True,
            snapshot=snapshot,
        )
        is ReviewConversationOperation.FOLLOW_UP
    )
    assert (
        classify_review_operation(
            "Rewrite the Evidence section to state the limitation.",
            active_review=True,
            snapshot=snapshot,
        )
        is ReviewConversationOperation.LOCAL_REVISION
    )
    assert (
        classify_review_operation(
            "Now investigate maize heat tolerance using a new source set.",
            active_review=True,
            snapshot=snapshot,
        )
        is ReviewConversationOperation.SCOPE_CHANGE
    )


def test_new_evidence_question_remains_a_follow_up() -> None:
    """A new evidence request does not implicitly replace the active review."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None

    assert (
        classify_review_operation(
            "What new evidence supports that claim?",
            active_review=True,
            snapshot=snapshot,
        )
        is ReviewConversationOperation.FOLLOW_UP
    )


def test_extract_review_checkpoint_admits_only_bounded_review_snapshot() -> (
    None
):
    """Checkpoint extraction excludes the full report and raw source bodies."""
    snapshot = extract_review_checkpoint(_checkpoint_state())

    assert isinstance(snapshot, ReviewCheckpointSnapshot)
    assert snapshot.research_question == "Review drought tolerance in rice"
    assert snapshot.source_ids == ("source-1", "source-2")
    assert snapshot.outline_headings == ("Background", "Evidence")
    assert snapshot.evidence_gaps == ("replication study",)
    assert snapshot.report_artifact_id == "report-1"
    assert snapshot.report_revision == 4
    assert _FULL_REPORT not in json.dumps(asdict(snapshot), sort_keys=True)
    assert all("raw source body" not in claim for claim in snapshot.key_claims)


def test_prepare_local_revision_uses_only_the_requested_section() -> None:
    """The local-revision prompt contains bounded evidence and one section."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()

    prepared = adapter.prepare(
        _projection("Rewrite the Evidence section to state the limitation."),
        snapshot=snapshot,
    )

    assert prepared["operation"] == ReviewConversationOperation.LOCAL_REVISION
    assert prepared["section_id"] == "evidence"
    assert (
        prepared["section_text"] == "Evidence claim needs a replication study."
    )
    assert prepared["thread_id"] == _THREAD_ID
    assert _FULL_REPORT not in prepared["prompt_context"]
    assert "raw source body" not in prepared["prompt_context"]


def test_unknown_local_revision_section_returns_clarification() -> None:
    """A section name must be validated against the active outline."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None

    with pytest.raises(ReviewClarificationError, match="section"):
        ReviewConversationAdapter().prepare(
            _projection("Rewrite the Limitations section to be shorter."),
            snapshot=snapshot,
        )


def test_reassemble_report_changes_one_section_only() -> None:
    """Unrelated section bytes survive a local revision unchanged."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    revised = RevisedSection(
        section_id="evidence",
        heading="Evidence",
        text="Evidence claim is now explicitly qualified.",
    )

    assembled = reassemble_report(snapshot.sections, revised)

    assert "Background claim remains supported." in assembled
    assert "Evidence claim is now explicitly qualified." in assembled
    assert "Evidence claim needs a replication study." not in assembled


def test_reassemble_markdown_preserves_unrelated_bytes() -> None:
    """Markdown reassembly replaces only the selected heading body."""
    report = (
        "## Background\nBackground claim remains supported.\n\n"
        "## Evidence\nEvidence claim needs a replication study.\n\n"
        "## Limitations\nLimitations remain open.\n"
    )
    assembled = reassemble_report(
        report,
        RevisedSection(
            section_id="evidence",
            heading="Evidence",
            text="Evidence claim is now explicitly qualified.",
        ),
    )

    assert assembled == (
        "## Background\nBackground claim remains supported.\n\n"
        "## Evidence\nEvidence claim is now explicitly qualified.\n\n"
        "## Limitations\nLimitations remain open.\n"
    )


@pytest.mark.asyncio
async def test_local_revision_reassembles_the_original_report_bytes() -> None:
    """A focused edit keeps framing, citations, and unrelated bytes intact."""
    report = (
        "# Review summary\n\n"
        "Intro framing with [document:7].\n\n"
        "## Background\nBackground claim [document:1].\n\n"
        "## Evidence\nEvidence claim [document:2].\n\n"
        "## Limitations\nLimitations remain open [document:3].\n"
    )
    checkpoint = {
        "original_user_query": "Review drought tolerance in rice",
        "summary_content": report,
        "research_dimensions": ["Background", "Evidence", "Limitations"],
        "report_artifact_id": "report-1",
        "report_revision": 4,
    }
    snapshot = extract_review_checkpoint(checkpoint)
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        _projection("Rewrite the Evidence section to state the limitation."),
        snapshot=checkpoint,
    )

    async def fake_chat(_prompt: str) -> dict[str, Any]:
        return {
            "choices": [
                {"message": {"content": "Evidence claim is qualified."}}
            ]
        }

    result = await adapter.local_revision(fake_chat)
    assert result["choices"][0]["message"]["content"] == (
        "# Review summary\n\n"
        "Intro framing with [document:7].\n\n"
        "## Background\nBackground claim [document:1].\n\n"
        "## Evidence\nEvidence claim is qualified.\n\n"
        "## Limitations\nLimitations remain open [document:3].\n"
    )


@pytest.mark.asyncio
async def test_empty_local_revision_does_not_advance_revision() -> None:
    """An empty focused response is a failed revision, not an unchanged one."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        _projection("Rewrite the Evidence section to state the limitation."),
        snapshot=snapshot,
    )

    async def empty_chat(_prompt: str) -> dict[str, Any]:
        return {"choices": [{"message": {"content": ""}}]}

    with pytest.raises(ReviewClarificationError, match="revision"):
        await adapter.local_revision(empty_chat)
    assert adapter.settle(False) == 4
    assert adapter.report_revision == 4


def test_review_invocation_keeps_operation_and_checkpoint_private() -> None:
    """The context adapter forwards only public arguments to the handler."""
    projection = _projection("What evidence supports that claim?")
    dispatch = review_agent_invocation(
        projection,
        selected_arguments={"review_checkpoint": _checkpoint_state()},
    )

    assert dispatch.arguments == {
        "user_query": projection.current_query,
        "locale": projection.locale,
    }
    assert dispatch.agent_thread_id == _THREAD_ID
    adapter = dispatch.private_agent_state["review_adapter"]
    assert adapter.operation is ReviewConversationOperation.FOLLOW_UP
    assert dispatch.private_agent_state["review_projection"] is projection


def test_scope_change_stages_focus_until_successful_settlement() -> None:
    """A failed scope switch leaves the prior Review checkpoint active."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    query = "Now investigate maize heat tolerance using a new source set."

    adapter.prepare(_projection(query), snapshot=snapshot)
    assert adapter.active_snapshot == snapshot
    assert adapter.staged_snapshot is not None
    assert adapter.staged_snapshot.research_question == query

    assert adapter.settle(False) == 4
    assert adapter.active_snapshot == snapshot
    assert adapter.staged_snapshot is None

    adapter.prepare(_projection(query), snapshot=snapshot)
    assert adapter.settle(True) == 5
    assert adapter.active_snapshot is not None
    assert adapter.active_snapshot.research_question == query


@pytest.mark.asyncio
async def test_load_review_checkpoint_uses_the_derived_thread_id() -> None:
    """Review checkpoint reads stay on the agent-private thread."""
    captured: dict[str, Any] = {}

    class FakeApp:
        async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
            captured.update(config)
            return _checkpoint_state()

    snapshot = await load_review_checkpoint(
        type("FakeAgent", (), {"app": FakeApp()})(),
        _THREAD_ID,
    )

    assert snapshot is not None
    assert captured == {"configurable": {"thread_id": _THREAD_ID}}


@pytest.mark.asyncio
async def test_review_wrapper_answers_follow_up_without_running_the_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing Review turn uses the chat seam instead of ``arun``."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    projection = _projection("What evidence supports that claim?")
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, snapshot=snapshot)
    captured: dict[str, str] = {}

    class FakeAgent:
        async def _chat(self, prompt: str) -> dict[str, Any]:
            captured["prompt"] = prompt
            return {"choices": [{"message": {"content": "Bounded answer."}}]}

        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("follow-up must not rerun the Review graph")

    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: FakeAgent()
    )
    result = await review_agent.review_agent_function(
        user_query=projection.current_query,
        thread_id=_THREAD_ID,
        review_adapter=adapter,
        review_projection=projection,
    )

    assert result["choices"][0]["message"]["content"] == "Bounded answer."
    assert _FULL_REPORT not in captured["prompt"]


@pytest.mark.asyncio
async def test_revise_section_prompt_is_bounded_and_returns_section() -> None:
    """The focused seam receives no full report and returns one section."""
    captured: dict[str, str] = {}

    async def fake_chat(prompt: str) -> dict[str, Any]:
        captured["prompt"] = prompt
        return {
            "choices": [
                {"message": {"content": "Evidence claim is now qualified."}}
            ]
        }

    revised = await revise_section(
        section_id="evidence",
        section_text="Evidence claim needs a replication study.",
        instruction="State the limitation.",
        evidence_summary="Replication study remains unresolved.",
        chat=fake_chat,
    )

    assert revised == RevisedSection(
        section_id="evidence",
        heading="evidence",
        text="Evidence claim is now qualified.",
    )
    assert _FULL_REPORT not in captured["prompt"]
    assert "Evidence claim needs a replication study." in captured["prompt"]


def test_delta_never_persists_the_full_report_and_settlement_controls_revision() -> (
    None
):
    """Context deltas stay bounded and artifact revision is success-only."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        _projection("What evidence supports that claim?"), snapshot=snapshot
    )
    delta = adapter.delta(
        {
            "result": {
                "formatted": {
                    "answer": "The evidence gap is replication, not absence of evidence."
                }
            }
        }
    )

    payload = json.dumps(delta.model_dump(mode="json"), sort_keys=True)
    assert _FULL_REPORT not in payload
    assert "replication" in payload
    assert adapter.report_revision == 4
    adapter.capture_result(
        {
            "phytomni_state": {
                "original_user_query": "new scope",
                "report_revision": 99,
            }
        }
    )
    assert adapter.settle(False) == 4
    assert adapter.report_revision == 4
    assert adapter.settle(True) == 5
    assert adapter.report_revision == 5


@pytest.mark.asyncio
async def test_review_wrapper_forwards_private_thread_id_without_schema_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility wrapper passes stable thread IDs to ``arun``."""
    captured: dict[str, Any] = {}

    class FakeAgent:
        async def arun(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"ok": True}

    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: FakeAgent()
    )
    result = await review_agent.review_agent_function(
        user_query="Review drought tolerance in rice",
        thread_id=_THREAD_ID,
    )

    assert result == {"ok": True}
    assert captured["thread_id"] == _THREAD_ID
    assert captured["user_query"] == "Review drought tolerance in rice"
