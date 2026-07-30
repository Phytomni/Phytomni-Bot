# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Run-scoped registry over the shared task database.

Owns the ``runs`` table in the same SQLite file as ``tasks`` so the
API and MCP paths read one source of truth. Child task ids are derived
from ``tasks.run_id`` on read (no denormalised column to drift).

Public dataclasses: RunSpec, RunFilter, Timestamps, RunRecord, RunRegistry.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .run_registry_models import (
    _A2A_COLUMNS,
    _CREATE_A2A_TASK_INDEX,
    _CREATE_A2UI_ACTIONS_DDL,
    _CREATE_A2UI_OWNER_ACTION_INDEX,
    _CREATE_RUNS_DDL,
    _CREATE_RUNS_USER_INDEX,
    _CREATE_TASKS_RUN_INDEX,
    _FAILURE_STATUSES,
    _NON_POLLABLE_RUN_STATUSES,
    _REQUEST_INFO_COLUMNS,
    _TERMINAL_RUN_STATUSES,
    A2ACorrelation,
    A2UIActionAudit,
    A2UIActionClaim,
    A2UIActionConflict,
    A2UIActionIdentity,
    A2UIActionInvariantError,
    RunFilter,
    RunOutcome,
    RunRecord,
    RunRequestInfo,
    RunSpec,
    Timestamps,
    _aggregate_status,
    _now_iso,
    local_run_spec,
)
from .run_registry_reports import (
    ReportArtifactSources,
    any_degraded,
    canonical_terminal_payload,
    collect_report_artifact_set,
    persist_report_compatibility,
    stored_submission_warnings,
)
from .run_registry_views import RunRegistryViewsMixin, _row_to_record
from .sqlite import sqlite_transaction
from .task_manager import (
    Submission,
    TaskManager,
    _expires_at_for,
    resolve_tasks_db_path,
)
from .task_reconcile import reconcile_task
from .terminal_answer import TerminalAnswerContext, synthesize_terminal_answer
from .terminal_artifacts import (
    ArtifactLister,
    ArtifactObjectLister,
    ManifestLoader,
    collect_terminal_artifacts,
    enumerate_artifact_paths,
)
from .terminal_report import (
    TerminalReportContext,
    assemble_terminal_report,
    is_terminal_report_agent,
)

__all__ = [
    "A2ACorrelation",
    "A2UIActionAudit",
    "A2UIActionClaim",
    "A2UIActionConflict",
    "A2UIActionIdentity",
    "A2UIActionInvariantError",
    "RunFilter",
    "RunRecord",
    "RunRegistry",
    "RunRequestInfo",
    "RunSpec",
    "Timestamps",
    "local_run_spec",
    "purge_run_children",
]


def purge_run_children(
    connection: sqlite3.Connection, run_ids: Sequence[str]
) -> None:
    """Delete DeepGenome children before their owning task and run rows."""
    ids = tuple(run_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    for table in ("deep_genome_remote_tasks", "deep_genome_sections"):
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if exists is None:
            continue
        connection.execute(
            f"DELETE FROM {table} WHERE umbrella_task_id IN ("
            "SELECT task_id FROM tasks WHERE run_id IN ("
            f"{placeholders}))",
            ids,
        )
    connection.execute(
        f"DELETE FROM tasks WHERE run_id IN ({placeholders})", ids
    )
    connection.execute(
        f"DELETE FROM runs WHERE run_id IN ({placeholders})", ids
    )


@dataclass(frozen=True, slots=True)
class _ReservedSubmissionRequest:
    """Validated inputs for one reserved-run submission projection."""

    run_id: str
    owner: str
    agent: str
    submissions: Sequence[Submission]
    result: dict[str, Any]
    now: str


class RunRegistry(RunRegistryViewsMixin):
    """Run-level CRUD + aggregation over the shared task database.

    Shares ``resolve_tasks_db_path`` with ``TaskManager`` so a run row
    and its child tasks are always co-located in one SQLite file.
    """

    def __init__(self, db_path: str | None = None) -> None:
        """Open or create the run registry at ``db_path``.

        Args:
            db_path: Optional override (defaults to the configured
                shared task DB).
        """
        self.db_path = db_path or resolve_tasks_db_path()
        self._init_db()

    if TYPE_CHECKING:
        # Runtime installation below keeps the historical explicit signature
        # while this annotation preserves the public static call seam.
        record_reserved_submissions: Callable[..., bool]

    def _init_db(self) -> None:
        """Create the ``runs`` table and shared indices if missing.

        Eagerly initialises the ``tasks`` table via ``TaskManager`` (its
        constructor is idempotent) so the ``idx_tasks_run`` index can be
        created even when the registry is opened before any task write.
        The request-context columns are added via per-column
        ``ALTER TABLE`` so a database created before they shipped
        catches up without losing the existing rows; a fresh database
        already has them via ``CREATE TABLE`` and the ALTER simply
        raises ``OperationalError`` which is swallowed.
        """
        TaskManager(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_RUNS_DDL)
            for column, column_type in (*_REQUEST_INFO_COLUMNS, *_A2A_COLUMNS):
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(
                        f"ALTER TABLE runs ADD COLUMN {column} {column_type}"
                    )
            conn.execute(_CREATE_RUNS_USER_INDEX)
            conn.execute(_CREATE_TASKS_RUN_INDEX)
            conn.execute(_CREATE_A2A_TASK_INDEX)
            conn.execute(_CREATE_A2UI_ACTIONS_DDL)
            conn.execute(_CREATE_A2UI_OWNER_ACTION_INDEX)
            conn.commit()
        finally:
            conn.close()

    def create_run(
        self,
        spec: RunSpec,
        *,
        outcome: RunOutcome | None = None,
        request_info: RunRequestInfo | None = None,
        a2a: A2ACorrelation | None = None,
    ) -> None:
        """Insert a new run row.

        Idempotent via ``INSERT OR REPLACE`` so a retried chokepoint
        write does not crash on the duplicate run_id key. The expiry is
        set immediately for terminal-on-creation runs (sync agents)
        using the OK / FAIL TTL knobs from ``ApiConfig``.

        Args:
            spec: Identity bundle (run_id, user_id, agent, origin).
            outcome: Initial status + terminal result/error bundle;
                defaults to a fresh ``RunOutcome()`` (status="running",
                no result, no error). Sync agents that finish on
                creation pass an explicit ``RunOutcome(status=...,
                result=...)`` or ``RunOutcome(status=..., error=...)``.
            request_info: Per-request metadata captured at the HTTP
                boundary; ``None`` (default) leaves every column NULL
                so MCP-path runs that never see request context
                continue to write the same five fields as before.
        """
        outcome = outcome or RunOutcome()
        status = outcome.status
        result = outcome.result
        error = outcome.error
        now = _now_iso()
        expires_at = _expires_at_for(status, now)
        info = request_info or RunRequestInfo()
        a2a_info = a2a or A2ACorrelation()
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, user_id, agent, origin, status,
                    result_json, error, created_at, updated_at,
                    expires_at,
                    dialogue_id, request_id, query, tool_name, model,
                    request_json,
                    locale,
                    a2a_task_id, a2a_context_id, a2a_message_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    spec.run_id,
                    spec.user_id,
                    spec.agent,
                    spec.origin,
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    now,
                    now,
                    expires_at,
                    info.dialogue_id,
                    info.request_id,
                    info.query,
                    info.tool_name,
                    info.model,
                    info.request_json,
                    info.locale,
                    a2a_info.task_id,
                    a2a_info.context_id,
                    a2a_info.message_id,
                ),
            )

    def reserve_run(
        self,
        spec: RunSpec,
        *,
        request_info: RunRequestInfo,
        result: dict[str, Any],
    ) -> None:
        """Reserve a fresh running run without replacing an existing row."""
        now = _now_iso()
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, user_id, agent, origin, status,
                    result_json, error, created_at, updated_at,
                    expires_at,
                    dialogue_id, request_id, query, tool_name, model,
                    request_json, locale,
                    a2a_task_id, a2a_context_id, a2a_message_id
                ) VALUES (
                    ?, ?, ?, ?, 'running', ?, NULL, ?, ?, NULL,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    spec.run_id,
                    spec.user_id,
                    spec.agent,
                    spec.origin,
                    json.dumps(result),
                    now,
                    now,
                    request_info.dialogue_id,
                    request_info.request_id,
                    request_info.query,
                    request_info.tool_name,
                    request_info.model,
                    request_info.request_json,
                    request_info.locale,
                    request_info.a2a.task_id,
                    request_info.a2a.context_id,
                    request_info.a2a.message_id,
                ),
            )

    def _record_reserved_submissions(
        self, request: _ReservedSubmissionRequest
    ) -> bool:
        """Atomically attach children and project a running reserved run.

        The owner, canonical agent, and running status are checked while a
        write transaction is held. This prevents a terminal settlement from
        interleaving between validation and child-row persistence.
        """
        with sqlite_transaction(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status FROM runs
                WHERE run_id = ? AND user_id = ? AND agent = ?
                """,
                (request.run_id, request.owner, request.agent),
            ).fetchone()
            if row is None or row[0] != "running":
                return False
            expected_identity = (
                request.run_id,
                request.owner,
                request.agent,
            )
            task_ids: set[str] = set()
            for submission in request.submissions:
                ctx = submission.run_context
                if (
                    ctx is None
                    or (
                        ctx.run_id,
                        ctx.user_id,
                        ctx.agent,
                    )
                    != expected_identity
                ):
                    return False
                if submission.task_id in task_ids:
                    return False
                task_ids.add(submission.task_id)
                existing = conn.execute(
                    """
                    SELECT run_id, user_id, agent FROM tasks
                    WHERE task_id = ?
                    """,
                    (submission.task_id,),
                ).fetchone()
                if existing is not None and existing != expected_identity:
                    return False
            for submission in request.submissions:
                ctx = submission.run_context
                assert ctx is not None
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, status, analysis_id, output_dir,
                        run_id, user_id, agent, origin, created_at,
                        updated_at, input_fingerprint, source_task_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        status = excluded.status,
                        analysis_id = excluded.analysis_id,
                        output_dir = excluded.output_dir,
                        run_id = excluded.run_id,
                        user_id = excluded.user_id,
                        agent = excluded.agent,
                        origin = excluded.origin,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at,
                        input_fingerprint = COALESCE(
                            excluded.input_fingerprint,
                            tasks.input_fingerprint
                        ),
                        source_task_id = COALESCE(
                            excluded.source_task_id,
                            tasks.source_task_id
                        )
                    """,
                    (
                        submission.task_id,
                        submission.status,
                        submission.analysis_id,
                        submission.output_dir,
                        ctx.run_id,
                        ctx.user_id,
                        ctx.agent,
                        ctx.origin,
                        ctx.created_at,
                        ctx.updated_at,
                        submission.input_fingerprint,
                        submission.source_task_id,
                    ),
                )
            cursor = conn.execute(
                """
                UPDATE runs
                SET result_json = ?, updated_at = ?
                WHERE run_id = ? AND user_id = ? AND agent = ?
                  AND status = 'running'
                """,
                (
                    json.dumps(request.result),
                    request.now,
                    request.run_id,
                    request.owner,
                    request.agent,
                ),
            )
            if cursor.rowcount != 1:
                raise sqlite3.OperationalError(
                    "reserved run changed during submission"
                )
            return True

    def update_running_result(
        self,
        run_id: str,
        *,
        owner: str,
        result: dict[str, Any],
    ) -> bool:
        """Update the projection of an owned run only while it is running."""
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs
                SET result_json = ?, updated_at = ?
                WHERE run_id = ? AND user_id = ? AND status = 'running'
                """,
                (json.dumps(result), _now_iso(), run_id, owner),
            )
            return cursor.rowcount == 1

    def fail_running_run(
        self,
        run_id: str,
        *,
        owner: str,
        result: dict[str, Any],
        error: str,
    ) -> bool:
        """Fail an owned run only while it is still running."""
        now = _now_iso()
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs
                SET status = 'failed',
                    result_json = ?,
                    error = ?,
                    updated_at = ?,
                    expires_at = ?
                WHERE run_id = ? AND user_id = ? AND status = 'running'
                """,
                (
                    json.dumps(result),
                    error,
                    now,
                    _expires_at_for("failed", now),
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount == 1

    def update_request_info(
        self,
        run_id: str,
        *,
        owner: str,
        request_info: RunRequestInfo,
    ) -> bool:
        """Backfill request-context columns on an already-created run.

        The HTTP layer calls this after the agent dispatch returns, so
        the chokepoint that minted the run row never has to know about
        request context. Owner-scoped so a foreign-owned row cannot be
        retro-stamped.

        Args:
            run_id: Run id to update.
            owner: Required user id; mismatched owner is a silent miss.
            request_info: Field values to write.

        Returns:
            True when a row was updated; False when no owned row
            matched (caller logs but does not raise).
        """
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs SET
                    dialogue_id = ?,
                    request_id = ?,
                    query = ?,
                    tool_name = ?,
                    model = ?,
                    request_json = ?,
                    locale = ?,
                    updated_at = ?
                WHERE run_id = ? AND user_id = ?
                """,
                (
                    request_info.dialogue_id,
                    request_info.request_id,
                    request_info.query,
                    request_info.tool_name,
                    request_info.model,
                    request_info.request_json,
                    request_info.locale,
                    _now_iso(),
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount > 0

    def settle_run(
        self,
        run_id: str,
        *,
        owner: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Settle an owned run to a terminal status in place.

        Unlike ``create_run``'s INSERT OR REPLACE, this is a targeted
        UPDATE that preserves ``created_at`` (and every request-info
        column), mirroring the ``_settle_terminal`` idiom used by the
        reconcile path. The terminal TTL is stamped the same way.

        Args:
            run_id: Run id to settle.
            owner: Required user id; a foreign-owned row is a no-op.
            status: Terminal status ("succeeded" / "failed").
            result: Optional terminal result payload (JSON-encoded).
            error: Optional terminal error message.

        Returns:
            True when an owned row was updated, False otherwise.
        """
        now = _now_iso()
        expires_at = _expires_at_for(status, now)
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE runs SET
                    status = ?,
                    result_json = ?,
                    error = ?,
                    updated_at = ?,
                    expires_at = ?
                WHERE run_id = ? AND user_id = ?
                """,
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    now,
                    expires_at,
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount > 0

    def list_runs(
        self,
        *,
        owner: str,
        run_filter: RunFilter | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[RunRecord]:
        """Return up to ``limit`` runs owned by ``owner``.

        Filters compose conjunctively. The result is ordered by
        ``created_at`` descending so a newcomer sees their most recent
        submissions first.

        Args:
            owner: Required user id; rows outside the owner are
                invisible.
            run_filter: Optional bundle of equality filters.
            limit: Maximum rows to return.
            offset: Skip this many leading rows (paging).

        Returns:
            Newest-first list of records.
        """
        run_filter = run_filter or RunFilter()
        where, params = _build_list_where(owner, run_filter)
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
                       locale,
                       a2a_task_id, a2a_context_id, a2a_message_id
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

    async def reconcile(
        self,
        run_id: str,
        *,
        owner: str,
        lister: ArtifactLister | None = None,
        object_lister: ArtifactObjectLister | None = None,
        manifest_loader: ManifestLoader | None = None,
    ) -> RunRecord | None:
        """Refresh a non-terminal run by polling its child tasks.

        Terminal cached runs are returned as-is without any task probe;
        this is the "terminal → no re-poll" guard. Non-terminal runs
        call ``reconcile_task`` exactly once per child, aggregate, and
        on a fresh terminal write the result_json / error plus the OK
        or FAIL TTL ``expires_at``.

        Args:
            run_id: Run id to reconcile.
            owner: Owner check (404 isolation preserved).

        Returns:
            Updated ``RunRecord`` or ``None`` (unknown / not-owner).
        """
        current = self.get_run(run_id, owner=owner)
        if current is None or current.status in _NON_POLLABLE_RUN_STATUSES:
            return current
        live: list[dict[str, Any]] = []
        for task_id in current.task_ids:
            live.append(await reconcile_task(task_id))
        new_status = _aggregate_status([row["status"] for row in live])
        if new_status not in _TERMINAL_RUN_STATUSES:
            return self._touch_running(current, new_status)
        if is_terminal_report_agent(current.spec.agent):
            return await self._settle_report_terminal(
                current,
                new_status,
                live,
                sources=ReportArtifactSources(
                    lister=lister,
                    object_lister=object_lister,
                    manifest_loader=manifest_loader,
                ),
            )

        if new_status == "succeeded":
            live = await enumerate_artifact_paths(live, lister=lister)
        artifacts = (
            collect_terminal_artifacts(live)
            if new_status == "succeeded"
            else []
        )
        answer = await synthesize_terminal_answer(
            TerminalAnswerContext(
                agent=current.spec.agent,
                status=new_status,
                live=live,
                artifacts=artifacts,
                query=current.request_info.query,
            )
        )
        legacy_result_payload, error = _terminal_payload(
            new_status,
            live,
            artifacts,
            answer,
            warnings=stored_submission_warnings(current.result),
        )
        return self._settle_terminal(
            current, new_status, legacy_result_payload, error
        )

    async def _settle_report_terminal(
        self,
        current: RunRecord,
        status: str,
        live: list[dict[str, Any]],
        *,
        sources: ReportArtifactSources,
    ) -> RunRecord:
        """Assemble and persist the canonical analyst-class report."""
        artifact_set = await collect_report_artifact_set(
            live,
            lister=sources.lister,
            object_lister=sources.object_lister,
            manifest_loader=sources.manifest_loader,
        )
        report = await assemble_terminal_report(
            context=TerminalReportContext(
                agent=current.spec.agent,
                status=status,
                live=live,
                artifacts=artifact_set.artifacts,
                query=current.request_info.query,
                locale=current.request_info.locale or "en-US",
            ),
            artifacts=artifact_set.artifacts if status == "succeeded" else (),
        )
        persist_report_compatibility(live, report, self.db_path)
        result_payload, error = canonical_terminal_payload(
            status,
            live,
            artifact_set,
            report,
            warnings=stored_submission_warnings(current.result),
        )
        return self._settle_terminal(current, status, result_payload, error)

    def purge_expired(self) -> int:
        """Delete runs whose ``expires_at`` has elapsed and their tasks.

        SQLite has no automatic CASCADE without ``PRAGMA foreign_keys``
        plus a schema migration, so cascade is performed in three
        explicit steps inside a single connection: select expired run
        ids, delete the child tasks rows, then delete the run rows.

        Returns:
            Number of run rows deleted.
        """
        now = _now_iso()
        with sqlite_transaction(self.db_path) as conn:
            expired = [
                row[0]
                for row in conn.execute(
                    "SELECT run_id FROM runs "
                    "WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (now,),
                ).fetchall()
            ]
            if not expired:
                return 0
            purge_run_children(conn, expired)
        return len(expired)

    def _touch_running(self, current: RunRecord, status: str) -> RunRecord:
        """Persist a non-terminal status refresh (updates updated_at)."""
        now = _now_iso()
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status, now, current.spec.run_id),
            )
        return RunRecord(
            spec=current.spec,
            status=status,
            result=current.result,
            error=current.error,
            timestamps=Timestamps(
                created_at=current.timestamps.created_at,
                updated_at=now,
                expires_at=current.timestamps.expires_at,
            ),
            task_ids=current.task_ids,
            request_info=current.request_info,
        )

    def _settle_terminal(
        self,
        current: RunRecord,
        status: str,
        result_payload: dict[str, Any] | None,
        error: str | None,
    ) -> RunRecord:
        """Cache a freshly-terminal run with TTL and result/error."""
        now = _now_iso()
        expires_at = _expires_at_for(status, now)
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                """
                UPDATE runs SET status = ?, result_json = ?, error = ?,
                       updated_at = ?, expires_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    (
                        json.dumps(result_payload)
                        if result_payload is not None
                        else None
                    ),
                    error,
                    now,
                    expires_at,
                    current.spec.run_id,
                ),
            )
        return RunRecord(
            spec=current.spec,
            status=status,
            result=result_payload,
            error=error,
            timestamps=Timestamps(
                created_at=current.timestamps.created_at,
                updated_at=now,
                expires_at=expires_at,
            ),
            task_ids=current.task_ids,
            request_info=current.request_info,
        )


def _reserved_parameter(
    name: str, kind: Any, annotation: object = inspect.Parameter.empty
) -> inspect.Parameter:
    """Build one parameter for the reserved-submission facade signature."""
    return inspect.Parameter(name, kind, annotation=annotation)


_RECORD_RESERVED_SUBMISSIONS_SIGNATURE = inspect.Signature(
    parameters=(
        _reserved_parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        _reserved_parameter(
            "run_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, "str"
        ),
        _reserved_parameter("owner", inspect.Parameter.KEYWORD_ONLY, "str"),
        _reserved_parameter("agent", inspect.Parameter.KEYWORD_ONLY, "str"),
        _reserved_parameter(
            "submissions",
            inspect.Parameter.KEYWORD_ONLY,
            "Sequence[Submission]",
        ),
        _reserved_parameter(
            "result", inspect.Parameter.KEYWORD_ONLY, "dict[str, Any]"
        ),
        _reserved_parameter("now", inspect.Parameter.KEYWORD_ONLY, "str"),
    ),
    return_annotation="bool",
)
_RECORD_RESERVED_SUBMISSIONS_ANNOTATIONS: dict[str, object] = {
    "run_id": "str",
    "owner": "str",
    "agent": "str",
    "submissions": "Sequence[Submission]",
    "result": "dict[str, Any]",
    "now": "str",
    "return": "bool",
}


def _record_reserved_submissions_facade(
    self: RunRegistry, *args: Any, **kwargs: Any
) -> bool:
    """Adapt the historical public call shape to the typed request object."""
    bound = _RECORD_RESERVED_SUBMISSIONS_SIGNATURE.bind(
        self, *args, **kwargs
    )
    request = _ReservedSubmissionRequest(
        run_id=bound.arguments["run_id"],
        owner=bound.arguments["owner"],
        agent=bound.arguments["agent"],
        submissions=bound.arguments["submissions"],
        result=bound.arguments["result"],
        now=bound.arguments["now"],
    )
    implementation = getattr(self, "_record_reserved_submissions")
    return implementation(request)


def _install_record_reserved_submissions_facade() -> None:
    """Install the compatibility facade after ``RunRegistry`` is defined."""
    facade = _record_reserved_submissions_facade
    metadata = (
        ("__signature__", _RECORD_RESERVED_SUBMISSIONS_SIGNATURE),
        ("__annotations__", _RECORD_RESERVED_SUBMISSIONS_ANNOTATIONS),
        ("__name__", "record_reserved_submissions"),
        ("__qualname__", "RunRegistry.record_reserved_submissions"),
        ("__module__", __name__),
        (
            "__doc__",
            getattr(RunRegistry, "_record_reserved_submissions").__doc__,
        ),
    )
    for name, value in metadata:
        setattr(facade, name, value)
    setattr(RunRegistry, "record_reserved_submissions", facade)


_install_record_reserved_submissions_facade()


def _build_list_where(
    owner: str, run_filter: RunFilter
) -> tuple[str, list[Any]]:
    """Return the WHERE fragment + params for ``list_runs``."""
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


def _terminal_payload(
    status: str,
    live: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    answer: str,
    *,
    warnings: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the (result_payload, error) pair for a terminal run.

    Both branches ship the same structured blocks so clients polling
    /v1/runs/{id} get a uniform shape regardless of terminal direction:

    - ``task_results``: the reconciled task rows (the existing field).
    - ``live_status``: the same reconciled rows surfaced under a stable
      "raw live blob" namespace.
    - ``artifacts``: the caller-supplied succeeded-task product index
      (empty on the failed branch); always present so clients can
      iterate it without a key-check.
    - ``formatted.answer``: the caller-synthesized renderable answer,
      always present when the caller provides one so clients see a
      compact display surface alongside the long-form ``final_report``.
    - ``degraded``: True when any reconciled child carries a degraded
      signal (e.g. a DeepGenome report that lost its gene profile); the
      per-task ``degraded_reason`` rides ``task_results``.
    """
    payload: dict[str, Any] = {
        "task_results": live,
        "live_status": live,
        "artifacts": artifacts,
        "final_report": _first_final_report(live),
        "degraded": any_degraded(live),
    }
    if answer:
        payload["formatted"] = {"answer": answer}
    if warnings:
        payload["execution"] = {"warnings": warnings}
    if status == "succeeded":
        return payload, None
    failed = [
        row.get("task_id", "?")
        for row in live
        if (row.get("status") or "").lower() in _FAILURE_STATUSES
    ]
    return payload, f"one or more tasks failed: {', '.join(failed)}"


def _first_final_report(live: list[dict[str, Any]]) -> str | None:
    """Return the first non-empty child ``final_report``, else None.

    DeepGenome's single umbrella child persists the assembled report on
    its reconciled row; other agents leave it absent. Surfacing the
    first non-empty value lets /v1/runs/{id} expose the report at the
    payload top level without the client walking ``task_results``.
    """
    for row in live:
        report = row.get("final_report")
        if isinstance(report, str) and report:
            return report
    return None
