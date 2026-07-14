# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded, request-scoped reads from the explicit memory store.

The accessor is the only graph-facing seam for local memory.  It keeps
SQLite construction lazy, resolves the authenticated user from request
context when a graph does not provide one explicitly, and turns an
unavailable read store into an observable empty result.  Graphs therefore
never receive a raw ``MemoryStore`` or a cross-user query primitive.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime
from functools import lru_cache
from typing import TypedDict

from ...config.defaults import ApiConfig
from ..request_context import current_request_user
from .migrations import MemorySchemaError
from .models import (
    DEFAULT_MEMORY_POLICY,
    MemoryPolicy,
    MemoryPolicyError,
    MemoryRecord,
)
from .sqlite import MemoryStore, MemoryStoreError

__all__ = [
    "MemoryAccessor",
    "MemoryGraphContext",
    "current_memory_accessor",
    "get_default_memory_accessor",
    "memory_accessor_context",
    "resolve_memory_accessor",
]

_LOGGER = logging.getLogger(__name__)
_DEFAULT_GRAPH_MEMORY_MAX_BYTES = 64 * 1024
_StoreFactory = Callable[[], MemoryStore]
_MEMORY_ACCESSOR_CONTEXT: ContextVar[MemoryAccessor | None] = ContextVar(
    "phytomni_memory_accessor", default=None
)


class MemoryGraphContext(TypedDict, total=False):
    """Typed LangGraph runtime context for an explicit memory accessor."""

    memory_accessor: MemoryAccessor


class MemoryAccessor:
    """Expose a bounded, user-scoped read-only view of ``MemoryStore``.

    Args:
        store: Optional already-open store, useful for tests and embedded
            deployments.  ``store_factory`` is used only when the first
            enabled read occurs, so flag-off graphs never touch SQLite.
        enabled: Whether reads are enabled.  Disabled accessors return an
            empty list without opening a store.
        store_factory: Lazy factory for a local store.
        policy: Policy used to cap retrieval count and bytes.  When a store
            is supplied its policy wins unless an explicit policy is given.
        max_bytes: Hard per-read UTF-8 byte budget.  ``None`` uses the
            conservative graph default and is always capped by policy.
    """

    def __init__(
        self,
        store: MemoryStore | None = None,
        *,
        enabled: bool = True,
        store_factory: _StoreFactory | None = None,
        policy: MemoryPolicy | None = None,
        max_bytes: int | None = None,
    ) -> None:
        """Initialize one graph-facing memory accessor."""
        if store is not None and store_factory is not None:
            raise ValueError(
                "memory accessor accepts store or factory, not both"
            )
        resolved_policy = policy or (
            store.policy if store is not None else DEFAULT_MEMORY_POLICY
        )
        configured_max_bytes = (
            _DEFAULT_GRAPH_MEMORY_MAX_BYTES if max_bytes is None else max_bytes
        )
        if configured_max_bytes < 1:
            raise MemoryPolicyError("memory retrieval bytes must be positive")
        self.enabled = enabled
        self.policy = resolved_policy
        self.max_bytes = min(configured_max_bytes, self.policy.max_total_bytes)
        self._store = store
        self._store_factory = store_factory
        self._degraded_reads = 0

    @property
    def degraded_reads(self) -> int:
        """Return the number of reads that degraded to an empty result."""
        return self._degraded_reads

    def _open_store(self) -> MemoryStore:
        """Open the configured store once, only after an enabled read."""
        if self._store is not None:
            return self._store
        if self._store_factory is None:
            raise MemoryStoreError("memory accessor has no store")
        self._store = self._store_factory()
        return self._store

    def _byte_budget(self, requested: int | None) -> int:
        """Return a positive request budget capped by policy and accessor."""
        if requested is not None and requested < 1:
            raise MemoryPolicyError("memory retrieval bytes must be positive")
        requested_budget = self.max_bytes if requested is None else requested
        return min(
            requested_budget, self.max_bytes, self.policy.max_total_bytes
        )

    def retrieve(
        self,
        *,
        user_id: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
        max_bytes: int | None = None,
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        """Return a newest-first, bounded prefix for one user namespace.

        ``user_id`` defaults to the authenticated request context.  When no
        user is bound, the method returns an empty list before opening the
        store.  Expired rows are filtered by ``MemoryStore.list``.  The
        byte budget is applied as a prefix over the store's deterministic
        newest-first ordering; a record that would exceed the budget stops
        the prefix rather than being truncated.
        """
        if not self.enabled:
            return []
        owner = current_request_user() if user_id is None else user_id
        if not owner:
            return []
        bounded_limit = self.policy.bounded_retrieval_limit(limit)
        budget = self._byte_budget(max_bytes)
        try:
            records = self._open_store().list(
                owner,
                kind=kind,
                limit=bounded_limit,
                now=now,
            )
        except (
            MemorySchemaError,
            MemoryStoreError,
            OSError,
            sqlite3.Error,
        ) as exc:
            self._degraded_reads += 1
            _LOGGER.warning(
                "memory read degraded to empty result: %s", type(exc).__name__
            )
            return []

        selected: list[MemoryRecord] = []
        used_bytes = 0
        for record in records:
            next_bytes = used_bytes + record.size_bytes
            if next_bytes > budget:
                break
            selected.append(record)
            used_bytes = next_bytes
        return selected

    def retrieve_for_request(
        self,
        *,
        kind: str | None = None,
        limit: int | None = None,
        max_bytes: int | None = None,
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        """Read memory for the current authenticated request namespace."""
        return self.retrieve(
            kind=kind,
            limit=limit,
            max_bytes=max_bytes,
            now=now,
        )


@contextmanager
def memory_accessor_context(
    accessor: MemoryAccessor | None,
) -> Generator[None, None, None]:
    """Temporarily override the accessor visible to graph runtime helpers."""
    token: Token[MemoryAccessor | None] = _MEMORY_ACCESSOR_CONTEXT.set(
        accessor
    )
    try:
        yield
    finally:
        _MEMORY_ACCESSOR_CONTEXT.reset(token)


@lru_cache(maxsize=8)
def _build_default_memory_accessor(
    enabled: bool,
    db_path: str,
) -> MemoryAccessor:
    """Build one cached accessor for a concrete settings pair."""
    if not enabled:
        return MemoryAccessor(enabled=False)

    def _factory() -> MemoryStore:
        return MemoryStore(db_path)

    return MemoryAccessor(enabled=True, store_factory=_factory)


def get_default_memory_accessor() -> MemoryAccessor:
    """Return the process-local lazy accessor for current API settings."""
    config = ApiConfig()
    return _build_default_memory_accessor(
        bool(config.MEMORY_ENABLED), str(config.MEMORY_DB_PATH)
    )


def current_memory_accessor() -> MemoryAccessor:
    """Return the context-bound accessor or the lazy configured default."""
    return _MEMORY_ACCESSOR_CONTEXT.get() or get_default_memory_accessor()


def resolve_memory_accessor(
    context: Mapping[str, object] | None = None,
) -> MemoryAccessor:
    """Resolve an accessor from LangGraph context with a safe fallback."""
    candidate = (context or {}).get("memory_accessor")
    if isinstance(candidate, MemoryAccessor):
        return candidate
    return current_memory_accessor()
