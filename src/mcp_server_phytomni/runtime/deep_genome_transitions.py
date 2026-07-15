# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Transactional DeepGenome child transitions and snapshot rebuilding."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .deep_genome_report_snapshot import (
    _WORK_ITEM_STATES,
    ReportRows,
    assemble_intermediate_report,
    derive_degraded_reason,
    derive_progress,
    derive_report_classification,
)

if TYPE_CHECKING:
    from .deep_genome_store import DeepGenomeSnapshot

_WORK_ITEM_STATUSES = _WORK_ITEM_STATES

_TERMINAL_WORK_ITEM_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out"}
)
_NONTERMINAL_WORK_ITEM_STATUSES = frozenset(
    {"planned", "submitted", "pending", "running"}
)
_FIXED_FAILURE_REASONS = {
    "failed": "analysis task failed",
    "cancelled": "analysis task cancelled",
    "timed_out": "analysis task timed out",
}


class DeepGenomeTransitionError(RuntimeError):
    """Raised when a persisted DeepGenome identity cannot be changed."""


class DeepGenomeTrackingError(DeepGenomeTransitionError):
    """Raised when a reserved umbrella cannot accept a tracking update."""


class DeepGenomeTransitionMixin:
    """Write child observations and rebuild one serialized report snapshot."""

    db_path: str

    def get_snapshot(self, umbrella_task_id: str) -> DeepGenomeSnapshot | None:
        """Return one snapshot from the concrete store implementation."""
        raise NotImplementedError

    @staticmethod
    def _nonblank_submission_field(value: Any, name: str) -> str:
        """Validate one identity through the concrete store implementation."""
        raise NotImplementedError

    @staticmethod
    def _assert_tracking_parent(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
    ) -> None:
        """Require one running, reserved DeepGenome umbrella."""
        parent = connection.execute(
            "SELECT t.status, r.status FROM tasks AS t "
            "JOIN runs AS r ON r.run_id = t.run_id "
            "WHERE t.task_id = ? AND t.agent = 'deep_genome' "
            "AND r.agent = 'deep_genome'",
            (umbrella_task_id,),
        ).fetchone()
        if parent is None:
            raise DeepGenomeTrackingError("reserved umbrella task is missing")
        if parent != ("running", "running"):
            raise DeepGenomeTransitionError("umbrella task is terminal")

    @staticmethod
    def _normalize_transition_status(value: Any) -> str:
        """Validate one local lifecycle state before touching SQLite."""
        if not isinstance(value, str):
            raise DeepGenomeTransitionError("status must be a known lifecycle")
        status = value.strip().lower()
        if status not in _WORK_ITEM_STATUSES:
            raise DeepGenomeTransitionError("status must be a known lifecycle")
        return status

    @staticmethod
    def _normalize_optional_markdown(
        value: Any,
        name: str,
    ) -> str | None:
        """Trim optional Markdown without persisting arbitrary objects."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise DeepGenomeTransitionError(f"{name} must be text")
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _failure_reason_for_status(status: str) -> str | None:
        """Return fixed local wording for one unavailable lifecycle state."""
        return _FIXED_FAILURE_REASONS.get(status)

    @staticmethod
    def _next_transition_content(
        current_status: str,
        next_status: str,
        current_summary: str | None,
        summary_markdown: Any,
    ) -> tuple[str | None, str | None]:
        """Resolve content fields while keeping failure text local-only."""
        if (
            current_status in _TERMINAL_WORK_ITEM_STATUSES
            and next_status != current_status
        ):
            raise DeepGenomeTransitionError("terminal state cannot change")
        if next_status in _FIXED_FAILURE_REASONS:
            return None, _FIXED_FAILURE_REASONS[next_status]
        if summary_markdown is None:
            return current_summary, None
        return (
            DeepGenomeTransitionMixin._normalize_optional_markdown(
                summary_markdown, "summary_markdown"
            ),
            None,
        )

    @staticmethod
    def _report_rows_for_connection(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
    ) -> Any:
        """Read all child rows while the write transaction remains open."""
        sections = tuple(
            {
                "section_key": row[0],
                "section_kind": row[1],
                "display_order": row[2],
                "status": row[3],
                "summary_markdown": row[4],
                "failure_reason": row[5],
            }
            for row in connection.execute(
                "SELECT section_key, section_kind, display_order, status, "
                "summary_markdown, failure_reason "
                "FROM deep_genome_sections WHERE umbrella_task_id = ? "
                "ORDER BY display_order, section_key",
                (umbrella_task_id,),
            )
        )
        work_items = tuple(
            {
                "section_key": row[0],
                "work_item_key": row[1],
                "status": row[2],
                "summary_markdown": row[3],
                "failure_reason": row[4],
                "display_order": row[5],
            }
            for row in connection.execute(
                "SELECT r.section_key, r.work_item_key, r.status, "
                "r.summary_markdown, r.failure_reason, s.display_order "
                "FROM deep_genome_remote_tasks AS r "
                "JOIN deep_genome_sections AS s ON "
                "s.umbrella_task_id = r.umbrella_task_id AND "
                "s.section_key = r.section_key "
                "WHERE r.umbrella_task_id = ? "
                "ORDER BY s.display_order, r.work_item_key",
                (umbrella_task_id,),
            )
        )
        return ReportRows(sections=sections, work_items=work_items)

    @staticmethod
    def _refresh_analysis_sections(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        now: str,
    ) -> None:
        """Project concrete observations onto logical section rows."""
        section_rows = tuple(
            connection.execute(
                "SELECT section_key, status, summary_markdown, "
                "failure_reason FROM deep_genome_sections "
                "WHERE umbrella_task_id = ? AND section_kind = 'analysis' "
                "ORDER BY display_order, section_key",
                (umbrella_task_id,),
            )
        )
        for section_key, old_status, old_summary, old_failure in section_rows:
            item_rows = tuple(
                connection.execute(
                    "SELECT status, summary_markdown, failure_reason, "
                    "work_item_key FROM deep_genome_remote_tasks "
                    "WHERE umbrella_task_id = ? AND section_key = ? "
                    "ORDER BY work_item_key",
                    (umbrella_task_id, section_key),
                )
            )
            if not item_rows:
                continue
            usable = [
                row
                for row in item_rows
                if row[0] == "succeeded"
                and isinstance(row[1], str)
                and row[1].strip()
            ]
            next_values: tuple[str, str | None, str | None]
            if usable:
                next_values = (
                    "succeeded",
                    "\n\n".join(row[1].strip() for row in usable),
                    None,
                )
            elif any(
                row[0] in _NONTERMINAL_WORK_ITEM_STATUSES for row in item_rows
            ):
                next_values = (
                    next(
                        status
                        for status in (
                            "running",
                            "pending",
                            "submitted",
                            "planned",
                        )
                        if any(row[0] == status for row in item_rows)
                    ),
                    None,
                    None,
                )
            else:
                next_values = (
                    "failed",
                    None,
                    "analysis section unavailable",
                )
            if next_values != (old_status, old_summary, old_failure):
                connection.execute(
                    "UPDATE deep_genome_sections SET status = ?, "
                    "summary_markdown = ?, failure_reason = ?, updated_at = ? "
                    "WHERE umbrella_task_id = ? AND section_key = ?",
                    (*next_values, now, umbrella_task_id, section_key),
                )

    @classmethod
    def _persist_report_snapshot(
        cls,
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        now: str,
    ) -> None:
        """Derive and persist one complete report snapshot in-transaction."""
        task = connection.execute(
            "SELECT report_revision, degraded_reason FROM tasks "
            "WHERE task_id = ? "
            "AND status = 'running' AND agent = 'deep_genome'",
            (umbrella_task_id,),
        ).fetchone()
        if task is None:
            raise DeepGenomeTrackingError("reserved umbrella task is missing")
        rows = cls._report_rows_for_connection(connection, umbrella_task_id)
        progress = derive_progress(rows)
        stage, completeness, _ = derive_report_classification(rows)
        intermediate = assemble_intermediate_report(rows)
        degraded_reason = derive_degraded_reason(rows, existing_reason=task[1])
        revision = task[0] if isinstance(task[0], int) else 0
        cursor = connection.execute(
            "UPDATE tasks SET intermediate_report = ?, report_revision = ?, "
            "report_stage = ?, report_completeness = ?, "
            "report_updated_at = ?, progress_json = ?, degraded_reason = ?, "
            "updated_at = ? WHERE task_id = ? AND status = 'running' "
            "AND agent = 'deep_genome'",
            (
                intermediate,
                revision + 1,
                stage,
                completeness,
                now,
                json.dumps(progress, sort_keys=True),
                degraded_reason,
                now,
                umbrella_task_id,
            ),
        )
        if cursor.rowcount != 1:
            raise DeepGenomeTrackingError("reserved umbrella task is missing")

    def apply_brief_gene_transition(
        self,
        umbrella_task_id: str,
        *,
        status: str,
        summary_markdown: str | None = None,
        failure_reason: str | None = None,
    ) -> DeepGenomeSnapshot:
        """Persist one BriefGene observation and rebuild the snapshot."""
        _ = failure_reason
        next_status = self._normalize_transition_status(status)
        now = datetime.now(UTC).isoformat()
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            self._assert_tracking_parent(connection, umbrella_task_id)
            row = connection.execute(
                "SELECT status, summary_markdown, failure_reason "
                "FROM deep_genome_sections WHERE umbrella_task_id = ? "
                "AND section_key = 'brief_gene' "
                "AND section_kind = 'brief_gene'",
                (umbrella_task_id,),
            ).fetchone()
            if row is None:
                raise DeepGenomeTrackingError(
                    "reserved BriefGene section is missing"
                )
            current_status, current_summary, current_failure = row
            next_summary, next_failure = self._next_transition_content(
                current_status, next_status, current_summary, summary_markdown
            )
            if (next_status, next_summary, next_failure) == (
                current_status,
                current_summary,
                current_failure,
            ):
                connection.rollback()
            else:
                connection.execute(
                    "UPDATE deep_genome_sections SET status = ?, "
                    "summary_markdown = ?, failure_reason = ?, updated_at = ? "
                    "WHERE umbrella_task_id = ? "
                    "AND section_key = 'brief_gene'",
                    (
                        next_status,
                        next_summary,
                        next_failure,
                        now,
                        umbrella_task_id,
                    ),
                )
                self._refresh_analysis_sections(
                    connection, umbrella_task_id, now
                )
                self._persist_report_snapshot(
                    connection, umbrella_task_id, now
                )
                connection.commit()
        except (sqlite3.Error, DeepGenomeTransitionError):
            connection.rollback()
            raise
        finally:
            connection.close()
        snapshot = self.get_snapshot(umbrella_task_id)
        if snapshot is None:
            raise DeepGenomeTrackingError("reserved umbrella task is missing")
        return snapshot

    @staticmethod
    def _read_work_item_transition(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        work_key: str,
        next_status: str,
        summary_markdown: Any,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        """Read one work item and calculate its sanitized next content."""
        row = connection.execute(
            "SELECT status, summary_markdown, failure_reason "
            "FROM deep_genome_remote_tasks WHERE umbrella_task_id = ? "
            "AND work_item_key = ?",
            (umbrella_task_id, work_key),
        ).fetchone()
        if row is None:
            raise DeepGenomeTrackingError("reserved work item is missing")
        next_summary, next_failure = (
            DeepGenomeTransitionMixin._next_transition_content(
                row[0], next_status, row[1], summary_markdown
            )
        )
        return row, (next_status, next_summary, next_failure)

    def apply_work_item_transition(
        self,
        umbrella_task_id: str,
        *,
        work_item_key: str,
        status: str,
        summary_markdown: str | None = None,
        failure_reason: str | None = None,
    ) -> DeepGenomeSnapshot:
        """Persist one concrete observation and rebuild the snapshot."""
        _ = failure_reason
        next_status = self._normalize_transition_status(status)
        work_key = self._nonblank_submission_field(
            work_item_key, "work_item_key"
        )
        now = datetime.now(UTC).isoformat()
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            self._assert_tracking_parent(connection, umbrella_task_id)
            current_values, next_values = self._read_work_item_transition(
                connection,
                umbrella_task_id,
                work_key,
                next_status,
                summary_markdown,
            )
            if next_values == current_values:
                connection.rollback()
            else:
                cursor = connection.execute(
                    "UPDATE deep_genome_remote_tasks SET status = ?, "
                    "summary_markdown = ?, failure_reason = ?, updated_at = ? "
                    "WHERE umbrella_task_id = ? AND work_item_key = ?",
                    (
                        *next_values,
                        now,
                        umbrella_task_id,
                        work_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DeepGenomeTrackingError(
                        "reserved work item is missing"
                    )
                self._refresh_analysis_sections(
                    connection, umbrella_task_id, now
                )
                self._persist_report_snapshot(
                    connection, umbrella_task_id, now
                )
                connection.commit()
        except (sqlite3.Error, DeepGenomeTransitionError):
            connection.rollback()
            raise
        finally:
            connection.close()
        snapshot = self.get_snapshot(umbrella_task_id)
        if snapshot is None:
            raise DeepGenomeTrackingError("reserved umbrella task is missing")
        return snapshot


__all__ = [
    "DeepGenomeTrackingError",
    "DeepGenomeTransitionError",
    "DeepGenomeTransitionMixin",
]
