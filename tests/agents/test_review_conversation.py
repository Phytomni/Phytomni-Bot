# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for bounded Review conversation operations."""

from __future__ import annotations

import asyncio
import base64
import json
import pickle
from collections.abc import Mapping
from dataclasses import asdict, fields
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from mcp_server_phytomni.agents.review.conversation import (
    ReviewCheckpointSnapshot,
    ReviewClarificationError,
    ReviewConversationAdapter,
    ReviewConversationOperation,
    RevisedSection,
    classify_review_operation,
    extract_review_checkpoint,
    reassemble_report,
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
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
    StagedTurn,
)

pytestmark = pytest.mark.agent

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
_THREAD_ID = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
_FULL_REPORT = "FULL_PRIOR_REPORT_MUST_NOT_CROSS_THE_CONTEXT_BOUNDARY"
_LEGACY_REVIEW_ADAPTER_PICKLE = (
    "gASVYQUAAAAAAACMLm1jcF9zZXJ2ZXJfcGh5dG9t"
    "bmkuYWdlbnRzLnJldmlldy5jb252ZXJzYXRpb26U"
    "jBlSZXZpZXdDb252ZXJzYXRpb25BZGFwdGVylJOU"
    "KYGUfZQojAZfc3RhdGWUaACME19SZXZpZXdBZGFw"
    "dGVyU3RhdGWUk5QpgZROfZQojApjaGVja3BvaW50"
    "lGgAjBZfUmV2aWV3Q2hlY2twb2ludFN0YXRllJOU"
    "KYGUTn2UKIwIcHJlcGFyZWSUaACME19QcmVwYXJl"
    "ZFJldmlld1R1cm6Uk5QpgZRdlCiMN21jcF9zZXJ2"
    "ZXJfcGh5dG9tbmkucnVudGltZS5jb252ZXJzYXRp"
    "b25fY29udGV4dC5tb2RlbHOUjBFDb250ZXh0UHJv"
    "amVjdGlvbpSTlCmBlH2UKIwIX19kaWN0X1+UfZQo"
    "jA1jdXJyZW50X3F1ZXJ5lIwgUmV2aWV3IGRyb3Vn"
    "aHQgdG9sZXJhbmNlIGluIHJpY2WUjAtpbnRlbnRf"
    "a2luZJSMCWZvbGxvd191cJSMDHRhc2tfc3VtbWFy"
    "eZSMAJSMFXJlbGV2YW50X3JlY2VudF90dXJuc5Rd"
    "lIwTcmVsZXZhbnRfdXNlcl90dXJuc5RdlIwccmVs"
    "ZXZhbnRfYXNzaXN0YW50X3N1bW1hcmllc5RdlIwP"
    "YWN0aXZlX2VudGl0aWVzlF2UjA5vcGVuX3F1ZXN0"
    "aW9uc5RdlIwNYXJ0aWZhY3RfcmVmc5RdlIwPYWdl"
    "bnRfdGhyZWFkX2lklIxEY3R4LWViNzQxM2ZmMDQ4"
    "ODBkMWYyYWU3NTU1YjRiNjFmNjNhZDA0ZWJmYjJk"
    "ZDU3NWY0YTYxNTk0MGFjZDM0YzIyMTGUjAZsb2Nh"
    "bGWUjAVlbi1VU5SMDHRva2VuX2J1ZGdldJRNABCM"
    "EWNvbnRleHRfdHJ1bmNhdGVklIl1jBJfX3B5ZGFu"
    "dGljX2V4dHJhX1+UTowXX19weWRhbnRpY19maWVs"
    "ZHNfc2V0X1+Uj5QoaCVoI2gxaCdoIWgtaC9oKWgf"
    "aBtoK2gykIwUX19weWRhbnRpY19wcml2YXRlX1+U"
    "TnViaACMG1Jldmlld0NvbnZlcnNhdGlvbk9wZXJh"
    "dGlvbpSTlGgehZRSlGgAjBhSZXZpZXdDaGVja3Bv"
    "aW50U25hcHNob3SUk5QpgZRdlChoHCkpKSloAIwN"
    "UmV2aWV3U2VjdGlvbpSTlCmBlF2UKIwKYmFja2dy"
    "b3VuZJSMCkJhY2tncm91bmSUjAdCb3VuZGVklGVi"
    "hZROSwBlYk5lYowPYWN0aXZlX3NuYXBzaG90lGg9"
    "jA9zdGFnZWRfc25hcHNob3SUTowPcmVwb3J0X3Jl"
    "dmlzaW9ulEsAjAdzZXR0bGVklImMFG9wZXJhdGlv"
    "bl9zdWNjZXNzZnVslIiMD3JlcG9ydF9kb2N1bWVu"
    "dJROdYaUYowGcmVzdWx0lGgAjBJfUmV2aWV3UmVz"
    "dWx0U3RhdGWUk5QpgZROfZQojBRsYXN0X3Jldmlz"
    "ZWRfc2VjdGlvbpROjA9jYXB0dXJlZF9yZXN1bHSU"
    "fZSME2NhbmRpZGF0ZV9kaXNjYXJkZWSUiYwTcGVu"
    "ZGluZ19yZXBvcnRfdGV4dJROjBBvcmRlcmVkX2Rv"
    "Y19saXN0lF2UjBBzZXR0bGVtZW50X2ZlbmNllE51"
    "hpRidYaUYowGX2FnZW50lE6MCl90aHJlYWRfaWSU"
    "aC6MEV9zdGFibGVfdGhyZWFkX2lklGgujBRfZXhl"
    "Y3V0aW9uX3RocmVhZF9pZJRoLowUX2NhbmRpZGF0"
    "ZV90aHJlYWRfaWSUTowIX3R1cm5faWSUjAh0dXJu"
    "LW9sZJR1Yi4="
)


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


def _agent_with_app(app: Any) -> SimpleNamespace:
    """Return a lightweight agent double exposing the supplied graph app."""
    return SimpleNamespace(app=app)


def _attach_agent(adapter: ReviewConversationAdapter, agent: Any) -> None:
    """Attach a graph double through the compatibility facade seam."""
    getattr(adapter, "attach_agent")(agent)


def _stateful_review_agent(
    stable_state: Mapping[str, Any],
    candidate_state: Mapping[str, Any] | None = None,
    *,
    record_update_values: bool = True,
) -> SimpleNamespace:
    """Build a graph double with durable state and promotion seams.

    The returned namespace mirrors the small subset of a Review agent used by
    restart, promotion, and candidate-cleanup tests.  Keeping the closures in
    one helper avoids inflating each test's local-variable complexity while
    preserving the same mutable state and call recording.
    """
    states: dict[str, dict[str, Any]] = {_THREAD_ID: dict(stable_state)}
    updates: list[Any] = []
    deleted: list[str] = []
    graph_threads: list[str | None] = []

    async def aget_state(config: dict[str, Any]) -> dict[str, Any]:
        """Read stable or candidate state for the current graph thread."""
        thread_id = config["configurable"]["thread_id"]
        return states.get(thread_id, {})

    async def aupdate_state(
        config: dict[str, Any], *, values: dict[str, Any]
    ) -> None:
        """Record and apply a durable promotion update."""
        await asyncio.sleep(0)
        thread_id = config["configurable"]["thread_id"]
        if record_update_values:
            updates.append((thread_id, values))
        else:
            updates.append(thread_id)
        states.setdefault(thread_id, {}).update(values)

    async def adelete_thread(thread_id: str) -> None:
        """Record candidate cleanup and remove its transient state."""
        deleted.append(thread_id)
        states.pop(thread_id, None)

    async def arun(**kwargs: Any) -> dict[str, Any]:
        """Write candidate state and return a bounded public answer."""
        if candidate_state is None:
            raise AssertionError("this graph double does not run a candidate")
        thread_id = kwargs["thread_id"]
        graph_threads.append(thread_id)
        states[thread_id] = dict(candidate_state)
        return {
            "choices": [{"message": {"content": "Current public answer."}}],
            "phytomni_state": candidate_state,
        }

    return SimpleNamespace(
        app=SimpleNamespace(
            states=states,
            updates=updates,
            deleted=deleted,
            aget_state=aget_state,
            aupdate_state=aupdate_state,
            adelete_thread=adelete_thread,
        ),
        graph_threads=graph_threads,
        arun=arun,
    )


def _simple_staged_turn(metadata: Mapping[str, Any]) -> StagedTurn:
    """Build the minimal staged turn shared by claim-boundary tests."""
    return StagedTurn(
        operation="append",
        base_context_version=0,
        selected_agent_id="ReviewAgent",
        route_source="explicit_selection",
        result={"choices": [{"message": {"content": "answer"}}]},
        delta={},
        ledger_version="a" * 64,
        schema_version=1,
        ledger_cursor=1,
        observed_mode="expert",
        stage_metadata={"_review_settlement": metadata},
    )


def _seed_review_settlement(
    tmp_path: Any,
    turn_id: str,
) -> tuple[str, ConversationContextStore, dict[str, Any]]:
    """Create the minimal staged Review turn used by claim tests."""
    prepared = ReviewConversationAdapter()
    prepared.prepare(
        _projection("Review maize heat tolerance", active=False),
        turn_id=turn_id,
    )
    _attach_agent(prepared, object())
    metadata = prepared.settlement_metadata()
    assert metadata is not None
    key = str(_CONVERSATION_KEY)
    store = ConversationContextStore(str(tmp_path / "context.sqlite"))
    store.begin_turn(key, turn_id, "append", 0)
    store.stage_turn(key, turn_id, _simple_staged_turn(metadata))
    return key, store, metadata


def test_legacy_review_adapter_pickle_loads_after_facade_split() -> None:
    """Load a pre-split adapter pickle through the compatibility facade."""
    restored = pickle.loads(base64.b64decode(_LEGACY_REVIEW_ADAPTER_PICKLE))

    assert isinstance(restored, ReviewConversationAdapter)
    assert restored.operation is ReviewConversationOperation.FOLLOW_UP
    assert restored.snapshot is not None
    assert restored.snapshot.research_question == (
        "Review drought tolerance in rice"
    )

    state = getattr(restored, "_state")
    assert type(state).__name__ == "_ReviewAdapterState"
    checkpoint = getattr(state, "checkpoint")
    result = getattr(state, "result")
    assert type(checkpoint).__name__ == "_ReviewCheckpointState"
    assert type(result).__name__ == "_ReviewResultState"
    assert type(getattr(checkpoint, "prepared")).__name__ == (
        "_PreparedReviewTurn"
    )


def _checkpoint_state() -> dict[str, Any]:
    """Return a checkpoint containing more data than the adapter admits."""
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


def _candidate_review_state(
    *,
    summary_content: str = "# Candidate report\n\nCandidate evidence.",
    report_revision: int = 4,
) -> dict[str, Any]:
    """Return the bounded candidate state used by settlement tests."""
    return {
        "original_user_query": "Review maize heat tolerance",
        "summary_content": summary_content,
        "research_dimensions": ["Evidence"],
        "report_artifact_id": "report-1",
        "report_revision": report_revision,
    }


def _follow_up_adapter(turn_id: str) -> ReviewConversationAdapter:
    """Prepare a bounded follow-up adapter for shared Review tests."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        _projection("What evidence supports that claim?"),
        snapshot=snapshot,
        turn_id=turn_id,
    )
    return adapter


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


def test_review_snapshot_preserves_direct_dataclass_metadata() -> None:
    """Field factoring keeps the original public introspection contract."""
    field_names = [item.name for item in fields(ReviewCheckpointSnapshot)]
    snapshot = ReviewCheckpointSnapshot("question")

    assert field_names == [
        "research_question",
        "source_ids",
        "outline_headings",
        "key_claims",
        "evidence_gaps",
        "sections",
        "report_artifact_id",
        "report_revision",
    ]
    assert list(ReviewCheckpointSnapshot.__annotations__) == field_names
    assert getattr(ReviewCheckpointSnapshot, "__slots__", ()) == tuple(
        field_names
    )
    assert not hasattr(snapshot, "__dict__")
    assert list(asdict(snapshot)) == field_names


def test_prepare_local_revision_uses_only_the_requested_section() -> None:
    """The local-revision prompt contains bounded evidence and one section."""
    snapshot = extract_review_checkpoint(_checkpoint_state())
    assert snapshot is not None
    adapter = ReviewConversationAdapter()

    prepared = adapter.prepare(
        _projection("Rewrite the Evidence section to state the limitation."),
        snapshot=snapshot,
        turn_id="local-1",
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
            turn_id="local-2",
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
        turn_id="local-3",
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
        turn_id="local-4",
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
        turn_id="local-5",
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
    adapter = _follow_up_adapter("follow-up-1")

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
        turn_id="follow-up-2",
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
    """A new Review turn receives a deterministic candidate thread."""
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


@pytest.mark.parametrize(
    ("query", "active"),
    [
        ("Review maize heat tolerance", False),
        ("What evidence supports that claim?", True),
        ("Rewrite the Evidence section to state the limitation.", True),
        ("Now investigate maize heat tolerance using a new source set.", True),
    ],
)
def test_prepare_requires_turn_id_for_every_review_operation(
    query: str, active: bool
) -> None:
    """The adapter rejects missing turn identity for every operation."""
    snapshot = _checkpoint_state() if active else None
    with pytest.raises(ReviewClarificationError, match="turn id"):
        ReviewConversationAdapter().prepare(
            _projection(query, active=active), snapshot=snapshot
        )


@pytest.mark.asyncio
async def test_prepare_from_agent_rejects_stale_snapshot() -> None:
    """A request-local checkpoint never substitutes for the stable thread."""

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Return no checkpoint for the stale-snapshot probe."""
        return {}

    empty_agent = _agent_with_app(SimpleNamespace(aget_state=aget_state))

    projection = _projection("What evidence supports that claim?")
    adapter = ReviewConversationAdapter()
    adapter.prepare(
        projection, snapshot=_checkpoint_state(), turn_id="stale-stable"
    )

    with pytest.raises(ReviewClarificationError, match="checkpoint"):
        await adapter.prepare_from_agent(
            projection,
            empty_agent,
            _THREAD_ID,
            turn_id="stale-stable",
        )


@pytest.mark.asyncio
async def test_prepare_from_agent_does_not_reuse_a_prior_turn_id() -> None:
    """A missing current envelope identity cannot run a new Review graph."""

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Return no checkpoint for the stale-turn probe."""
        return {}

    empty_agent = _agent_with_app(SimpleNamespace(aget_state=aget_state))

    projection = _projection("Review maize heat tolerance", active=False)
    adapter = ReviewConversationAdapter()
    adapter.prepare(projection, turn_id="stale-turn")

    with pytest.raises(ReviewClarificationError, match="turn id"):
        await adapter.prepare_from_agent(projection, empty_agent, _THREAD_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Review maize heat tolerance",
        "What evidence supports that claim?",
        "Rewrite the Evidence section to state the limitation.",
        "Now investigate maize heat tolerance using a new source set.",
    ],
)
async def test_prepare_from_agent_requires_turn_id_before_checkpoint_read(
    query: str,
) -> None:
    """Every context operation fails before reading without turn identity."""
    reads = 0

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Record an attempted checkpoint read and return the fixture state."""
        nonlocal reads
        reads += 1
        return _checkpoint_state()

    empty_agent = _agent_with_app(SimpleNamespace(aget_state=aget_state))

    projection = _projection(
        query, active=query != "Review maize heat tolerance"
    )
    with pytest.raises(ReviewClarificationError, match="turn id"):
        await ReviewConversationAdapter().prepare_from_agent(
            projection, empty_agent, _THREAD_ID
        )
    assert reads == 0


@pytest.mark.asyncio
async def test_missing_candidate_fails_readiness_before_settlement() -> None:
    """A successful public answer cannot stage without a durable candidate."""

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Return no candidate checkpoint for the readiness probe."""
        return {}

    empty_agent = _agent_with_app(SimpleNamespace(aget_state=aget_state))

    projection = _projection("Review maize heat tolerance", active=False)
    adapter = ReviewConversationAdapter()
    await adapter.prepare_from_agent(
        projection, empty_agent, _THREAD_ID, turn_id="10"
    )
    adapter.capture_result(
        {"choices": [{"message": {"content": "Current answer."}}]}
    )

    assert await adapter.validate_settlement_candidate() is False
    assert adapter.settlement_ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            {
                "version": 1,
                "operation": "new_review",
                "stable_thread_id": _THREAD_ID,
                "turn_id": "malformed-1",
                "settlement_state": "pending",
            },
            "candidate",
        ),
        (
            {
                "version": 1,
                "operation": "unknown",
                "stable_thread_id": _THREAD_ID,
                "candidate_thread_id": "candidate",
                "turn_id": "malformed-2",
                "settlement_state": "pending",
            },
            "metadata",
        ),
        (
            {
                "version": 1,
                "operation": "follow_up",
                "stable_thread_id": _THREAD_ID,
                "candidate_thread_id": None,
                "turn_id": "malformed-3",
                "settlement_state": "unknown",
            },
            "state",
        ),
    ],
)
async def test_restore_settlement_rejects_malformed_metadata(
    metadata: dict[str, Any], message: str
) -> None:
    """Restart reconstruction rejects malformed private metadata."""
    reads = 0

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Fail if malformed metadata reaches the checkpoint reader."""
        nonlocal reads
        reads += 1
        raise AssertionError("malformed metadata must not read a checkpoint")

    agent = _agent_with_app(SimpleNamespace(aget_state=aget_state))

    with pytest.raises(ReviewClarificationError, match=message):
        await ReviewConversationAdapter().restore_settlement(
            metadata,
            agent,
            {"choices": [{"message": {"content": "Current answer."}}]},
        )
    assert reads == 0


@pytest.mark.asyncio
async def test_restore_settlement_requires_a_ready_candidate_report() -> None:
    """A durable candidate without a current report cannot be promoted."""

    async def aget_state(config: dict[str, Any]) -> dict[str, Any]:
        """Return the stable snapshot and an incomplete candidate state."""
        if config["configurable"]["thread_id"] == _THREAD_ID:
            return _checkpoint_state()
        return {"original_user_query": "Review candidate"}

    agent = _agent_with_app(SimpleNamespace(aget_state=aget_state))

    candidate = f"{_THREAD_ID}:candidate"
    with pytest.raises(ReviewClarificationError, match="candidate checkpoint"):
        await ReviewConversationAdapter().restore_settlement(
            {
                "version": 1,
                "operation": "new_review",
                "stable_thread_id": _THREAD_ID,
                "candidate_thread_id": candidate,
                "turn_id": "restart-1",
                "report_revision": 4,
                "settlement_state": "pending",
            },
            agent,
            {"choices": [{"message": {"content": "Current answer."}}]},
        )


@pytest.mark.asyncio
async def test_restore_settlement_requires_the_active_report_document() -> (
    None
):
    """A follow-up cannot be reconstructed from a semantic snapshot alone."""

    async def aget_state(_config: dict[str, Any]) -> dict[str, Any]:
        """Return a semantic snapshot without the required report document."""
        return {
            "original_user_query": "Review drought tolerance in rice",
            "research_dimensions": ["Evidence"],
        }

    agent = _agent_with_app(SimpleNamespace(aget_state=aget_state))

    with pytest.raises(ReviewClarificationError, match="report document"):
        await ReviewConversationAdapter().restore_settlement(
            {
                "version": 1,
                "operation": "follow_up",
                "stable_thread_id": _THREAD_ID,
                "candidate_thread_id": None,
                "turn_id": "restart-2",
                "report_revision": 4,
                "settlement_state": "pending",
            },
            agent,
            {"choices": [{"message": {"content": "Current answer."}}]},
        )
