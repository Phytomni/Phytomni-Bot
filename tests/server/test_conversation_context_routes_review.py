# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review settlement error edges for conversation-context HTTP routes."""

# pylint: disable=protected-access

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import httpx
import pytest
from tests.server.test_conversation_context_routes import (
    _CONVERSATION_KEY,
    _LEDGER_VERSION,
    _headers,
    _settlement_payload,
    _stage_turn,
    _tombstone_payload,
)
from tests.support.http_fakes import open_asgi_client

from mcp_server_phytomni.agents.review.conversation import _candidate_thread_id
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.routes import conversation_context
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
    ConversationTombstonedError,
    ReviewMutationLockTimeoutError,
    StoredTurn,
)

pytestmark = pytest.mark.server


@pytest.fixture(name="context_client")
async def enabled_context_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, ConversationContextStore]]:
    """Yield an enabled API client with isolated context and key stores."""
    tasks_db = tmp_path / "server_tasks.db"
    keys_db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tasks_db))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_db))
    key = ApiKeyStore(str(keys_db)).create(user_id="u1").api_key
    store = ConversationContextStore(str(tasks_db))
    async with open_asgi_client(
        monkeypatch, create_app(), base_url="http://api.context.test"
    ) as client:
        yield client, key, store


async def test_review_invalid_private_marker_fails_closed(context_client):
    """A non-mapping private marker cannot be promoted."""
    client, key, store = context_client
    _stage_turn(store, review_metadata="not-a-mapping")
    response = await client.post(
        "/v1/conversation-context/settle",
        headers=_headers(key),
        json=_settlement_payload(),
    )
    assert response.status_code == 503
    failed = store.load_turn(str(_CONVERSATION_KEY), "1")
    assert failed is not None and failed.state == "failed"


async def test_review_ack_false_and_missing_callback(monkeypatch, tmp_path):
    """Pending Review settlement requires a successful adapter ack."""
    tasks_db = tmp_path / "server_tasks.db"
    keys_db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tasks_db))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_db))
    key = ApiKeyStore(str(keys_db)).create(user_id="u1").api_key
    store = ConversationContextStore(str(tasks_db))
    stable = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    _stage_turn(
        store,
        review_metadata={
            "version": 1,
            "operation": "new_review",
            "stable_thread_id": stable,
            "candidate_thread_id": _candidate_thread_id(stable, "1"),
            "turn_id": "1",
            "report_revision": 0,
            "settlement_state": "pending",
        },
    )

    class FalseExecutor:
        """Executor that refuses Review settlement acknowledgement."""

        async def execute(self, **_kwargs: object) -> None:
            """Expert execution must not run on this settlement path."""
            raise AssertionError("no expert")

        async def acknowledge_review_settlement_for_turn(
            self, *_args: object, **_kwargs: object
        ) -> bool:
            """Refuse the Review ack so settlement stays pending."""
            return False

    async with open_asgi_client(
        monkeypatch,
        create_app(context_executor=FalseExecutor()),
        base_url="http://api.context.test",
    ) as client:
        denied = await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(),
        )
    assert denied.status_code == 503
    deps = conversation_context.ContextRouteDependencies(
        require_agents=lambda: None,
        get_store=lambda: store,
        acknowledge_review_settlement=None,
    )
    payload = conversation_context.ContextSettlementRequest(
        schema_version=1,
        conversation_key=_CONVERSATION_KEY,
        turn_id="1",
        ledger_version=_LEDGER_VERSION,
    )
    with pytest.raises(conversation_context.HTTPException) as missing:
        await conversation_context._acknowledge_review_settlement(
            str(_CONVERSATION_KEY),
            payload,
            store.load_turn(str(_CONVERSATION_KEY), "1"),
            deps,
        )
    assert missing.value.status_code == 503


async def test_settlement_and_tombstone_lock_timeouts(
    context_client, monkeypatch
):
    """Busy mutation locks fail closed without mutating context."""
    client, key, store = context_client
    _stage_turn(store)

    async def _busy(_store: object, **_kwargs: object) -> None:
        raise ReviewMutationLockTimeoutError()

    monkeypatch.setattr(
        conversation_context, "acquire_review_mutation_lock", _busy
    )
    assert (
        await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(),
        )
    ).status_code == 503
    assert (
        await client.post(
            "/v1/conversation-context/tombstone",
            headers=_headers(key),
            json=_tombstone_payload(),
        )
    ).status_code == 503


async def test_commit_maps_storage_conflicts(context_client, monkeypatch):
    """Commit failures stay mapped to public settlement conflicts."""
    client, key, store = context_client
    _stage_turn(store)

    def _tombstone(*_args: object, **_kwargs: object) -> None:
        raise ConversationTombstonedError("t")

    def _missing(*_args: object, **_kwargs: object) -> None:
        raise KeyError("turn")

    monkeypatch.setattr(
        ConversationContextStore, "commit_staged_turn", _tombstone
    )
    assert (
        await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(),
        )
    ).status_code == 409
    monkeypatch.setattr(
        ConversationContextStore, "commit_staged_turn", _missing
    )
    assert (
        await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(),
        )
    ).status_code == 404


def _settlement_request() -> Any:
    """Build one Review settlement payload for helper tests."""
    return conversation_context.ContextSettlementRequest(
        schema_version=1,
        conversation_key=_CONVERSATION_KEY,
        turn_id="1",
        ledger_version=_LEDGER_VERSION,
    )


def _stored_turn(
    *,
    state: Literal["in_progress", "staged", "committed", "failed"],
    metadata: dict[str, object],
) -> StoredTurn:
    """Build one stored turn for Review preflight tests."""
    return StoredTurn(
        conversation_key=str(_CONVERSATION_KEY),
        turn_id="1",
        operation="append",
        base_context_version=0,
        state=state,
        selected_agent_id="ReviewAgent",
        route_source="instant_lock",
        result={},
        delta={},
        stage_metadata=metadata,
        ledger_version=_LEDGER_VERSION,
        created_at="now",
        updated_at="now",
        expires_at=None,
    )


async def test_review_validation_helpers_cover_conflict_states():
    """Review preflight rejects tombstones, version drift, and bad states."""
    payload = _settlement_request()
    staged = _stored_turn(
        state="staged",
        metadata={"_review_settlement": {"turn_id": "1"}},
    )
    with pytest.raises(conversation_context.HTTPException):
        conversation_context._validate_review_context_state(
            staged,
            {"settlement_state": "pending"},
            SimpleNamespace(state="tombstoned", context_version=1),
            payload,
        )
    with pytest.raises(conversation_context.HTTPException):
        conversation_context._validate_review_context_state(
            staged,
            {"settlement_state": "pending"},
            SimpleNamespace(state="active", context_version=3),
            payload,
        )
    failed = _stored_turn(state="failed", metadata={})
    with pytest.raises(conversation_context.HTTPException):
        conversation_context._validate_review_context_state(
            failed, {"settlement_state": "pending"}, None, payload
        )
    committed = _stored_turn(state="committed", metadata={})
    with pytest.raises(conversation_context.HTTPException):
        conversation_context._validate_review_context_state(
            committed, {"settlement_state": "pending"}, None, payload
        )
    with pytest.raises(conversation_context.HTTPException):
        conversation_context._validate_review_context_state(
            staged, {"settlement_state": "failed"}, None, payload
        )


async def test_load_review_turn_rejects_unbound_key_and_missing_metadata():
    """Review metadata must match the conversation key and stay bounded."""
    payload = _settlement_request()

    class _Store:
        """Store double that returns an unbound Review turn."""

        def load_turn(self, _key: str, _turn_id: str) -> StoredTurn:
            """Return a staged turn whose conversation key is not a UUID."""
            return StoredTurn(
                conversation_key="not-a-uuid",
                turn_id="1",
                operation="append",
                base_context_version=0,
                state="staged",
                selected_agent_id="ReviewAgent",
                route_source="instant_lock",
                result={},
                delta={},
                stage_metadata={
                    "_review_settlement": {
                        "version": 1,
                        "operation": "follow_up",
                        "stable_thread_id": "ctx-" + "a" * 64,
                        "turn_id": "1",
                        "report_revision": 0,
                        "settlement_state": "pending",
                    }
                },
                ledger_version=_LEDGER_VERSION,
                created_at="now",
                updated_at="now",
                expires_at=None,
            )

        def mark_review_settlement_failed(
            self, *_args: object, **_kwargs: object
        ) -> None:
            """Ignore settlement-failure bookkeeping in this test."""

        def mark_turn_failed(self, *_args: object, **_kwargs: object) -> None:
            """Ignore turn-failure bookkeeping in this test."""

    with pytest.raises(conversation_context.HTTPException) as invalid:
        conversation_context._load_and_validate_review_turn(
            cast(Any, _Store()), "not-a-uuid", payload
        )
    assert invalid.value.status_code == 503

    class _Bare(_Store):
        """Store double that returns a Chat turn without Review metadata."""

        def load_turn(self, _key: str, _turn_id: str) -> StoredTurn:
            """Return a staged Chat turn with empty stage metadata."""
            return StoredTurn(
                conversation_key=str(_CONVERSATION_KEY),
                turn_id="1",
                operation="append",
                base_context_version=0,
                state="staged",
                selected_agent_id="ChatAgent",
                route_source="instant_lock",
                result={},
                delta={},
                stage_metadata={},
                ledger_version=_LEDGER_VERSION,
                created_at="now",
                updated_at="now",
                expires_at=None,
            )

    with pytest.raises(conversation_context.HTTPException) as missing:
        conversation_context._load_and_validate_review_turn(
            cast(Any, _Bare()), str(_CONVERSATION_KEY), payload
        )
    assert missing.value.status_code == 409


async def test_delete_checkpoint_threads_skips_stable_ids(monkeypatch):
    """Candidate cleanup does not delete the stable Review thread twice."""

    class _Checkpointer:
        """Record deleted checkpoint thread ids."""

        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            """Record one deleted thread id."""
            self.deleted.append(thread_id)

    checkpointer = _Checkpointer()
    monkeypatch.setattr(
        conversation_context, "ensure_checkpointer", lambda: checkpointer
    )
    stable = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    await conversation_context._delete_checkpoint_threads(
        _CONVERSATION_KEY, (stable, "candidate-extra")
    )
    assert (
        checkpointer.deleted.count(stable) == 1
        and "candidate-extra" in checkpointer.deleted
    )
