# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Provider-join lease operations for the Runtime V2 work repository."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from .execution_store_support_v2 import execution_is_live
from .execution_work_models_v2 import ExecutionWorkNotFoundError
from .execution_work_status_v2 import TERMINAL_WORK_UNIT_VALUES
from .sqlite import sqlite_transaction


class ProviderJoinLeaseRepositoryMixin:
    """Single-flight join discovery and lease ownership operations."""

    db_path: str
    _clock: Callable[[], datetime]

    def list_ready_provider_joins(
        self,
        *,
        limit: int = 100,
    ) -> tuple[tuple[str, str], ...]:
        """Return remote executions whose durable work is fully terminal."""
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            rows = connection.execute(
                "SELECT r.user_id, r.execution_id FROM runs r "
                "WHERE r.execution_id IS NOT NULL "
                "AND r.execution_terminal_outcome IS NULL "
                "AND r.execution_tombstoned_at IS NULL "
                "AND (r.execution_provider_join_lease_expires_at IS NULL "
                "OR r.execution_provider_join_lease_expires_at <= ?) "
                "AND EXISTS (SELECT 1 FROM execution_work_units p "
                "WHERE p.owner_ref = r.user_id "
                "AND p.execution_id = r.execution_id "
                "AND p.provider_kind IS NOT NULL) "
                "AND NOT EXISTS (SELECT 1 FROM execution_work_units w "
                "WHERE w.owner_ref = r.user_id "
                "AND w.execution_id = r.execution_id "
                "AND w.status NOT IN (?, ?, ?, ?, ?)) "
                "ORDER BY r.updated_at, r.execution_id LIMIT ?",
                (now, *TERMINAL_WORK_UNIT_VALUES, limit),
            ).fetchall()
        return tuple(
            (str(owner), str(execution_id)) for owner, execution_id in rows
        )

    def claim_provider_join_lease(
        self,
        execution_id: str,
        *,
        owner: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        """Single-flight one provider terminal join across supervisors."""
        if not lease_token or len(lease_token) > 128:
            raise ValueError("bounded lease_token is required")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        now = self._clock()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not execution_is_live(connection, owner, execution_id):
                raise ExecutionWorkNotFoundError(execution_id)
            result = connection.execute(
                "UPDATE runs SET execution_provider_join_lease_owner = ?, "
                "execution_provider_join_lease_expires_at = ? "
                "WHERE user_id = ? AND execution_id = ? "
                "AND execution_terminal_outcome IS NULL "
                "AND execution_tombstoned_at IS NULL "
                "AND (execution_provider_join_lease_owner IS NULL "
                "OR execution_provider_join_lease_expires_at IS NULL "
                "OR execution_provider_join_lease_expires_at <= ?)",
                (
                    lease_token,
                    expires,
                    owner,
                    execution_id,
                    now.isoformat(),
                ),
            )
            connection.commit()
        return result.rowcount == 1

    def renew_provider_join_lease(
        self,
        execution_id: str,
        *,
        owner: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        """Extend only the exact claim token held by this join attempt."""
        if not lease_token or len(lease_token) > 128:
            raise ValueError("bounded lease_token is required")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        expires = (
            self._clock() + timedelta(seconds=lease_seconds)
        ).isoformat()
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE runs SET execution_provider_join_lease_expires_at = ? "
                "WHERE user_id = ? AND execution_id = ? "
                "AND execution_provider_join_lease_owner = ? "
                "AND execution_provider_join_lease_expires_at > ?",
                (
                    expires,
                    owner,
                    execution_id,
                    lease_token,
                    self._clock().isoformat(),
                ),
            )
            connection.commit()
        return result.rowcount == 1

    def owns_provider_join_lease(
        self,
        execution_id: str,
        *,
        owner: str,
        lease_token: str,
    ) -> bool:
        """Check the exact unexpired token before the Runtime commit."""
        now = self._clock().isoformat()
        with sqlite_transaction(self.db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM runs WHERE user_id = ? AND execution_id = ? "
                "AND execution_provider_join_lease_owner = ? "
                "AND execution_provider_join_lease_expires_at > ? "
                "AND execution_terminal_outcome IS NULL "
                "AND execution_tombstoned_at IS NULL",
                (owner, execution_id, lease_token, now),
            ).fetchone()
        return row is not None

    def release_provider_join_lease(
        self,
        execution_id: str,
        *,
        owner: str,
        lease_token: str,
    ) -> bool:
        """Release only the provider join lease owned by this supervisor."""
        with sqlite_transaction(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE runs SET execution_provider_join_lease_owner = NULL, "
                "execution_provider_join_lease_expires_at = NULL "
                "WHERE user_id = ? AND execution_id = ? "
                "AND execution_provider_join_lease_owner = ?",
                (owner, execution_id, lease_token),
            )
            connection.commit()
        return result.rowcount == 1


__all__ = ["ProviderJoinLeaseRepositoryMixin"]
