# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Owner-scoped A2A task registration and resume runtime.

The FastAPI application owns route decorators and request parsing.  This
module owns the A2A lifecycle after an SDK request has been mapped: registry
correlation, task projection, resume-payload validation, graph resumption,
and terminal or input-required settlement.  Dependencies are supplied by the
application so this module never imports ``api.app`` or registers routes.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...mcp.result_formatting import strip_agent_result
from ...runtime.resume import NoCheckpointError, detect_interrupt
from ...runtime.run_registry import (
    RunOutcome,
    RunRecord,
    RunRegistry,
    RunSpec,
)
from .. import run_lifecycle
from .executor import A2ARegistration, task_from_run_record

_LOGGER = logging.getLogger(__name__)

type RegistryFactory = Callable[[str], RunRegistry]
type CurrentUser = Callable[[], str | None]
type DbPath = Callable[[], str]
type GraphFactory = Callable[[], Any]
type ResumeGraph = Callable[
    [Any, str, Mapping[str, Any]], Awaitable[dict[str, Any]]
]
type ResumePayloadMapper = Callable[[str, Mapping[str, Any]], dict[str, Any]]
type InterruptResult = Callable[[Mapping[str, Any]], dict[str, Any]]
type InterruptProjector = Callable[[Mapping[str, Any]], dict[str, Any]]
type ChatInterruptBody = Callable[..., dict[str, Any]]
type ReviewInterruptBody = Callable[..., dict[str, Any]]
type ChatFormatter = Callable[..., dict[str, Any]]
type ReviewFormatter = Callable[[Mapping[str, Any]], dict[str, Any]]

__all__ = [
    "A2ARegistryDependencies",
    "A2AGraphDependencies",
    "A2AInterruptDependencies",
    "A2AProjectionDependencies",
    "A2AResumeDependencies",
    "A2ARuntimeDependencies",
    "get_task",
    "record_registration",
    "resume_payload",
    "resume_task",
]


@dataclass(frozen=True, slots=True)
class A2ARegistryDependencies:
    """Registry and request-context seams for A2A task projections."""

    registry_factory: RegistryFactory
    current_user: CurrentUser
    tasks_db_path: DbPath


@dataclass(frozen=True, slots=True)
class A2AGraphDependencies:
    """Graph factories and resume-kernel seam for A2A."""

    chat_graph: GraphFactory
    review_graph: GraphFactory
    resume_graph: ResumeGraph


@dataclass(frozen=True, slots=True)
class A2AInterruptDependencies:
    """Interrupt result, projection, and response-body seams."""

    chat_interrupt_result: InterruptResult
    review_interrupt_result: InterruptResult
    project_review_interrupt: InterruptProjector
    chat_interrupt_body: ChatInterruptBody
    review_interrupt_body: ReviewInterruptBody


@dataclass(frozen=True, slots=True)
class A2AProjectionDependencies:
    """Payload and terminal-result projection seams."""

    resume_payload: ResumePayloadMapper
    interrupts: A2AInterruptDependencies
    format_chat_result: ChatFormatter
    format_review_result: ReviewFormatter


@dataclass(frozen=True, slots=True)
class A2AResumeDependencies:
    """Registry, graph, and response-projection seams for A2A resume."""

    registry: A2ARegistryDependencies
    graphs: A2AGraphDependencies
    projection: A2AProjectionDependencies


@dataclass(frozen=True, slots=True)
class A2ARuntimeDependencies:
    """All application seams used by the A2A task runtime."""

    registry: A2ARegistryDependencies
    resume: A2AResumeDependencies


def record_registration(
    registration: A2ARegistration,
    *,
    dependencies: A2ARegistryDependencies,
) -> None:
    """Persist A2A ids against an existing or newly-streaming run.

    Blocking A2A calls already have a run row from ``_invoke_agent_run``;
    streaming calls use the A2A task id as their run id and need a small
    running row before the first SSE event.  Both paths converge on the same
    owner-scoped registry and remain best-effort like the other API
    bookkeeping helpers.
    """
    owner = dependencies.current_user() or "anonymous"
    registry = dependencies.registry_factory(dependencies.tasks_db_path())
    try:
        updated = registry.update_a2a_correlation(
            registration.run_id,
            owner=owner,
            correlation=registration.correlation,
        )
        if updated:
            return
        registry.create_run(
            RunSpec(
                run_id=registration.run_id,
                user_id=owner,
                agent=registration.agent,
                origin="local",
            ),
            outcome=RunOutcome(status="running"),
            request_info=registration.request_info,
            a2a=registration.correlation,
        )
    except (sqlite3.Error, OSError) as exc:
        _LOGGER.warning(
            "A2A run correlation write failed for %s: %s",
            registration.run_id,
            exc.__class__.__name__,
        )


def get_task(
    task_id: str,
    history_length: int,
    *,
    dependencies: A2ARegistryDependencies,
) -> Any:
    """Return an owner-scoped A2A task projection, or ``None``."""
    owner = dependencies.current_user() or "anonymous"
    record = dependencies.registry_factory(
        dependencies.tasks_db_path()
    ).get_run_by_a2a_task(task_id, owner=owner)
    if record is None:
        return None
    return task_from_run_record(record, history_length)


def resume_payload(
    agent: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate A2A input data into the shared resume payload."""
    if agent == "review":
        approved = arguments.get("approved")
        if not isinstance(approved, bool):
            raise ValueError("A2A review resume requires boolean approved")
        return {
            "approved": approved,
            "edits": arguments.get("edits"),
        }
    if agent != "chat":
        raise ValueError("A2A resume is supported only for chat and review")
    if arguments.get("cancelled") is True:
        return {"widget": arguments.get("widget"), "cancelled": True}
    fields = arguments.get("fields")
    if isinstance(fields, Mapping):
        return {
            "widget": "form",
            "fields": dict(fields),
        }
    if arguments.get("selected") is not None:
        return {
            "widget": "choice",
            "selected": arguments["selected"],
        }
    approved = arguments.get("approved", arguments.get("accepted"))
    if not isinstance(approved, bool):
        raise ValueError("A2A chat resume requires boolean approved")
    return {"widget": "confirm", "accepted": approved}


@dataclass(frozen=True, slots=True)
class _ResumeContext:
    """Validated owner-scoped state for one A2A resume request."""

    owner: str
    record: RunRecord
    generation: int
    resume_payload: dict[str, Any]
    registry: RunRegistry


def _resume_context(
    task_id: str,
    context_id: str,
    arguments: Mapping[str, Any],
    *,
    dependencies: A2AResumeDependencies,
) -> _ResumeContext | None:
    """Validate ownership, context, status, generation, and payload."""
    owner = dependencies.registry.current_user() or "anonymous"
    registry = dependencies.registry.registry_factory(
        dependencies.registry.tasks_db_path()
    )
    record = registry.get_run_by_a2a_task(task_id, owner=owner)
    if record is None:
        return None
    if context_id != (record.a2a.context_id or ""):
        raise ValueError("A2A context_id does not match the task")
    if record.status != "input_required":
        raise ValueError("A2A task is not awaiting input")
    stored = record.result or {}
    generation = stored.get("generation", 0)
    if not isinstance(generation, int) or isinstance(generation, bool):
        generation = 0
    supplied_generation = arguments.get("generation")
    if isinstance(supplied_generation, bool) or not isinstance(
        supplied_generation, int | float
    ):
        raise ValueError("A2A generation mismatch or expired input")
    if (
        isinstance(supplied_generation, float)
        and not supplied_generation.is_integer()
    ):
        raise ValueError("A2A generation mismatch or expired input")
    if int(supplied_generation) != generation:
        raise ValueError("A2A generation mismatch or expired input")
    return _ResumeContext(
        owner=owner,
        record=record,
        generation=generation,
        resume_payload=dependencies.projection.resume_payload(
            record.spec.agent, arguments
        ),
        registry=registry,
    )


def _settle_interrupt(
    context: _ResumeContext,
    interrupt: Mapping[str, Any],
    *,
    dependencies: A2AResumeDependencies,
) -> tuple[dict[str, Any], int]:
    """Persist and shape a re-interrupt from one resumed A2A graph."""
    agent = context.record.spec.agent
    next_generation = context.generation + 1
    if agent == "chat":
        interrupt_dict = dict(interrupt)
        result = dependencies.projection.interrupts.chat_interrupt_result(
            interrupt_dict
        )
        body = dependencies.projection.interrupts.chat_interrupt_body(
            run_id=context.record.spec.run_id,
            interrupt=interrupt_dict,
        )
    else:
        interrupt_dict = (
            dependencies.projection.interrupts.project_review_interrupt(
                interrupt
            )
        )
        result = dependencies.projection.interrupts.review_interrupt_result(
            interrupt_dict
        )
        body = dependencies.projection.interrupts.review_interrupt_body(
            thread_id=context.record.spec.run_id,
            interrupt=interrupt_dict,
        )
    result["generation"] = next_generation
    context.registry.settle_run(
        context.record.spec.run_id,
        owner=context.owner,
        status="input_required",
        result=result,
        expected_revision=context.record.revision,
    )
    body["generation"] = next_generation
    return body, 200


def _terminal_result(
    context: _ResumeContext,
    final_state: Mapping[str, Any],
    *,
    dependencies: A2AResumeDependencies,
) -> dict[str, Any]:
    """Format one terminal Chat or Review result for registry storage."""
    if context.record.spec.agent == "chat":
        stored = context.record.result or {}
        interrupt = stored.get("interrupt", {})
        draft = (
            interrupt.get("draft", {})
            if isinstance(interrupt, Mapping)
            else {}
        )
        prior_surface = (
            draft.get("a2ui", {}) if isinstance(draft, Mapping) else {}
        )
        return dependencies.projection.format_chat_result(
            final_state,
            prior_surface=(
                prior_surface if isinstance(prior_surface, Mapping) else {}
            ),
            resume_payload=context.resume_payload,
        )
    return dependencies.projection.format_review_result(final_state)


async def resume_task(
    task_id: str,
    context_id: str,
    arguments: Mapping[str, Any],
    *,
    dependencies: A2AResumeDependencies,
) -> tuple[dict[str, Any], int] | None:
    """Resume an owner-scoped A2A pause through the shared graph kernel."""
    context = _resume_context(
        task_id,
        context_id,
        arguments,
        dependencies=dependencies,
    )
    if context is None:
        return None
    agent = context.record.spec.agent
    graph = (
        dependencies.graphs.chat_graph()
        if agent == "chat"
        else dependencies.graphs.review_graph()
    )
    try:
        final_state = await dependencies.graphs.resume_graph(
            graph,
            context.record.spec.run_id,
            context.resume_payload,
        )
    except NoCheckpointError as exc:
        raise ValueError("A2A task has no resumable checkpoint") from exc
    interrupt = detect_interrupt(final_state, context.record.spec.run_id)
    if interrupt is not None:
        return _settle_interrupt(
            context,
            interrupt,
            dependencies=dependencies,
        )
    result = _terminal_result(
        context,
        final_state,
        dependencies=dependencies,
    )
    context.registry.settle_run(
        context.record.spec.run_id,
        owner=context.owner,
        status="succeeded",
        result=result,
        expected_revision=context.record.revision,
    )
    return (
        run_lifecycle.agent_run_response(
            run_id=context.record.spec.run_id,
            agent=agent,
            status="succeeded",
            result=strip_agent_result(result),
        ),
        200,
    )
