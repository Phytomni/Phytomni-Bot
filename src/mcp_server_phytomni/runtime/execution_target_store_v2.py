# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private owner-scoped delivery bindings for public execution targets."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .execution_journal_schema import migrate_execution_journal_v2


class ExecutionTargetBindingConflictError(RuntimeError):
    """A stable public target id was reused for another private object."""


class ExecutionTargetBindingFenceError(RuntimeError):
    """A superseded provider join attempted to bind a public target."""


@dataclass(frozen=True, slots=True)
class ExecutionTargetBindingV2:
    """Private delivery metadata; never serialize this record publicly."""

    owner: str
    execution_id: str
    kind: str
    target_id: str
    role: str
    name: str
    media_type: str
    size_bytes: int
    delivery_ref: str


@runtime_checkable
class ExecutionTargetStore(Protocol):
    """Immutable target binding boundary used by Runtime and HTTP delivery."""

    def put(self, binding: ExecutionTargetBindingV2) -> None: ...

    def get(
        self,
        *,
        owner: str,
        execution_id: str,
        kind: str,
        target_id: str,
    ) -> ExecutionTargetBindingV2 | None: ...


class SQLiteExecutionTargetStore:
    """SQLite implementation co-located with the canonical execution journal."""

    def __init__(
        self,
        db_path: str,
        *,
        expected_provider_join_lease_token: str | None = None,
    ) -> None:
        if expected_provider_join_lease_token is not None and (
            not expected_provider_join_lease_token
            or len(expected_provider_join_lease_token) > 128
        ):
            raise ValueError("invalid provider join lease token")
        self.db_path = db_path
        self._expected_provider_join_lease_token = (
            expected_provider_join_lease_token
        )
        self._init_db()

    def _init_db(self) -> None:
        from .run_registry import RunRegistry

        RunRegistry(self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            migrate_execution_journal_v2(connection)
            connection.commit()

    def put(self, binding: ExecutionTargetBindingV2) -> None:
        """Persist once; a target id can never be rebound to another object."""
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            token = self._expected_provider_join_lease_token
            fence_clause = (
                " AND execution_provider_join_lease_owner = ? "
                "AND execution_provider_join_lease_expires_at > ?"
                if token is not None
                else ""
            )
            fence_parameters = (
                (token, datetime.now(UTC).isoformat())
                if token is not None
                else ()
            )
            authorized = connection.execute(
                "SELECT 1 FROM runs WHERE user_id = ? AND execution_id = ? "
                "AND execution_tombstoned_at IS NULL" + fence_clause,
                (
                    binding.owner,
                    binding.execution_id,
                    *fence_parameters,
                ),
            ).fetchone()
            if authorized is None:
                connection.rollback()
                if token is not None:
                    raise ExecutionTargetBindingFenceError(
                        "provider_join_lease_lost"
                    )
                raise LookupError("execution target binding unavailable")
            existing = connection.execute(
                "SELECT role, name, media_type, size_bytes, delivery_ref "
                "FROM execution_target_bindings_v2 WHERE owner_ref = ? "
                "AND execution_id = ? AND target_kind = ? AND target_id = ?",
                (
                    binding.owner,
                    binding.execution_id,
                    binding.kind,
                    binding.target_id,
                ),
            ).fetchone()
            values = (
                binding.role,
                binding.name,
                binding.media_type,
                binding.size_bytes,
                binding.delivery_ref,
            )
            if existing is not None:
                connection.commit()
                if tuple(existing) != values:
                    raise ExecutionTargetBindingConflictError(
                        "execution target binding conflict"
                    )
                return
            connection.execute(
                "INSERT INTO execution_target_bindings_v2 "
                "(owner_ref, execution_id, target_kind, target_id, role, "
                "name, media_type, size_bytes, delivery_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    binding.owner,
                    binding.execution_id,
                    binding.kind,
                    binding.target_id,
                    *values,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()

    def get(
        self,
        *,
        owner: str,
        execution_id: str,
        kind: str,
        target_id: str,
    ) -> ExecutionTargetBindingV2 | None:
        """Return an owner-authorized live binding with unknown/foreign parity."""
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT b.role, b.name, b.media_type, b.size_bytes, "
                "b.delivery_ref FROM execution_target_bindings_v2 b "
                "JOIN runs r ON r.user_id = b.owner_ref "
                "AND r.execution_id = b.execution_id "
                "WHERE b.owner_ref = ? AND b.execution_id = ? "
                "AND b.target_kind = ? AND b.target_id = ? "
                "AND r.execution_tombstoned_at IS NULL",
                (owner, execution_id, kind, target_id),
            ).fetchone()
        if row is None:
            return None
        return ExecutionTargetBindingV2(
            owner=owner,
            execution_id=execution_id,
            kind=kind,
            target_id=target_id,
            role=str(row[0]),
            name=str(row[1]),
            media_type=str(row[2]),
            size_bytes=int(row[3]),
            delivery_ref=str(row[4]),
        )
