# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""First-class fingerprint jobs and per-user-task claims.

One ``(fingerprint, generation)`` row is the real analysis-platform
job. Each caller-owned task is a claim on that generation. Stop
detaches the claim; the EI job is terminated only when the last
active claim leaves a still-running generation. A cancelled or
failed generation is skipped so the next submit relaunches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from .sqlite import sqlite_connection, sqlite_transaction

__all__ = [
    "ACTIVE_CLAIM",
    "DEAD_JOB_STATUSES",
    "FingerprintJob",
    "FingerprintJobDeadError",
    "RegisterResult",
    "RunCancelResult",
    "attach_reuse_claim",
    "cancel_run_claims",
    "ensure_schema",
    "get_latest_job",
    "mark_job_terminal",
    "register_submitted_job",
]

_JOB_RUNNING = "running"
_JOB_SUCCEEDED = "succeeded"
_JOB_FAILED = "failed"
_JOB_CANCELLED = "cancelled"
_JOB_CANCELLING = "cancelling"

ACTIVE_CLAIM = "active"
_CLAIM_CANCELLED = "cancelled"

DEAD_JOB_STATUSES = frozenset({_JOB_FAILED, _JOB_CANCELLED, _JOB_CANCELLING})
_REUSABLE_JOB_STATUSES = frozenset({_JOB_RUNNING, _JOB_SUCCEEDED})

_CREATE_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS fingerprint_jobs (
    fingerprint TEXT NOT NULL,
    generation INTEGER NOT NULL,
    ei_task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    output_dir TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (fingerprint, generation)
)
"""

_CREATE_CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS fingerprint_job_claims (
    claimant_task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    generation INTEGER NOT NULL,
    claim_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_fingerprint_jobs_ei "
    "ON fingerprint_jobs(ei_task_id)",
    "CREATE INDEX IF NOT EXISTS idx_fingerprint_jobs_fp "
    "ON fingerprint_jobs(fingerprint, generation DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fingerprint_claims_run "
    "ON fingerprint_job_claims(run_id, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_fingerprint_claims_job "
    "ON fingerprint_job_claims(fingerprint, generation, claim_state)",
)


class FingerprintJobDeadError(RuntimeError):
    """Raised when a claim cannot attach to a dead generation."""


@dataclass(frozen=True, slots=True)
class FingerprintJob:
    """One analysis-platform job keyed by fingerprint generation."""

    fingerprint: str
    generation: int
    ei_task_id: str
    status: str
    output_dir: str


@dataclass(frozen=True, slots=True)
class RegisterResult:
    """Outcome of recording a freshly submitted EI job."""

    job: FingerprintJob
    orphan_ei_task_id: str | None


@dataclass(frozen=True, slots=True)
class RunCancelResult:
    """Claims detached for one user run, plus EI ids to terminate."""

    detached_claimants: tuple[str, ...]
    terminate_ei_ids: tuple[str, ...]


def _now_iso() -> str:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC).isoformat()


def ensure_schema(db_path: str) -> None:
    """Create fingerprint job tables and indexes when missing."""
    with sqlite_transaction(db_path) as connection:
        connection.execute(_CREATE_JOBS_DDL)
        connection.execute(_CREATE_CLAIMS_DDL)
        for statement in _CREATE_INDEXES:
            connection.execute(statement)


def _ensure_schema_on(connection: object) -> None:
    """Create tables on an already-open transaction."""
    execute = getattr(connection, "execute")
    execute(_CREATE_JOBS_DDL)
    execute(_CREATE_CLAIMS_DDL)
    for statement in _CREATE_INDEXES:
        execute(statement)


def _row_to_job(row: object) -> FingerprintJob:
    """Project one SELECT row into a job value."""
    (
        fingerprint,
        generation,
        ei_task_id,
        status,
        output_dir,
    ) = row  # type: ignore[misc]
    return FingerprintJob(
        fingerprint=str(fingerprint),
        generation=int(generation),
        ei_task_id=str(ei_task_id),
        status=str(status),
        output_dir=str(output_dir or ""),
    )


def _select_latest(
    connection: object, fingerprint: str
) -> FingerprintJob | None:
    """Return the newest generation for ``fingerprint``, if any."""
    row = getattr(connection, "execute")(
        "SELECT fingerprint, generation, ei_task_id, status, output_dir "
        "FROM fingerprint_jobs WHERE fingerprint = ? "
        "ORDER BY generation DESC LIMIT 1",
        (fingerprint,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def get_latest_job(db_path: str, fingerprint: str) -> FingerprintJob | None:
    """Return the newest fingerprint generation, creating schema first."""
    with sqlite_connection(db_path) as connection:
        _ensure_schema_on(connection)
        return _select_latest(connection, fingerprint)


def _insert_claim(
    connection: object,
    *,
    claimant_task_id: str,
    run_id: str,
    user_id: str,
    fingerprint: str,
    generation: int,
    now: str,
) -> None:
    """Insert or refresh one active claim."""
    getattr(connection, "execute")(
        "INSERT INTO fingerprint_job_claims ("
        "claimant_task_id, run_id, user_id, fingerprint, generation, "
        "claim_state, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(claimant_task_id) DO UPDATE SET "
        "run_id = excluded.run_id, "
        "user_id = excluded.user_id, "
        "fingerprint = excluded.fingerprint, "
        "generation = excluded.generation, "
        "claim_state = excluded.claim_state, "
        "updated_at = excluded.updated_at",
        (
            claimant_task_id,
            run_id,
            user_id,
            fingerprint,
            generation,
            ACTIVE_CLAIM,
            now,
            now,
        ),
    )


def attach_reuse_claim(
    db_path: str,
    *,
    fingerprint: str,
    ei_task_id: str,
    output_dir: str,
    claimant_task_id: str,
    run_id: str,
    user_id: str,
    job_status: Literal["running", "succeeded"] = "running",
) -> FingerprintJob:
    """Attach a caller-owned task to a live generation, promoting if needed.

    A missing job row is inserted as generation 1 so legacy ``tasks``
    reuse rows become first-class jobs. A dead or cancelling latest
    generation raises ``FingerprintJobDeadError`` so the caller relaunches.
    """
    if job_status not in _REUSABLE_JOB_STATUSES:
        raise ValueError("job_status must be running or succeeded")
    now = _now_iso()
    with sqlite_connection(db_path) as connection:
        _ensure_schema_on(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            latest = _select_latest(connection, fingerprint)
            if latest is not None and latest.status in DEAD_JOB_STATUSES:
                raise FingerprintJobDeadError(
                    "fingerprint generation is no longer reusable"
                )
            if latest is None:
                connection.execute(
                    "INSERT INTO fingerprint_jobs ("
                    "fingerprint, generation, ei_task_id, status, "
                    "output_dir, created_at, updated_at"
                    ") VALUES (?, 1, ?, ?, ?, ?, ?)",
                    (
                        fingerprint,
                        ei_task_id,
                        job_status,
                        output_dir,
                        now,
                        now,
                    ),
                )
                generation = 1
                status = job_status
            else:
                if latest.ei_task_id != ei_task_id:
                    raise FingerprintJobDeadError(
                        "reuse ei_task_id does not match the live generation"
                    )
                if (
                    job_status == _JOB_SUCCEEDED
                    and latest.status == _JOB_RUNNING
                ):
                    connection.execute(
                        "UPDATE fingerprint_jobs SET status = ?, "
                        "updated_at = ? WHERE fingerprint = ? "
                        "AND generation = ?",
                        (job_status, now, fingerprint, latest.generation),
                    )
                    status = job_status
                else:
                    status = latest.status
                generation = latest.generation
            _insert_claim(
                connection,
                claimant_task_id=claimant_task_id,
                run_id=run_id,
                user_id=user_id,
                fingerprint=fingerprint,
                generation=generation,
                now=now,
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return FingerprintJob(
            fingerprint=fingerprint,
            generation=generation,
            ei_task_id=ei_task_id,
            status=status,
            output_dir=output_dir,
        )


def register_submitted_job(
    db_path: str,
    *,
    fingerprint: str,
    ei_task_id: str,
    output_dir: str,
    claimant_task_id: str,
    run_id: str,
    user_id: str,
    force_new: bool = False,
) -> RegisterResult:
    """Record a newly submitted EI job or attach after a lost race.

    ``force_new`` opens the next generation even when a running job
    exists (DeepGenome polling callers that must own a terminal task).
    Otherwise a concurrent miss attaches to the winner and returns the
    caller's EI id as ``orphan_ei_task_id`` so the caller can terminate
    the duplicate.
    """
    now = _now_iso()
    with sqlite_connection(db_path) as connection:
        _ensure_schema_on(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            latest = _select_latest(connection, fingerprint)
            open_new = (
                force_new
                or latest is None
                or latest.status in DEAD_JOB_STATUSES
            )
            if not open_new and latest is not None:
                _insert_claim(
                    connection,
                    claimant_task_id=claimant_task_id,
                    run_id=run_id,
                    user_id=user_id,
                    fingerprint=fingerprint,
                    generation=latest.generation,
                    now=now,
                )
                orphan = (
                    ei_task_id if ei_task_id != latest.ei_task_id else None
                )
                connection.execute("COMMIT")
                return RegisterResult(latest, orphan)
            generation = 1 if latest is None else latest.generation + 1
            connection.execute(
                "INSERT INTO fingerprint_jobs ("
                "fingerprint, generation, ei_task_id, status, "
                "output_dir, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    fingerprint,
                    generation,
                    ei_task_id,
                    _JOB_RUNNING,
                    output_dir,
                    now,
                    now,
                ),
            )
            _insert_claim(
                connection,
                claimant_task_id=claimant_task_id,
                run_id=run_id,
                user_id=user_id,
                fingerprint=fingerprint,
                generation=generation,
                now=now,
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return RegisterResult(
            FingerprintJob(
                fingerprint=fingerprint,
                generation=generation,
                ei_task_id=ei_task_id,
                status=_JOB_RUNNING,
                output_dir=output_dir,
            ),
            None,
        )


def mark_job_terminal(
    db_path: str,
    ei_task_id: str,
    status: Literal["succeeded", "failed", "cancelled"],
) -> bool:
    """Advance a running or cancelling job to a terminal status."""
    if status not in {_JOB_SUCCEEDED, _JOB_FAILED, _JOB_CANCELLED}:
        raise ValueError("status must be a terminal fingerprint job state")
    now = _now_iso()
    with sqlite_connection(db_path) as connection:
        _ensure_schema_on(connection)
        cursor = connection.execute(
            "UPDATE fingerprint_jobs SET status = ?, updated_at = ? "
            "WHERE ei_task_id = ? AND status IN (?, ?)",
            (
                status,
                now,
                ei_task_id,
                _JOB_RUNNING,
                _JOB_CANCELLING,
            ),
        )
        return int(cursor.rowcount) == 1


def _active_claim_count(
    connection: object, fingerprint: str, generation: int
) -> int:
    """Count still-active claims on one generation."""
    row = getattr(connection, "execute")(
        "SELECT COUNT(*) FROM fingerprint_job_claims "
        "WHERE fingerprint = ? AND generation = ? AND claim_state = ?",
        (fingerprint, generation, ACTIVE_CLAIM),
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def cancel_run_claims(
    db_path: str,
    *,
    run_id: str,
    user_id: str,
) -> RunCancelResult:
    """Detach this run's claims and mark last-claim jobs for terminate.

    Succeeded generations are never terminated. A running generation
    whose active count reaches zero is marked ``cancelling`` and its
    ``ei_task_id`` is returned so the caller can ``task_delete``.
    """
    if not run_id or not user_id:
        return RunCancelResult((), ())
    now = _now_iso()
    with sqlite_connection(db_path) as connection:
        _ensure_schema_on(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "tasks" in tables:
                claims = connection.execute(
                    "SELECT claimant_task_id, fingerprint, generation "
                    "FROM fingerprint_job_claims "
                    "WHERE claim_state = ? AND ("
                    "(run_id = ? AND user_id = ?) OR claimant_task_id IN ("
                    "SELECT task_id FROM tasks WHERE run_id = ? "
                    "AND user_id = ?))",
                    (ACTIVE_CLAIM, run_id, user_id, run_id, user_id),
                ).fetchall()
            else:
                claims = connection.execute(
                    "SELECT claimant_task_id, fingerprint, generation "
                    "FROM fingerprint_job_claims "
                    "WHERE run_id = ? AND user_id = ? AND claim_state = ?",
                    (run_id, user_id, ACTIVE_CLAIM),
                ).fetchall()
            detached: list[str] = []
            touched: set[tuple[str, int]] = set()
            for claimant_task_id, fingerprint, generation in claims:
                connection.execute(
                    "UPDATE fingerprint_job_claims SET claim_state = ?, "
                    "updated_at = ? WHERE claimant_task_id = ? "
                    "AND claim_state = ?",
                    (
                        _CLAIM_CANCELLED,
                        now,
                        claimant_task_id,
                        ACTIVE_CLAIM,
                    ),
                )
                detached.append(str(claimant_task_id))
                touched.add((str(fingerprint), int(generation)))
            terminate: list[str] = []
            for fingerprint, generation in touched:
                if (
                    _active_claim_count(connection, fingerprint, generation)
                    > 0
                ):
                    continue
                job = connection.execute(
                    "SELECT ei_task_id, status FROM fingerprint_jobs "
                    "WHERE fingerprint = ? AND generation = ?",
                    (fingerprint, generation),
                ).fetchone()
                if job is None:
                    continue
                ei_task_id, status = str(job[0]), str(job[1])
                if status != _JOB_RUNNING:
                    continue
                updated = connection.execute(
                    "UPDATE fingerprint_jobs SET status = ?, "
                    "updated_at = ? WHERE fingerprint = ? "
                    "AND generation = ? AND status = ?",
                    (
                        _JOB_CANCELLING,
                        now,
                        fingerprint,
                        generation,
                        _JOB_RUNNING,
                    ),
                )
                if int(updated.rowcount) == 1:
                    terminate.append(ei_task_id)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return RunCancelResult(tuple(detached), tuple(terminate))
