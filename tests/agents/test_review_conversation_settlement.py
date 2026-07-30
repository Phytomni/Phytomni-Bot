# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable Review settlement and restart tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from mcp_server_phytomni.agents.review.conversation import (
    ReviewClarificationError,
    ReviewConversationAdapter,
)
from mcp_server_phytomni.runtime.conversation_context.adapters import (
    ConversationContextExecutor,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
    StagedTurn,
    StoredTurn,
)
from tests.agents.test_review_conversation import (
    _CONVERSATION_KEY,
    _THREAD_ID,
    _attach_agent,
    _candidate_review_state,
    _checkpoint_state,
    _projection,
    _seed_review_settlement,
    _stateful_review_agent,
)

pytestmark = pytest.mark.agent


async def test_review_ack_reconstructs_from_durable_metadata_after_restart(
    tmp_path: Any,
) -> None:
    """A fresh executor promotes a staged candidate without the old adapter."""
    stable_state = _checkpoint_state()
    candidate_state = _candidate_review_state()

    agent = _stateful_review_agent(
        stable_state,
        record_update_values=False,
    )
    prepared = ReviewConversationAdapter()
    _attach_agent(prepared, agent)
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
            result={"choices": [{"message": {"content": "answer"}}]},
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
        durable_metadata: Mapping[str, Any], staged_turn: StoredTurn
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
    acknowledgements = await asyncio.gather(
        executor.acknowledge_review_settlement_for_turn(
            key, "10", accepted=True
        ),
        executor.acknowledge_review_settlement_for_turn(
            key, "10", accepted=True
        ),
    )
    assert acknowledgements == [True, True]
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


@pytest.mark.asyncio
async def test_review_ack_claim_serializes_separate_executors(
    tmp_path: Any,
) -> None:
    """Separate workers share one durable Review claim boundary."""
    key, store, _metadata = _seed_review_settlement(tmp_path, "worker-1")
    started = asyncio.Event()
    release = asyncio.Event()
    settle_calls = 0

    class FakeAdapter:
        """Adapter double that blocks one settlement until released."""

        report_revision = 1

        def mark_failed(self) -> None:
            """Keep the failed branch side-effect free for this probe."""
            return None

        async def discard_pending_candidate(self) -> None:
            """Keep candidate cleanup side-effect free for this probe."""
            return None

        async def settle_async(self, _success: bool) -> int:
            """Block promotion so a second executor must wait on the claim."""
            nonlocal settle_calls
            settle_calls += 1
            started.set()
            await release.wait()
            return self.report_revision

    async def loader(
        _metadata: Mapping[str, Any], _staged_turn: StoredTurn
    ) -> ReviewConversationAdapter:
        return FakeAdapter()

    def executor() -> ConversationContextExecutor:
        return ConversationContextExecutor(
            store_factory=lambda: ConversationContextStore(store.db_path),
            select_agent=lambda *_args, **_kwargs: pytest.fail(
                "settlement must not route"
            ),
            review_settlement_loader=loader,
        )

    async def acknowledge(contender: ConversationContextExecutor) -> bool:
        """Acknowledge one contender through a concrete coroutine wrapper."""
        return await contender.acknowledge_review_settlement_for_turn(
            key, "worker-1", accepted=True
        )

    tasks: list[asyncio.Task[bool]] = [
        asyncio.create_task(acknowledge(executor())) for _ in range(2)
    ]
    await started.wait()
    await asyncio.sleep(0)
    assert settle_calls == 1
    release.set()
    results = await asyncio.gather(*tasks)

    assert sorted(results) == [True, True]
    assert settle_calls == 1
    stored = store.load_turn(key, "worker-1")
    assert stored is not None
    assert stored.stage_metadata is not None
    assert (
        stored.stage_metadata["_review_settlement"]["settlement_state"]
        == "promoted"
    )


@pytest.mark.asyncio
async def test_review_ack_fence_blocks_promotion_after_tombstone(
    tmp_path: Any,
) -> None:
    """A tombstone waits for an in-flight promotion before cleanup."""
    key, store, metadata = _seed_review_settlement(tmp_path, "fenced-1")
    started = asyncio.Event()
    release = asyncio.Event()
    settle_calls = 0

    class FencedAdapter:
        """Adapter double that verifies a settlement fence before promotion."""

        report_revision = 1

        def __init__(self) -> None:
            self._fence: Any = None

        def set_settlement_fence(self, fence: Any) -> None:
            """Store the claim fence installed by the executor."""
            self._fence = fence

        def mark_failed(self) -> None:
            """Keep the failed branch side-effect free for this probe."""
            return None

        async def discard_pending_candidate(self) -> None:
            """Keep candidate cleanup side-effect free for this probe."""
            return None

        async def settle_async(self, _success: bool) -> int:
            """Release the blocked promotion only while its claim is valid."""
            nonlocal settle_calls
            settle_calls += 1
            started.set()
            await release.wait()
            assert self._fence is not None
            if not self._fence():
                raise RuntimeError("Review settlement claim was fenced")
            return self.report_revision

    async def loader(
        _metadata: Mapping[str, Any], _staged_turn: StoredTurn
    ) -> ReviewConversationAdapter:
        return FencedAdapter()

    executor = ConversationContextExecutor(
        store_factory=lambda: ConversationContextStore(store.db_path),
        select_agent=lambda *_args, **_kwargs: pytest.fail(
            "settlement must not route"
        ),
        review_settlement_loader=loader,
    )

    async def acknowledge() -> bool:
        """Acknowledge the fenced candidate through a concrete coroutine."""
        return await executor.acknowledge_review_settlement_for_turn(
            key, "fenced-1", accepted=True
        )

    task: asyncio.Task[bool] = asyncio.create_task(acknowledge())
    await started.wait()
    with ThreadPoolExecutor(max_workers=1) as pool:
        tombstone = pool.submit(store.tombstone, key)
        await asyncio.sleep(0.05)
        assert tombstone.done() is False
        release.set()
        await asyncio.sleep(0.05)
        assert tombstone.result(timeout=5) == (
            metadata["candidate_thread_id"],
        )

    assert await task is True
    assert settle_calls == 1
    assert store.load_turn(key, "fenced-1") is None


@pytest.mark.asyncio
async def test_review_loader_failure_persists_terminal_failed_marker(
    tmp_path: Any,
) -> None:
    """A restart reconstruction failure is not retried as a healthy ack."""
    key, store, _metadata = _seed_review_settlement(tmp_path, "loader-failure")
    loads = 0

    async def loader(
        _metadata: Mapping[str, Any], _staged_turn: StoredTurn
    ) -> ReviewConversationAdapter:
        nonlocal loads
        loads += 1
        raise ReviewClarificationError("restart checkpoint unavailable")

    executor = ConversationContextExecutor(
        store_factory=lambda: store,
        select_agent=lambda *_args, **_kwargs: pytest.fail(
            "settlement must not route"
        ),
        review_settlement_loader=loader,
    )
    with pytest.raises(ReviewClarificationError, match="checkpoint"):
        await executor.acknowledge_review_settlement_for_turn(
            key, "loader-failure", accepted=True
        )
    assert loads == 1
    failed = store.load_turn(key, "loader-failure")
    assert failed is not None
    assert failed.stage_metadata is not None
    assert (
        failed.stage_metadata["_review_settlement"]["settlement_state"]
        == "failed"
    )
    assert (
        await executor.acknowledge_review_settlement_for_turn(
            key, "loader-failure", accepted=True
        )
        is False
    )
    assert loads == 1
