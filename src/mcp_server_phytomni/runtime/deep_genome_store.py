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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from .run_registry import RunRegistry
from .task_manager import _CREATE_TASKS_DDL, _TASK_ADD_COLUMN_STATEMENTS

__all__ = [
    "DeepGenomeRemoteTaskRow",
    "DeepGenomeReservation",
    "DeepGenomeSectionRow",
    "DeepGenomeSnapshot",
    "DeepGenomeStore",
]

_WORK_ITEM_STATUSES = (
    "planned",
    "submitted",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)
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
class _SnapshotIdentity:
    """Identity fields shared by the public snapshot DTO."""

    umbrella_task_id: str
    status: str


@dataclass(frozen=True)
class _SnapshotReport:
    """Report fields shared by the public snapshot DTO."""

    intermediate_report: str | None
    final_report: str | None
    report_stage: str
    report_completeness: str
    report_revision: int
    report_updated_at: str | None


@dataclass(frozen=True)
class _SnapshotHealth:
    """Progress and degradation fields shared by the snapshot DTO."""

    progress: Mapping[str, int | bool | str]
    degraded: bool
    degraded_reason: str | None
    failures: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class DeepGenomeSnapshot(_SnapshotIdentity, _SnapshotReport, _SnapshotHealth):
    """Public-shaped local snapshot DTO used by later read paths."""


@dataclass(frozen=True)
class _SectionIdentity:
    """Identity fields for one logical section row."""

    umbrella_task_id: str
    section_key: str
    section_kind: str
    display_order: int


@dataclass(frozen=True)
class _SectionContent:
    """State and timestamps for one logical section row."""

    status: str
    summary_markdown: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DeepGenomeSectionRow(_SectionIdentity, _SectionContent):
    """Immutable row projection for one logical DeepGenome section."""


@dataclass(frozen=True)
class _RemoteIdentity:
    """Identity fields for one concrete remote work item."""

    umbrella_task_id: str
    section_key: str
    work_item_key: str
    status: str


@dataclass(frozen=True)
class _RemoteContent:
    """Remote identities, content, and timestamps for one work item."""

    submitted_task_id: str | None
    poll_task_id: str | None
    summary_markdown: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DeepGenomeRemoteTaskRow(_RemoteIdentity, _RemoteContent):
    """Immutable row projection for one concrete remote work item."""


class DeepGenomeStore:
    """Own the additive DeepGenome schema in one SQLite database."""

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
        RunRegistry(self.db_path)
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
            failures=(),
        )
