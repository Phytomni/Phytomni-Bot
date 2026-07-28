# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Unit tests for context turn orchestration and settlement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest

from mcp_server_phytomni.runtime.conversation_context.models import (
    ArtifactRefV1,
    BusinessContext,
    ContextDelta,
    ContextEntity,
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
    AgentSelection,
    ConversationContextService,
    PrepareStatus,
    SettlementMismatchError,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

pytestmark = pytest.mark.unit

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
_DIALOGUE_ID = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad8")


def _envelope(
    *,
    turn_id: str = "1",
    mode: str = "expert",
    operation: str = "append",
    requested_agent_id: str | None = None,
    allowed_agent_ids: list[str] | None = None,
    ledger_cursor: int = 1,
    ledger_version: str = "a" * 64,
    base_business_context_version: int = 0,
    artifact_refs: list[dict[str, str]] | None = None,
) -> ConversationEnvelopeV1:
    """Build one bounded gateway envelope for the service seam."""
    return ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": str(_CONVERSATION_KEY),
            "dialogue_id": str(_DIALOGUE_ID),
            "turn_id": turn_id,
            "request_id": f"request-{turn_id}",
            "operation": operation,
            "mode": mode,
            "current_message": {
                "content": "keep rice samples",
                "locale": "en-US",
            },
            "requested_agent_id": requested_agent_id,
            "allowed_agent_ids": allowed_agent_ids
            or ["ChatAgent", "KnowledgeAgent", "DataAgent"],
            "ledger_cursor": ledger_cursor,
            "ledger_version": ledger_version,
            "base_business_context_version": base_business_context_version,
            "history_delta": [
                {
                    "turn_id": turn_id,
                    "role": "user",
                    "content": "keep rice samples",
                }
            ],
            "artifact_refs": artifact_refs or [],
        }
    )


@pytest.fixture
def store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> ConversationContextStore:
    """Use the configured local Bot task database only."""
    path = tmp_path / "server_tasks.db"
    monkeypatch.setenv("API_TASKS_DB_PATH", str(path))
    return ConversationContextStore()


def _service(
    store: ConversationContextStore,
    *,
    router: Callable[..., Awaitable[AgentSelection]] | None = None,
    invoke: Callable[..., Awaitable[AgentOutcome]] | None = None,
    delegate: Callable[..., Awaitable[dict[str, object]]] | None = None,
) -> ConversationContextService:
    """Create an injected service with deterministic terminal output."""

    async def default_router(
        _query: str, allowed: tuple[str, ...], _context: BusinessContext
    ) -> AgentSelection:
        return AgentSelection(allowed[-1], "ROUTER_SELECTED")

    async def default_invoke(
        _agent: str, _envelope: ConversationEnvelopeV1, _projection: object
    ) -> AgentOutcome:
        return AgentOutcome(
            result={"answer": "terminal"},
            assistant_summary="terminal summary",
            context_delta=ContextDelta(summary_update="terminal summary"),
        )

    async def default_delegate(
        _agent: str, _envelope: ConversationEnvelopeV1
    ) -> dict[str, object]:
        return {"status": "accepted", "run_id": "run-1"}

    return ConversationContextService(
        store,
        router=router or default_router,
        invoke=invoke or default_invoke,
        delegate_async=delegate or default_delegate,
    )


@pytest.mark.asyncio
async def test_instant_selects_chat_without_router(
    store: ConversationContextStore,
) -> None:
    """Instant stays locked to ChatAgent regardless of router availability."""
    routed = False

    async def router(*_args: object) -> AgentSelection:
        nonlocal routed
        routed = True
        raise AssertionError("instant must not invoke the router")

    result = await _service(store, router=router).execute_turn(
        _envelope(mode="instant", allowed_agent_ids=["ChatAgent"])
    )

    assert result.status is PrepareStatus.RETURN_STAGED
    assert result.stage is not None
    assert result.stage.selected_agent_id == "ChatAgent"
    assert result.stage.route_source == "instant_lock"
    assert routed is False


@pytest.mark.asyncio
async def test_forced_expert_selects_requested_allowlisted_agent(
    store: ConversationContextStore,
) -> None:
    """An explicit Expert selection bypasses automatic routing."""
    result = await _service(store).execute_turn(
        _envelope(requested_agent_id="KnowledgeAgent")
    )

    assert result.stage is not None
    assert result.stage.selected_agent_id == "KnowledgeAgent"
    assert result.stage.route_source == "explicit_selection"


@pytest.mark.asyncio
async def test_unforced_expert_passes_complete_ordered_allowlist(
    store: ConversationContextStore,
) -> None:
    """The router receives Go's whole ordered allowlist, not a V1 subset."""
    received: tuple[str, ...] | None = None

    async def router(
        _query: str, allowed: tuple[str, ...], _context: BusinessContext
    ) -> AgentSelection:
        nonlocal received
        received = allowed
        return AgentSelection("DataAgent", "ROUTER_SELECTED")

    allowed = ["ReviewAgent", "DataAgent", "DeepGenomeAgent"]
    await _service(store, router=router).execute_turn(
        _envelope(allowed_agent_ids=allowed)
    )

    assert received == tuple(allowed)


@pytest.mark.asyncio
async def test_duplicate_staged_and_committed_turns_do_not_reinvoke_agent(
    store: ConversationContextStore,
) -> None:
    """Retries reuse staged output, then expose committed settlement metadata."""
    calls = 0

    async def invoke(*_args: object) -> AgentOutcome:
        nonlocal calls
        calls += 1
        return AgentOutcome(
            result={"answer": "once"},
            assistant_summary="once",
            context_delta=ContextDelta(summary_update="once"),
        )

    service = _service(store, invoke=invoke)
    envelope = _envelope()
    first = await service.execute_turn(envelope)
    duplicate = await service.execute_turn(envelope)
    await service.acknowledge_settlement(envelope, "b" * 64)
    committed = await service.execute_turn(envelope)

    assert first.status is PrepareStatus.RETURN_STAGED
    assert duplicate.status is PrepareStatus.RETURN_STAGED
    assert duplicate.result == {"answer": "once"}
    assert committed.status is PrepareStatus.RETURN_COMMITTED
    assert committed.stage is not None
    assert calls == 1


@pytest.mark.asyncio
async def test_settlement_persists_acknowledged_ledger_for_next_append(
    store: ConversationContextStore,
) -> None:
    """A settlement version becomes the valid base for the next ledger turn."""
    service = _service(store)
    first = _envelope()

    await service.execute_turn(first)
    await service.acknowledge_settlement(first, "b" * 64)
    next_turn = await service.prepare_turn(
        _envelope(
            turn_id="2",
            ledger_cursor=2,
            ledger_version="b" * 64,
            base_business_context_version=1,
        )
    )

    assert next_turn.status is PrepareStatus.READY


@pytest.mark.asyncio
async def test_duplicate_in_progress_turn_returns_explicit_status(
    store: ConversationContextStore,
) -> None:
    """A concurrent retry never starts a second agent invocation."""
    envelope = _envelope()
    store.begin_turn(
        str(envelope.conversation_key), envelope.turn_id, "append", 0
    )

    prepared = await _service(store).prepare_turn(envelope)

    assert prepared.status is PrepareStatus.IN_PROGRESS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_version, cursor, operation, base_version",
    [(1, 1, "append", 0), (1, 4, "append", 1), (1, 4, "replace", 1)],
)
async def test_stale_or_invalidated_context_requires_rebuild(
    store: ConversationContextStore,
    existing_version: int,
    cursor: int,
    operation: str,
    base_version: int,
) -> None:
    """Stale versions, append regression, and replace invalidation fail closed."""
    service = _service(store)
    first = await service.execute_turn(_envelope())
    assert first.stage is not None
    await service.acknowledge_settlement(_envelope(), "b" * 64)
    envelope = _envelope(
        turn_id="2",
        operation=operation,
        ledger_cursor=cursor,
        ledger_version="b" * 64,
        base_business_context_version=base_version,
    )

    prepared = await service.prepare_turn(envelope)

    assert prepared.status is PrepareStatus.REBUILD_REQUIRED
    assert existing_version == 1


@pytest.mark.asyncio
async def test_rebuild_required_turn_is_failed_and_retries_as_rebuild_required(
    store: ConversationContextStore,
) -> None:
    """A rejected context proposal cannot trap a retry in IN_PROGRESS."""
    service = _service(store)
    envelope = _envelope(base_business_context_version=3)

    first = await service.prepare_turn(envelope)
    duplicate = await service.prepare_turn(envelope)

    assert first.status is PrepareStatus.REBUILD_REQUIRED
    assert duplicate.status is PrepareStatus.REBUILD_REQUIRED
    assert (
        store.begin_turn(str(_CONVERSATION_KEY), "1", "append", 3).turn.state
        == "failed"
    )
    with pytest.raises(ValueError, match="duplicate turn proposal"):
        await service.prepare_turn(_envelope(base_business_context_version=2))


@pytest.mark.asyncio
async def test_missing_or_schema_incompatible_context_requires_rebuild(
    store: ConversationContextStore,
) -> None:
    """Only a version-zero initial context may rebuild locally from a ledger."""
    service = _service(store)
    missing = await service.prepare_turn(
        _envelope(base_business_context_version=3)
    )
    assert missing.status is PrepareStatus.REBUILD_REQUIRED

    first = await service.execute_turn(_envelope(turn_id="2"))
    assert first.stage is not None
    await service.acknowledge_settlement(_envelope(turn_id="2"), "b" * 64)
    with store._write() as connection:
        connection.execute(
            "UPDATE conversation_contexts SET schema_version = 99 WHERE conversation_key = ?",
            (str(_CONVERSATION_KEY),),
        )
    incompatible = await service.prepare_turn(
        _envelope(
            turn_id="3",
            ledger_cursor=3,
            ledger_version="b" * 64,
            base_business_context_version=1,
        )
    )
    assert incompatible.status is PrepareStatus.REBUILD_REQUIRED


@pytest.mark.asyncio
async def test_delta_failure_stages_degraded_but_failed_or_canceled_does_not(
    store: ConversationContextStore,
) -> None:
    """A visible answer survives delta failure; nonterminal output has no summary."""
    outcomes = iter(
        [
            AgentOutcome(
                result={"answer": "visible"},
                assistant_summary="not retained",
                context_delta=ContextDelta(summary_update="not retained"),
                context_delta_error=True,
            ),
            AgentOutcome(result={"answer": "failed"}, status="failed"),
            AgentOutcome(result={"answer": "canceled"}, status="canceled"),
        ]
    )

    async def invoke(*_args: object) -> AgentOutcome:
        return next(outcomes)

    service = _service(store, invoke=invoke)
    degraded = await service.execute_turn(_envelope())
    failed = await service.execute_turn(_envelope(turn_id="2"))
    canceled = await service.execute_turn(_envelope(turn_id="3"))

    assert degraded.stage is not None
    assert degraded.stage.context_degraded is True
    assert degraded.context is not None
    assert degraded.context.task_summary == ""
    assert failed.status is PrepareStatus.IN_PROGRESS
    assert canceled.status is PrepareStatus.IN_PROGRESS
    assert (
        store.begin_turn(str(_CONVERSATION_KEY), "2", "append", 0).turn.state
        == "failed"
    )
    assert (
        store.begin_turn(str(_CONVERSATION_KEY), "3", "append", 0).turn.state
        == "failed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["degraded", "invalid", "candidate"])
async def test_review_invalid_outcome_fails_before_staging(
    store: ConversationContextStore,
    failure: str,
) -> None:
    """Review failures never become healthy empty-delta stages."""
    turn_id = {"degraded": "21", "invalid": "22", "candidate": "23"}[failure]
    metadata: dict[str, object] = {
        "version": 1,
        "operation": "new_review",
        "stable_thread_id": "review-stable",
        "candidate_thread_id": "review-candidate",
        "turn_id": turn_id,
        "report_revision": 0,
        "settlement_state": "pending",
    }
    if failure == "candidate":
        metadata.pop("candidate_thread_id")
    if failure == "degraded":
        outcome = AgentOutcome(
            result={"answer": "visible"},
            context_delta=ContextDelta(summary_update="must not stage"),
            context_delta_error=True,
            private_stage_metadata=metadata,
        )
    elif failure == "invalid":
        outcome = AgentOutcome(
            result={"answer": "visible"},
            context_delta=ContextDelta(
                artifact_upserts=[
                    ArtifactRefV1(
                        artifact_id="untrusted",
                        display_name="untrusted",
                    )
                ]
            ),
            private_stage_metadata=metadata,
        )
    else:
        outcome = AgentOutcome(
            result={"answer": "visible"},
            context_delta=ContextDelta(),
            private_stage_metadata=metadata,
        )

    async def invoke(*_args: object) -> AgentOutcome:
        return outcome

    envelope = _envelope(
        turn_id=turn_id,
        requested_agent_id="ReviewAgent",
        allowed_agent_ids=["ReviewAgent"],
    )
    result = await _service(store, invoke=invoke).execute_turn(envelope)

    assert result.status is PrepareStatus.IN_PROGRESS
    assert result.stage is None
    stored = store.load_turn(str(_CONVERSATION_KEY), turn_id)
    assert stored is not None
    assert stored.state == "failed"
    assert stored.delta is None


@pytest.mark.asyncio
async def test_invalid_delta_is_discarded_before_degraded_staging(
    store: ConversationContextStore,
) -> None:
    """An invalid artifact delta cannot mutate the successful answer context."""
    unauthorized = ArtifactRefV1(
        artifact_id="not-authorized",
        display_name="untrusted output",
    )

    async def invoke(*_args: object) -> AgentOutcome:
        return AgentOutcome(
            result={"answer": "visible"},
            assistant_summary="must not persist",
            context_delta=ContextDelta(
                summary_update="must not persist",
                entity_upserts=[
                    ContextEntity(
                        entity_id="gene-1",
                        entity_type="gene",
                        label="rice gene",
                    )
                ],
                artifact_upserts=[unauthorized],
            ),
        )

    result = await _service(store, invoke=invoke).execute_turn(_envelope())

    assert result.status is PrepareStatus.RETURN_STAGED
    assert result.stage is not None
    assert result.stage.context_degraded is True
    assert result.context is not None
    assert result.context.task_summary == ""
    assert result.context.active_entities == []
    assert result.context.assistant_summaries == []
    assert result.context.artifact_index == []


@pytest.mark.asyncio
async def test_valid_entity_and_artifact_delta_stages_successfully(
    store: ConversationContextStore,
) -> None:
    """Keyed entity and artifact merges replace existing matching IDs."""
    initial_artifact = ArtifactRefV1(
        artifact_id="artifact-1",
        display_name="initial results table",
    )
    updated_artifact = ArtifactRefV1(
        artifact_id="artifact-1",
        display_name="updated results table",
    )
    outcomes = iter(
        (
            AgentOutcome(
                result={"answer": "first"},
                context_delta=ContextDelta(
                    entity_upserts=[
                        ContextEntity(
                            entity_id="gene-1",
                            entity_type="gene",
                            label="initial rice gene",
                        )
                    ],
                    artifact_upserts=[initial_artifact],
                ),
            ),
            AgentOutcome(
                result={"answer": "second"},
                context_delta=ContextDelta(
                    entity_upserts=[
                        ContextEntity(
                            entity_id="gene-1",
                            entity_type="gene",
                            label="updated rice gene",
                        )
                    ],
                    artifact_upserts=[updated_artifact],
                ),
            ),
        )
    )

    async def invoke(*_args: object) -> AgentOutcome:
        return next(outcomes)

    service = _service(store, invoke=invoke)
    first = _envelope(
        artifact_refs=[initial_artifact.model_dump(mode="json")],
    )
    await service.execute_turn(first)
    await service.acknowledge_settlement(first, "b" * 64)
    result = await service.execute_turn(
        _envelope(
            turn_id="2",
            ledger_cursor=2,
            ledger_version="b" * 64,
            base_business_context_version=1,
            artifact_refs=[updated_artifact.model_dump(mode="json")],
        )
    )

    assert result.status is PrepareStatus.RETURN_STAGED
    assert result.context is not None
    assert [
        (item.entity_id, item.label) for item in result.context.active_entities
    ] == [("gene-1", "updated rice gene")]
    assert [
        (item.artifact_id, item.display_name)
        for item in result.context.artifact_index
    ] == [("artifact-1", "updated results table")]


@pytest.mark.asyncio
async def test_incremental_advance_preserves_unpaired_user_before_later_pair(
    store: ConversationContextStore,
) -> None:
    """Normal advance appends ordered turns without collapsing history."""
    outcomes = iter(
        (
            AgentOutcome(result={"answer": "U2"}),
            AgentOutcome(
                result={"answer": "A3"},
                assistant_summary="A3",
                context_delta=ContextDelta(summary_update="A3"),
            ),
        )
    )

    async def invoke(*_args: object) -> AgentOutcome:
        return next(outcomes)

    service = _service(store, invoke=invoke)
    first = _envelope()
    await service.execute_turn(first)
    await service.acknowledge_settlement(first, "b" * 64)
    second_payload = _envelope(
        turn_id="2",
        ledger_cursor=2,
        ledger_version="b" * 64,
        base_business_context_version=1,
    ).model_dump(mode="json")
    second_payload["current_message"] = {
        "content": "U3",
        "locale": "en-US",
    }
    second_payload["history_delta"] = [
        {"turn_id": "2", "role": "user", "content": "U3"}
    ]
    second = await service.execute_turn(
        ConversationEnvelopeV1.model_validate(second_payload)
    )

    assert second.context is not None
    assert [
        (item.role, item.content) for item in second.context.recent_turns
    ] == [
        ("user", "keep rice samples"),
        ("user", "U3"),
        ("assistant", "A3"),
    ]
    assert second.context.recent_user_turns == ["keep rice samples", "U3"]
    assert second.context.assistant_summaries == ["A3"]


@pytest.mark.asyncio
async def test_duplicate_turns_reconstruct_staged_metadata(
    store: ConversationContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retries preserve route and degradation metadata across settlement."""
    from mcp_server_phytomni.runtime.conversation_context import (
        service as module,
    )

    original_projection = module.build_context_projection

    def truncated_projection(*args: object, **kwargs: object):
        return original_projection(*args, **kwargs).model_copy(
            update={"context_truncated": True}
        )

    async def invoke(*_args: object) -> AgentOutcome:
        return AgentOutcome(
            result={"answer": "visible"},
            context_delta_error=True,
        )

    monkeypatch.setattr(
        module, "build_context_projection", truncated_projection
    )
    service = _service(store, invoke=invoke)
    envelope = _envelope(requested_agent_id="ChatAgent")
    first = await service.execute_turn(envelope)
    duplicate = await service.execute_turn(envelope)
    await service.acknowledge_settlement(envelope, "b" * 64)
    committed = await service.execute_turn(envelope)

    assert first.stage is not None
    assert first.stage.route_source == "explicit_selection"
    assert first.stage.route_reason_code == "EXPLICIT_SELECTION"
    assert first.stage.context_truncated is True
    assert first.stage.context_rebuilt is True
    assert first.stage.context_degraded is True
    assert duplicate.stage == first.stage
    assert committed.stage == first.stage


@pytest.mark.asyncio
async def test_review_settlement_metadata_is_durable_but_not_public(
    store: ConversationContextStore,
) -> None:
    """Opaque Review checkpoint identities survive retries without API leakage."""

    async def invoke(*_args: object) -> AgentOutcome:
        return AgentOutcome(
            result={"answer": "review"},
            context_delta=ContextDelta(summary_update="review"),
            private_stage_metadata={
                "version": 1,
                "operation": "new_review",
                "stable_thread_id": "ctx-stable",
                "candidate_thread_id": "ctx-candidate",
                "turn_id": "1",
                "report_revision": 0,
                "settlement_state": "pending",
            },
        )

    service = _service(store, invoke=invoke)
    envelope = _envelope(
        requested_agent_id="ReviewAgent", allowed_agent_ids=["ReviewAgent"]
    )
    prepared = await service.execute_turn(envelope)
    stored = store.load_turn(str(_CONVERSATION_KEY), envelope.turn_id)

    assert prepared.stage is not None
    assert stored is not None
    assert stored.stage_metadata is not None
    assert (
        stored.stage_metadata["_review_settlement"]["candidate_thread_id"]
        == "ctx-candidate"
    )
    assert "_review_settlement" not in prepared.stage.__dict__

    assert service.update_review_settlement_metadata(
        str(_CONVERSATION_KEY),
        envelope.turn_id,
        {"settlement_state": "promoted"},
    )
    updated = store.load_turn(str(_CONVERSATION_KEY), envelope.turn_id)
    assert updated is not None
    assert updated.stage_metadata is not None
    assert (
        updated.stage_metadata["_review_settlement"]["settlement_state"]
        == "promoted"
    )


@pytest.mark.asyncio
async def test_async_agent_delegates_without_projection_or_staging(
    store: ConversationContextStore,
) -> None:
    """Non-eligible Expert agents retain their current accepted-run lifecycle."""
    invoked = False

    async def invoke(*_args: object) -> AgentOutcome:
        nonlocal invoked
        invoked = True
        raise AssertionError("async agent must not use synchronous invocation")

    service = _service(store, invoke=invoke)
    envelope = _envelope(
        requested_agent_id="DeepGenomeAgent",
        allowed_agent_ids=["ChatAgent", "DeepGenomeAgent"],
    )
    result = await service.execute_turn(envelope)

    assert result.result == {"status": "accepted", "run_id": "run-1"}
    assert result.stage is None
    assert result.projection is None
    assert invoked is False


@pytest.mark.asyncio
async def test_settlement_acknowledgment_is_idempotent_and_checks_ledger(
    store: ConversationContextStore,
) -> None:
    """Only the staged ledger proposal may be committed, once."""
    service = _service(store)
    envelope = _envelope()
    await service.execute_turn(envelope)

    with pytest.raises(SettlementMismatchError):
        await service.acknowledge_settlement(
            _envelope(ledger_version="f" * 64), "b" * 64
        )
    first = await service.acknowledge_settlement(envelope, "b" * 64)
    repeated = await service.acknowledge_settlement(envelope, "b" * 64)

    assert first.context_version == repeated.context_version == 1
    with pytest.raises(SettlementMismatchError):
        await service.acknowledge_settlement(envelope, "c" * 64)


@pytest.mark.asyncio
async def test_different_conversations_execute_without_a_global_lock(
    store: ConversationContextStore,
) -> None:
    """One slow dialogue does not serialize an independent dialogue."""
    started: set[str] = set()
    release = asyncio.Event()

    async def invoke(
        _agent: str, envelope: ConversationEnvelopeV1, _projection: object
    ) -> AgentOutcome:
        started.add(envelope.turn_id)
        if len(started) == 2:
            release.set()
        await release.wait()
        return AgentOutcome(
            result={"answer": envelope.turn_id},
            assistant_summary="done",
            context_delta=ContextDelta(summary_update="done"),
        )

    service = _service(store, invoke=invoke)
    second = _envelope(turn_id="2").model_copy(
        update={
            "conversation_key": UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43b00")
        }
    )
    first_task = asyncio.create_task(service.execute_turn(_envelope()))
    second_task = asyncio.create_task(service.execute_turn(second))
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)

    assert started == {"1", "2"}
