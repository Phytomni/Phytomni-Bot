# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Owner-scoped A2UI, A2A, and run-row projections.

The storage transaction remains owned by :class:`RunRegistry`; this mixin
keeps secondary projection seams together without changing their public
methods or row-shaping behavior.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .run_registry_models import (
    A2ACorrelation,
    A2UIActionAudit,
    A2UIActionClaim,
    A2UIActionConflict,
    A2UIActionIdentity,
    RunFilter,
    RunRecord,
    RunRequestInfo,
    RunSpec,
    Timestamps,
    _now_iso,
    _surface_identity_from_result,
)
from .sqlite import sqlite_transaction

__all__ = ["RunRegistryViewsMixin"]


class RunRegistryViewsMixin:
    """Expose secondary run projections on the primary registry class."""

    db_path: str

    def list_runs(
        self,
        *,
        owner: str,
        run_filter: RunFilter | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[RunRecord]:
        """Return the newest owner-scoped runs matching all filters."""
        where, params = _build_list_where(owner, run_filter or RunFilter())
        params.extend([limit, offset])
        records: list[RunRecord] = []
        with sqlite_transaction(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT run_id, user_id, agent, origin, status,
                       result_json, error, created_at, updated_at,
                       expires_at,
                       dialogue_id, request_id, query, tool_name, model,
                       request_json,
                       locale, external_execution_id,
                       a2a_task_id, a2a_context_id, a2a_message_id,
                       stage, failure_json, revision
                FROM runs WHERE {where}
                ORDER BY created_at DESC, run_id
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()
            for run_row in rows:
                task_rows = conn.execute(
                    "SELECT task_id FROM tasks WHERE run_id = ? "
                    "ORDER BY task_id",
                    (run_row["run_id"],),
                ).fetchall()
                records.append(_row_to_record(run_row, task_rows))
        return records

    def claim_a2ui_action(
        self,
        *,
        run_id: str,
        owner: str,
        **action: str,
    ) -> A2UIActionClaim:
        """Atomically claim the current A2UI surface for one uplink.

        The write lock is acquired before reading the run row. This makes
        the primary key on ``(run_id, surface_id)`` a cross-process
        compare-and-set gate rather than a process-local best effort.

        ``surface_id``, ``widget``, ``action_id``, and ``channel`` remain
        required keyword arguments at the public call boundary. They are
        collected here so the storage method stays below the repository's
        argument-count limit while preserving the established call shape.
        """
        try:
            surface_id = action.pop("surface_id")
            widget = action.pop("widget")
            action_id = action.pop("action_id")
            channel = action.pop("channel")
        except KeyError as exc:
            raise TypeError(
                f"missing required A2UI action field: {exc.args[0]}"
            ) from exc
        if action:
            unexpected = next(iter(action))
            raise TypeError(f"unexpected A2UI action field: {unexpected}")
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, result_json
                FROM runs
                WHERE run_id = ? AND user_id = ?
                """,
                (run_id, owner),
            ).fetchone()
            if row is None or row[0] not in {
                "input_required",
                "waiting_input",
            }:
                conn.rollback()
                raise A2UIActionConflict("run is not awaiting input")
            open_surface = _surface_identity_from_result(row[1])
            if open_surface != (surface_id, widget):
                conn.rollback()
                raise A2UIActionConflict("surface does not match open pause")
            try:
                conn.execute(
                    """
                    INSERT INTO run_a2ui_actions (
                        run_id, user_id, surface_id, widget, action_id,
                        channel, outcome, claimed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?)
                    """,
                    (
                        run_id,
                        owner,
                        surface_id,
                        widget,
                        action_id,
                        channel,
                        _now_iso(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise A2UIActionConflict(
                    "surface has already been claimed"
                ) from exc
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return A2UIActionClaim(
            run_id=run_id,
            surface_id=surface_id,
            widget=widget,
            action_id=action_id,
            channel=channel,
        )

    def complete_a2ui_action(
        self,
        claim: A2UIActionClaim,
        *,
        owner: str,
        outcome: str,
    ) -> bool:
        """Complete an owned claim exactly once."""
        if outcome not in {"succeeded", "input_required", "failed"}:
            raise ValueError("invalid a2ui action outcome")
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE run_a2ui_actions
                SET outcome = ?, completed_at = ?
                WHERE run_id = ? AND user_id = ? AND surface_id = ?
                  AND action_id = ? AND outcome = 'claimed'
                """,
                (
                    outcome,
                    _now_iso(),
                    claim.run_id,
                    owner,
                    claim.surface_id,
                    claim.action_id,
                ),
            )
            return cursor.rowcount == 1

    def list_a2ui_actions(
        self,
        *,
        owner: str,
        run_id: str | None = None,
    ) -> list[A2UIActionAudit]:
        """Return safe, owner-scoped A2UI action audit projections."""
        clauses = ["user_id = ?"]
        parameters: list[str] = [owner]
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        where = " AND ".join(clauses)
        with sqlite_transaction(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_id, surface_id, widget, action_id, channel,
                       outcome, claimed_at, completed_at
                FROM run_a2ui_actions
                WHERE """ + where + " ORDER BY claimed_at ASC",
                parameters,
            ).fetchall()
        return [
            A2UIActionAudit(
                identity=A2UIActionIdentity(
                    run_id=row["run_id"],
                    surface_id=row["surface_id"],
                    widget=row["widget"],
                    action_id=row["action_id"],
                ),
                channel=row["channel"],
                outcome=row["outcome"],
                claimed_at=row["claimed_at"],
                completed_at=row["completed_at"],
            )
            for row in rows
        ]

    def get_run(self, run_id: str, *, owner: str) -> RunRecord | None:
        """Return the run owned by ``owner`` or ``None``."""
        with sqlite_transaction(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT run_id, user_id, agent, origin, status, result_json,
                       error, created_at, updated_at, expires_at,
                       dialogue_id, request_id, query, tool_name, model,
                       request_json,
                       locale, external_execution_id,
                       a2a_task_id, a2a_context_id, a2a_message_id,
                       stage, failure_json, revision
                FROM runs WHERE run_id = ? AND user_id = ?
                """,
                (run_id, owner),
            ).fetchone()
            if row is None:
                return None
            task_rows = conn.execute(
                "SELECT task_id FROM tasks WHERE run_id = ? ORDER BY task_id",
                (run_id,),
            ).fetchall()
        return _row_to_record(row, task_rows)

    def get_run_by_execution_id(
        self,
        execution_id: str,
        *,
        owner: str,
    ) -> RunRecord | None:
        """Resolve one browser-known execution identity within its owner."""
        if not execution_id:
            return None
        with sqlite_transaction(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT run_id, user_id, agent, origin, status, result_json,
                       error, created_at, updated_at, expires_at,
                       dialogue_id, request_id, query, tool_name, model,
                       request_json, locale, external_execution_id,
                       a2a_task_id, a2a_context_id, a2a_message_id,
                       stage, failure_json, revision
                FROM runs
                WHERE external_execution_id = ? AND user_id = ?
                LIMIT 1
                """,
                (execution_id, owner),
            ).fetchone()
            if row is None:
                return None
            task_rows = conn.execute(
                "SELECT task_id FROM tasks WHERE run_id = ? ORDER BY task_id",
                (row["run_id"],),
            ).fetchall()
        return _row_to_record(row, task_rows)

    def update_a2a_correlation(
        self,
        run_id: str,
        *,
        owner: str,
        correlation: A2ACorrelation,
    ) -> bool:
        """Attach or replace A2A ids on an owned run row."""
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs SET
                    a2a_task_id = ?,
                    a2a_context_id = ?,
                    a2a_message_id = ?,
                    updated_at = ?
                WHERE run_id = ? AND user_id = ?
                """,
                (
                    correlation.task_id,
                    correlation.context_id,
                    correlation.message_id,
                    _now_iso(),
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount > 0

    def get_run_by_a2a_task(
        self,
        task_id: str,
        *,
        owner: str,
    ) -> RunRecord | None:
        """Return the owned run projected by an A2A task id."""
        if not task_id:
            return None
        with sqlite_transaction(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT run_id, user_id, agent, origin, status, result_json,
                       error, created_at, updated_at, expires_at,
                       dialogue_id, request_id, query, tool_name, model,
                       request_json,
                       locale, external_execution_id,
                       a2a_task_id, a2a_context_id, a2a_message_id,
                       stage, failure_json, revision
                FROM runs WHERE a2a_task_id = ? AND user_id = ?
                ORDER BY updated_at DESC, run_id DESC LIMIT 1
                """,
                (task_id, owner),
            ).fetchone()
            if row is None:
                return None
            task_rows = conn.execute(
                "SELECT task_id FROM tasks WHERE run_id = ? ORDER BY task_id",
                (row["run_id"],),
            ).fetchall()
        return _row_to_record(row, task_rows)


def _row_to_record(row: sqlite3.Row, task_rows: list[Any]) -> RunRecord:
    """Build a RunRecord from a ``runs`` row and child task rows."""
    result_json = row["result_json"]
    return RunRecord(
        spec=RunSpec(
            run_id=row["run_id"],
            user_id=row["user_id"],
            agent=row["agent"],
            origin=row["origin"],
        ),
        status=row["status"],
        result=json.loads(result_json) if result_json else None,
        error=row["error"],
        timestamps=Timestamps(
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        ),
        task_ids=tuple(t[0] for t in task_rows),
        request_info=RunRequestInfo(
            dialogue_id=row["dialogue_id"],
            request_id=row["request_id"],
            query=row["query"],
            tool_name=row["tool_name"],
            model=row["model"],
            request_json=row["request_json"],
            locale=row["locale"],
            execution_id=row["external_execution_id"],
            a2a=A2ACorrelation(
                task_id=row["a2a_task_id"],
                context_id=row["a2a_context_id"],
                message_id=row["a2a_message_id"],
            ),
        ),
        stage=row["stage"],
        failure=_safe_failure(row["failure_json"]),
        revision=row["revision"],
    )


def _safe_failure(value: object) -> dict[str, Any] | None:
    """Load private failure JSON without making reads fail closed."""
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return dict(decoded) if isinstance(decoded, dict) else None


def _build_list_where(
    owner: str, run_filter: RunFilter
) -> tuple[str, list[Any]]:
    """Return the WHERE fragment and parameters for ``list_runs``."""
    clauses = ["user_id = ?"]
    params: list[Any] = [owner]
    for column, value in (
        ("status", run_filter.status),
        ("agent", run_filter.agent),
        ("origin", run_filter.origin),
        ("dialogue_id", run_filter.dialogue_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if run_filter.created_after is not None:
        clauses.append("created_at >= ?")
        params.append(run_filter.created_after)
    if run_filter.created_before is not None:
        clauses.append("created_at <= ?")
        params.append(run_filter.created_before)
    return " AND ".join(clauses), params
