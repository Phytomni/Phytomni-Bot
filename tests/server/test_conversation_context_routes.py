# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""HTTP contracts for the feature-gated conversation-context protocol."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
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
    StagedTurn,
)

pytestmark = pytest.mark.server

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
_LEDGER_VERSION = "a" * 64


@pytest.fixture(name="context_client")
async def enabled_context_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, ConversationContextStore]]:
    """Yield an enabled API client with isolated context and key stores."""
    tasks_db = tmp_path / "server_tasks.db"
    keys_db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "true")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tasks_db))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_db))
    key = ApiKeyStore(str(keys_db)).create(user_id="u1").api_key
    store = ConversationContextStore(str(tasks_db))
    async with open_asgi_client(
        monkeypatch, create_app(), base_url="http://api.context.test"
    ) as client:
        yield client, key, store


def _headers(key: str) -> dict[str, str]:
    """Build the existing bearer-key authorization header."""
    return {"Authorization": f"Bearer {key}"}


def _settlement_payload(
    *,
    turn_id: str = "1",
    ledger_version: str = _LEDGER_VERSION,
) -> dict[str, str | int]:
    """Build one bounded V1 settlement request."""
    return {
        "schema_version": 1,
        "conversation_key": str(_CONVERSATION_KEY),
        "turn_id": turn_id,
        "ledger_version": ledger_version,
    }


def _tombstone_payload() -> dict[str, str | int]:
    """Build one bounded V1 tombstone request."""
    return {
        "schema_version": 1,
        "conversation_key": str(_CONVERSATION_KEY),
    }


def _stage_turn(
    store: ConversationContextStore,
    *,
    turn_id: str = "1",
    base_version: int = 0,
    review_metadata: dict[str, object] | None = None,
) -> None:
    """Seed one staged terminal result without exposing it to HTTP."""
    store.begin_turn(str(_CONVERSATION_KEY), turn_id, "append", base_version)
    store.stage_turn(
        str(_CONVERSATION_KEY),
        turn_id,
        StagedTurn(
            operation="append",
            base_context_version=base_version,
            selected_agent_id="ChatAgent",
            route_source="instant_lock",
            result={"raw_result": "RAW_RESULT_MUST_NOT_LEAK"},
            delta={
                "schema_version": 1,
                "version": base_version + 1,
                "last_applied_ledger_cursor": base_version + 1,
                "last_applied_ledger_version": _LEDGER_VERSION,
                "observed_mode": "instant",
                "task_summary": "PRIVATE_SUMMARY_MUST_NOT_LEAK",
                "active_entities": [
                    {
                        "entity_id": "gene-1",
                        "entity_type": "gene",
                        "label": "PRIVATE_ENTITY_MUST_NOT_LEAK",
                    }
                ],
                "open_questions": [],
                "recent_user_turns": [],
                "assistant_summaries": ["PRIVATE_ASSISTANT_MUST_NOT_LEAK"],
                "artifact_index": [
                    {
                        "artifact_id": "artifact-1",
                        "display_name": "PRIVATE_ARTIFACT_MUST_NOT_LEAK",
                    }
                ],
                "per_agent_memory": {},
            },
            ledger_version=_LEDGER_VERSION,
            schema_version=1,
            ledger_cursor=base_version + 1,
            observed_mode="instant",
            stage_metadata=(
                {"_review_settlement": review_metadata}
                if review_metadata is not None
                else {}
            ),
        ),
    )


def _promote_review_marker(
    store: ConversationContextStore, key: str, turn_id: str
) -> None:
    """Model the production executor's durable reservation in route fakes."""
    turn = store.load_turn(key, turn_id)
    assert turn is not None
    claim = store.claim_review_settlement(
        key,
        turn_id,
        expected_ledger_version=turn.ledger_version,
        expected_base_context_version=turn.base_context_version,
    )
    assert claim.status == "claimed"
    assert claim.claim_token is not None
    assert claim.fence_token is not None
    reserved = store.reserve_review_settlement(
        key,
        turn_id,
        claim_token=claim.claim_token,
        fence_token=claim.fence_token,
        expected_ledger_version=turn.ledger_version,
        expected_base_context_version=turn.base_context_version,
    )
    assert reserved.status == "promoting"
    assert store.finalize_review_settlement(
        key,
        turn_id,
        claim_token=claim.claim_token,
        fence_token=claim.fence_token,
        state="promoted",
        report_revision=1,
    )


async def test_enabled_catalog_advertises_context_protocol(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
) -> None:
    """Enabled deployments publish only the top-level V1 protocol marker."""
    client, key, _store = context_client

    response = await client.get("/v1/agents", headers=_headers(key))

    assert response.status_code == 200
    body = response.json()
    assert body["protocols"] == {"conversation_context": [1]}
    assert all("conversation_context" not in row for row in body["data"])


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/v1/conversation-context/settle", _settlement_payload()),
        ("/v1/conversation-context/tombstone", _tombstone_payload()),
    ],
)
async def test_context_mutations_require_existing_agents_auth(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
    path: str,
    payload: dict[str, str | int],
) -> None:
    """Settlement and tombstone retain the normal authentication gate."""
    client, _key, _store = context_client

    response = await client.post(path, json=payload)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/v1/conversation-context/settle", _settlement_payload()),
        ("/v1/conversation-context/tombstone", _tombstone_payload()),
    ],
)
async def test_context_mutations_require_agents_scope(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
    tmp_path: Path,
    path: str,
    payload: dict[str, str | int],
) -> None:
    """Authenticated keys without agents scope cannot mutate context."""
    client, _key, _store = context_client
    restricted_key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite"))
        .create(user_id="restricted", scopes=["service"])
        .api_key
    )

    response = await client.post(
        path, headers=_headers(restricted_key), json=payload
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "path,payload",
    [
        (
            "/v1/conversation-context/settle",
            {**_settlement_payload(), "unexpected": "field"},
        ),
        (
            "/v1/conversation-context/settle",
            {**_settlement_payload(), "turn_id": "invalid"},
        ),
        (
            "/v1/conversation-context/tombstone",
            {**_tombstone_payload(), "unexpected": "field"},
        ),
        (
            "/v1/conversation-context/tombstone",
            {**_tombstone_payload(), "conversation_key": "invalid"},
        ),
    ],
)
async def test_context_mutations_reject_malformed_or_extra_fields(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
    path: str,
    payload: dict[str, object],
) -> None:
    """Strict V1 request models fail malformed and unknown fields."""
    client, key, _store = context_client

    response = await client.post(path, headers=_headers(key), json=payload)

    assert response.status_code == 422


async def test_disabled_context_mutations_return_not_found(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
) -> None:
    """A default-off deployment does not expose a half-active protocol."""
    for path, payload in (
        ("/v1/conversation-context/settle", _settlement_payload()),
        ("/v1/conversation-context/tombstone", _tombstone_payload()),
    ):
        response = await api_client.post(
            path, headers=_headers(issued_api_key), json=payload
        )
        assert response.status_code == 404
    schema = (await api_client.get("/openapi.json")).json()
    assert "/v1/conversation-context/settle" not in schema["paths"]
    assert "/v1/conversation-context/tombstone" not in schema["paths"]


async def test_settlement_commits_once_and_redacts_context_contents(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
) -> None:
    """A staged delta commits once without returning private data."""
    client, key, store = context_client
    _stage_turn(store)

    committed = await client.post(
        "/v1/conversation-context/settle",
        headers=_headers(key),
        json=_settlement_payload(),
    )
    repeated = await client.post(
        "/v1/conversation-context/settle",
        headers=_headers(key),
        json=_settlement_payload(),
    )

    assert committed.status_code == 200
    assert committed.json() == {
        "schema_version": 1,
        "state": "committed",
        "context_version": 1,
    }
    assert repeated.status_code == 200
    assert repeated.json() == {
        "schema_version": 1,
        "state": "already_applied",
        "context_version": 1,
    }
    for marker in (
        "PRIVATE_SUMMARY_MUST_NOT_LEAK",
        "PRIVATE_ENTITY_MUST_NOT_LEAK",
        "PRIVATE_ARTIFACT_MUST_NOT_LEAK",
        "PRIVATE_ASSISTANT_MUST_NOT_LEAK",
        "RAW_RESULT_MUST_NOT_LEAK",
    ):
        assert marker not in committed.text
        assert marker not in repeated.text


async def test_settlement_route_invokes_injected_review_ack_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The route promotes Review before committing shared context."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "true")
    tasks_db = tmp_path / "server_tasks.db"
    keys_db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tasks_db))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_db))
    key = ApiKeyStore(str(keys_db)).create(user_id="u1").api_key

    class SpyExecutor:
        """Record Review acknowledgement before context commit."""

        def __init__(self) -> None:
            """Initialize call and pre-ack context snapshots."""
            self.calls: list[tuple[str, str, bool, str]] = []
            self.context_versions_at_ack: list[int | None] = []

        async def execute(self, **_kwargs: object) -> object:
            """Reject accidental Expert execution in this route test."""
            raise AssertionError("the route test does not invoke Expert")

        async def acknowledge_review_settlement_for_turn(
            self,
            conversation_key: str,
            turn_id: str,
            **options: Any,
        ) -> bool:
            """Promote the staged marker and record the public call shape."""
            accepted = options["accepted"]
            staged_turn = options["staged_turn"]
            expected_ledger_version = options.get("expected_ledger_version")
            mutation_lock_held = options.get("mutation_lock_held", False)
            assert expected_ledger_version == _LEDGER_VERSION
            assert mutation_lock_held is True
            _promote_review_marker(store, conversation_key, turn_id)
            context = store.load_context(conversation_key)
            self.context_versions_at_ack.append(
                None if context is None else context.context_version
            )
            self.calls.append(
                (
                    conversation_key,
                    turn_id,
                    accepted,
                    getattr(staged_turn, "stage_metadata", {})[
                        "_review_settlement"
                    ]["candidate_thread_id"],
                )
            )
            return True

    executor = SpyExecutor()
    store = ConversationContextStore(str(tasks_db))
    stable_thread = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    metadata = {
        "version": 1,
        "operation": "new_review",
        "stable_thread_id": stable_thread,
        "candidate_thread_id": _candidate_thread_id(stable_thread, "1"),
        "turn_id": "1",
        "report_revision": 0,
        "settlement_state": "pending",
    }
    _stage_turn(store, review_metadata=metadata)

    async with open_asgi_client(
        monkeypatch,
        create_app(context_executor=executor),
        base_url="http://api.context.test",
    ) as client:
        response = await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(),
        )

    assert response.status_code == 200
    assert executor.calls == [
        (
            str(_CONVERSATION_KEY),
            "1",
            True,
            _candidate_thread_id(stable_thread, "1"),
        )
    ]
    assert executor.context_versions_at_ack == [None]
    assert "candidate_thread_id" not in response.text


async def test_review_promotion_failure_does_not_commit_shared_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed private promotion leaves the Bot context staged for retry."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "true")
    tasks_db = tmp_path / "server_tasks.db"
    keys_db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tasks_db))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_db))
    key = ApiKeyStore(str(keys_db)).create(user_id="u1").api_key
    store = ConversationContextStore(str(tasks_db))
    stable_thread = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    metadata = {
        "version": 1,
        "operation": "new_review",
        "stable_thread_id": stable_thread,
        "candidate_thread_id": _candidate_thread_id(stable_thread, "1"),
        "turn_id": "1",
        "report_revision": 0,
        "settlement_state": "pending",
    }
    _stage_turn(store, review_metadata=metadata)

    class FailingExecutor:
        """Reject private Review promotion while retaining staged state."""

        async def execute(self, **_kwargs: object) -> object:
            """Reject accidental Expert execution in this route test."""
            raise AssertionError("the route test does not invoke Expert")

        async def acknowledge_review_settlement_for_turn(
            self,
            conversation_key: str,
            _turn_id: str,
            **options: Any,
        ) -> bool:
            """Fail private promotion after checking the lock boundary."""
            accepted = options["accepted"]
            staged_turn = options["staged_turn"]
            expected_ledger_version = options.get("expected_ledger_version")
            mutation_lock_held = options.get("mutation_lock_held", False)
            assert accepted is True
            assert expected_ledger_version == _LEDGER_VERSION
            assert mutation_lock_held is True
            assert store.load_context(conversation_key) is None
            del staged_turn
            raise RuntimeError("private promotion unavailable")

    async with open_asgi_client(
        monkeypatch,
        create_app(context_executor=FailingExecutor()),
        base_url="http://api.context.test",
    ) as client:
        response = await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(),
        )

    assert response.status_code == 503
    assert store.load_context(str(_CONVERSATION_KEY)) is None
    staged = store.load_turn(str(_CONVERSATION_KEY), "1")
    assert staged is not None
    assert staged.state == "staged"


async def test_review_settlement_rejects_arbitrary_thread_namespace(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
) -> None:
    """A candidate derived from an arbitrary stable ID cannot settle."""
    client, key, store = context_client
    stable_thread = "ctx-arbitrary"
    _stage_turn(
        store,
        review_metadata={
            "version": 1,
            "operation": "new_review",
            "stable_thread_id": stable_thread,
            "candidate_thread_id": _candidate_thread_id(stable_thread, "1"),
            "turn_id": "1",
            "report_revision": 0,
            "settlement_state": "pending",
        },
    )

    response = await client.post(
        "/v1/conversation-context/settle",
        headers=_headers(key),
        json=_settlement_payload(),
    )

    assert response.status_code == 503
    assert store.load_context(str(_CONVERSATION_KEY)) is None
    failed = store.load_turn(str(_CONVERSATION_KEY), "1")
    assert failed is not None
    assert failed.stage_metadata is not None
    assert (
        failed.stage_metadata["_review_settlement"]["settlement_state"]
        == "failed"
    )


async def test_review_stale_ledger_is_rejected_before_private_ack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale Review request cannot promote before the ledger check."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "true")
    tasks_db = tmp_path / "server_tasks.db"
    keys_db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tasks_db))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_db))
    key = ApiKeyStore(str(keys_db)).create(user_id="u1").api_key
    store = ConversationContextStore(str(tasks_db))
    stable_thread = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    metadata = {
        "version": 1,
        "operation": "new_review",
        "stable_thread_id": stable_thread,
        "candidate_thread_id": _candidate_thread_id(stable_thread, "1"),
        "turn_id": "1",
        "report_revision": 0,
        "settlement_state": "pending",
    }
    _stage_turn(store, review_metadata=metadata)

    class SpyExecutor:
        """Count acknowledgements while stale requests are rejected."""

        calls = 0

        async def execute(self, **_kwargs: object) -> object:
            """Reject accidental Expert execution in this route test."""
            raise AssertionError("the route test does not invoke Expert")

        async def acknowledge_review_settlement_for_turn(
            self,
            _conversation_key: str,
            _turn_id: str,
            **options: Any,
        ) -> bool:
            """Count the accepted request and promote its marker."""
            accepted = options["accepted"]
            staged_turn = options["staged_turn"]
            expected_ledger_version = options.get("expected_ledger_version")
            mutation_lock_held = options.get("mutation_lock_held", False)
            assert accepted is True
            assert staged_turn is not None
            assert expected_ledger_version == _LEDGER_VERSION
            assert mutation_lock_held is True
            self.calls += 1
            _promote_review_marker(store, _conversation_key, _turn_id)
            return True

    executor = SpyExecutor()
    async with open_asgi_client(
        monkeypatch,
        create_app(context_executor=executor),
        base_url="http://api.context.test",
    ) as client:
        stale = await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(ledger_version="b" * 64),
        )
        assert stale.status_code == 409
        assert executor.calls == 0
        assert store.load_context(str(_CONVERSATION_KEY)) is None
        pending = store.load_turn(str(_CONVERSATION_KEY), "1")
        assert pending is not None
        assert pending.stage_metadata is not None
        assert pending.stage_metadata["_review_settlement"][
            "settlement_state"
        ] == ("pending")

        accepted = await client.post(
            "/v1/conversation-context/settle",
            headers=_headers(key),
            json=_settlement_payload(),
        )

    assert accepted.status_code == 200
    assert executor.calls == 1


async def test_review_reservation_serializes_competing_context_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A competing context commit cannot stale a reserved Review settlement."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "true")
    tasks_db = tmp_path / "server_tasks.db"
    keys_db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tasks_db))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(keys_db))
    key = ApiKeyStore(str(keys_db)).create(user_id="u1").api_key
    store = ConversationContextStore(str(tasks_db))
    stable_thread = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    _stage_turn(
        store,
        review_metadata={
            "version": 1,
            "operation": "new_review",
            "stable_thread_id": stable_thread,
            "candidate_thread_id": _candidate_thread_id(stable_thread, "1"),
            "turn_id": "1",
            "report_revision": 0,
            "settlement_state": "pending",
        },
    )
    _stage_turn(store, turn_id="2", base_version=0)
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingExecutor:
        """Hold Review acknowledgement while a competing turn waits."""

        async def execute(self, **_kwargs: object) -> object:
            """Reject accidental Expert execution in this route test."""
            raise AssertionError("the route test does not invoke Expert")

        async def acknowledge_review_settlement_for_turn(
            self,
            _conversation_key: str,
            _turn_id: str,
            **options: Any,
        ) -> bool:
            """Hold the mutation lock until the competing request is queued."""
            accepted = options["accepted"]
            staged_turn = options["staged_turn"]
            expected_ledger_version = options.get("expected_ledger_version")
            mutation_lock_held = options.get("mutation_lock_held", False)
            assert accepted is True
            assert staged_turn is not None
            assert expected_ledger_version == _LEDGER_VERSION
            assert mutation_lock_held is True
            started.set()
            await release.wait()
            _promote_review_marker(store, _conversation_key, _turn_id)
            return True

    async with open_asgi_client(
        monkeypatch,
        create_app(context_executor=BlockingExecutor()),
        base_url="http://api.context.test",
    ) as client:
        review_task = asyncio.create_task(
            client.post(
                "/v1/conversation-context/settle",
                headers=_headers(key),
                json=_settlement_payload(turn_id="1"),
            )
        )
        await started.wait()
        competing_task = asyncio.create_task(
            client.post(
                "/v1/conversation-context/settle",
                headers=_headers(key),
                json=_settlement_payload(turn_id="2"),
            )
        )
        await asyncio.sleep(0.05)
        assert competing_task.done() is False
        release.set()
        review_response, competing_response = await asyncio.gather(
            review_task, competing_task
        )

    assert review_response.status_code == 200
    assert competing_response.status_code == 409


async def test_settlement_rejects_unknown_or_mismatched_turns(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
) -> None:
    """Settlement fails closed when the staged proposal cannot be matched."""
    client, key, store = context_client
    _stage_turn(store)

    mismatch = await client.post(
        "/v1/conversation-context/settle",
        headers=_headers(key),
        json=_settlement_payload(ledger_version="b" * 64),
    )
    unknown = await client.post(
        "/v1/conversation-context/settle",
        headers=_headers(key),
        json=_settlement_payload(turn_id="2"),
    )

    assert mismatch.status_code == 409
    assert unknown.status_code == 404


async def test_tombstone_clears_state_and_deletes_sync_threads(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tombstone removes staged context and synchronous checkpoint threads."""
    class _Checkpointer:
        """Collect synchronous checkpoint deletions for tombstone tests."""

        def __init__(self) -> None:
            """Start with no deleted checkpoint threads."""
            self.deleted: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            """Record one checkpoint deletion."""
            self.deleted.append(thread_id)

        def deleted_thread_ids(self) -> tuple[str, ...]:
            """Return an immutable deletion snapshot for assertions."""
            return tuple(self.deleted)

    client, key, store = context_client
    _stage_turn(store)
    first = await client.post(
        "/v1/conversation-context/settle",
        headers=_headers(key),
        json=_settlement_payload(),
    )
    assert first.status_code == 200
    _stage_turn(store, turn_id="2", base_version=1)
    checkpointer = _Checkpointer()
    monkeypatch.setattr(
        conversation_context, "ensure_checkpointer", lambda: checkpointer
    )

    response = await client.post(
        "/v1/conversation-context/tombstone",
        headers=_headers(key),
        json=_tombstone_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "state": "tombstoned",
        "context_version": 1,
    }
    context = store.load_context(str(_CONVERSATION_KEY))
    assert context is not None
    assert context.context == {}
    assert context.checkpoint_cleanup_state == "complete"
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM conversation_turns "
            "WHERE conversation_key = ?",
            (str(_CONVERSATION_KEY),),
        ).fetchone() == (0,)
    assert set(checkpointer.deleted_thread_ids()) == {
        agent_thread_id(_CONVERSATION_KEY, agent_id)
        for agent_id in (
            "ChatAgent",
            "KnowledgeAgent",
            "DataAgent",
            "ReviewAgent",
            "BriefGeneAgent",
        )
    }
    with pytest.raises(ConversationTombstonedError):
        store.begin_turn(str(_CONVERSATION_KEY), "3", "append", 1)


async def test_tombstone_deletes_durable_review_candidate_thread(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tombstone cleanup includes candidate threads in staged metadata."""
    class _Checkpointer:
        """Collect candidate checkpoint deletions for tombstone tests."""

        def __init__(self) -> None:
            """Start with no deleted candidate threads."""
            self.deleted: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            """Record one candidate checkpoint deletion."""
            self.deleted.append(thread_id)

        def deleted_thread_ids(self) -> tuple[str, ...]:
            """Return an immutable deletion snapshot for assertions."""
            return tuple(self.deleted)

    client, key, store = context_client
    stable_thread = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    candidate_thread = _candidate_thread_id(stable_thread, "1")
    _stage_turn(
        store,
        review_metadata={
            "version": 1,
            "operation": "new_review",
            "stable_thread_id": stable_thread,
            "candidate_thread_id": candidate_thread,
            "turn_id": "1",
            "report_revision": 0,
            "settlement_state": "pending",
        },
    )
    checkpointer = _Checkpointer()
    monkeypatch.setattr(
        conversation_context, "ensure_checkpointer", lambda: checkpointer
    )

    response = await client.post(
        "/v1/conversation-context/tombstone",
        headers=_headers(key),
        json=_tombstone_payload(),
    )

    assert response.status_code == 200
    assert candidate_thread in checkpointer.deleted_thread_ids()


async def test_tombstone_retry_replays_durable_candidate_cleanup(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed cleanup retains the candidate thread for the next request."""
    class _Checkpointer:
        """Fail one cleanup attempt and retain its deletion history."""

        def __init__(self) -> None:
            """Start in failing mode with no deleted candidates."""
            self.fail = True
            self.deleted: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            """Record a deletion and optionally fail the cleanup attempt."""
            self.deleted.append(thread_id)
            if self.fail:
                raise RuntimeError("checkpoint cleanup unavailable")

        def deleted_thread_ids(self) -> tuple[str, ...]:
            """Return an immutable deletion snapshot for assertions."""
            return tuple(self.deleted)

    client, key, store = context_client
    stable_thread = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    candidate_thread = _candidate_thread_id(stable_thread, "1")
    _stage_turn(
        store,
        review_metadata={
            "version": 1,
            "operation": "scope_change",
            "stable_thread_id": stable_thread,
            "candidate_thread_id": candidate_thread,
            "turn_id": "1",
            "report_revision": 0,
            "settlement_state": "pending",
        },
    )
    checkpointer = _Checkpointer()
    monkeypatch.setattr(
        conversation_context, "ensure_checkpointer", lambda: checkpointer
    )

    first = await client.post(
        "/v1/conversation-context/tombstone",
        headers=_headers(key),
        json=_tombstone_payload(),
    )
    checkpointer.fail = False
    second = await client.post(
        "/v1/conversation-context/tombstone",
        headers=_headers(key),
        json=_tombstone_payload(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert candidate_thread in checkpointer.deleted_thread_ids()
    complete = store.load_context(str(_CONVERSATION_KEY))
    assert complete is not None
    assert complete.checkpoint_cleanup_state == "complete"


async def test_tombstone_is_idempotent_and_retries_pending_cleanup(
    context_client: tuple[httpx.AsyncClient, str, ConversationContextStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup failure retains retryable state and safe deletion."""
    class _Checkpointer:
        """Toggle a failing checkpoint cleanup for idempotence coverage."""

        fail = True

        async def adelete_thread(self, _thread_id: str) -> None:
            """Raise while failure mode is enabled."""
            if self.fail:
                raise RuntimeError("private checkpoint failure")

        def set_failure(self, enabled: bool) -> None:
            """Enable or disable the simulated cleanup failure."""
            self.fail = enabled

    client, key, store = context_client
    checkpointer = _Checkpointer()
    monkeypatch.setattr(
        conversation_context, "ensure_checkpointer", lambda: checkpointer
    )

    failed_cleanup = await client.post(
        "/v1/conversation-context/tombstone",
        headers=_headers(key),
        json=_tombstone_payload(),
    )
    pending = store.load_context(str(_CONVERSATION_KEY))
    checkpointer.set_failure(False)
    retried = await client.post(
        "/v1/conversation-context/tombstone",
        headers=_headers(key),
        json=_tombstone_payload(),
    )

    assert failed_cleanup.status_code == 200
    assert failed_cleanup.json()["state"] == "tombstoned"
    assert pending is not None
    assert pending.checkpoint_cleanup_state == "pending"
    assert retried.status_code == 200
    assert retried.json() == {
        "schema_version": 1,
        "state": "already_applied",
        "context_version": 0,
    }
    complete = store.load_context(str(_CONVERSATION_KEY))
    assert complete is not None
    assert complete.checkpoint_cleanup_state == "complete"
