# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable Research work-unit binding and subdivision storage helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from sqlite3 import Row
from typing import TYPE_CHECKING, Any

from . import research_input_store as _store
from .research_input_types import ResearchWorkUnitRecord
from .sqlite import sqlite_transaction

if TYPE_CHECKING:
    from .research_input_store import ResearchInputStore

_VALIDATED_OUTPUT_SQL = (
    "SELECT output_json FROM research_work_units WHERE unit_id = ? AND "
    "state = 'succeeded' AND input_digest = ? AND policy_digest = ? AND "
    "execution_fingerprint = ? AND evidence_digest = ?"
)


class _ResearchInputStoreBindings:
    """Mixin exposing the durable resolver binding operations."""

    def load_validated_output(
        self: Any,
        unit_id: str,
        *bindings: str,
        **named: str,
    ) -> dict[str, Any] | None:
        """Load output only when all four durable bindings match."""
        return load_validated_output(self, unit_id, *bindings, **named)

    def replace_work_unit_with_children(
        self: Any,
        record: ResearchWorkUnitRecord,
        children: Sequence[ResearchWorkUnitRecord],
        now: datetime,
    ) -> bool:
        """Atomically close a sent unit and enqueue verified children."""
        return replace_work_unit_with_children(self, record, children, now)


def load_validated_output(
    store: ResearchInputStore,
    unit_id: str,
    *bindings: str,
    **named: str,
) -> dict[str, Any] | None:
    """Load output only when all four durable resolver bindings match."""
    names = (
        "input_digest",
        "policy_digest",
        "execution_fingerprint",
        "evidence_digest",
    )
    if named:
        if bindings or set(named) != set(names):
            raise TypeError("four resolver output bindings are required")
        bindings = tuple(named[name] for name in names)
    if len(bindings) != 4:
        raise TypeError("four resolver output bindings are required")
    with sqlite_transaction(store.db_path) as connection:
        row = connection.execute(
            _VALIDATED_OUTPUT_SQL, (unit_id, *bindings)
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    try:
        output = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return output if isinstance(output, dict) else None


def replace_work_unit_with_children(
    store: ResearchInputStore,
    record: ResearchWorkUnitRecord,
    children: Sequence[ResearchWorkUnitRecord],
    now: datetime,
) -> bool:
    """Atomically close a sent unit and enqueue verified child units."""
    child_rows = tuple(children)
    if not child_rows or any(
        child.run_id != record.run_id
        or child.state != "pending"
        or child.unit_id == record.unit_id
        for child in child_rows
    ):
        return False
    now_iso = _utc_iso(now)
    with sqlite_transaction(store.db_path) as connection:
        connection.row_factory = Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            getattr(_store, "_WORK_SELECT"), (record.unit_id,)
        ).fetchone()
        if (
            row is None
            or row["state"] != "sent"
            or row["lease_owner"] != record.lease_owner
            or row["revision"] != record.revision
        ):
            return False
        for child in child_rows:
            inserted = connection.execute(
                getattr(_store, "_WORK_INSERT_SQL"),
                getattr(_store, "_work_insert_values")(child, now_iso),
            )
            if inserted.rowcount != 1:
                raise sqlite3.IntegrityError("research child collision")
        closed = connection.execute(
            "UPDATE research_work_units SET state = 'cancelled', "
            "lease_owner = NULL, lease_expires_at = NULL, "
            "failure_code = 'research_input_resolution_unavailable', "
            "failure_retryable = 0, updated_at = ?, completed_at = ?, "
            "revision = revision + 1 WHERE unit_id = ? AND "
            "lease_owner = ? AND revision = ? AND state = 'sent' AND "
            + getattr(_store, "_PARENT_LIVE"),
            (now_iso,) * 2
            + (record.unit_id, record.lease_owner, record.revision),
        )
        if closed.rowcount != 1:
            raise sqlite3.IntegrityError("research parent changed")
    return True


def _utc_iso(value: datetime) -> str:
    """Format a recovery timestamp consistently with the store."""
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return value.astimezone(UTC).isoformat()
