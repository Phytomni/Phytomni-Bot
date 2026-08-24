# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Isolation and settlement tests for context turn orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from tests.unit.runtime.conversation_context.test_service import (
    _envelope,
    _service,
)

from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextDelta,
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
    SettlementMismatchError,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

pytestmark = pytest.mark.unit


@pytest.fixture(name="store")
def conversation_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> ConversationContextStore:
    """Use the configured local Bot task database only."""
    path = tmp_path / "server_tasks.db"
    monkeypatch.setenv("API_TASKS_DB_PATH", str(path))
    return ConversationContextStore()


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
