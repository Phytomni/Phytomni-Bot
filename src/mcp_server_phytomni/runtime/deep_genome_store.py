# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Transactional SQLite storage contracts for DeepGenome reporting.

This module owns only the additive schema and read DTOs at this stage. Later
workflow tasks add reservation and transition methods on the same store; the
schema initializer is deliberately complete and repeatable before those
writers are introduced.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

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
