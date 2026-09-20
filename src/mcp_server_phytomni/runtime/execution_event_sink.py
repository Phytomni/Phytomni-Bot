# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Low-intrusion, context-bound production of durable execution events."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from importlib import import_module
from typing import Any, NotRequired, TypedDict, Unpack, cast

from .execution_event_flags import execution_event_production_enabled
from .execution_event_observability import observe_execution_event
from .execution_event_store import ExecutionEventStore
from .execution_events import (
    ExecutionEventIntent,
    ExecutionEventV1,
    parse_execution_event_intent,
)

type ExecutionEventEmitter = Callable[
    [ExecutionEventIntent],
    ExecutionEventV1 | None,
]


def _sink_emitter(sink: object) -> ExecutionEventEmitter:
    """Resolve the small structural sink boundary at bind time."""
    emitter = getattr(sink, "emit", None)
    if not callable(emitter):
        raise TypeError("execution event sink must expose emit")
    return cast(ExecutionEventEmitter, emitter)


class NoOpExecutionEventSink:
    """Compatibility sink for MCP, legacy, and unadvertised paths."""

    def emit(self, intent: ExecutionEventIntent) -> None:
        """Discard one event intent for compatibility-only execution paths."""
        del intent

    @property
    def degraded(self) -> bool:
        """Return the fixed healthy state of a no-op sink."""
        return False


class PublishingExecutionEventSink:
    """Publish only events that the delegated durable sink committed."""

    def __init__(
        self,
        delegate: object,
        publish: Callable[[ExecutionEventV1], None],
    ) -> None:
        self._delegate = _sink_emitter(delegate)
        self._delegate_sink = delegate
        self._publish = publish

    @property
    def degraded(self) -> bool:
        """Mirror the delegate's optional degradation state."""
        return bool(getattr(self._delegate_sink, "degraded", False))

    def emit(self, intent: ExecutionEventIntent) -> ExecutionEventV1 | None:
        """Publish an event only after the delegate durably commits it."""
        event = self._delegate(intent)
        if event is not None:
            self._publish(event)
        return event


_CURRENT_SINK: ContextVar[ExecutionEventEmitter | None] = ContextVar(
    "execution_event_sink",
    default=None,
)


@contextmanager
def bind_execution_event_sink(sink: object) -> Iterator[None]:
    """Bind one sink to the current asynchronous request/run context."""
    token = _CURRENT_SINK.set(_sink_emitter(sink))
    try:
        yield
    finally:
        _CURRENT_SINK.reset(token)


def current_execution_event_sink() -> ExecutionEventEmitter:
    """Return the bound emitter or the stateless compatibility no-op."""
    return _CURRENT_SINK.get() or NoOpExecutionEventSink().emit


def emit_execution_event(
    intent: ExecutionEventIntent,
) -> ExecutionEventV1 | None:
    """Emit through the active sink without coupling producers to storage."""
    adapter = import_module(".legacy_event_adapter_v2", __package__)
    adapter.adapt_legacy_event_intent_to_v2(intent)
    return current_execution_event_sink()(intent)


def set_todos(items: Sequence[Mapping[str, Any]]) -> ExecutionEventV1 | None:
    """Replace the current run Todo list with one validated whole snapshot."""
    return emit_execution_event(
        event_intent(
            "todo.snapshot",
            status="running",
            payload={"items": [dict(item) for item in items]},
        )
    )


def emit_reasoning_summary(
    text: str,
    *,
    visibility: str = "user",
    summary_key: str | None = None,
    idempotency_key: str | None = None,
) -> ExecutionEventV1 | None:
    """Publish only an explicitly user-visible provider summary."""
    if visibility != "user":
        raise ValueError("reasoning summary must be explicitly user-visible")
    return emit_execution_event(
        event_intent(
            "reasoning.summary",
            status="running",
            summary_key=summary_key,
            summary_text="Reasoning summary",
            payload={"text": text},
            idempotency_key=idempotency_key,
        )
    )


def emit_decision_note(
    text: str,
    *,
    summary_key: str | None = None,
    idempotency_key: str | None = None,
) -> ExecutionEventV1 | None:
    """Publish one bounded explicit agent decision, never private context."""
    return emit_execution_event(
        event_intent(
            "decision.note",
            status="running",
            summary_key=summary_key,
            summary_text="Decision note",
            payload={"text": text},
            idempotency_key=idempotency_key,
        )
    )


class _EventIntentFields(TypedDict):
    status: str
    summary_key: NotRequired[str | None]
    summary_text: NotRequired[str | None]
    payload: NotRequired[Mapping[str, Any] | None]
    target: NotRequired[Mapping[str, Any] | None]
    task_id: NotRequired[str | None]
    parent_event_id: NotRequired[str | None]
    idempotency_key: NotRequired[str | None]


def event_intent(
    kind: str,
    **fields: Unpack[_EventIntentFields],
) -> ExecutionEventIntent:
    """Build a validated public intent from bounded semantic fields."""
    summary_text = fields.get("summary_text")
    resolved_payload = dict(fields.get("payload") or {})
    if kind in {"decision.note", "reasoning.summary"} and not resolved_payload:
        resolved_payload = {"text": summary_text or kind}
    raw: dict[str, Any] = {
        "kind": kind,
        "status": fields["status"],
        "summary": {
            "key": fields.get("summary_key") or f"activity.{kind}",
            "text": summary_text or kind,
        },
        "payload": resolved_payload,
    }
    for key, value in (
        ("target", fields.get("target")),
        ("task_id", fields.get("task_id")),
        ("parent_event_id", fields.get("parent_event_id")),
        ("idempotency_key", fields.get("idempotency_key")),
    ):
        if value is not None:
            raw[key] = value
    return parse_execution_event_intent(raw)


def _tracking_degraded_intent() -> ExecutionEventIntent:
    return event_intent(
        "tracking.degraded",
        status="running",
        summary_key="activity.tracking.degraded",
        summary_text="Execution tracking was temporarily unavailable.",
        payload={
            "code": "event_persistence_unavailable",
            "retryable": True,
        },
        idempotency_key="tracking:degraded:event-persistence",
    )


class DurableExecutionEventSink:
    """Best-effort producer that never leaks storage failures into a run."""

    def __init__(
        self,
        store: ExecutionEventStore,
        *,
        run_id: str,
        owner: str,
        on_degraded: Callable[[ExecutionEventIntent], None] | None = None,
    ) -> None:
        self._store = store
        self.run_id = run_id
        self.owner = owner
        self._on_degraded = on_degraded
        self._pending_degradation = False

    @property
    def degraded(self) -> bool:
        """Whether a persistence failure is awaiting durable recovery."""
        return self._pending_degradation

    def emit(self, intent: ExecutionEventIntent) -> ExecutionEventV1 | None:
        """Persist one event, reporting and later recording any degradation."""
        if not execution_event_production_enabled():
            observe_execution_event("production_disabled")
            return None
        if self._pending_degradation:
            try:
                self._store.append(
                    self.run_id,
                    owner=self.owner,
                    intent=_tracking_degraded_intent(),
                )
            except (
                LookupError,
                OSError,
                RuntimeError,
                ValueError,
                sqlite3.Error,
            ):
                observe_execution_event("append_failed")
                return None
            self._pending_degradation = False
            observe_execution_event("tracking_recovered")
        try:
            event = self._store.append(
                self.run_id,
                owner=self.owner,
                intent=intent,
            )
            observe_execution_event("append_committed")
            return event
        except (LookupError, OSError, RuntimeError, ValueError, sqlite3.Error):
            observe_execution_event("append_failed")
            self._pending_degradation = True
            if self._on_degraded is not None:
                with suppress(Exception):
                    self._on_degraded(_tracking_degraded_intent())
            return None
