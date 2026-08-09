# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline boundary scenarios for five-agent conversation continuity."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import tests.conftest as test_config
from tests.support.research_fakes import local_server_pytest_generate_tests

from mcp_server_phytomni.agents.brief_gene.conversation import (
    BriefGeneConversationAdapter,
    BriefGeneConversationOperation,
)
from mcp_server_phytomni.agents.review.conversation import _candidate_thread_id
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.conversation_context.models import (
    ArtifactRefV1,
    ContextDelta,
    ContextEntity,
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
    AgentSelection,
    AsyncAgentAcceptance,
    ConversationContextService,
    PrepareStatus,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

pytestmark = pytest.mark.server


_original_layer_marker = getattr(test_config, "_layer_marker_for_item")


pytest_generate_tests = local_server_pytest_generate_tests(
    _original_layer_marker, __file__, test_config
)


_CANONICAL_AGENT_IDS = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)
_ARTIFACT_A = ArtifactRefV1(
    artifact_id="artifact-owner-a",
    display_name="owner A artifact",
)
_ARTIFACT_B = ArtifactRefV1(
    artifact_id="artifact-owner-b",
    display_name="owner B artifact",
)


@dataclass
class _BriefGeneScenarioState:
    """Mutable observations shared by the Brief Gene fake and assertions."""

    calls: list[tuple[str, list[str], str]]
    operations: list[BriefGeneConversationOperation]
    follow_up_prompts: list[str]
    results: list[dict[str, Any]]
    gene_ids: tuple[str, str]
    artifacts: tuple[ArtifactRefV1, ArtifactRefV1]


def _conversation_key(number: int) -> UUID:
    """Return a stable opaque key for one in-process scenario."""
    return UUID(f"00000000-0000-0000-0000-{number:012d}")


def _ledger_version(turn_id: str) -> str:
    """Return a deterministic 64-character ledger version."""
    return f"{int(turn_id):064x}"


def _envelope(
    **options: Any,
) -> ConversationEnvelopeV1:
    """Build the actual Pydantic envelope used at the Bot boundary."""
    key = options["key"]
    turn_id = options["turn_id"]
    message = options["message"]
    mode = options.get("mode", "expert")
    requested_agent_id = options.get("requested_agent_id")
    allowed_agent_ids = options.get("allowed_agent_ids", ("ChatAgent",))
    base_version = options.get("base_version", 0)
    operation = options.get("operation", "append")
    artifacts = options.get("artifacts", ())
    history = options.get("history")
    return ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(key),
            "dialogue_id": str(_conversation_key(key.int % 1000 + 1000)),
            "turn_id": turn_id,
            "request_id": f"request-{turn_id}",
            "operation": operation,
            "mode": mode,
            "current_message": {"content": message, "locale": "en-US"},
            "requested_agent_id": requested_agent_id,
            "allowed_agent_ids": list(allowed_agent_ids),
            "ledger_cursor": int(turn_id),
            "ledger_version": _ledger_version(turn_id),
            "base_business_context_version": base_version,
            "history_delta": history
            or [{"turn_id": turn_id, "role": "user", "content": message}],
            "artifact_refs": [
                item.model_dump(mode="json") for item in artifacts
            ],
        }
    )


def _service(
    tmp_path: Path,
    *,
    router: Callable[..., Awaitable[AgentSelection]],
    invoke: Callable[..., Awaitable[AgentOutcome]],
    delegate_async: Callable[..., Awaitable[AsyncAgentAcceptance]],
) -> ConversationContextService:
    """Construct a service with an isolated durable Bot store."""
    return ConversationContextService(
        ConversationContextStore(str(tmp_path / "conversation.sqlite")),
        router=router,
        invoke=invoke,
        delegate_async=delegate_async,
    )


async def _commit(
    service: ConversationContextService,
    envelope: ConversationEnvelopeV1,
) -> None:
    """Acknowledge the same ledger version that was staged."""
    prepared = await service.execute_turn(envelope)
    assert prepared.status is PrepareStatus.RETURN_STAGED
    await service.acknowledge_settlement(envelope, envelope.ledger_version)


def _outcome(
    agent_id: str,
    *,
    delta: ContextDelta | None = None,
    summary: str | None = "ASSISTANT_SUMMARY_OUTPUT_SENTINEL",
    result: dict[str, Any] | None = None,
    private_stage_metadata: dict[str, Any] | None = None,
) -> AgentOutcome:
    """Return a terminal fake result without a full answer or report."""
    return AgentOutcome(
        result=result or {"status": "succeeded", "agent": agent_id},
        assistant_summary=summary,
        context_delta=delta or ContextDelta(),
        private_stage_metadata=private_stage_metadata,
    )


def _async_acceptance_delegate(
    run_id: str = "run-opaque",
) -> Callable[..., Awaitable[AsyncAgentAcceptance]]:
    """Return the shared durable acceptance fake for async agents."""

    async def delegate(*_args: Any, **_kwargs: Any) -> AsyncAgentAcceptance:
        return AsyncAgentAcceptance(
            result={"id": run_id, "run_id": run_id, "status": "running"},
            status_code=202,
        )

    return delegate


def _knowledge_data_review_invoke(
    key: UUID,
    artifact: ArtifactRefV1,
    projections: dict[str, Any],
) -> Callable[..., Awaitable[AgentOutcome]]:
    """Build the three-agent output fake used by the projection scenario."""

    async def invoke(
        agent: str, _envelope: Any, projection: Any
    ) -> AgentOutcome:
        projections[agent] = projection
        if agent == "KnowledgeAgent":
            delta = ContextDelta(
                summary_update="bounded knowledge summary",
                entity_upserts=[
                    ContextEntity(
                        entity_id="entity-focus",
                        entity_type="dataset",
                        label="focus item",
                    )
                ],
                artifact_upserts=[artifact],
            )
            result: dict[str, Any] = {
                "status": "succeeded",
                "agent": agent,
                "answer": (
                    "# Evidence summary\n\n"
                    "KNOWLEDGE_ANSWER_OUTPUT_SENTINEL: sanitized evidence "
                    "text with bounded citations."
                ),
                "references": [
                    {"artifact_id": "artifact-evidence", "section": "results"}
                ],
            }
        elif agent == "DataAgent":
            delta = ContextDelta()
            result = {
                "status": "succeeded",
                "agent": agent,
                "formatted": {
                    "answer": "DATA_TABLE_OUTPUT_SENTINEL: comparison table",
                    "tabular": {
                        "columns": ["sample", "score"],
                        "rows": [["sample-a", 0.91], ["sample-b", 0.87]],
                    },
                },
            }
        else:
            delta = ContextDelta()
            result = {
                "status": "succeeded",
                "agent": agent,
                "report": (
                    "# Review report\n\n"
                    "REVIEW_REPORT_OUTPUT_SENTINEL: sanitized report text "
                    "with a bounded conclusion."
                ),
                "table": {
                    "columns": ["criterion", "finding"],
                    "rows": [["coverage", "bounded"]],
                },
            }
        private = None
        if agent == "ReviewAgent":
            stable = agent_thread_id(key, agent)
            private = {
                "version": 1,
                "operation": "new_review",
                "stable_thread_id": stable,
                "candidate_thread_id": _candidate_thread_id(stable, "3"),
                "turn_id": "3",
                "report_revision": 0,
                "settlement_state": "pending",
            }
        return _outcome(
            agent,
            delta=delta,
            result=result,
            private_stage_metadata=private,
        )

    return invoke


def _assert_knowledge_data_review_staged(
    prepared: Any,
    service: ConversationContextService,
    key: UUID,
    projections: dict[str, Any],
    raw_output_sentinels: tuple[str, ...],
) -> str:
    """Assert the staged Review projection and return its safe context text."""
    artifact = projections["DataAgent"].artifact_refs[0]
    assert prepared.stage is not None
    assert set(projections) == {"KnowledgeAgent", "DataAgent", "ReviewAgent"}
    assert projections["KnowledgeAgent"].active_entities == []
    for projection in (
        projections["DataAgent"],
        projections["ReviewAgent"],
    ):
        assert [item.entity_id for item in projection.active_entities] == [
            "entity-focus"
        ]
        assert projection.artifact_refs == [artifact]
        assert "full answer" not in projection.task_summary
    assert prepared.stored_turn is not None
    committed = service.store.load_context(str(key))
    assert committed is not None
    context = committed.context
    assert context["version"] == 2
    assert context["task_summary"] == "bounded knowledge summary"
    assert context["active_entities"] == [
        {
            "entity_id": "entity-focus",
            "entity_type": "dataset",
            "label": "focus item",
        }
    ]
    assert context["artifact_index"] == [artifact.model_dump(mode="json")]
    assert [turn["role"] for turn in context["recent_turns"]] == [
        "user",
        "user",
    ]
    assert context["assistant_summaries"] == []
    committed_context = json.dumps(context, sort_keys=True)
    staged_delta = json.dumps(prepared.stored_turn.delta, sort_keys=True)
    proposed_context = json.dumps(
        (
            prepared.context.model_dump(mode="json")
            if prepared.context is not None
            else {}
        ),
        sort_keys=True,
    )
    assert prepared.stage.selected_agent_id == "ReviewAgent"
    assert prepared.stage.route_source == "explicit_selection"
    assert prepared.stage.context_degraded is False
    assert prepared.stored_turn.stage_metadata is not None
    review_stage = prepared.stored_turn.stage_metadata["_review_settlement"]
    assert review_stage["settlement_state"] == "pending"
    assert review_stage["candidate_thread_id"] == _candidate_thread_id(
        review_stage["stable_thread_id"], "3"
    )
    assert prepared.result is not None
    visible_result = json.dumps(prepared.result, sort_keys=True)
    assert "REVIEW_REPORT_OUTPUT_SENTINEL" in visible_result
    assert "table" in visible_result
    for sentinel in raw_output_sentinels:
        assert sentinel not in committed_context
        assert sentinel not in staged_delta
        assert sentinel not in proposed_context
    return committed_context


async def _assert_knowledge_data_review_replay(
    service: ConversationContextService,
    third: ConversationEnvelopeV1,
    prepared: Any,
    committed_context: str,
    raw_output_sentinels: tuple[str, ...],
) -> None:
    """Assert replay returns the same safe result without raw outputs."""
    replayed = await service.execute_turn(third)
    assert replayed.status is PrepareStatus.RETURN_STAGED
    assert replayed.result == prepared.result
    replayed_context = service.store.load_context(str(third.conversation_key))
    assert replayed_context is not None
    replayed_context_json = json.dumps(
        replayed_context.context, sort_keys=True
    )
    for sentinel in raw_output_sentinels:
        assert sentinel not in replayed_context_json
    assert '"answer"' not in committed_context
    assert '"report"' not in committed_context
    assert '"tabular"' not in committed_context
    assert '"table"' not in committed_context


def _brief_gene_invoke(
    state: _BriefGeneScenarioState,
) -> Callable[..., Awaitable[AgentOutcome]]:
    """Build the Brief Gene adapter fake for three sequential operations."""

    async def invoke(
        agent: str, envelope: Any, projection: Any
    ) -> AgentOutcome:
        state.calls.append(
            (
                envelope.current_message.content,
                [item.entity_id for item in projection.active_entities],
                agent,
            )
        )
        adapter = BriefGeneConversationAdapter()
        prepared = adapter.prepare(projection)
        operation = prepared["operation"]
        state.operations.append(operation)
        if operation is BriefGeneConversationOperation.FOLLOW_UP:

            async def follow_up_chat(prompt: str) -> dict[str, Any]:
                state.follow_up_prompts.append(prompt)
                return {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    "BRIEF_FOLLOW_UP_ANSWER_SENTINEL: "
                                    "bounded follow-up answer."
                                )
                            }
                        }
                    ]
                }

            result = await adapter.follow_up(follow_up_chat)
            state.results.append(result)
            return _outcome(
                agent,
                delta=adapter.delta(result),
                summary="bounded follow-up summary",
                result=result,
            )

        gene_id = (
            state.gene_ids[0]
            if not projection.active_entities
            else state.gene_ids[1]
        )
        artifact_ref = (
            state.artifacts[0]
            if gene_id == state.gene_ids[0]
            else state.artifacts[1]
        )
        evidence_id = (
            "evidence-first"
            if gene_id == state.gene_ids[0]
            else "evidence-second"
        )
        result = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "# Brief Gene Analysis\n\n"
                            f"BRIEF_REPORT_OUTPUT_SENTINEL_{gene_id}: "
                            "sanitized report body."
                        ),
                        "doc_list": [
                            {
                                "file_id": evidence_id,
                                "content": "sanitized evidence body",
                            }
                        ],
                    }
                }
            ],
            "phytomni_state": {
                "gene_id": gene_id,
                "species_code": (
                    "osa" if gene_id == state.gene_ids[0] else "ath"
                ),
                "report_summary": "bounded Brief Gene report summary",
                "report_artifact_id": artifact_ref.artifact_id,
                "report_revision": 1,
                "retrieved_docs": [
                    {
                        "file_id": evidence_id,
                        "content": "sanitized evidence body",
                    }
                ],
            },
        }
        resolved = {
            "gene_id": gene_id,
            "species_code": "osa" if gene_id == state.gene_ids[0] else "ath",
        }
        assert adapter.capture_result(result, resolved=resolved)
        state.results.append(result)
        return _outcome(
            agent,
            delta=adapter.delta(result),
            summary="bounded report summary",
            result=result,
        )

    return invoke


async def test_instant_chat_keeps_pronoun_continuity_and_chat_lock(
    tmp_path: Path,
) -> None:
    """Instant turns preserve a bounded entity while never invoking routing."""
    routed: list[tuple[str, tuple[str, ...]]] = []
    captured: list[tuple[str, str, list[str]]] = []

    async def router(
        query: str, allowed: tuple[str, ...], _context: Any
    ) -> AgentSelection:
        routed.append((query, allowed))
        return AgentSelection("KnowledgeAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, projection: Any
    ) -> AgentOutcome:
        captured.append(
            (
                agent,
                projection.current_query,
                [item.entity_id for item in projection.active_entities],
            )
        )
        delta = ContextDelta(
            entity_upserts=(
                [
                    ContextEntity(
                        entity_id="entity-focus",
                        entity_type="dataset",
                        label="focus item",
                    )
                ]
                if len(captured) == 1
                else []
            )
        )
        return _outcome(agent, delta=delta)

    async def delegate(*_args: Any, **_kwargs: Any) -> AsyncAgentAcceptance:
        raise AssertionError("Instant Chat must not delegate asynchronously")

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(1)
    first = _envelope(
        key=key,
        turn_id="1",
        message="Describe the focus item.",
        mode="instant",
    )
    await _commit(service, first)
    second = _envelope(
        key=key,
        turn_id="2",
        message="What about its status?",
        mode="instant",
        base_version=1,
    )
    prepared = await service.execute_turn(second)

    assert prepared.status is PrepareStatus.RETURN_STAGED
    assert prepared.stage is not None
    assert prepared.stage.selected_agent_id == "ChatAgent"
    assert prepared.stage.route_source == "instant_lock"
    assert captured[1] == (
        "ChatAgent",
        "What about its status?",
        ["entity-focus"],
    )
    assert not routed


async def test_expert_forced_then_automatic_uses_fresh_complete_allowlist(
    tmp_path: Path,
) -> None:
    """An explicit turn does not pin the next automatic Expert turn."""
    routed: list[tuple[str, ...]] = []
    invoked: list[str] = []

    async def router(
        _query: str, allowed: tuple[str, ...], _context: Any
    ) -> AgentSelection:
        routed.append(allowed)
        return AgentSelection("DataAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, _projection: Any
    ) -> AgentOutcome:
        invoked.append(agent)
        return _outcome(agent)

    delegate = _async_acceptance_delegate()

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(2)
    first = _envelope(
        key=key,
        turn_id="1",
        message="Find bounded evidence.",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=("KnowledgeAgent",),
    )
    await _commit(service, first)
    second = _envelope(
        key=key,
        turn_id="2",
        message="Now compare the evidence.",
        allowed_agent_ids=_CANONICAL_AGENT_IDS,
        base_version=1,
    )
    prepared = await service.execute_turn(second)

    assert prepared.stage is not None
    assert prepared.stage.selected_agent_id == "DataAgent"
    assert routed == [_CANONICAL_AGENT_IDS]
    assert invoked == ["KnowledgeAgent", "DataAgent"]


async def test_knowledge_data_review_preserve_refs_without_full_text(
    tmp_path: Path,
) -> None:
    """Cross-agent projections never retain complete tool outputs."""
    projections: dict[str, Any] = {}
    artifact = ArtifactRefV1(
        artifact_id="artifact-evidence",
        display_name="bounded evidence",
    )
    raw_output_sentinels = (
        "ASSISTANT_SUMMARY_OUTPUT_SENTINEL",
        "KNOWLEDGE_ANSWER_OUTPUT_SENTINEL",
        "DATA_TABLE_OUTPUT_SENTINEL",
        "REVIEW_REPORT_OUTPUT_SENTINEL",
    )

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    delegate = _async_acceptance_delegate()

    key = _conversation_key(3)
    service = _service(
        tmp_path,
        router=router,
        invoke=_knowledge_data_review_invoke(key, artifact, projections),
        delegate_async=delegate,
    )
    first = _envelope(
        key=key,
        turn_id="1",
        message="Find evidence for the focus item.",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=("KnowledgeAgent",),
        artifacts=(artifact,),
    )
    await _commit(service, first)
    second = _envelope(
        key=key,
        turn_id="2",
        message="Use the focus item and evidence.",
        requested_agent_id="DataAgent",
        allowed_agent_ids=("DataAgent",),
        base_version=1,
        artifacts=(artifact,),
    )
    await _commit(service, second)
    third = _envelope(
        key=key,
        turn_id="3",
        message="Review the bounded result.",
        requested_agent_id="ReviewAgent",
        allowed_agent_ids=("ReviewAgent",),
        base_version=2,
        artifacts=(artifact,),
    )
    prepared = await service.execute_turn(third)
    committed_context = _assert_knowledge_data_review_staged(
        prepared, service, key, projections, raw_output_sentinels
    )
    await _assert_knowledge_data_review_replay(
        service, third, prepared, committed_context, raw_output_sentinels
    )


async def test_brief_gene_context_reuses_and_replaces_identifier(
    tmp_path: Path,
) -> None:
    """Brief Gene follows up on prior state and replaces it for a new id."""
    first_gene = "Os01g0100100"
    second_gene = "At1g01010"
    first_artifact = ArtifactRefV1(
        artifact_id="brief-report-first",
        display_name="first Brief Gene report",
    )
    second_artifact = ArtifactRefV1(
        artifact_id="brief-report-second",
        display_name="second Brief Gene report",
    )
    state = _BriefGeneScenarioState(
        calls=[],
        operations=[],
        follow_up_prompts=[],
        results=[],
        gene_ids=(first_gene, second_gene),
        artifacts=(first_artifact, second_artifact),
    )

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("BriefGeneAgent", "ROUTER")

    delegate = _async_acceptance_delegate()

    service = _service(
        tmp_path,
        router=router,
        invoke=_brief_gene_invoke(state),
        delegate_async=delegate,
    )
    key = _conversation_key(4)
    await _commit(
        service,
        _envelope(
            key=key,
            turn_id="1",
            message=f"Identifier {first_gene}",
            allowed_agent_ids=("BriefGeneAgent",),
            requested_agent_id="BriefGeneAgent",
            artifacts=(first_artifact,),
        ),
    )
    await _commit(
        service,
        _envelope(
            key=key,
            turn_id="2",
            message="What about its function?",
            allowed_agent_ids=("BriefGeneAgent",),
            requested_agent_id="BriefGeneAgent",
            base_version=1,
            artifacts=(first_artifact,),
        ),
    )
    third = _envelope(
        key=key,
        turn_id="3",
        message=f"Use the new identifier {second_gene}.",
        allowed_agent_ids=("BriefGeneAgent",),
        requested_agent_id="BriefGeneAgent",
        base_version=2,
        artifacts=(second_artifact,),
    )
    prepared = await service.execute_turn(third)

    assert prepared.stage is not None
    assert state.calls[0] == (f"Identifier {first_gene}", [], "BriefGeneAgent")
    assert state.calls[1] == (
        "What about its function?",
        [
            "brief_gene.gene.os01g0100100",
            "brief_gene.species.osa",
            "brief_gene.report_revision.1",
            "brief_gene.evidence.evidence-first",
            "brief_gene.artifact.brief-report-first",
        ],
        "BriefGeneAgent",
    )
    assert state.calls[2] == (
        f"Use the new identifier {second_gene}.",
        [
            "brief_gene.gene.os01g0100100",
            "brief_gene.species.osa",
            "brief_gene.report_revision.1",
            "brief_gene.evidence.evidence-first",
            "brief_gene.artifact.brief-report-first",
        ],
        "BriefGeneAgent",
    )
    assert state.operations == [
        BriefGeneConversationOperation.NEW_REPORT,
        BriefGeneConversationOperation.FOLLOW_UP,
        BriefGeneConversationOperation.NEW_IDENTIFIER,
    ]
    assert len(state.follow_up_prompts) == 1
    assert first_gene in state.follow_up_prompts[0]
    assert "bounded Brief Gene report summary" in state.follow_up_prompts[0]
    assert "evidence-first" in state.follow_up_prompts[0]
    follow_up_result = state.results[1]
    assert follow_up_result["choices"][0]["message"]["doc_list"] == [
        {"file_id": "evidence-first"}
    ]
    assert "phytomni_state" not in follow_up_result
    assert "report_artifact_id" not in json.dumps(follow_up_result)
    assert state.results[0]["phytomni_state"]["gene_id"] == first_gene
    assert state.results[2]["phytomni_state"]["gene_id"] == second_gene
    assert prepared.result == state.results[2]
    assert prepared.context is not None
    assert {item.entity_id for item in prepared.context.active_entities} >= {
        "brief_gene.gene.at1g01010",
        "brief_gene.species.ath",
        "brief_gene.report_revision.1",
        "brief_gene.evidence.evidence-second",
    }
    assert "brief_gene.gene.os01g0100100" not in {
        item.entity_id for item in prepared.context.active_entities
    }
