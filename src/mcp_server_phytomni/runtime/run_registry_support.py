# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Bounded persistence helpers shared by the run registry facade."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any, NamedTuple

from ..mcp.formatting.models import ResultDelivery
from .execution_event_store import purge_execution_event_children
from .execution_journal_schema import migrate_execution_journal_v2
from .research_input_store import purge_research_children
from .run_registry_delivery import (
    PrivateDeliveryState,
    private_delivery_from_result,
    result_delivery_from_result,
)
from .run_registry_models import (
    _A2A_COLUMNS,
    _CREATE_A2A_TASK_INDEX,
    _CREATE_A2UI_ACTIONS_DDL,
    _CREATE_A2UI_OWNER_ACTION_INDEX,
    _CREATE_RUNS_DDL,
    _CREATE_RUNS_EXECUTION_INDEX,
    _CREATE_RUNS_USER_INDEX,
    _CREATE_TASKS_RUN_INDEX,
    _REQUEST_INFO_COLUMNS,
    _RESEARCH_COORDINATOR_COLUMNS,
)
from .run_registry_protocols import _SETTLE_RUN_SIGNATURE
from .sqlite import sqlite_transaction
from .task_manager import TaskManager
from .task_manager import _expires_at_for as _task_expires_at_for


class SettleRunRequest(NamedTuple):
    """Validated arguments for one compatibility settlement call."""

    run_id: str
    owner: str
    status: str
    result: dict[str, Any] | None
    error: str | None
    expected_revision: int | None


def bind_settle_run_request(
    registry: Any,
    *args: Any,
    **kwargs: Any,
) -> SettleRunRequest:
    """Bind the historical settlement signature and validate its CAS key."""
    bound = _SETTLE_RUN_SIGNATURE.bind(registry, *args, **kwargs)
    bound.apply_defaults()
    expected_revision = bound.arguments["expected_revision"]
    if expected_revision is not None and (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ValueError("expected_revision must be non-negative")
    return SettleRunRequest(
        run_id=bound.arguments["run_id"],
        owner=bound.arguments["owner"],
        status=bound.arguments["status"],
        result=bound.arguments["result"],
        error=bound.arguments["error"],
        expected_revision=expected_revision,
    )


def expires_at_for(status: str, now_iso: str) -> str | None:
    """Apply the shared terminal TTL policy, including cancellation."""
    if status == "cancelled":
        return _task_expires_at_for("failed", now_iso)
    return _task_expires_at_for(status, now_iso)


def initialize_run_registry(db_path: str) -> None:
    """Create the run table, additive columns, and shared indices."""
    TaskManager(db_path)
    with sqlite_transaction(db_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_CREATE_RUNS_DDL)
        existing = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)")
        }
        for column, column_type in (
            *_REQUEST_INFO_COLUMNS,
            *_A2A_COLUMNS,
            *_RESEARCH_COORDINATOR_COLUMNS,
        ):
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE runs ADD COLUMN {column} {column_type}"
                )
        connection.execute(_CREATE_RUNS_USER_INDEX)
        connection.execute(_CREATE_RUNS_EXECUTION_INDEX)
        connection.execute(_CREATE_TASKS_RUN_INDEX)
        connection.execute(_CREATE_A2A_TASK_INDEX)
        connection.execute(_CREATE_A2UI_ACTIONS_DDL)
        connection.execute(_CREATE_A2UI_OWNER_ACTION_INDEX)
        migrate_execution_journal_v2(connection)


def purge_run_children(
    connection: sqlite3.Connection,
    run_ids: Sequence[str],
) -> None:
    """Delete DeepGenome children before their owning task and run rows."""
    ids = tuple(run_ids)
    if not ids:
        return
    purge_execution_event_children(connection, ids)
    purge_research_children(connection, ids)
    placeholders = ",".join("?" for _ in ids)
    for table in ("deep_genome_remote_tasks", "deep_genome_sections"):
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if exists is None:
            continue
        connection.execute(
            f"DELETE FROM {table} WHERE umbrella_task_id IN ("
            "SELECT task_id FROM tasks WHERE run_id IN ("
            f"{placeholders}))",
            ids,
        )
    connection.execute(
        f"DELETE FROM tasks WHERE run_id IN ({placeholders})",
        ids,
    )
    connection.execute(
        f"DELETE FROM runs WHERE run_id IN ({placeholders})",
        ids,
    )


def pending_delivery(result: object) -> ResultDelivery | None:
    """Return an actionable delivery only after immutable inventory exists."""
    delivery = result_delivery_from_result(result)
    if (
        delivery is None
        or delivery.status != "pending"
        or not delivery.inventory_digest
    ):
        return None
    return delivery


def private_delivery(result: object) -> PrivateDeliveryState | None:
    """Read bounded private delivery coordination from a stored result."""
    return private_delivery_from_result(result)


__all__ = [
    "SettleRunRequest",
    "bind_settle_run_request",
    "expires_at_for",
    "initialize_run_registry",
    "pending_delivery",
    "private_delivery",
    "purge_run_children",
]
