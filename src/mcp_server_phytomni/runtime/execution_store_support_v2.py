# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared construction mechanics for execution event stores."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module

from ..storage.path_policy import IdFactory
from .execution_event_limits import ExecutionEventLimits
from .execution_journal_schema import migrate_execution_journal_v2
from .sqlite import sqlite_transaction


@dataclass(frozen=True, slots=True)
class EventStoreSettings:
    """Resolved clocks, identifiers, and bounds for one event store."""

    event_id_factory: Callable[[], str]
    clock: Callable[[], str]
    limits: ExecutionEventLimits


def event_store_settings(
    event_id_factory: Callable[[], str] | None,
    clock: Callable[[], str] | None,
    limits: ExecutionEventLimits,
) -> EventStoreSettings:
    """Resolve optional event-store dependencies once at construction."""
    return EventStoreSettings(
        event_id_factory=event_id_factory
        or (lambda: IdFactory().new_id("evt")),
        clock=clock or _now_iso,
        limits=limits,
    )


def validate_provider_join_lease_token(token: str | None) -> str | None:
    """Return a bounded provider-join fence token or reject it."""
    if token is not None and (not token or len(token) > 128):
        raise ValueError("invalid provider join lease token")
    return token


def validate_optional_aware_iso8601(
    value: str | None,
    *,
    field_name: str,
) -> str | None:
    """Return an optional timezone-aware ISO-8601 timestamp."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO-8601") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def execution_is_live(
    connection: sqlite3.Connection,
    owner: str,
    execution_id: str,
) -> bool:
    """Check whether an owner-scoped execution is live."""
    found = connection.execute(
        "SELECT 1 FROM runs WHERE user_id = ? AND execution_id = ? "
        "AND execution_tombstoned_at IS NULL LIMIT 1",
        (owner, execution_id),
    ).fetchone()
    return found is not None


def initialize_execution_v2_store(
    db_path: str,
    *,
    configure_journal: bool = False,
) -> None:
    """Ensure the registry and additive V2 schema exist for one store."""
    registry_module = import_module(".run_registry", __package__)
    registry_module.RunRegistry(db_path)
    with sqlite_transaction(db_path) as connection:
        if configure_journal:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        migrate_execution_journal_v2(connection)
        connection.commit()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
