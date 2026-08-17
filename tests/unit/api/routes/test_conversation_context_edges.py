# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP error-path coverage for conversation-context routes."""

# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.api.routes import conversation_context as route_mod
from mcp_server_phytomni.api.routes.conversation_context import (
    ContextRouteDependencies,
)
from mcp_server_phytomni.api.schemas import (
    ContextSettlementRequest,
    ContextTombstoneRequest,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ContextVersionConflictError,
    ConversationContextStore,
    ConversationTombstonedError,
    ReviewMutationLockTimeoutError,
    StoredTurn,
)

pytestmark = pytest.mark.unit

_LEDGER = "a" * 64


def _payload(
    *,
    conversation_key: Any | None = None,
    turn_id: str = "1",
    ledger_version: str = _LEDGER,
) -> ContextSettlementRequest:
    """Build one settlement request with a valid ledger digest."""
    return ContextSettlementRequest(
        schema_version=1,
        conversation_key=conversation_key or uuid4(),
        turn_id=turn_id,
        ledger_version=ledger_version,
    )


def _turn(**overrides: Any) -> StoredTurn:
    """Build one durable turn row for route-unit tests."""
    values: dict[str, Any] = {
        "conversation_key": "key",
        "turn_id": "1",
        "operation": "stage",
        "base_context_version": 0,
        "state": "staged",
        "selected_agent_id": "ReviewAgent",
        "route_source": "router",
        "result": None,
        "delta": None,
        "stage_metadata": None,
        "ledger_version": _LEDGER,
        "created_at": "t0",
        "updated_at": "t0",
        "expires_at": None,
    }
    values.update(overrides)
    return StoredTurn(**values)


class _Store:
    """Minimal store seam for isolated route error tests."""

    def __init__(
        self,
        turn: StoredTurn | None = None,
        *,
        commit_error: BaseException | None = None,
    ) -> None:
        self.turn = turn
        self.commit_error = commit_error
        self.failed: list[tuple[str, str]] = []

    def load_turn(self, key: str, turn_id: str) -> StoredTurn | None:
        """Return the configured staged turn."""
        del key, turn_id
        return self.turn

    def mark_turn_failed(self, key: str, turn_id: str) -> None:
        """Record a failed private Review marker."""
        self.failed.append((key, turn_id))

    def mark_review_settlement_failed(
        self, key: str, turn_id: str, mutation_lock_held: bool = False
    ) -> None:
        """Record invalid Review settlement metadata."""
        del mutation_lock_held
        self.failed.append((key, turn_id))

    def commit_staged_turn(self, *args: Any, **kwargs: Any) -> Any:
        """Raise the configured commit failure, if any."""
        del args, kwargs
        if self.commit_error is not None:
            raise self.commit_error
        raise AssertionError("unexpected commit")

    def load_context(self, key: str) -> None:
        """Unused in these error-path tests."""
        del key


class _Lock:
    """Mutation lock stand-in that records release."""

    def __init__(self) -> None:
        self.released = False

    def acquire(self) -> bool:
        """These route doubles start already held."""
        return True

    def release(self) -> None:
        """Mark the lock released."""
        self.released = True


def _as_store(store: _Store) -> ConversationContextStore:
    """Treat one route double as the durable conversation store."""
    return cast(ConversationContextStore, store)


def _deps(store: _Store, callback: Any = None) -> ContextRouteDependencies:
    """Bind one fake store into the route dependency object."""
    return ContextRouteDependencies(
        require_agents=lambda: None,
        get_store=lambda: _as_store(store),
        acknowledge_review_settlement=callback,
    )


def test_load_review_turn_not_found() -> None:
    """A missing Review turn maps to HTTP 404."""
    payload = _payload()
    with pytest.raises(HTTPException) as caught:
        route_mod._load_and_validate_review_turn(
            _as_store(_Store()), "key", payload
        )
    assert caught.value.status_code == 404


def test_load_review_turn_invalid_private_marker() -> None:
    """A private marker without mapping metadata is 503."""
    store = _Store(_turn(stage_metadata={"_review_settlement": "broken"}))
    payload = _payload()
    with pytest.raises(HTTPException) as caught:
        route_mod._load_and_validate_review_turn(
            _as_store(store), "key", payload
        )
    assert caught.value.status_code == 503
    assert store.failed == [("key", "1")]


def test_load_review_turn_without_metadata_is_conflict() -> None:
    """A Review path without settlement metadata is a 409."""
    payload = _payload()
    with pytest.raises(HTTPException) as caught:
        route_mod._load_and_validate_review_turn(
            _as_store(_Store(_turn(stage_metadata={"other": 1}))),
            "key",
            payload,
        )
    assert caught.value.status_code == 409


def test_load_review_turn_invalid_conversation_key() -> None:
    """A non-UUID conversation key cannot match the stable thread id."""
    store = _Store(
        _turn(stage_metadata={"_review_settlement": {"turn_id": "1"}})
    )
    payload = _payload()
    with pytest.raises(HTTPException) as caught:
        route_mod._load_and_validate_review_turn(
            _as_store(store), "not-a-uuid", payload
        )
    assert caught.value.status_code == 503
    assert store.failed == [("not-a-uuid", "1")]


def test_validate_review_context_rejects_tombstone() -> None:
    """Tombstoned context cannot be settled."""
    with pytest.raises(HTTPException) as caught:
        route_mod._validate_review_context_state(
            _turn(),
            {"settlement_state": "pending"},
            SimpleNamespace(state="tombstoned", context_version=0),
            _payload(),
        )
    assert caught.value.status_code == 409


def test_validate_review_context_version_mismatch() -> None:
    """A staged turn must still sit on its base context version."""
    with pytest.raises(HTTPException) as caught:
        route_mod._validate_review_context_state(
            _turn(base_context_version=2),
            {"settlement_state": "pending"},
            SimpleNamespace(state="active", context_version=1),
            _payload(),
        )
    assert caught.value.status_code == 409


def test_validate_review_context_rejects_failed_state() -> None:
    """Only staged or committed Review turns can settle."""
    with pytest.raises(HTTPException) as caught:
        route_mod._validate_review_context_state(
            _turn(state="failed"),
            {"settlement_state": "pending"},
            None,
            _payload(),
        )
    assert caught.value.status_code == 409


def test_validate_review_context_committed_must_be_promoted() -> None:
    """A committed turn that is not promoted is a conflict."""
    with pytest.raises(HTTPException) as caught:
        route_mod._validate_review_context_state(
            _turn(state="committed"),
            {"settlement_state": "settling"},
            None,
            _payload(),
        )
    assert caught.value.status_code == 409


def test_validate_review_context_rejects_unknown_settlement() -> None:
    """Unknown settlement states fail closed."""
    with pytest.raises(HTTPException) as caught:
        route_mod._validate_review_context_state(
            _turn(state="staged"),
            {"settlement_state": "unknown"},
            None,
            _payload(),
        )
    assert caught.value.status_code == 409


async def test_acknowledge_review_requires_callback() -> None:
    """Missing Review adapter callback is a 503."""
    payload = _payload()
    with pytest.raises(HTTPException) as caught:
        await route_mod._acknowledge_review_settlement(
            "key", payload, _turn(), _deps(_Store())
        )
    assert caught.value.status_code == 503
    assert "not available" in str(caught.value.detail)


async def test_acknowledge_review_requires_promotion() -> None:
    """A false acknowledgment does not settle the turn."""

    async def _callback(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False

    payload = _payload()
    with pytest.raises(HTTPException) as caught:
        await route_mod._acknowledge_review_settlement(
            "key", payload, _turn(), _deps(_Store(), _callback)
        )
    assert caught.value.status_code == 503
    assert "not promoted" in str(caught.value.detail)


def test_commit_review_turn_maps_storage_errors() -> None:
    """Review commit maps missing, tombstoned, and version conflicts."""
    payload = _payload()
    cases = (
        (KeyError("missing"), 404),
        (ConversationTombstonedError("gone"), 409),
        (ContextVersionConflictError("stale"), 409),
    )
    for error, status in cases:
        store = _Store(commit_error=error)
        with pytest.raises(HTTPException) as caught:
            route_mod._commit_review_turn(_as_store(store), "key", payload)
        assert caught.value.status_code == status


async def test_settle_review_route_lock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy Review lock maps to HTTP 503."""

    async def _busy(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise ReviewMutationLockTimeoutError()

    monkeypatch.setattr(route_mod, "acquire_review_mutation_lock", _busy)
    with pytest.raises(HTTPException) as caught:
        await route_mod._settle_review_context_route(
            _as_store(_Store()), "key", _payload(), _deps(_Store())
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == "Review settlement is busy"


async def test_commit_route_lock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy standard settlement lock maps to HTTP 503."""

    async def _busy(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise ReviewMutationLockTimeoutError()

    monkeypatch.setattr(route_mod, "acquire_review_mutation_lock", _busy)
    with pytest.raises(HTTPException) as caught:
        await route_mod._commit_context_route(
            _as_store(_Store()), "key", _payload()
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == "context settlement is busy"


async def test_commit_route_maps_missing_and_tombstoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standard commit maps storage KeyError and tombstone conflicts."""

    async def _hold(*args: Any, **kwargs: Any) -> _Lock:
        del args, kwargs
        return _Lock()

    monkeypatch.setattr(route_mod, "acquire_review_mutation_lock", _hold)
    payload = _payload()
    missing = _Store(commit_error=KeyError("missing"))
    with pytest.raises(HTTPException) as caught:
        await route_mod._commit_context_route(
            _as_store(missing), "key", payload
        )
    assert caught.value.status_code == 404

    tombstoned = _Store(commit_error=ConversationTombstonedError("gone"))
    with pytest.raises(HTTPException) as caught:
        await route_mod._commit_context_route(
            _as_store(tombstoned), "key", payload
        )
    assert caught.value.status_code == 409


async def test_tombstone_route_lock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy deletion lock maps to HTTP 503."""

    async def _busy(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise ReviewMutationLockTimeoutError()

    monkeypatch.setattr(route_mod, "acquire_review_mutation_lock", _busy)
    payload = ContextTombstoneRequest(
        schema_version=1, conversation_key=uuid4()
    )
    with pytest.raises(HTTPException) as caught:
        await route_mod._tombstone_context_route(payload, _deps(_Store()))
    assert caught.value.status_code == 503
    assert caught.value.detail == "conversation deletion is busy"
