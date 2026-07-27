# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for bounded Review conversation operations."""

from __future__ import annotations

import json
from collections.abc import Mapping
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
    ConversationContextExecutor,
    review_agent_invocation,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextDelta,
    ContextProjection,
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
    PrepareStatus,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
    StagedTurn,
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
    """New evidence wording does not implicitly replace the active review."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None

    for query in (
        "What new evidence supports that claim?",
        "Review the new evidence supporting that claim.",
    ):
        assert (
            classify_review_operation(
                query,
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
async def test_local_revision_preserves_ordered_citation_metadata() -> None:
    """A cited local revision keeps the Review renderer's ordered documents."""
    report = (
        "# Review summary\n\n"
        "Intro framing with [document:7].\n\n"
        "## Evidence\nEvidence claim [document:1].\n"
    )
    documents = [
        {"doc_id": 7, "title": "First source", "content": "first"},
        {"doc_id": 1, "title": "Second source", "content": "second"},
    ]
    checkpoint = {
        "values": {
            "original_user_query": "Review drought tolerance in rice",
            "summary_content": report,
            "research_dimensions": ["Evidence"],
            "ordered_doc_list": documents,
            "report_artifact_id": "report-1",
            "report_revision": 4,
        }
    }
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
    assert result["choices"][0]["message"]["doc_list"] == documents


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "No answer generated."])
async def test_invalid_local_revision_does_not_advance_revision(
    content: str,
) -> None:
    """Empty and placeholder focused responses fail the revision."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        _projection("Rewrite the Evidence section to state the limitation."),
        snapshot=snapshot,
    )

    async def empty_chat(_prompt: str) -> dict[str, Any]:
        return {"choices": [{"message": {"content": content}}]}

    with pytest.raises(ReviewClarificationError, match="revision"):
        await adapter.local_revision(empty_chat)
    assert adapter.settle(False) == 4
    assert adapter.report_revision == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "No answer generated."])
async def test_invalid_follow_up_is_not_a_successful_answer(
    content: str,
) -> None:
    """Empty and placeholder follow-ups fail instead of inventing an answer."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        _projection("What evidence supports that claim?"), snapshot=snapshot
    )

    async def empty_chat(_prompt: str) -> dict[str, Any]:
        return {"choices": [{"message": {"content": content}}]}

    with pytest.raises(ReviewClarificationError, match="follow-up"):
        await adapter.follow_up(empty_chat)
    assert adapter.settlement_ready is False
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
    assert adapter.snapshot is None
    assert dispatch.private_agent_state["review_projection"] is projection


def test_review_invocation_derives_candidate_thread_from_turn_id() -> None:
    """A new Review graph turn receives a deterministic private candidate thread."""
    projection = _projection("Review maize heat tolerance", active=False)
    dispatch = review_agent_invocation(projection, turn_id="turn-7")

    adapter = dispatch.private_agent_state["review_adapter"]
    assert adapter.operation is ReviewConversationOperation.NEW_REVIEW
    assert adapter.stable_thread_id == _THREAD_ID
    assert adapter.candidate_thread_id is not None
    assert adapter.execution_thread_id == adapter.candidate_thread_id
    assert dispatch.agent_thread_id == _THREAD_ID
    assert dispatch.private_agent_state["review_turn_id"] == "turn-7"


def test_new_review_requires_a_turn_id_before_graph_execution() -> None:
    """A caller without the envelope identity cannot receive a graph thread."""
    with pytest.raises(ReviewClarificationError, match="turn id"):
        ReviewConversationAdapter().prepare(
            _projection("Review maize heat tolerance", active=False)
        )


@pytest.mark.asyncio
async def test_prepare_from_agent_rejects_stale_snapshot_when_stable_is_missing() -> (
    None
):
    """A request-local checkpoint never substitutes for the stable thread."""

    class EmptyApp:
        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return {}

    class EmptyAgent:
        def __init__(self) -> None:
            self.app = EmptyApp()

    projection = _projection("What evidence supports that claim?")
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, snapshot=_checkpoint_state())

    with pytest.raises(ReviewClarificationError, match="checkpoint"):
        await adapter.prepare_from_agent(
            projection,
            EmptyAgent(),
            _THREAD_ID,
        )


@pytest.mark.asyncio
async def test_prepare_from_agent_does_not_reuse_a_prior_turn_id() -> None:
    """A missing current envelope identity cannot run a new Review graph."""

    class EmptyApp:
        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return {}

    class EmptyAgent:
        def __init__(self) -> None:
            self.app = EmptyApp()

    projection = _projection("Review maize heat tolerance", active=False)
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id="stale-turn")

    with pytest.raises(ReviewClarificationError, match="turn id"):
        await adapter.prepare_from_agent(projection, EmptyAgent(), _THREAD_ID)


@pytest.mark.asyncio
async def test_missing_candidate_fails_readiness_before_settlement() -> None:
    """A successful public answer cannot stage without a durable candidate."""

    class EmptyApp:
        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return {}

    class EmptyAgent:
        def __init__(self) -> None:
            self.app = EmptyApp()

    projection = _projection("Review maize heat tolerance", active=False)
    adapter = ReviewConversationAdapter()
    await adapter.prepare_from_agent(
        projection, EmptyAgent(), _THREAD_ID, turn_id="10"
    )
    adapter.capture_result(
        {"choices": [{"message": {"content": "Current answer."}}]}
    )

    assert await adapter.validate_settlement_candidate() is False
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
async def test_review_ack_reconstructs_from_durable_metadata_after_restart(
    tmp_path: Any,
) -> None:
    """A fresh executor promotes a staged candidate without the old adapter."""
    stable_state = _checkpoint_state()
    candidate_state = {
        "original_user_query": "Review maize heat tolerance",
        "summary_content": "# Candidate report\n\nCandidate evidence.",
        "research_dimensions": ["Evidence"],
        "report_artifact_id": "report-1",
        "report_revision": 4,
    }

    class FakeApp:
        def __init__(self) -> None:
            self.states = {
                _THREAD_ID: dict(stable_state),
            }
            self.updates: list[str] = []

        async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
            return self.states.get(config["configurable"]["thread_id"], {})

        async def aupdate_state(
            self, config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            thread_id = config["configurable"]["thread_id"]
            self.updates.append(thread_id)
            self.states.setdefault(thread_id, {}).update(values)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()

    agent = FakeAgent()
    prepared = ReviewConversationAdapter()
    prepared._agent = agent
    prepared.prepare(
        _projection("Review maize heat tolerance", active=False), turn_id="10"
    )
    metadata = prepared.settlement_metadata()
    assert metadata is not None
    metadata["report_revision"] = 4
    candidate_thread = metadata["candidate_thread_id"]
    assert isinstance(candidate_thread, str)
    agent.app.states[candidate_thread] = candidate_state

    store = ConversationContextStore(str(tmp_path / "context.sqlite"))
    key = str(_CONVERSATION_KEY)
    store.begin_turn(key, "10", "append", 0)
    store.stage_turn(
        key,
        "10",
        StagedTurn(
            operation="append",
            base_context_version=0,
            selected_agent_id="ReviewAgent",
            route_source="explicit_selection",
            result={"choices": [{"message": {"content": "answer"}}]},
            delta={
                "schema_version": 1,
                "version": 1,
                "last_applied_ledger_cursor": 1,
                "last_applied_ledger_version": "a" * 64,
                "observed_mode": "expert",
                "task_summary": "review",
                "active_entities": [],
                "open_questions": [],
                "recent_user_turns": [],
                "assistant_summaries": [],
                "artifact_index": [],
                "per_agent_memory": {},
            },
            ledger_version="a" * 64,
            schema_version=1,
            ledger_cursor=1,
            observed_mode="expert",
            stage_metadata={
                "selected_agent_id": "ReviewAgent",
                "route_source": "explicit_selection",
                "route_reason_code": "EXPLICIT_SELECTION",
                "base_business_context_version": 0,
                "proposed_business_context_version": 1,
                "last_applied_ledger_cursor": 1,
                "context_truncated": False,
                "context_rebuilt": True,
                "context_degraded": False,
                "_review_settlement": metadata,
            },
        ),
    )

    loaded: list[dict[str, Any]] = []

    async def loader(
        durable_metadata: Mapping[str, Any], staged_turn: StagedTurn
    ) -> ReviewConversationAdapter:
        loaded.append(dict(durable_metadata))
        adapter = ReviewConversationAdapter()
        await adapter.restore_settlement(
            durable_metadata,
            agent,
            staged_turn.result,
        )
        return adapter

    executor = ConversationContextExecutor(
        store_factory=lambda: store,
        select_agent=lambda *_args, **_kwargs: pytest.fail(
            "settlement reconstruction must not route"
        ),
        review_settlement_loader=loader,
    )
    assert await executor.acknowledge_review_settlement_for_turn(
        key, "10", accepted=True
    )
    assert agent.app.states[_THREAD_ID]["report_revision"] == 5
    assert agent.app.updates == [_THREAD_ID]
    assert loaded
    stored = store.load_turn(key, "10")
    assert stored is not None
    assert stored.stage_metadata is not None
    assert (
        stored.stage_metadata["_review_settlement"]["settlement_state"]
        == "promoted"
    )
    assert await executor.acknowledge_review_settlement_for_turn(
        key, "10", accepted=True
    )
    assert agent.app.updates == [_THREAD_ID]


def test_scope_change_stages_focus_until_successful_settlement() -> None:
    """A failed scope switch leaves the prior Review checkpoint active."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    query = "Now investigate maize heat tolerance using a new source set."

    adapter.prepare(_projection(query), snapshot=snapshot, turn_id="scope-1")
    assert adapter.active_snapshot == snapshot
    assert adapter.staged_snapshot is not None
    assert adapter.staged_snapshot.research_question == query

    assert adapter.settle(False) == 4
    assert adapter.active_snapshot == snapshot
    assert adapter.staged_snapshot is None

    adapter.prepare(_projection(query), snapshot=snapshot, turn_id="scope-1")
    assert adapter.settle(True) == 5
    assert adapter.active_snapshot is not None
    assert adapter.active_snapshot.research_question == query


@pytest.mark.parametrize("answer", ["", "No answer generated."])
def test_capture_result_does_not_rescue_invalid_public_answer_with_stale_report(
    answer: str,
) -> None:
    """A stale private report cannot make an invalid current result stageable."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        _projection("What evidence supports that claim?"), snapshot=snapshot
    )

    adapter.capture_result(
        {
            "choices": [{"message": {"content": answer}}],
            "phytomni_state": {
                "summary_content": "A stale report from an earlier turn.",
                "original_user_query": "Review drought tolerance in rice",
                "report_revision": 4,
            },
        }
    )

    assert adapter.settlement_ready is False
    assert adapter.delta().summary_update is not None
    assert adapter.report_revision == 4


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
        class FakeApp:
            async def aget_state(
                self, _config: dict[str, Any]
            ) -> dict[str, Any]:
                return _checkpoint_state()

        def __init__(self) -> None:
            self.app = self.FakeApp()

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
async def test_review_wrapper_marks_failed_when_full_graph_returns_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-graph clarification must clear the prepared success flag."""
    projection = _projection("Review drought tolerance in rice", active=False)
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id="clarification-1")

    class FakeAgent:
        async def arun(self, **_kwargs: Any) -> dict[str, Any]:
            raise ReviewClarificationError("graph clarification")

    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: FakeAgent()
    )
    result = await review_agent.review_agent_function(
        user_query=projection.current_query,
        thread_id=adapter.execution_thread_id,
        review_adapter=adapter,
        review_projection=projection,
        review_turn_id="clarification-1",
    )

    assert result["choices"][0]["message"]["content"] == "graph clarification"
    assert adapter.settlement_ready is False


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
            "choices": [
                {
                    "message": {
                        "content": "A bounded evidence answer.",
                    }
                }
            ],
            "phytomni_state": {
                "original_user_query": "new scope",
                "report_revision": 99,
            },
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


@pytest.mark.asyncio
async def test_executor_defers_review_checkpoint_until_explicit_ack(
    tmp_path: Any,
) -> None:
    """Staging never updates Review state before the durable ack seam."""
    envelope = ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(_CONVERSATION_KEY),
            "dialogue_id": str(_CONVERSATION_KEY),
            "turn_id": "1",
            "request_id": "request-1",
            "operation": "append",
            "mode": "expert",
            "current_message": {
                "content": "Review drought tolerance in rice",
                "locale": "en-US",
            },
            "requested_agent_id": "ReviewAgent",
            "allowed_agent_ids": ["ReviewAgent"],
            "ledger_cursor": 1,
            "ledger_version": "a" * 64,
            "base_business_context_version": 0,
            "history_delta": [
                {
                    "turn_id": "1",
                    "role": "user",
                    "content": "Review drought tolerance in rice",
                }
            ],
            "artifact_refs": [],
        }
    )

    class FakeApp:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        async def aget_state(self, _config: dict[str, Any]) -> dict[str, Any]:
            return _checkpoint_state()

        async def aupdate_state(
            self, _config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append(values)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()

    fake_agent = FakeAgent()
    captured: dict[str, Any] = {}

    async def invoke(
        _selected_agent_id: str,
        _envelope: ConversationEnvelopeV1,
        dispatch: Any,
    ) -> AgentOutcome:
        adapter = dispatch.private_agent_state["review_adapter"]
        await adapter.prepare_from_agent(
            dispatch.private_agent_state["review_projection"],
            fake_agent,
            dispatch.agent_thread_id,
        )
        adapter.capture_result(
            {"choices": [{"message": {"content": "Review complete."}}]}
        )
        captured["adapter"] = adapter
        return AgentOutcome(result={"ok": True}, context_delta=ContextDelta())

    async def forbidden_router(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("explicit Review selection must not route")

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(
            str(tmp_path / "context.sqlite")
        ),
        select_agent=forbidden_router,
    )
    prepared = await executor.execute(
        envelope=envelope,
        invoke=invoke,
        delegate_async=lambda *_args: pytest.fail(
            "Review must not delegate asynchronously"
        ),
    )

    assert prepared.status is PrepareStatus.RETURN_STAGED
    adapter = captured["adapter"]
    assert fake_agent.app.updates == []
    assert adapter.report_revision == 4
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=False)
        is True
    )
    assert fake_agent.app.updates == []
    assert adapter.report_revision == 4
    assert adapter.active_snapshot is not None
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is False
    )

    accepted_adapter = ReviewConversationAdapter()
    await accepted_adapter.prepare_from_agent(
        _projection("Review drought tolerance in rice"),
        fake_agent,
        _THREAD_ID,
    )
    accepted_adapter.capture_result(
        {"choices": [{"message": {"content": "Review complete."}}]}
    )
    await executor.defer_review_settlement(envelope, accepted_adapter)
    assert fake_agent.app.updates == []
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is True
    )
    assert fake_agent.app.updates == [{"report_revision": 5}]
    assert accepted_adapter.report_revision == 5


@pytest.mark.asyncio
async def test_new_review_promotes_candidate_only_after_ack_and_reject_discards_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Full-graph state stays on a candidate thread until durable acceptance."""
    envelope = ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(_CONVERSATION_KEY),
            "dialogue_id": str(_CONVERSATION_KEY),
            "turn_id": "7",
            "request_id": "request-7",
            "operation": "append",
            "mode": "expert",
            "current_message": {
                "content": "Review maize heat tolerance",
                "locale": "en-US",
            },
            "requested_agent_id": "ReviewAgent",
            "allowed_agent_ids": ["ReviewAgent"],
            "ledger_cursor": 7,
            "ledger_version": "b" * 64,
            "base_business_context_version": 0,
            "history_delta": [
                {
                    "turn_id": "7",
                    "role": "user",
                    "content": "Review maize heat tolerance",
                }
            ],
            "artifact_refs": [],
        }
    )
    stable_state = _checkpoint_state()
    candidate_state = {
        "original_user_query": "Review maize heat tolerance",
        "summary_content": "# New report\n\nNew evidence.",
        "research_dimensions": ["Evidence"],
        "report_artifact_id": "report-1",
        "report_revision": 0,
    }

    class FakeApp:
        def __init__(self) -> None:
            self.states: dict[str, dict[str, Any]] = {_THREAD_ID: stable_state}
            self.updates: list[tuple[str, dict[str, Any]]] = []
            self.deleted: list[str] = []

        async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
            thread_id = config["configurable"]["thread_id"]
            return self.states.get(thread_id, {})

        async def aupdate_state(
            self, config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            thread_id = config["configurable"]["thread_id"]
            self.updates.append((thread_id, values))
            self.states.setdefault(thread_id, {}).update(values)

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)
            self.states.pop(thread_id, None)

    class FakeAgent:
        def __init__(self) -> None:
            self.app = FakeApp()
            self.graph_threads: list[str | None] = []

        async def arun(self, **kwargs: Any) -> dict[str, Any]:
            self.graph_threads.append(kwargs["thread_id"])
            self.app.states[kwargs["thread_id"]] = candidate_state.copy()
            return {
                "choices": [
                    {"message": {"content": "Current public answer."}}
                ],
                "phytomni_state": candidate_state,
            }

    fake_agent = FakeAgent()
    monkeypatch.setattr(
        review_agent, "get_cached_agent", lambda *_args: fake_agent
    )
    projection = _projection(
        "Start a new review of maize heat tolerance", active=False
    )
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id=envelope.turn_id)
    result = await review_agent.review_agent_function(
        user_query=projection.current_query,
        thread_id=adapter.execution_thread_id,
        review_adapter=adapter,
        review_projection=projection,
        review_turn_id=envelope.turn_id,
    )

    assert result["choices"][0]["message"]["content"] == (
        "Current public answer."
    )
    assert fake_agent.graph_threads == [adapter.candidate_thread_id]
    assert adapter.candidate_thread_id != adapter.stable_thread_id
    stable_before_ack = dict(fake_agent.app.states[_THREAD_ID])

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(
            str(tmp_path / "context.sqlite")
        ),
        select_agent=lambda *_args, **_kwargs: pytest.fail(
            "the direct settlement test must not route"
        ),
    )
    await executor.defer_review_settlement(envelope, adapter)
    assert fake_agent.app.updates == []
    assert fake_agent.app.states[_THREAD_ID] == stable_before_ack
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is True
    )
    assert fake_agent.app.updates[0][0] == _THREAD_ID
    assert fake_agent.app.states[_THREAD_ID]["summary_content"] == (
        "# New report\n\nNew evidence."
    )
    assert fake_agent.app.states[_THREAD_ID]["report_revision"] == 5
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is False
    )

    rejected_envelope = envelope.model_copy(update={"turn_id": "8"})
    rejected_projection = _projection(
        "Start a new review of barley heat tolerance", active=False
    )
    rejected_adapter = ReviewConversationAdapter()
    rejected_adapter.prepare(rejected_projection, turn_id="8")
    await review_agent.review_agent_function(
        user_query=rejected_projection.current_query,
        thread_id=rejected_adapter.execution_thread_id,
        review_adapter=rejected_adapter,
        review_projection=rejected_projection,
        review_turn_id="8",
    )
    rejected_candidate = rejected_adapter.candidate_thread_id
    assert rejected_candidate is not None
    await executor.defer_review_settlement(rejected_envelope, rejected_adapter)
    assert (
        await executor.acknowledge_review_settlement(
            rejected_envelope, accepted=False
        )
        is True
    )
    assert fake_agent.app.states[_THREAD_ID]["report_revision"] == 5
    assert rejected_candidate in fake_agent.app.deleted


@pytest.mark.asyncio
async def test_failed_review_ack_discards_candidate_without_advancing_stable_state(
    tmp_path: Any,
) -> None:
    """A lost promotion acknowledgement cannot advance the active checkpoint."""
    stable_state = _checkpoint_state()
    candidate_state = {
        "original_user_query": "Review maize heat tolerance",
        "summary_content": "# Candidate report\n\nCandidate evidence.",
        "research_dimensions": ["Evidence"],
        "report_artifact_id": "report-1",
        "report_revision": 4,
    }

    class FailingApp:
        def __init__(self) -> None:
            self.states = {_THREAD_ID: dict(stable_state)}
            self.updates: list[str] = []
            self.deleted: list[str] = []

        async def aget_state(self, config: dict[str, Any]) -> dict[str, Any]:
            thread_id = config["configurable"]["thread_id"]
            return self.states.get(thread_id, candidate_state)

        async def aupdate_state(
            self, config: dict[str, Any], *, values: dict[str, Any]
        ) -> None:
            self.updates.append(config["configurable"]["thread_id"])
            raise RuntimeError("ledger acknowledgement lost")

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

    class FailingAgent:
        def __init__(self) -> None:
            self.app = FailingApp()

    projection = _projection(
        "Start a new review of maize heat tolerance", active=True
    )
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id="9")
    agent = FailingAgent()
    await adapter.prepare_from_agent(
        projection, agent, _THREAD_ID, turn_id="9"
    )
    adapter.capture_result(
        {
            "choices": [{"message": {"content": "Candidate answer."}}],
            "phytomni_state": candidate_state,
        }
    )
    stable_before_ack = dict(agent.app.states[_THREAD_ID])

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(
            str(tmp_path / "context.sqlite")
        ),
        select_agent=lambda *_args, **_kwargs: pytest.fail(
            "the direct settlement test must not route"
        ),
    )
    envelope = ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(_CONVERSATION_KEY),
            "dialogue_id": str(_CONVERSATION_KEY),
            "turn_id": "9",
            "request_id": "request-9",
            "operation": "append",
            "mode": "expert",
            "current_message": {
                "content": projection.current_query,
                "locale": "en-US",
            },
            "requested_agent_id": "ReviewAgent",
            "allowed_agent_ids": ["ReviewAgent"],
            "ledger_cursor": 9,
            "ledger_version": "c" * 64,
            "base_business_context_version": 0,
            "history_delta": [],
            "artifact_refs": [],
        }
    )
    await executor.defer_review_settlement(envelope, adapter)

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        await executor.acknowledge_review_settlement(envelope, accepted=True)

    assert agent.app.states[_THREAD_ID] == stable_before_ack
    assert agent.app.updates == [_THREAD_ID]
    assert adapter.report_revision == 4
    assert adapter.settlement_ready is False
    assert adapter.candidate_thread_id in agent.app.deleted
    assert (
        await executor.acknowledge_review_settlement(envelope, accepted=True)
        is False
    )
