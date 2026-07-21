# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Transactional SQLite storage contracts for DeepGenome reporting.

The store owns the additive schema, the immutable read DTOs, and the first
durable launch barrier. Reservation writes the owner run, umbrella task, and
required BriefGene section in one SQLite transaction before the coordinator
can be scheduled.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import TYPE_CHECKING, Any

from .deep_genome_store_projection import (
    DeepGenomeRemoteTaskRow,
    DeepGenomeSectionRow,
    DeepGenomeSnapshot,
    snapshot_to_formatted_report_metadata,
    snapshot_to_public_dict,
)
from .deep_genome_transitions import (
    _TERMINAL_WORK_ITEM_STATUSES,
    _WORK_ITEM_STATUSES,
    DeepGenomeTrackingError,
    DeepGenomeTransitionError,
    DeepGenomeTransitionMixin,
)
from .task_manager import (
    _CREATE_TASKS_DDL,
    _TASK_ADD_COLUMN_STATEMENTS,
    _expires_at_for,
)

if TYPE_CHECKING:
    from ..agents.deep_genome.work_items import WorkItemSpec

__all__ = [
    "DeepGenomeRemoteTaskRow",
    "DeepGenomeReservation",
    "DeepGenomeSectionRow",
    "DeepGenomeSnapshot",
    "DeepGenomeStore",
    "DeepGenomeTrackingError",
    "DeepGenomeTransitionError",
    "RemoteSubmission",
    "snapshot_to_formatted_report_metadata",
    "snapshot_to_public_dict",
]

_WORK_ITEM_STATUS_SQL = ", ".join(
    f"'{status}'" for status in _WORK_ITEM_STATUSES
)

_CREATE_SECTIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS deep_genome_sections (
    umbrella_task_id TEXT NOT NULL,
    section_key TEXT NOT NULL,
    section_kind TEXT NOT NULL CHECK (
        section_kind IN ('brief_gene', 'analysis')
    ),
    display_order INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({_WORK_ITEM_STATUS_SQL})),
    summary_markdown TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (umbrella_task_id, section_key),
    FOREIGN KEY (umbrella_task_id) REFERENCES tasks(task_id)
)
"""

_CREATE_REMOTE_TASKS_DDL = f"""
CREATE TABLE IF NOT EXISTS deep_genome_remote_tasks (
    umbrella_task_id TEXT NOT NULL,
    section_key TEXT NOT NULL,
    work_item_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({_WORK_ITEM_STATUS_SQL})),
    submitted_task_id TEXT,
    poll_task_id TEXT,
    summary_markdown TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (umbrella_task_id, work_item_key),
    FOREIGN KEY (umbrella_task_id, section_key)
        REFERENCES deep_genome_sections(umbrella_task_id, section_key)
)
"""


@dataclass(frozen=True)
class DeepGenomeReservation:
    """Identity returned after an umbrella run is durably reserved."""

    run_id: str
    umbrella_task_id: str
    owner: str
    output_dir: str


@dataclass(frozen=True)
class RemoteSubmission:
    """Caller and effective identities accepted by a remote platform."""

    submitted_task_id: str
    poll_task_id: str
    output_dir: str


class DeepGenomeStore(DeepGenomeTransitionMixin):
    """Own the additive DeepGenome schema in one SQLite database."""

    LOCAL_COORDINATOR_FAILURE = "local coordinator failed to start"

    def __init__(self, db_path: str):
        """Initialize the schema using one transactional migration."""
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        """Create or upgrade all DeepGenome tables atomically."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(_CREATE_TASKS_DDL)
            existing = {
                row[1] for row in conn.execute("PRAGMA table_info(tasks)")
            }
            for column, statement in _TASK_ADD_COLUMN_STATEMENTS:
                if column not in existing:
                    conn.execute(statement)
            conn.execute(_CREATE_SECTIONS_DDL)
            conn.execute(_CREATE_REMOTE_TASKS_DDL)
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _reservation_execute(
        self,
        connection: sqlite3.Connection,
        stage: str,
        statement: str,
        parameters: tuple[object, ...],
    ) -> None:
        """Execute one named reservation statement.

        The small seam keeps the transaction statements explicit and gives
        tests a deterministic way to inject a SQLite failure at each write.
        Production callers always use the ordinary connection execution.
        """
        _ = stage
        connection.execute(statement, parameters)

    def reserve_run(
        self,
        *,
        run_id: str,
        umbrella_task_id: str,
        owner: str,
        output_dir: str,
    ) -> DeepGenomeReservation:
        """Atomically reserve a DeepGenome owner run and profile section.

        The run registry schema is initialized before the transaction, while
        the three reservation rows themselves are inserted with plain
        ``INSERT`` statements under one ``BEGIN IMMEDIATE``. Identity
        collisions therefore raise and roll back instead of replacing an
        existing owner's rows.
        """
        # ``RunRegistry`` owns the runs DDL and its additive columns. It is
        # initialized before opening the reservation connection so schema
        # setup cannot be mistaken for a partially committed reservation.
        # ``run_registry`` imports this reconciliation module; resolve it by
        # name here to keep the storage layer's schema bootstrap acyclic.
        registry_module = import_module(
            "mcp_server_phytomni.runtime.run_registry"
        )
        registry_type = getattr(registry_module, "RunRegistry")
        registry_type(self.db_path)
        now = datetime.now(UTC).isoformat()
        placeholder = {
            "task_id": umbrella_task_id,
            "status": "running",
            "output_dir": output_dir,
        }
        initial_result = {
            "task_results": [placeholder],
            "live_status": [placeholder],
            "artifacts": [],
        }
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            self._reservation_execute(
                conn,
                "run",
                """
                INSERT INTO runs (
                    run_id, user_id, agent, origin, status, result_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    owner,
                    "deep_genome",
                    "remote",
                    "running",
                    json.dumps(initial_result),
                    now,
                    now,
                ),
            )
            self._reservation_execute(
                conn,
                "task",
                """
                INSERT INTO tasks (
                    task_id, status, analysis_id, output_dir,
                    run_id, user_id, agent, origin, created_at, updated_at,
                    intermediate_report, report_revision, report_stage,
                    report_completeness, report_updated_at, progress_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    umbrella_task_id,
                    "running",
                    "",
                    output_dir,
                    run_id,
                    owner,
                    "deep_genome",
                    "remote",
                    now,
                    now,
                    None,
                    0,
                    "waiting_for_brief_gene",
                    "none",
                    now,
                    json.dumps({}),
                ),
            )
            self._reservation_execute(
                conn,
                "brief_gene",
                """
                INSERT INTO deep_genome_sections (
                    umbrella_task_id, section_key, section_kind,
                    display_order, status, summary_markdown, failure_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    umbrella_task_id,
                    "brief_gene",
                    "brief_gene",
                    0,
                    "planned",
                    None,
                    None,
                    now,
                    now,
                ),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()
        return DeepGenomeReservation(
            run_id=run_id,
            umbrella_task_id=umbrella_task_id,
            owner=owner,
            output_dir=output_dir,
        )

    def compensate_launch_failure(
        self,
        reservation: DeepGenomeReservation,
    ) -> None:
        """Fail a reserved launch and remove its seeded profile section."""
        now = datetime.now(UTC).isoformat()
        reason = self.LOCAL_COORDINATOR_FAILURE
        expires_at = _expires_at_for("failed", now)
        placeholder = {
            "task_id": reservation.umbrella_task_id,
            "status": "failed",
            "output_dir": reservation.output_dir,
        }
        failed_result = {
            "task_results": [placeholder],
            "live_status": [placeholder],
            "artifacts": [],
        }
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE runs SET status = ?, result_json = ?, error = ?,
                       updated_at = ?, expires_at = ?
                WHERE run_id = ? AND user_id = ?
                """,
                (
                    "failed",
                    json.dumps(failed_result),
                    reason,
                    now,
                    expires_at,
                    reservation.run_id,
                    reservation.owner,
                ),
            )
            conn.execute(
                """
                UPDATE tasks SET status = ?, final_report = NULL,
                       degraded_reason = ?, updated_at = ?
                WHERE task_id = ? AND run_id = ?
                """,
                (
                    "failed",
                    reason,
                    now,
                    reservation.umbrella_task_id,
                    reservation.run_id,
                ),
            )
            conn.execute(
                """
                DELETE FROM deep_genome_sections
                WHERE umbrella_task_id = ? AND section_key = ?
                """,
                (reservation.umbrella_task_id, "brief_gene"),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _progress_for_connection(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        *,
        planning_complete: bool,
    ) -> dict[str, int | bool | str]:
        """Derive the persisted concrete-work progress counters."""
        brief_row = connection.execute(
            "SELECT status FROM deep_genome_sections "
            "WHERE umbrella_task_id = ? AND section_key = 'brief_gene'",
            (umbrella_task_id,),
        ).fetchone()
        total = connection.execute(
            "SELECT COUNT(*) FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ?",
            (umbrella_task_id,),
        ).fetchone()[0]
        counts = {status: 0 for status in _WORK_ITEM_STATUSES}
        for status, count in connection.execute(
            "SELECT status, COUNT(*) FROM deep_genome_remote_tasks "
            "WHERE umbrella_task_id = ? GROUP BY status",
            (umbrella_task_id,),
        ):
            if status in counts:
                counts[status] = int(count)
        return {
            "planning_complete": planning_complete,
            "brief_gene_status": (
                brief_row[0] if brief_row is not None else "unknown"
            ),
            "total": int(total),
            **counts,
        }

    @classmethod
    def _update_progress(
        cls,
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        now: str,
        *,
        planning_complete: bool,
    ) -> None:
        """Persist the current work-item counters on the umbrella row."""
        progress = cls._progress_for_connection(
            connection,
            umbrella_task_id,
            planning_complete=planning_complete,
        )
        connection.execute(
            "UPDATE tasks SET progress_json = ?, updated_at = ? "
            "WHERE task_id = ?",
            (json.dumps(progress), now, umbrella_task_id),
        )

    @staticmethod
    def _nonblank_submission_field(value: Any, name: str) -> str:
        """Validate one remote identity field without exposing its value."""
        if not isinstance(value, str) or not value.strip():
            raise DeepGenomeTransitionError(f"{name} must be nonblank")
        return value.strip()

    @staticmethod
    def _validate_seed_plan(
        items: Sequence[WorkItemSpec],
    ) -> tuple[tuple[WorkItemSpec, ...], dict[str, int]]:
        """Validate concrete identities and derive section display order."""
        plan = tuple(items)
        if not plan:
            raise DeepGenomeTransitionError("work item plan must not be empty")
        if len({item.work_item_key for item in plan}) != len(plan):
            raise DeepGenomeTransitionError("work item identity is duplicated")
        display_orders: set[int] = set()
        section_orders: dict[str, int] = {}
        for item in plan:
            for field_name in (
                "section_key",
                "work_item_key",
                "analysis_type",
                "compute_resource",
                "target_gene",
                "species_code",
            ):
                DeepGenomeStore._nonblank_submission_field(
                    getattr(item, field_name, None), field_name
                )
            if (
                isinstance(item.display_order, bool)
                or not isinstance(item.display_order, int)
                or item.display_order < 0
            ):
                raise DeepGenomeTransitionError(
                    "display_order must be a nonnegative integer"
                )
            if item.display_order in display_orders:
                raise DeepGenomeTransitionError("display_order must be unique")
            display_orders.add(item.display_order)
            if item.section_key == "brief_gene":
                raise DeepGenomeTransitionError(
                    "brief_gene is not an optional work item"
                )
            section_orders[item.section_key] = min(
                section_orders.get(item.section_key, item.display_order + 1),
                item.display_order + 1,
            )
        return plan, section_orders

    @staticmethod
    def _assert_active_umbrella(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
    ) -> None:
        """Reject acceptance after the owner run or task is terminal."""
        try:
            DeepGenomeTransitionMixin._assert_tracking_parent(
                connection, umbrella_task_id
            )
        except DeepGenomeTrackingError as error:
            raise DeepGenomeTransitionError(str(error)) from error

    @staticmethod
    def _assert_seed_parent(
        connection: sqlite3.Connection,
        reservation: DeepGenomeReservation,
    ) -> None:
        """Require an owner-scoped reservation with successful BriefGene."""
        parent = connection.execute(
            "SELECT t.status, r.status FROM tasks AS t "
            "JOIN runs AS r ON r.run_id = t.run_id "
            "WHERE t.task_id = ? AND t.run_id = ? AND t.user_id = ? "
            "AND t.agent = 'deep_genome' AND r.agent = 'deep_genome'",
            (
                reservation.umbrella_task_id,
                reservation.run_id,
                reservation.owner,
            ),
        ).fetchone()
        if parent is None:
            raise DeepGenomeTransitionError(
                "reserved umbrella task is missing"
            )
        if parent != ("running", "running"):
            raise DeepGenomeTransitionError("umbrella task is terminal")
        brief_status = connection.execute(
            "SELECT status FROM deep_genome_sections "
            "WHERE umbrella_task_id = ? AND section_key = 'brief_gene'",
            (reservation.umbrella_task_id,),
        ).fetchone()
        if brief_status is None:
            raise DeepGenomeTransitionError(
                "reserved BriefGene section is missing"
            )
        if brief_status[0] != "succeeded":
            raise DeepGenomeTransitionError(
                "BriefGene must succeed before planning analysis"
            )

    @staticmethod
    def _seed_sections(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        section_orders: Mapping[str, int],
        now: str,
    ) -> None:
        """Insert analysis sections and verify their immutable identities."""
        for section_key, display_order in section_orders.items():
            connection.execute(
                "INSERT OR IGNORE INTO deep_genome_sections ("
                "umbrella_task_id, section_key, section_kind, "
                "display_order, status, summary_markdown, failure_reason, "
                "created_at, updated_at"
                ") VALUES (?, ?, 'analysis', ?, 'planned', NULL, NULL, ?, ?)",
                (umbrella_task_id, section_key, display_order, now, now),
            )
            existing = connection.execute(
                "SELECT section_kind, display_order FROM "
                "deep_genome_sections WHERE umbrella_task_id = ? "
                "AND section_key = ?",
                (umbrella_task_id, section_key),
            ).fetchone()
            if existing != ("analysis", display_order):
                raise DeepGenomeTransitionError(
                    "section identity is immutable"
                )

    @staticmethod
    def _seed_work_items(
        connection: sqlite3.Connection,
        umbrella_task_id: str,
        plan: Sequence[WorkItemSpec],
        now: str,
    ) -> None:
        """Insert concrete work items and verify their parent identities."""
        for item in plan:
            connection.execute(
                "INSERT OR IGNORE INTO deep_genome_remote_tasks ("
                "umbrella_task_id, section_key, work_item_key, status, "
                "submitted_task_id, poll_task_id, summary_markdown, "
                "failure_reason, created_at, updated_at"
                ") VALUES (?, ?, ?, 'planned', NULL, NULL, NULL, NULL, ?, ?)",
                (
                    umbrella_task_id,
                    item.section_key,
                    item.work_item_key,
                    now,
                    now,
                ),
            )
            existing = connection.execute(
                "SELECT section_key FROM deep_genome_remote_tasks "
                "WHERE umbrella_task_id = ? AND work_item_key = ?",
                (umbrella_task_id, item.work_item_key),
            ).fetchone()
            if existing != (item.section_key,):
                raise DeepGenomeTransitionError(
                    "work item identity is immutable"
                )

    def seed_plan(
        self,
        reservation: DeepGenomeReservation,
        items: Sequence[WorkItemSpec],
    ) -> DeepGenomeSnapshot:
        """Persist logical sections and concrete work items idempotently."""
        plan, section_orders = self._validate_seed_plan(items)
        now = datetime.now(UTC).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            self._assert_seed_parent(conn, reservation)
            self._seed_sections(
                conn,
                reservation.umbrella_task_id,
                section_orders,
                now,
            )
            self._seed_work_items(
                conn,
                reservation.umbrella_task_id,
                plan,
                now,
            )

            self._update_progress(
                conn,
                reservation.umbrella_task_id,
                now,
                planning_complete=True,
            )
            conn.execute(
                "UPDATE tasks SET report_stage = ?, report_completeness = ? "
                "WHERE task_id = ?",
                (
                    "intermediate",
                    "partial",
                    reservation.umbrella_task_id,
                ),
            )
            conn.commit()
        except (sqlite3.Error, DeepGenomeTransitionError):
            conn.rollback()
            raise
        finally:
            conn.close()
        snapshot = self.get_snapshot(reservation.umbrella_task_id)
        if snapshot is None:
            raise DeepGenomeTransitionError(
                "reserved umbrella task is missing"
            )
        return snapshot

    def accept_remote_submission(
        self,
        umbrella_task_id: str,
        *,
        work_item_key: str,
        submission: RemoteSubmission,
    ) -> DeepGenomeSnapshot:
        """Persist caller/effective remote ids exactly once."""
        submitted_id = self._nonblank_submission_field(
            submission.submitted_task_id, "submitted_task_id"
        )
        poll_id = self._nonblank_submission_field(
            submission.poll_task_id, "poll_task_id"
        )
        self._nonblank_submission_field(submission.output_dir, "output_dir")
        now = datetime.now(UTC).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            self._assert_active_umbrella(conn, umbrella_task_id)
            row = conn.execute(
                "SELECT status, submitted_task_id, poll_task_id "
                "FROM deep_genome_remote_tasks WHERE umbrella_task_id = ? "
                "AND work_item_key = ?",
                (umbrella_task_id, work_item_key),
            ).fetchone()
            if row is None:
                raise DeepGenomeTransitionError("unknown work item")
            status, stored_submitted, stored_poll = row
            changed = False
            if stored_submitted is None and stored_poll is None:
                if status != "planned":
                    raise DeepGenomeTransitionError(
                        "remote submission is no longer planned"
                    )
                cursor = conn.execute(
                    "UPDATE deep_genome_remote_tasks "
                    "SET status = 'submitted', "
                    "submitted_task_id = ?, poll_task_id = ?, updated_at = ? "
                    "WHERE umbrella_task_id = ? AND work_item_key = ? "
                    "AND status = 'planned' AND submitted_task_id IS NULL "
                    "AND poll_task_id IS NULL",
                    (
                        submitted_id,
                        poll_id,
                        now,
                        umbrella_task_id,
                        work_item_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DeepGenomeTransitionError("identity is immutable")
                changed = True
            elif (stored_submitted, stored_poll) != (submitted_id, poll_id):
                raise DeepGenomeTransitionError("identity is immutable")
            elif status == "planned":
                conn.execute(
                    "UPDATE deep_genome_remote_tasks "
                    "SET status = 'submitted', "
                    "updated_at = ? WHERE umbrella_task_id = ? "
                    "AND work_item_key = ? AND submitted_task_id = ? "
                    "AND poll_task_id = ?",
                    (
                        now,
                        umbrella_task_id,
                        work_item_key,
                        submitted_id,
                        poll_id,
                    ),
                )
                changed = True
            if changed:
                self._update_progress(
                    conn,
                    umbrella_task_id,
                    now,
                    planning_complete=True,
                )
            conn.commit()
        except (sqlite3.Error, DeepGenomeTransitionError):
            conn.rollback()
            raise
        finally:
            conn.close()
        snapshot = self.get_snapshot(umbrella_task_id)
        if snapshot is None:
            raise DeepGenomeTransitionError(
                "reserved umbrella task is missing"
            )
        return snapshot

    def get_snapshot(self, umbrella_task_id: str) -> DeepGenomeSnapshot | None:
        """Read the additive report fields without changing legacy shape."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT status, intermediate_report, final_report,
                       report_stage, report_completeness, report_revision,
                       report_updated_at, progress_json, degraded_reason
                FROM tasks WHERE task_id = ?
                """,
                (umbrella_task_id,),
            ).fetchone()
            failure_rows = tuple(
                conn.execute(
                    "SELECT work_item_key, status, summary_markdown FROM "
                    "deep_genome_remote_tasks WHERE umbrella_task_id = ? "
                    "ORDER BY work_item_key",
                    (umbrella_task_id,),
                )
            )
        finally:
            conn.close()
        if row is None:
            return None
        progress: Mapping[str, int | bool | str] = {}
        if isinstance(row[7], str):
            try:
                decoded = json.loads(row[7])
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                progress = {
                    str(key): value
                    for key, value in decoded.items()
                    if isinstance(value, (bool, int, str))
                }
        failures = tuple(
            {
                "work_item_key": work_item_key,
                "status": status,
                "reason": self._failure_reason_for_status(status)
                or "analysis task unavailable",
            }
            for work_item_key, status, summary_markdown in failure_rows
            if status in _TERMINAL_WORK_ITEM_STATUSES
            and (
                status != "succeeded"
                or not isinstance(summary_markdown, str)
                or not summary_markdown.strip()
            )
        )
        return DeepGenomeSnapshot(
            umbrella_task_id=umbrella_task_id,
            status=row[0] or "running",
            intermediate_report=row[1],
            final_report=row[2],
            report_stage=row[3] or "waiting_for_brief_gene",
            report_completeness=row[4] or "none",
            report_revision=row[5] if isinstance(row[5], int) else 0,
            report_updated_at=row[6],
            progress=progress,
            degraded=bool(row[8]),
            degraded_reason=row[8],
            failures=failures,
        )
