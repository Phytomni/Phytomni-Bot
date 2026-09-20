# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Context-bound execution event sink behavior tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.runtime.execution_event_observability import (
    execution_event_observations,
    observe_execution_event,
    reset_execution_event_observations,
)
from mcp_server_phytomni.runtime.execution_event_sink import (
    DurableExecutionEventSink,
    NoOpExecutionEventSink,
    bind_execution_event_sink,
    emit_decision_note,
    emit_execution_event,
    emit_reasoning_summary,
    event_intent,
    set_todos,
)
from mcp_server_phytomni.runtime.execution_event_store import (
    SQLiteExecutionEventStore,
)
from mcp_server_phytomni.runtime.execution_events import (
    ExecutionEventIntent,
    PublicTextPayload,
    PublicTodoSnapshotPayload,
    PublicTrackingDegradedPayload,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_models import RunSpec


def _intent(kind: str = "run.started") -> ExecutionEventIntent:

    return event_intent(kind, status="running")


@dataclass
class _RecordingSink:
    """Collect execution-event intents emitted by a bound sink."""

    intents: list[ExecutionEventIntent] = field(default_factory=list)

    def emit(self, intent: ExecutionEventIntent) -> None:
        """Record an execution-event intent for later assertions."""
        self.intents.append(intent)


def test_unbound_and_explicit_noop_paths_never_touch_business_code() -> None:
    """Verify unbound and explicit noop paths never touch business code."""

    assert emit_execution_event(_intent()) is None
    with bind_execution_event_sink(NoOpExecutionEventSink()):
        assert emit_execution_event(_intent("run.succeeded")) is None


def test_context_binding_is_nested_and_restored() -> None:
    """Verify context binding is nested and restored."""

    outer = _RecordingSink()
    inner = _RecordingSink()
    with bind_execution_event_sink(outer):
        emit_execution_event(_intent("run.started"))
        with bind_execution_event_sink(inner):
            emit_execution_event(_intent("decision.note"))
        emit_execution_event(_intent("run.succeeded"))

    assert [intent.kind for intent in outer.intents] == [
        "run.started",
        "run.succeeded",
    ]
    assert [intent.kind for intent in inner.intents] == ["decision.note"]


def test_set_todos_emits_one_whole_list_snapshot() -> None:
    """Verify set todos emits one whole list snapshot."""

    recorder = _RecordingSink()
    with bind_execution_event_sink(recorder):
        set_todos(
            [
                {
                    "id": "retrieve",
                    "label_key": "todo.retrieve",
                    "status": "in_progress",
                },
                {
                    "id": "report",
                    "label_key": "todo.report",
                    "status": "pending",
                },
            ]
        )

    assert [intent.kind for intent in recorder.intents] == ["todo.snapshot"]
    payload = recorder.intents[0].payload
    assert isinstance(payload, PublicTodoSnapshotPayload)
    assert [item.id for item in payload.items] == [
        "retrieve",
        "report",
    ]


def test_reasoning_projection_requires_explicit_public_visibility() -> None:
    """Verify reasoning projection requires explicit public visibility."""

    recorder = _RecordingSink()
    with bind_execution_event_sink(recorder):
        emit_reasoning_summary(
            "Compared the public evidence.",
            summary_key="review.evidence_compared",
            idempotency_key="review:evidence-compared",
        )
        emit_decision_note(
            "Selected the bounded analysis path.",
            summary_key="review.path_selected",
            idempotency_key="review:path-selected",
        )
        with pytest.raises(ValueError, match="explicitly user-visible"):
            emit_reasoning_summary("private scratchpad", visibility="private")

    assert [intent.kind for intent in recorder.intents] == [
        "reasoning.summary",
        "decision.note",
    ]
    payload = recorder.intents[0].payload
    assert isinstance(payload, PublicTextPayload)
    assert payload.text == "Compared the public evidence."
    assert recorder.intents[0].summary.key == "review.evidence_compared"
    assert recorder.intents[0].idempotency_key == "review:evidence-compared"
    assert recorder.intents[1].summary.key == "review.path_selected"
    assert recorder.intents[1].idempotency_key == "review:path-selected"


def _durable_sink(tmp_path: Path, *, store=None, on_degraded=None):

    db_path = tmp_path / "sink.db"
    if store is None:
        registry = RunRegistry(str(db_path))
        registry.create_run(RunSpec("run-1", "alice", "chat", "local"))
    resolved_store = store or SQLiteExecutionEventStore(str(db_path))
    return resolved_store, DurableExecutionEventSink(
        resolved_store,
        run_id="run-1",
        owner="alice",
        on_degraded=on_degraded,
    )


def test_durable_sink_appends_typed_intent(tmp_path: Path) -> None:
    """Verify durable sink appends typed intent."""
    store, sink = _durable_sink(tmp_path)

    event = sink.emit(_intent())

    assert event is not None
    assert event.kind == "run.started"
    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert page.items == (event,)


def test_production_flag_disables_append_without_affecting_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify production flag disables append without affecting run."""

    reset_execution_event_observations()
    monkeypatch.setenv("PHYTOMNI_EXECUTION_EVENTS_ENABLED", "false")
    store, sink = _durable_sink(tmp_path)

    assert sink.emit(_intent()) is None
    page = store.list_events("run-1", owner="alice")
    assert page is not None
    assert page.items == ()
    assert execution_event_observations()["production_disabled"] == 1


def test_rollout_observations_have_fixed_labels(tmp_path: Path) -> None:
    """Verify rollout observations have fixed labels."""

    reset_execution_event_observations()
    _store, sink = _durable_sink(tmp_path)
    assert sink.emit(_intent()) is not None

    assert execution_event_observations() == {
        "append_committed": 1,
        "append_failed": 0,
        "tracking_recovered": 0,
        "production_disabled": 0,
    }
    with pytest.raises(
        ValueError, match="unknown execution-event observation"
    ):
        observe_execution_event("run-id-would-be-unbounded")


@dataclass
class _RecoveringStore:
    """Fail one append before forwarding events to a durable store."""

    delegate: Any
    failures: int = 1

    def append(self, run_id, *, owner, intent):
        """Append configured test state to the captured test state."""
        if self.failures:
            self.failures -= 1
            raise OSError("private filesystem detail")
        return self.delegate.append(run_id, owner=owner, intent=intent)


def test_advertised_sink_reports_sanitized_degradation_and_recovers(
    tmp_path: Path,
) -> None:
    """Verify advertised sink reports sanitized degradation and recovers."""
    notices: list[ExecutionEventIntent] = []
    delegate, _sink = _durable_sink(tmp_path)
    recovering = _RecoveringStore(delegate)
    _ignored, sink = _durable_sink(
        tmp_path,
        store=recovering,
        on_degraded=notices.append,
    )

    assert sink.emit(_intent("run.started")) is None
    recovered = sink.emit(_intent("run.resumed"))

    assert recovered is not None
    assert [notice.kind for notice in notices] == ["tracking.degraded"]
    payload = notices[0].payload
    assert isinstance(payload, PublicTrackingDegradedPayload)
    assert payload.code == "event_persistence_unavailable"
    page = delegate.list_events("run-1", owner="alice")
    assert page is not None
    assert [event.kind for event in page.items] == [
        "tracking.degraded",
        "run.resumed",
    ]
