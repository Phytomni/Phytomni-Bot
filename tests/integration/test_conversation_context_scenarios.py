"""Offline boundary scenarios for five-agent conversation continuity."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from tests.support.chat_fakes import install_chat_handler

from mcp_server_phytomni.agents.review.conversation import _candidate_thread_id
from mcp_server_phytomni.api.schemas import ChatCompletionRequest
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
    ConversationContextService,
    PrepareStatus,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

pytestmark = pytest.mark.integration

_CANONICAL_AGENT_IDS = (
    "ChatAgent",
    "KnowledgeAgent",
    "DataAgent",
    "AnalystAgent",
    "ReviewAgent",
    "BriefGeneAgent",
    "DeepGenomeAgent",
    "InSilicoResearchAgent",
    "DigitalDesignAgent",
    "GeneNetworkAgent",
)
_ARTIFACT_A = ArtifactRefV1(
    artifact_id="artifact-owner-a",
    display_name="owner A artifact",
)
_ARTIFACT_B = ArtifactRefV1(
    artifact_id="artifact-owner-b",
    display_name="owner B artifact",
)


def _conversation_key(number: int) -> UUID:
    """Return a stable opaque key for one in-process scenario."""
    return UUID(f"00000000-0000-0000-0000-{number:012d}")


def _ledger_version(turn_id: str) -> str:
    """Return a deterministic 64-character ledger version."""
    return f"{int(turn_id):064x}"


def _envelope(
    *,
    key: UUID,
    turn_id: str,
    message: str,
    mode: str = "expert",
    requested_agent_id: str | None = None,
    allowed_agent_ids: tuple[str, ...] = ("ChatAgent",),
    base_version: int = 0,
    operation: str = "append",
    artifacts: tuple[ArtifactRefV1, ...] = (),
    history: list[dict[str, str]] | None = None,
) -> ConversationEnvelopeV1:
    """Build the actual Pydantic envelope used at the Bot boundary."""
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
    delegate_async: Callable[..., Awaitable[dict[str, object]]],
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
    summary: str | None = "bounded assistant summary",
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

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
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
    assert routed == []


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

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"status": "running", "run_id": "run-opaque"}

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
    """Cross-agent projections carry only bounded entities and artifact refs."""
    projections: dict[str, Any] = {}
    artifact = ArtifactRefV1(
        artifact_id="artifact-evidence",
        display_name="bounded evidence",
    )

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

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
        else:
            delta = ContextDelta()
        private = None
        if agent == "ReviewAgent":
            stable = agent_thread_id(key, agent)
            candidate_marker = {
                "version": 1,
                "operation": "new_review",
                "stable_thread_id": stable,
                "candidate_thread_id": _candidate_thread_id(stable, "3"),
                "turn_id": "3",
                "report_revision": 0,
                "settlement_state": "pending",
            }
            private = candidate_marker
        return _outcome(
            agent,
            delta=delta,
            result={
                "status": "succeeded",
                "agent": agent,
                "artifact_id": "artifact-evidence",
            },
            private_stage_metadata=private,
        )

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"status": "running", "run_id": "run-opaque"}

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(3)
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
    assert "full report" not in str(prepared.stored_turn.delta)


async def test_brief_gene_pronoun_reuses_context_and_new_identifier_replaces_it(
    tmp_path: Path,
) -> None:
    """Brief Gene receives the prior entity for a follow-up and a new one later."""
    calls: list[tuple[str, list[str], str]] = []

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("BriefGeneAgent", "ROUTER")

    async def invoke(
        agent: str, envelope: Any, projection: Any
    ) -> AgentOutcome:
        calls.append(
            (
                envelope.current_message.content,
                [item.entity_id for item in projection.active_entities],
                agent,
            )
        )
        is_new = "new identifier" in envelope.current_message.content
        delta = ContextDelta(
            entity_upserts=(
                [
                    ContextEntity(
                        entity_id=(
                            "entity-second" if is_new else "entity-first"
                        ),
                        entity_type="gene",
                        label="opaque identifier",
                    )
                ]
                if is_new or not projection.active_entities
                else []
            )
        )
        return _outcome(
            agent,
            delta=delta,
            result={
                "status": "succeeded",
                "agent": agent,
                "report_action": (
                    "new"
                    if is_new or not projection.active_entities
                    else "follow_up"
                ),
            },
        )

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"status": "running", "run_id": "run-opaque"}

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(4)
    await _commit(
        service,
        _envelope(
            key=key,
            turn_id="1",
            message="Identifier item-alpha",
            allowed_agent_ids=("BriefGeneAgent",),
            requested_agent_id="BriefGeneAgent",
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
        ),
    )
    third = _envelope(
        key=key,
        turn_id="3",
        message="Use the new identifier item-beta.",
        allowed_agent_ids=("BriefGeneAgent",),
        requested_agent_id="BriefGeneAgent",
        base_version=2,
    )
    prepared = await service.execute_turn(third)

    assert prepared.stage is not None
    assert calls[0] == ("Identifier item-alpha", [], "BriefGeneAgent")
    assert calls[1] == (
        "What about its function?",
        ["entity-first"],
        "BriefGeneAgent",
    )
    assert calls[2] == (
        "Use the new identifier item-beta.",
        ["entity-first"],
        "BriefGeneAgent",
    )
    assert prepared.result == {
        "status": "succeeded",
        "agent": "BriefGeneAgent",
        "report_action": "new",
    }


async def test_permission_revocation_blocks_explicit_and_automatic_selection(
    tmp_path: Path,
) -> None:
    """A fresh allowlist prevents both forced and routed use of a revoked agent."""
    invoked = 0

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("KnowledgeAgent", "ROUTER")

    async def invoke(*_args: Any, **_kwargs: Any) -> AgentOutcome:
        nonlocal invoked
        invoked += 1
        return _outcome("KnowledgeAgent")

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"status": "running", "run_id": "run-opaque"}

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(5)
    await _commit(
        service,
        _envelope(
            key=key,
            turn_id="1",
            message="Establish bounded evidence.",
            requested_agent_id="KnowledgeAgent",
            allowed_agent_ids=("KnowledgeAgent", "ChatAgent"),
        ),
    )
    with pytest.raises(ValueError, match="requested_agent_id must be allowed"):
        _envelope(
            key=key,
            turn_id="2",
            message="Use the revoked agent.",
            requested_agent_id="KnowledgeAgent",
            allowed_agent_ids=("ChatAgent",),
            base_version=1,
        )
    automatic = _envelope(
        key=key,
        turn_id="2",
        message="Route this with the current permission set.",
        allowed_agent_ids=("ChatAgent",),
        base_version=1,
    )
    with pytest.raises(ValueError, match="outside the envelope allowlist"):
        await service.execute_turn(automatic)
    assert invoked == 1
    failed = service.store.load_turn(str(key), "2")
    assert failed is not None
    assert failed.state == "failed"


async def test_bot_restart_rebuilds_from_bounded_go_summaries_and_runs_once(
    tmp_path: Path,
) -> None:
    """A fresh store asks for rebuild, then executes the rebuilt turn once."""
    invoked = 0

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, _projection: Any
    ) -> AgentOutcome:
        nonlocal invoked
        invoked += 1
        return _outcome(agent)

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"status": "running", "run_id": "run-opaque"}

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(6)
    stale = _envelope(
        key=key,
        turn_id="2",
        message="Continue from the bounded summary.",
        base_version=1,
        history=[
            {"turn_id": "1", "role": "user", "content": "First bounded turn."},
            {
                "turn_id": "2",
                "role": "assistant",
                "summary": "Prior bounded summary.",
            },
            {
                "turn_id": "2",
                "role": "user",
                "content": "Continue from the bounded summary.",
            },
        ],
    )
    first = await service.execute_turn(stale)
    assert first.status is PrepareStatus.REBUILD_REQUIRED
    assert invoked == 0

    rebuilt = _envelope(
        key=key,
        turn_id="3",
        message="Continue after rebuild.",
        operation="rebuild",
        history=[
            {"turn_id": "1", "role": "user", "content": "First bounded turn."},
            {
                "turn_id": "2",
                "role": "assistant",
                "summary": "Prior bounded summary.",
            },
            {
                "turn_id": "3",
                "role": "user",
                "content": "Continue after rebuild.",
            },
        ],
    )
    prepared = await service.execute_turn(rebuilt)

    assert prepared.status is PrepareStatus.RETURN_STAGED
    assert prepared.stage is not None
    assert prepared.stage.context_rebuilt is True
    assert invoked == 1


async def test_browser_and_go_retry_reuses_one_staged_turn_and_invocation(
    tmp_path: Path,
) -> None:
    """A changed transport request ID cannot duplicate a stable turn ID."""
    invoked = 0

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, _projection: Any
    ) -> AgentOutcome:
        nonlocal invoked
        invoked += 1
        return _outcome(agent)

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"status": "running", "run_id": "run-opaque"}

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(7)
    envelope = _envelope(
        key=key,
        turn_id="1",
        message="Execute once.",
    )
    await _commit(service, envelope)
    retry = envelope.model_copy(update={"request_id": "request-retry"})
    duplicate = await service.execute_turn(retry)

    assert duplicate.status is PrepareStatus.RETURN_COMMITTED
    assert duplicate.result == {"status": "succeeded", "agent": "ChatAgent"}
    assert invoked == 1


async def test_chat_cancellation_fails_turn_without_assistant_summary(
    tmp_path: Path,
) -> None:
    """Cancellation before terminal output never stages assistant context."""

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(*_args: Any, **_kwargs: Any) -> AgentOutcome:
        raise asyncio.CancelledError

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise AssertionError("Chat cancellation must not delegate")

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    key = _conversation_key(8)
    envelope = _envelope(
        key=key,
        turn_id="1",
        message="Cancel this chat turn.",
    )
    with pytest.raises(asyncio.CancelledError):
        await service.execute_turn(envelope)

    stored = service.store.load_turn(str(key), "1")
    assert stored is not None
    assert stored.state == "failed"
    assert service.store.load_context(str(key)) is None


async def test_cross_owner_boundary_isolates_dialogues_and_artifacts(
    tmp_path: Path,
) -> None:
    """An owner-scoped gateway rejects foreign keys before Bot execution."""
    captured: list[tuple[str, list[str]]] = []

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("ChatAgent", "ROUTER")

    async def invoke(
        agent: str, _envelope: Any, projection: Any
    ) -> AgentOutcome:
        captured.append(
            (agent, [item.artifact_id for item in projection.artifact_refs])
        )
        return _outcome(agent)

    async def delegate(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {"status": "running", "run_id": "run-opaque"}

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    owner_keys = {
        "owner-a": _conversation_key(9),
        "owner-b": _conversation_key(10),
    }

    async def gateway(owner: str, envelope: ConversationEnvelopeV1) -> Any:
        if owner_keys.get(owner) != envelope.conversation_key:
            raise PermissionError("dialogue is not owned by caller")
        return await service.execute_turn(envelope)

    owner_a_turn = _envelope(
        key=owner_keys["owner-a"],
        turn_id="1",
        message="Owner A request.",
        artifacts=(_ARTIFACT_A,),
    )
    await gateway("owner-a", owner_a_turn)
    foreign = owner_a_turn.model_copy(update={"request_id": "request-foreign"})
    with pytest.raises(PermissionError, match="not owned"):
        await gateway("owner-b", foreign)
    owner_b_turn = _envelope(
        key=owner_keys["owner-b"],
        turn_id="1",
        message="Owner B request.",
        artifacts=(_ARTIFACT_B,),
    )
    await gateway("owner-b", owner_b_turn)

    assert captured == [
        ("ChatAgent", ["artifact-owner-a"]),
        ("ChatAgent", ["artifact-owner-b"]),
    ]


async def test_async_expert_selection_keeps_running_202_lifecycle(
    tmp_path: Path,
) -> None:
    """Async Expert selection delegates a running result without sync staging."""
    invoked: list[str] = []
    delegated: list[str] = []

    async def router(*_args: Any, **_kwargs: Any) -> AgentSelection:
        return AgentSelection("AnalystAgent", "ROUTER")

    async def invoke(*_args: Any, **_kwargs: Any) -> AgentOutcome:
        invoked.append("sync")
        return _outcome("AnalystAgent")

    async def delegate(agent: str, _envelope: Any) -> dict[str, object]:
        delegated.append(agent)
        return {
            "status": "running",
            "id": "run-opaque",
            "task_ids": ["task-opaque"],
        }

    service = _service(
        tmp_path, router=router, invoke=invoke, delegate_async=delegate
    )
    prepared = await service.execute_turn(
        _envelope(
            key=_conversation_key(11),
            turn_id="1",
            message="Submit the bounded analysis.",
            allowed_agent_ids=_CANONICAL_AGENT_IDS,
        )
    )

    assert prepared.status is PrepareStatus.READY
    assert prepared.result == {
        "status": "running",
        "id": "run-opaque",
        "task_ids": ["task-opaque"],
    }
    assert delegated == ["AnalystAgent"]
    assert invoked == []
    assert prepared.stage is None


async def test_legacy_request_and_response_shape_stay_v0_when_context_is_off(
    api_client: Any,
    issued_api_key: str,
    chat_completion: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a V1 envelope, the ordinary ChatCompletion path is unchanged."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "0")
    captured: dict[str, Any] = {}
    install_chat_handler(monkeypatch, captured, content="legacy answer")

    request = ChatCompletionRequest(
        model="phyto-chat",
        messages=[{"role": "user", "content": "legacy request"}],
    )
    assert request.conversation is None
    assert request.model_dump(exclude_none=True) == {
        "model": "phyto-chat",
        "messages": [{"role": "user", "content": "legacy request"}],
        "stream": False,
    }

    response = await chat_completion(
        api_client,
        issued_api_key,
        content="legacy request",
    )
    body = response.json()
    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["model"] == "phyto-chat"
    assert body["choices"][0]["message"]["content"] == "legacy answer"
    assert "conversation_context" not in body
    assert captured["user_query"] == "legacy request"
