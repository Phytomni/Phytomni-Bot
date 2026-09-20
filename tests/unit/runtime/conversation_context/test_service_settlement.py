# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Conversation-context async settlement and failure tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tests.unit.runtime.conversation_context.test_service import (
    _CONVERSATION_KEY,
    _envelope,
    _service,
)

from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
    AsyncAcceptanceError,
    AsyncAgentAcceptance,
    PrepareStatus,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

pytestmark = pytest.mark.unit


@pytest.fixture(name="store")
def conversation_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> ConversationContextStore:
    """Use an isolated configured task database for settlement tests."""
    path = tmp_path / "server_tasks.db"
    monkeypatch.setenv("API_TASKS_DB_PATH", str(path))
    return ConversationContextStore()


@pytest.mark.asyncio
async def test_async_agent_stages_durable_acceptance_without_projection(
    store: ConversationContextStore,
) -> None:
    """A durable async acceptance advances only bounded context metadata."""
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

    assert result.status is PrepareStatus.RETURN_STAGED
    assert result.projection is None
    assert result.stage is not None
    assert result.stage.selected_agent_id == "DeepGenomeAgent"
    assert result.stage.route_source == "explicit_selection"
    assert result.stage.context_truncated is False
    assert result.result is not None
    assert result.result["run_id"] == "run-1"
    assert result.context is not None
    assert result.context.recent_user_turns == ["keep rice samples"]
    assert "run-1" not in result.context.model_dump_json()
    assert invoked is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "acceptance",
    [
        AsyncAgentAcceptance(
            result={"id": "", "run_id": "", "status": "running"},
            status_code=202,
        ),
        AsyncAgentAcceptance(
            result={"id": "run-1", "run_id": "run-2", "status": "running"},
            status_code=202,
        ),
        AsyncAgentAcceptance(
            result={"id": "run-1", "run_id": "run-1", "status": "failed"},
            status_code=202,
        ),
        AsyncAgentAcceptance(
            result={"id": "run-1", "run_id": "run-1", "status": "running"},
            status_code=200,
        ),
    ],
)
async def test_async_acceptance_must_prove_durable_run(
    store: ConversationContextStore,
    acceptance: AsyncAgentAcceptance,
) -> None:
    """Malformed or non-202 async responses never stage context."""

    async def delegate(
        _agent: str, _envelope: ConversationEnvelopeV1
    ) -> AsyncAgentAcceptance:
        return acceptance

    service = _service(store, delegate=delegate)
    envelope = _envelope(
        requested_agent_id="DeepGenomeAgent",
        allowed_agent_ids=["ChatAgent", "DeepGenomeAgent"],
    )

    with pytest.raises(AsyncAcceptanceError):
        await service.execute_turn(envelope)

    stored = store.load_turn(str(_CONVERSATION_KEY), envelope.turn_id)
    assert stored is not None
    assert stored.state == "failed"
    assert stored.result is None
    assert stored.stage_metadata is None
    assert store.load_context(str(_CONVERSATION_KEY)) is None


@pytest.mark.asyncio
async def test_async_acceptance_replay_does_not_delegate_twice(
    store: ConversationContextStore,
) -> None:
    """Staged and committed async retries replay the original run identity."""
    calls = 0

    async def delegate(
        _agent: str, _envelope: ConversationEnvelopeV1
    ) -> AsyncAgentAcceptance:
        nonlocal calls
        calls += 1
        return AsyncAgentAcceptance(
            result={"id": "run-1", "run_id": "run-1", "status": "running"},
            status_code=202,
        )

    service = _service(store, delegate=delegate)
    envelope = _envelope(
        requested_agent_id="DeepGenomeAgent",
        allowed_agent_ids=["ChatAgent", "DeepGenomeAgent"],
    )

    first = await service.execute_turn(envelope)
    staged_retry = await service.execute_turn(
        envelope.model_copy(update={"request_id": "request-retry"})
    )
    await service.acknowledge_settlement(envelope, "b" * 64)
    committed_retry = await service.execute_turn(envelope)

    assert first.status is PrepareStatus.RETURN_STAGED
    assert staged_retry.status is PrepareStatus.RETURN_STAGED
    assert committed_retry.status is PrepareStatus.RETURN_COMMITTED
    assert first.result == staged_retry.result == committed_retry.result
    assert calls == 1


@pytest.mark.asyncio
async def test_sync_result_survives_context_stage_failure(
    store: ConversationContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal result remains valid when context staging cannot persist."""
    invoked = 0

    async def invoke(*_args: object) -> AgentOutcome:
        nonlocal invoked
        invoked += 1
        return AgentOutcome(result={"answer": "terminal"})

    def fail_stage(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("private storage detail")

    monkeypatch.setattr(store, "stage_turn", fail_stage)
    service = _service(store, invoke=invoke)
    envelope = _envelope()
    prepared = await service.execute_turn(envelope)
    retry = await service.execute_turn(envelope)

    assert prepared.result == {"answer": "terminal"}
    assert prepared.stage is None
    assert prepared.context_persistence_degraded is True
    assert retry.status is PrepareStatus.IN_PROGRESS
    assert invoked == 1


@pytest.mark.asyncio
async def test_async_acceptance_survives_context_stage_failure(
    store: ConversationContextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted async run remains opaque after context staging loss."""
    delegated = 0

    async def delegate(
        _agent: str, _envelope: ConversationEnvelopeV1
    ) -> AsyncAgentAcceptance:
        nonlocal delegated
        delegated += 1
        return AsyncAgentAcceptance(
            result={"id": "run-1", "run_id": "run-1", "status": "running"},
            status_code=202,
        )

    def fail_stage(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("private storage detail")

    monkeypatch.setattr(store, "stage_turn", fail_stage)
    service = _service(store, delegate=delegate)
    envelope = _envelope(
        requested_agent_id="DeepGenomeAgent",
        allowed_agent_ids=["DeepGenomeAgent"],
    )
    prepared = await service.execute_turn(envelope)
    retry = await service.execute_turn(envelope)

    assert prepared.result == {
        "id": "run-1",
        "run_id": "run-1",
        "status": "running",
    }
    assert prepared.stage is None
    assert prepared.context_persistence_degraded is True
    assert retry.status is PrepareStatus.IN_PROGRESS
    assert delegated == 1
