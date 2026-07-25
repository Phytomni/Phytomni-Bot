# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Submit-handler recorder: persist run + task rows for remote agents.

Owns the chokepoint that turns a successful submit-style handler return
into one ``runs`` row plus N child ``tasks`` rows in the local SQLite
registry, then binds the freshly-minted ``run_id`` to the request
contextvar so the HTTP layer can echo it back. Extracted from
``mcp/handlers.py`` so the dispatcher stays a thin schema-validation
shell rather than carrying registry-write logic alongside it.
"""

import functools
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from ..storage.path_policy import IdFactory
from .request_context import (
    bind_accepted_task_ids,
    bind_recorder_degraded,
    bind_run_id,
    current_pre_recorded_task_id,
    current_request_user,
    current_run_id,
)
from .run_registry import RunOutcome, RunRegistry, RunSpec
from .submission_outcome import project_submission_warnings
from .task_manager import (
    RunContext,
    Submission,
    TaskManager,
    resolve_tasks_db_path,
)

logger = logging.getLogger(__name__)

__all__ = [
    "extract_task_submissions",
    "record_submitted_task",
    "records_submission",
]

SubmissionTuple = tuple[str, str, str | None, str | None]
SubmissionExtractor = Callable[
    [Mapping[str, Any]], tuple[SubmissionTuple, ...]
]


def _extract_single_submission(
    result: Mapping[str, Any],
) -> tuple[SubmissionTuple, ...]:
    """Extract one top-level analyst-style task identity."""
    task_id = result.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return ()
    fingerprint = result.get("input_fingerprint")
    source_task_id = result.get("source_task_id")
    return (
        (
            task_id,
            str(result.get("output_dir") or ""),
            fingerprint if isinstance(fingerprint, str) else None,
            source_task_id if isinstance(source_task_id, str) else None,
        ),
    )


def _extract_research_submissions(
    result: Mapping[str, Any],
) -> tuple[SubmissionTuple, ...]:
    """Extract the canonical or legacy research task-id collection."""
    task_values = result.get("task_ids")
    if isinstance(task_values, Mapping):
        task_values = task_values.values()
    elif not isinstance(task_values, list):
        task_values = ()
    output_dir = str(result.get("output_dir") or "")
    return tuple(
        (value, output_dir, None, None)
        for value in task_values
        if isinstance(value, str) and value
    )


def _extract_network_submission(
    result: Mapping[str, Any],
) -> tuple[SubmissionTuple, ...]:
    """Extract the nested network task identity."""
    nested = result.get("network_task")
    if not isinstance(nested, Mapping):
        return ()
    task_id = nested.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return ()
    return ((task_id, str(nested.get("output_dir") or ""), None, None),)


def _extract_design_submissions(
    result: Mapping[str, Any],
) -> tuple[SubmissionTuple, ...]:
    """Extract one task identity per design result entry."""
    design_results = result.get("design_task_result")
    if not isinstance(design_results, list):
        return ()
    pairs: list[SubmissionTuple] = []
    for nested in design_results:
        if not isinstance(nested, Mapping):
            continue
        task_id = nested.get("task_id")
        if isinstance(task_id, str) and task_id:
            pairs.append(
                (task_id, str(nested.get("output_dir") or ""), None, None)
            )
    return tuple(pairs)


_SUBMISSION_EXTRACTORS: dict[str, SubmissionExtractor] = {
    "analyst": _extract_single_submission,
    "deep_genome": _extract_single_submission,
    "research": _extract_research_submissions,
    "network": _extract_network_submission,
    "design": _extract_design_submissions,
}


def extract_task_submissions(
    result: Mapping[str, Any], agent: str
) -> tuple[tuple[str, str, str | None, str | None], ...]:
    """Extract per-task identity tuples by per-agent wrapper shape.

    Each public submit wrapper returns task identity in its own shape,
    so the chokepoint dispatches by agent slug rather than guessing:

    - ``analyst`` / ``deep_genome``: ``task_id`` at the top level (with
      ``output_dir`` alongside). Analyst additionally carries
      ``input_fingerprint`` for the duplicate-submission dedup contract
      and, on a content-addressed dedup reuse, a ``source_task_id``
      pointing at the prior tenant's remote task id; other agents leave
      both slots ``None``.
    - ``research``: a canonical ``task_ids`` list, with a legacy dict
      mapping research-goal names to task ids also accepted; the top-level
      ``output_dir`` is shared across children.
    - ``network``: nested under ``network_task`` (``task_id`` +
      ``output_dir`` inside).
    - ``design``: a ``design_task_result`` list of AnalystAgent
      submission dicts (one per design kind: protein / promoter /
      terminator), each with its own ``task_id`` and ``output_dir``.

    Args:
        result: Raw wrapper result dict (pre-formatter).
        agent: Public agent alias (e.g. ``"analyst"``).

    Returns:
        Tuple of ``(task_id, output_dir, input_fingerprint,
        source_task_id)`` tuples; empty when nothing recognizable is
        present so the caller skips writing. ``input_fingerprint`` and
        ``source_task_id`` are ``None`` for agents / submissions that do
        not participate in the dedup contract.
    """
    extractor = _SUBMISSION_EXTRACTORS.get(agent)
    return extractor(result) if extractor is not None else ()


def _initial_submission_result(
    result: Mapping[str, Any],
    submissions: tuple[SubmissionTuple, ...],
) -> dict[str, Any]:
    """Build the in-flight result envelope seeded before child writes."""
    task_rows = [
        {
            "task_id": task_id,
            "status": "submitted",
            "output_dir": output_dir,
        }
        for task_id, output_dir, _fingerprint, _source in submissions
    ]
    initial_result: dict[str, Any] = {
        "task_results": task_rows,
        "live_status": task_rows,
        "artifacts": [],
    }
    warnings = project_submission_warnings(result.get("submission_warnings"))
    if warnings:
        initial_result["execution"] = {"warnings": warnings}
    return initial_result


def record_submitted_task(result: Any, *, agent: str) -> None:
    """Persist submitted tasks plus their owning run row.

    Mints a fresh ``run_id`` via ``IdFactory().new_id("run", agent)``,
    writes one ``runs`` row (``origin="remote"``, ``status="running"``)
    via ``RunRegistry.create_run``, then writes one child task row per
    extracted task id — all sharing the same ``run_id`` so
    ``RunRegistry.reconcile`` can join them by ``tasks.run_id``.
    Best-effort: a registry / SQLite / OS error must never break an
    already-successful remote submission. On such a failure the
    chokepoint (1) records the full traceback via
    ``logger.exception`` so operators can diagnose the persistence
    issue from logs, and (2) sets the ``recorder_degraded`` request
    contextvar so the HTTP layer can surface the degraded-tracking
    state to the client. Accepted upstream task ids are bound before
    local persistence so the HTTP layer can still return real work
    identities when that write fails.

    The chokepoint binds the freshly-minted ``run_id`` to the request
    contextvar **only after** every child task row has been written,
    so a half-failed record never surfaces a run id without its task
    ids. The companion ``current_recorder_degraded`` flag marks this as
    a persistence failure while ``current_accepted_task_ids`` preserves
    the accepted upstream identities.

    The ``if result.get("dedup_hit") is True: return`` guard below is
    a defensive early-exit: no production wrapper currently sets this
    sentinel (analyst reuse now mints a caller-owned row and records it
    normally), but the guard is preserved so any future wrapper that
    does signal ``dedup_hit`` is handled safely without accidentally
    overwriting a prior run's registry rows.

    The MCP tool's return dict is *not* mutated (no ``run_id`` is
    surfaced to the client) so the existing stdio MCP contract stays
    byte-equivalent; the HTTP API path reads ``tasks.run_id`` back
    when it needs the run identity.

    Args:
        result: The wrapper result returned by a submit-style handler.
        agent: Public agent alias (e.g. ``"analyst"``) recorded on the
            run and task rows.
    """
    if not isinstance(result, dict):
        return
    if result.get("dedup_hit") is True:
        return
    submissions = extract_task_submissions(result, agent)
    if not submissions:
        return
    accepted_task_ids = tuple(task_id for task_id, *_rest in submissions)
    bind_accepted_task_ids(accepted_task_ids)
    if (
        agent == "deep_genome"
        and current_run_id() is not None
        and current_pre_recorded_task_id() is not None
        and len(submissions) == 1
        and submissions[0][0] == current_pre_recorded_task_id()
    ):
        return
    user_id = current_request_user() or "anonymous"
    run_id = IdFactory().new_id("run", agent)
    now = datetime.now(UTC).isoformat()
    db_path = resolve_tasks_db_path()
    # Seed the run row with the same envelope shape ``_terminal_payload``
    # writes later so a client polling ``GET /v1/runs/{id}`` while the
    # run is still in flight sees ``task_results`` / ``live_status`` /
    # ``artifacts`` keyed exactly as on the terminal branch, just with
    # placeholder ``submitted`` rows and an empty artifacts list.
    initial_result = _initial_submission_result(result, submissions)
    try:
        RunRegistry(db_path).create_run(
            RunSpec(
                run_id=run_id,
                user_id=user_id,
                agent=agent,
                origin="remote",
            ),
            outcome=RunOutcome(result=initial_result),
        )
        manager = TaskManager(db_path)
        for (
            task_id,
            output_dir,
            input_fingerprint,
            source_task_id,
        ) in submissions:
            manager.record(
                Submission(
                    task_id=task_id,
                    status="submitted",
                    output_dir=output_dir,
                    run_context=RunContext(
                        run_id=run_id,
                        user_id=user_id,
                        agent=agent,
                        origin="remote",
                        created_at=now,
                        updated_at=now,
                    ),
                    input_fingerprint=input_fingerprint,
                    source_task_id=source_task_id,
                )
            )
        bind_run_id(run_id)
    except (sqlite3.Error, OSError):
        logger.exception(
            "Failed to persist remote submission to local registry "
            "(agent=%s, task_count=%d); the remote tasks are live but "
            "GET /v1/runs/{run_id} will return 404 until the registry "
            "write succeeds on a later attempt.",
            agent,
            len(submissions),
        )
        bind_recorder_degraded(True)
        return


def records_submission(
    agent: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator factory: log a submit handler's run + task on return.

    The handler runs unchanged; its result is forwarded verbatim and
    also recorded in the unified run+task registry. ``functools.wraps``
    preserves the handler name so the ``TOOL_HANDLERS`` mapping in
    ``mcp/app.py`` is unaffected, and the static ``agent`` slug avoids
    name-introspection at call time.

    Args:
        agent: Public agent alias (e.g. ``"analyst"``) recorded on the
            run and task rows.

    Returns:
        Decorator that wraps an async submit handler.
    """

    def decorator(
        handler: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        """Wrap one async submit handler with the recorder hook."""

        @functools.wraps(handler)
        async def _wrapper(args: Any) -> Any:
            """Await the handler, record the run + task, return the result.

            Args:
                args: The validated tool-argument model.

            Returns:
                The handler's result, unchanged.
            """
            result = await handler(args)
            record_submitted_task(result, agent=agent)
            return result

        return _wrapper

    return decorator
