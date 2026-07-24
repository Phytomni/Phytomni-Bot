# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Explicit, quiesced DeepGenome task-database rollback preparation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .deep_genome_store import DeepGenomeStore
from .run_registry import RunRegistry
from .sqlite import sqlite_transaction
from .task_manager import _expires_at_for

__all__ = [
    "RollbackRefusedError",
    "RollbackResult",
    "main",
    "prepare_rollback",
]

_NON_TERMINAL_STATUSES = ("running", "submitted", "pending")
_ROLLBACK_FAILURE_REASON = "workflow interrupted by rollback preparation"


class RollbackRefusedError(RuntimeError):
    """Raised when persisted work lacks rollback acknowledgement."""

    def __init__(self, count: int, backup_path: str | None = None) -> None:
        self.count = count
        self.backup_path = backup_path
        super().__init__(f"nonterminal DeepGenome runs: {count}")


@dataclass(frozen=True)
class RollbackResult:
    """Counts and paths emitted by one successful rollback preparation."""

    database_path: str
    backup_path: str
    remote_tasks_deleted: int
    sections_deleted: int
    nonterminal_runs_failed: int


def _require_database_path(db_path: str) -> Path:
    """Validate the explicit database path without creating a new file."""
    if not isinstance(db_path, str) or not db_path.strip():
        raise ValueError("database path is required")
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError("database path is not a file")
    return path


def _next_backup_path(path: Path) -> Path:
    """Return a non-existing UTC timestamped sibling backup path."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = path.with_name(f"{path.name}.rollback-{stamp}.bak")
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.name}.rollback-{stamp}-{suffix}.bak"
        )
        suffix += 1
    return candidate


def _create_backup(source_path: Path, backup_path: Path, mode: int) -> None:
    """Copy a live SQLite database through the SQLite backup API."""
    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
    os.chmod(backup_path, mode)


def _assert_integrity(path: Path) -> None:
    """Require SQLite's complete integrity check to return ``ok``."""
    with sqlite_transaction(str(path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise sqlite3.DatabaseError("SQLite integrity check failed")


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    """Count one known DeepGenome table."""
    return int(
        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    )


def _nonterminal_run_ids(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Return owner runs with either a nonterminal run or umbrella row."""
    placeholders = ",".join("?" for _ in _NON_TERMINAL_STATUSES)
    rows = connection.execute(
        "SELECT DISTINCT r.run_id FROM runs AS r "
        "JOIN tasks AS t ON t.run_id = r.run_id "
        "WHERE r.agent = 'deep_genome' AND t.agent = 'deep_genome' "
        f"AND (lower(r.status) IN ({placeholders}) "
        f"OR lower(t.status) IN ({placeholders}))",
        (*_NON_TERMINAL_STATUSES, *_NON_TERMINAL_STATUSES),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _mark_nonterminal_failed(
    connection: sqlite3.Connection, run_ids: Sequence[str], now: str
) -> None:
    """Set acknowledged nonterminal owner rows to a fixed failed reason."""
    if not run_ids:
        return
    placeholders = ",".join("?" for _ in _NON_TERMINAL_STATUSES)
    for run_id in run_ids:
        task = connection.execute(
            "SELECT task_id, output_dir FROM tasks "
            "WHERE run_id = ? AND agent = 'deep_genome' "
            "ORDER BY task_id LIMIT 1",
            (run_id,),
        ).fetchone()
        if task is None:
            raise sqlite3.DatabaseError("DeepGenome umbrella task is missing")
        task_id, output_dir = task
        result_row = {
            "task_id": str(task_id),
            "status": "failed",
            "output_dir": str(output_dir or ""),
            "final_report": None,
        }
        result_payload = {
            "task_results": [result_row],
            "live_status": [result_row],
            "artifacts": [],
            "final_report": None,
            "degraded": True,
        }
        connection.execute(
            "UPDATE tasks SET status = 'failed', final_report = NULL, "
            "degraded_reason = ?, "
            "updated_at = ? WHERE run_id = ? AND agent = 'deep_genome' "
            f"AND lower(status) IN ({placeholders})",
            (
                _ROLLBACK_FAILURE_REASON,
                now,
                run_id,
                *_NON_TERMINAL_STATUSES,
            ),
        )
        connection.execute(
            "UPDATE runs SET status = 'failed', result_json = ?, error = ?, "
            "updated_at = ?, expires_at = ? "
            "WHERE run_id = ? AND agent = 'deep_genome'",
            (
                json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
                _ROLLBACK_FAILURE_REASON,
                now,
                _expires_at_for("failed", now),
                run_id,
            ),
        )


def _delete_children(connection: sqlite3.Connection) -> tuple[int, int]:
    """Delete concrete rows before logical sections and return row counts."""
    remote_count = _table_count(connection, "deep_genome_remote_tasks")
    section_count = _table_count(connection, "deep_genome_sections")
    connection.execute("DELETE FROM deep_genome_remote_tasks")
    connection.execute("DELETE FROM deep_genome_sections")
    return remote_count, section_count


def prepare_rollback(
    db_path: str, *, mark_nonterminal_failed: bool = False
) -> RollbackResult:
    """Backup and empty DeepGenome child tables for an older binary.

    The caller must stop the API first. This command can inspect persisted
    rows only; it does not claim to observe another process's live registry.
    """
    database = _require_database_path(db_path)
    RunRegistry(str(database))
    DeepGenomeStore(str(database))
    mode = stat.S_IMODE(database.stat().st_mode)
    backup_path = _next_backup_path(database)
    _create_backup(database, backup_path, mode)
    _assert_integrity(backup_path)

    marked = 0
    with sqlite_transaction(str(database), timeout=10.0) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("BEGIN IMMEDIATE")
        run_ids = _nonterminal_run_ids(connection)
        if run_ids and not mark_nonterminal_failed:
            raise RollbackRefusedError(len(run_ids), str(backup_path))
        now = datetime.now(UTC).isoformat()
        if run_ids:
            _mark_nonterminal_failed(connection, run_ids, now)
            marked = len(run_ids)
        remote_count, section_count = _delete_children(connection)

    os.chmod(database, mode)
    os.chmod(backup_path, mode)
    _assert_integrity(database)
    return RollbackResult(
        database_path=str(database),
        backup_path=str(backup_path),
        remote_tasks_deleted=remote_count,
        sections_deleted=section_count,
        nonterminal_runs_failed=marked,
    )


def _parser() -> argparse.ArgumentParser:
    """Build the deterministic task-database command parser."""
    parser = argparse.ArgumentParser(prog="phytomni-task-db")
    commands = parser.add_subparsers(dest="command")
    rollback = commands.add_parser(
        "prepare-deep-genome-rollback",
        help="backup and remove DeepGenome child rows",
    )
    rollback.add_argument("--db", required=True, help="explicit SQLite path")
    rollback.add_argument(
        "--mark-nonterminal-failed",
        action="store_true",
        help="acknowledge stopped service and fail persisted work",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the task-database command and return a stable exit code."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 1
    if args.command != "prepare-deep-genome-rollback":
        parser.print_usage(sys.stderr)
        return 2
    try:
        result = prepare_rollback(
            args.db,
            mark_nonterminal_failed=args.mark_nonterminal_failed,
        )
    except RollbackRefusedError as error:
        print(f"rollback refused: {error}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError):
        print("rollback preparation failed", file=sys.stderr)
        return 1
    print(f"database_path={result.database_path}")
    print(f"backup_path={result.backup_path}")
    print(f"remote_tasks_deleted={result.remote_tasks_deleted}")
    print(f"sections_deleted={result.sections_deleted}")
    print(f"nonterminal_runs_failed={result.nonterminal_runs_failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
