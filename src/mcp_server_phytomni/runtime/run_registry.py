# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Run-scoped registry over the shared task database.

Owns the ``runs`` table in the same SQLite file as ``tasks``. Child
task ids are derived from ``tasks.run_id`` on read.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import TYPE_CHECKING, Any

from ..mcp.formatting.models import ResultDelivery
from .execution_defaults import empty_execution_projection
from .execution_event_producers import (
    emit_remote_artifacts,
    emit_remote_progress,
    emit_run_settlement,
    emit_run_started,
)
from .execution_store_support_v2 import validate_provider_join_lease_token
from .live_tasks import (
    is_live_running,
    register_live_task,
)
from .research_input_store_support import queue_grants
from .run_registry_delivery import (
    DeliveryFailure,
    DeliveryRevision,
    ResultDeliveryDependencies,
    RunningResultWrite,
    begin_delivery_reconcile,
    begin_delivery_retry,
    default_result_delivery_dependencies,
    delivery_attempts_exhausted,
    delivery_task_key,
    replace_running_result,
    run_delivery_worker,
    settle_delivery_failure,
)
from .run_registry_models import (
    _NON_POLLABLE_RUN_STATUSES,
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
from .run_registry_protocols import (
    _RECONCILE_SIGNATURE,
    _SETTLE_RUN_SIGNATURE,
    _ReconcileRequest,
    _ReservedSubmissionRequest,
    install_record_reserved_submissions_facade,
)
from .run_registry_reports import (
    ReportArtifactSources,
    _ReportSettlementRequest,
    annotate_live_with_stored_tasks,
    attach_partial_child_degraded,
    mark_partial_child_failure,
    settle_report_terminal,
    stored_submission_warnings,
    touch_running_run,
)
from .run_registry_reports import legacy_terminal_payload as _terminal_payload
from .run_registry_support import (
    bind_settle_run_request as _bind_settle_run_request,
)
from .run_registry_support import (
    expires_at_for as _expires_at_for,
)
from .run_registry_support import (
    initialize_run_registry,
    purge_run_children,
)
from .run_registry_support import (
    pending_delivery as _pending_delivery,
)
from .run_registry_support import (
    private_delivery as _private_delivery,
)
from .run_registry_views import RunRegistryViewsMixin
from .sqlite import sqlite_transaction
from .task_manager import (
    resolve_tasks_db_path,
)
from .task_reconcile import reconcile_task
from .terminal_answer import TerminalAnswerContext, synthesize_terminal_answer
from .terminal_artifacts import (
    collect_terminal_artifacts,
    enumerate_artifact_paths,
)
from .terminal_report import assemble_terminal_report, is_terminal_report_agent

_ZERO_OWNED_CHILD_SQL = (
    " AND NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.run_id = runs.run_id "
    "AND tasks.user_id = runs.user_id AND tasks.agent = runs.agent)"
)
_RESEARCH_STAGE_RANK = {
    "input_resolution": 0,
    "planning": 1,
    "execution": 2,
    "report_assembly": 3,
}


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


if TYPE_CHECKING:
    from .run_registry_protocols import RecordReservedSubmissionsCallable


class RunRegistry(RunRegistryViewsMixin):
    """Run-level CRUD and aggregation over the shared task database.
    ``resolve_tasks_db_path`` keeps run rows and child tasks co-located.
    """

    def __init__(
        self,
        db_path: str | None = None,
        *,
        delivery_dependencies: ResultDeliveryDependencies | None = None,
        expected_provider_join_lease_token: str | None = None,
    ) -> None:
        """Open or create the run registry at ``db_path``.

        Args:
            db_path: Optional override (defaults to the configured
                shared task DB).
        """
        self.db_path = db_path or resolve_tasks_db_path()
        self._delivery_dependencies = (
            delivery_dependencies or default_result_delivery_dependencies()
        )
        self._expected_provider_join_lease_token = (
            validate_provider_join_lease_token(
                expected_provider_join_lease_token
            )
        )
        initialize_run_registry(self.db_path)

    if TYPE_CHECKING:
        # Runtime installation below keeps the historical explicit signature
        # while this annotation preserves the public static call seam.
        record_reserved_submissions: RecordReservedSubmissionsCallable

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
                    locale, external_execution_id,
                    a2a_task_id, a2a_context_id, a2a_message_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    info.execution_id,
                    a2a_info.task_id,
                    a2a_info.context_id,
                    a2a_info.message_id,
                ),
            )
        if status in _TERMINAL_RUN_STATUSES:
            emit_run_started(
                self.db_path,
                run_id=spec.run_id,
                owner=spec.user_id,
            )
            emit_run_settlement(
                self.db_path,
                run_id=spec.run_id,
                owner=spec.user_id,
                status=status,
                revision=0,
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
                    request_json, locale, external_execution_id,
                    a2a_task_id, a2a_context_id, a2a_message_id
                ) VALUES (
                    ?, ?, ?, ?, 'running', ?, NULL, ?, ?, NULL,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    request_info.execution_id,
                    request_info.a2a.task_id,
                    request_info.a2a.context_id,
                    request_info.a2a.message_id,
                ),
            )
        emit_run_started(
            self.db_path,
            run_id=spec.run_id,
            owner=spec.user_id,
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
            expected_identity = (request.run_id, request.owner, request.agent)
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
                if existing not in (
                    None,
                    expected_identity,
                    (None, None, None),
                ):
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
            return replace_running_result(
                conn,
                RunningResultWrite(
                    run_id=run_id,
                    owner=owner,
                    result=result,
                    expected_provider_join_lease_token=(
                        self._expected_provider_join_lease_token
                    ),
                ),
            )

    def update_active_result(
        self,
        run_id: str,
        *,
        owner: str,
        result: dict[str, Any],
    ) -> bool:
        """Update a Runtime-owned non-terminal result projection only."""
        with sqlite_transaction(self.db_path) as conn:
            return replace_running_result(
                conn,
                RunningResultWrite(
                    run_id=run_id,
                    owner=owner,
                    result=result,
                    statuses=(
                        "running",
                        "input_required",
                        "waiting_input",
                    ),
                ),
            )

    def transition_research_stage(
        self, current: RunRecord, stage: str
    ) -> RunRecord | None:
        """Advance a Research stage with owner and revision CAS."""
        if current.spec.agent != "research" or stage not in {
            "input_resolution",
            "planning",
            "execution",
            "report_assembly",
        }:
            raise ValueError("unsupported Research lifecycle stage")
        if (
            current.stage is not None
            and current.stage not in _RESEARCH_STAGE_RANK
        ):
            return None
        if _RESEARCH_STAGE_RANK[stage] <= _RESEARCH_STAGE_RANK.get(
            current.stage or "", -1
        ):
            return None
        now = _now_iso()
        fence_clause, fence_parameters = self._provider_join_fence()
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE runs SET stage = ?, revision = revision + 1, "
                "updated_at = ? WHERE run_id = ? AND user_id = ? "
                "AND status = 'running' AND revision = ?" + fence_clause,
                (
                    stage,
                    now,
                    current.spec.run_id,
                    current.spec.user_id,
                    current.revision,
                    *fence_parameters,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_run(current.spec.run_id, owner=current.spec.user_id)

    def fail_running_run(
        self,
        run_id: str,
        *,
        owner: str,
        result: dict[str, Any],
        error: str,
        **kwargs: Any,
    ) -> bool:
        """Fail an owned run only while it is still running."""
        expected_revision = kwargs.pop("expected_revision", None)
        if kwargs:
            raise TypeError("unexpected fail_running_run keyword")
        if expected_revision is None:
            return False
        now = _now_iso()
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE runs SET status = 'failed', result_json = ?, "
                "error = ?, stage = NULL, revision = revision + 1, "
                "updated_at = ?, expires_at = ? "
                "WHERE run_id = ? AND user_id = ? AND status = 'running' "
                "AND revision = ?",
                (
                    json.dumps(result),
                    error,
                    now,
                    _expires_at_for("failed", now),
                    run_id,
                    owner,
                    expected_revision,
                ),
            )
            queue_grants(conn, run_id, now, cursor.rowcount)
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
                    external_execution_id = ?,
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
                    request_info.execution_id,
                    _now_iso(),
                    run_id,
                    owner,
                ),
            )
            return cursor.rowcount > 0

    def settle_run(
        self,
        *args: Any,
        **kwargs: Any,
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
        request = _bind_settle_run_request(self, *args, **kwargs)
        if request.expected_revision is None:
            return False
        now = _now_iso()
        expires_at = _expires_at_for(request.status, now)
        status_where = (
            "status = 'running' OR (status = 'input_required' "
            "AND agent IN ('chat', 'review'))"
            if request.status in _TERMINAL_RUN_STATUSES
            else "status IN ('running', 'input_required')"
        )
        parameters: tuple[Any, ...] = (
            request.status,
            (
                json.dumps(request.result)
                if request.result is not None
                else None
            ),
            request.error,
            now,
            expires_at,
            request.run_id,
            request.owner,
            request.expected_revision,
        )
        with sqlite_transaction(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE runs SET status = ?, result_json = ?, error = ?, "
                "stage = NULL, revision = revision + 1, "
                "updated_at = ?, expires_at = ? WHERE run_id = ? "
                "AND user_id = ? AND (" + status_where + ") "
                "AND revision = ?",
                parameters,
            )
            queue_grants(conn, request.run_id, now, cursor.rowcount)
            changed = cursor.rowcount > 0
        if changed:
            emit_run_settlement(
                self.db_path,
                run_id=request.run_id,
                owner=request.owner,
                status=request.status,
                revision=request.expected_revision + 1,
            )
        return changed

    setattr(settle_run, "__signature__", _SETTLE_RUN_SIGNATURE)

    def begin_delivery_retry(self, run_id: str, *, owner: str) -> bool:
        """Transition an owned retryable delivery failure to a new revision."""
        return begin_delivery_retry(self, run_id, owner=owner)

    def begin_delivery_reconcile(self, run_id: str, *, owner: str) -> bool:
        """Reopen a legacy archive failure for child-only reconciliation."""
        return begin_delivery_reconcile(self, run_id, owner=owner)

    def _schedule_delivery(
        self, current: RunRecord, delivery: ResultDelivery
    ) -> None:
        """Launch one process-local worker unless that revision is live."""
        target = DeliveryRevision(
            current.spec.run_id,
            current.spec.user_id,
            delivery.revision,
            delivery.inventory_digest,
        )
        key = delivery_task_key(target.run_id, target.revision)
        if is_live_running(key):
            return
        task = asyncio.create_task(
            run_delivery_worker(
                self,
                target,
                self._delivery_dependencies,
                key,
            )
        )
        register_live_task(key, task)

    def _reconcile_pending_delivery(
        self, current: RunRecord
    ) -> tuple[bool, RunRecord | None]:
        """Resume or exhaust a pending archive-delivery revision."""
        delivery = _pending_delivery(current.result)
        if delivery is None:
            return False, None
        private = _private_delivery(current.result)
        if private is None or current.status != "running":
            return False, None
        key = delivery_task_key(current.spec.run_id, delivery.revision)
        if not is_live_running(key):
            target = DeliveryRevision(
                current.spec.run_id,
                current.spec.user_id,
                delivery.revision,
                delivery.inventory_digest,
            )
            if delivery_attempts_exhausted(private.attempts_claimed):
                settle_delivery_failure(
                    self,
                    target,
                    DeliveryFailure(
                        private.last_error_code or "archive_publish_failed",
                        True,
                    ),
                )
            else:
                self._schedule_delivery(current, delivery)
        return True, self.get_run(
            current.spec.run_id, owner=current.spec.user_id
        )

    async def _reconcile_children(
        self, current: RunRecord, request: _ReconcileRequest
    ) -> RunRecord | None:
        """Poll children once and settle the resulting aggregate status."""
        live = [await reconcile_task(task_id) for task_id in current.task_ids]
        live = annotate_live_with_stored_tasks(live, current.result)
        emit_remote_progress(
            self.db_path,
            run_id=current.spec.run_id,
            owner=current.spec.user_id,
            revision=current.revision,
            task_rows=live,
        )
        new_status = _aggregate_status([row["status"] for row in live])
        if new_status not in _TERMINAL_RUN_STATUSES:
            return touch_running_run(self, current, new_status, live)
        current, live, partial = mark_partial_child_failure(
            current, live, new_status
        )
        if is_terminal_report_agent(current.spec.agent):
            return await settle_report_terminal(
                _ReportSettlementRequest(
                    registry=self,
                    current=current,
                    status=new_status,
                    live=live,
                    sources=ReportArtifactSources(
                        lister=request.lister,
                        object_lister=request.object_lister,
                        manifest_loader=request.manifest_loader,
                    ),
                    assembler=assemble_terminal_report,
                ),
            )
        if new_status == "succeeded":
            live = await enumerate_artifact_paths(live, lister=request.lister)
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
        result_payload, error = _terminal_payload(
            new_status,
            live,
            artifacts,
            answer,
            warnings=stored_submission_warnings(current.result),
        )
        if partial:
            result_payload = attach_partial_child_degraded(result_payload)
        return self._settle_terminal(
            current, RunOutcome(new_status, result_payload, error)
        )

    async def reconcile(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> RunRecord | None:
        """Refresh a non-terminal run by polling its child tasks.

        Terminal cached runs return without probes. Other runs reconcile each
        child once; fresh terminal writes cache result/error with the
        status TTL.

        Args:
            run_id: Run id to reconcile.
            owner: Owner check (404 isolation preserved).

        Returns:
            Updated ``RunRecord`` or ``None`` (unknown / not-owner).
        """
        bound = _RECONCILE_SIGNATURE.bind(self, *args, **kwargs)
        bound.apply_defaults()
        request = _ReconcileRequest(
            run_id=bound.arguments["run_id"],
            owner=bound.arguments["owner"],
            lister=bound.arguments["lister"],
            object_lister=bound.arguments["object_lister"],
            manifest_loader=bound.arguments["manifest_loader"],
        )
        current = self.get_run(request.run_id, owner=request.owner)
        if current is None:
            return current
        handled, resumed = self._reconcile_pending_delivery(current)
        if handled:
            return resumed
        if current.status in _NON_POLLABLE_RUN_STATUSES:
            return current
        return await self._reconcile_children(current, request)

    setattr(reconcile, "__signature__", _RECONCILE_SIGNATURE)

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

    def _touch_running(
        self, current: RunRecord, status: str
    ) -> RunRecord | None:
        """Refresh only the still-running owner row, then return its winner."""
        now = _now_iso()
        fence_clause, fence_parameters = self._provider_join_fence()
        with sqlite_transaction(self.db_path) as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? "
                "WHERE run_id = ? AND user_id = ? AND status = 'running'"
                + fence_clause,
                (
                    status,
                    now,
                    current.spec.run_id,
                    current.spec.user_id,
                    *fence_parameters,
                ),
            )
        return self.get_run(current.spec.run_id, owner=current.spec.user_id)

    def _settle_orphaned_run(self, current: RunRecord) -> RunRecord | None:
        """Fail only an owned running row that still has no owned child."""
        return self._settle_terminal(
            current,
            RunOutcome(
                "failed",
                empty_execution_projection(degraded=True),
                "background_submission_worker_lost",
            ),
            require_zero_child=True,
        )

    def _settle_terminal(
        self,
        current: RunRecord,
        outcome: RunOutcome,
        *,
        require_zero_child: bool = False,
    ) -> RunRecord | None:
        """Cache a freshly-terminal run with TTL and result/error."""
        now = _now_iso()
        expires_at = _expires_at_for(outcome.status, now)
        fence_clause, fence_parameters = self._provider_join_fence()
        query = (
            "UPDATE runs SET status = ?, result_json = ?, error = ?, "
            "stage = NULL, revision = revision + 1, updated_at = ?, "
            "expires_at = ? WHERE run_id = ? AND user_id = ? "
            "AND status = 'running' AND revision = ?"
            + fence_clause
            + (_ZERO_OWNED_CHILD_SQL if require_zero_child else "")
        )
        with sqlite_transaction(self.db_path) as conn:
            changed = conn.execute(
                query,
                (
                    outcome.status,
                    (
                        json.dumps(outcome.result)
                        if outcome.result is not None
                        else None
                    ),
                    outcome.error,
                    now,
                    expires_at,
                    current.spec.run_id,
                    current.spec.user_id,
                    current.revision,
                    *fence_parameters,
                ),
            )
            queue_grants(conn, current.spec.run_id, now, changed.rowcount)
            did_change = changed.rowcount > 0
        settled = self.get_run(current.spec.run_id, owner=current.spec.user_id)
        if did_change:
            artifacts = (
                outcome.result.get("artifacts", [])
                if isinstance(outcome.result, dict)
                else []
            )
            if isinstance(artifacts, list):
                emit_remote_artifacts(
                    self.db_path,
                    run_id=current.spec.run_id,
                    owner=current.spec.user_id,
                    revision=current.revision + 1,
                    artifacts=artifacts,
                )
            emit_run_settlement(
                self.db_path,
                run_id=current.spec.run_id,
                owner=current.spec.user_id,
                status=outcome.status,
                revision=current.revision + 1,
            )
        return settled

    def _provider_join_fence(self) -> tuple[str, tuple[str, ...]]:
        """Return the optional domain-CAS clause for one join attempt."""
        token = self._expected_provider_join_lease_token
        if token is None:
            return "", ()
        return (
            " AND execution_provider_join_lease_owner = ? "
            "AND execution_provider_join_lease_expires_at > ?",
            (token, _now_iso()),
        )


install_record_reserved_submissions_facade(RunRegistry)
