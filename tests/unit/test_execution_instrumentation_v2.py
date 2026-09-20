# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Context-safe shared instrumentation and durable child scheduling."""

from __future__ import annotations

import ast
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.support.execution_runtime_v2 import (
    ExecutionStartRequest,
    build_local_graph_runtime,
    run_execution_start,
    todo_snapshots,
)

from mcp_server_phytomni.mcp.formatting.agui import custom
from mcp_server_phytomni.mcp.progress_events import emit_progress
from mcp_server_phytomni.public_agent_catalog import public_agent_spec
from mcp_server_phytomni.runtime.checkpoint_instrumentation_v2 import (
    record_projected_input_required,
)
from mcp_server_phytomni.runtime.execution_drivers_v2 import (
    ResumableGraphDriver,
)
from mcp_server_phytomni.runtime.execution_event_sink import (
    emit_decision_note,
    emit_reasoning_summary,
)
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    current_execution_boundary,
    instrument_model_invocation,
    instrument_tool_invocation,
    record_model_attempt_started,
    record_model_retry,
    schedule_durable_child_work,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    EventStatus,
    ExecutionStatus,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOperation,
    DriverOutcome,
    TransportNeutralResult,
)
from mcp_server_phytomni.runtime.execution_runtime_v2 import ExecutionRuntime
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SQLiteExecutionWorkRepository,
)
from mcp_server_phytomni.runtime.langgraph_runner import invoke_graph


def test_runtime_context_propagates_and_child_intent_precedes_scheduling(
    tmp_path: Path,
) -> None:
    """Verify runtime context propagates and child intent precedes
    scheduling."""

    db_path = str(tmp_path / "instrumentation.db")
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)
    observed: list[tuple[str, bool]] = []

    @dataclass(eq=False)
    class InnerGraph:
        """Nested graph used to verify execution-context propagation."""

        async def ainvoke(self, value, **kwargs):
            """Ainvoke helper: runtime context propagates and child intent."""
            assert not kwargs
            return {"inner": value}

    @dataclass(eq=False)
    class Graph:
        """Outer graph that schedules a context-bound child invocation."""

        async def ainvoke(self, value, **kwargs):
            """Ainvoke helper: runtime context propagates and child intent."""
            boundary = current_execution_boundary(required=True)
            assert boundary is not None
            assert boundary.context.parent_span_id is not None
            assert not kwargs
            return {"value": await invoke_graph(InnerGraph(), value)}

    async def child() -> str:
        boundary = current_execution_boundary(required=True)
        assert boundary is not None
        registered = work.get_work_unit(
            boundary.context.execution_id,
            "work-child",
            owner=boundary.context.owner_ref,
        )
        observed.append(
            (boundary.context.execution_id, registered is not None)
        )
        return "done"

    async def start_handler(context, command, services):
        del command
        boundary = current_execution_boundary(required=True)
        assert boundary is not None
        assert boundary.context is context
        assert boundary.services is services
        assert await invoke_graph(Graph(), {"query": "rice"}) == {
            "value": {"inner": {"query": "rice"}}
        }
        task = schedule_durable_child_work(
            work_unit_id="work-child",
            operation_key="child.lookup",
            driver="local_graph",
            call=child,
        )
        assert await task == "done"
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = build_local_graph_runtime(db_path, start_handler, journal, work)
    spec = public_agent_spec("knowledge")
    assert spec is not None
    assert (
        run_execution_start(
            runtime,
            ExecutionStartRequest(
                "turn-context", "a" * 64, agent_slug=spec.slug
            ),
        ).status.value
        == "succeeded"
    )
    assert observed == [("turn-context", True)]
    page = journal.list_events("turn-context", owner="alice", limit=20)
    assert page is not None
    types = [event.type.value for event in page.items]
    assert "span.created" in types
    assert "span.started" in types
    assert "span.succeeded" in types
    assert "work_unit.registered" in types
    assert types.index("work_unit.registered") < types.index(
        "execution.succeeded"
    )
    created = [
        event for event in page.items if event.type.value == "span.created"
    ]
    assert len(created) == 2
    assert created[1].parent_span_id == created[0].span_id
    public_text = str([event.to_public_dict() for event in page.items])
    assert "InnerGraph" not in public_text
    assert "private_node" not in public_text
    assert "agent.knowledge.workflow" in public_text


def test_execution_boundary_is_absent_outside_runtime() -> None:
    """Verify execution boundary is absent outside runtime."""

    assert current_execution_boundary() is None


def test_checkpoint_surface_is_recorded_once_after_durable_projection(
    tmp_path: Path,
) -> None:
    """Verify checkpoint surface is recorded once after durable projection."""

    db_path = str(tmp_path / "checkpoint-facts.db")
    journal = SQLiteExecutionJournal(db_path)

    async def start_handler(context, command, services):
        del context, command, services
        projection = {
            "interrupt": {
                "draft": {
                    "a2ui": {
                        "surface_id": "surface-review-1",
                        "widget": "confirm",
                        "private": "not-published",
                    }
                }
            }
        }
        record_projected_input_required(projection)
        record_projected_input_required(projection)
        return DriverOutcome(status=ExecutionStatus.WAITING_INPUT)

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=journal,
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={
            "resumable_graph": ResumableGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )
    outcome = run_execution_start(
        runtime,
        ExecutionStartRequest(
            "turn-checkpoint", "f" * 64, agent_slug="review"
        ),
    )

    assert outcome.status.value == "waiting_input"
    page = journal.list_events("turn-checkpoint", owner="alice", limit=20)
    assert page is not None
    required = [
        event for event in page.items if event.type.value == "input.required"
    ]
    assert len(required) == 1
    assert required[0].public_payload.model_dump(mode="json") == {
        "surface_id": "surface-review-1",
        "widget": "confirm",
        "action_revision": 1,
    }
    assert "not-published" not in str(required[0].to_public_dict())


def test_legacy_progress_and_explicit_public_notes_adapt_to_v2_once(
    tmp_path: Path,
) -> None:
    """Verify legacy progress and explicit public notes adapt to V2 once."""

    db_path = str(tmp_path / "legacy-adapter.db")
    journal = SQLiteExecutionJournal(db_path)

    async def start_handler(context, command, services):
        del context, command, services
        emit_progress("retrieving", 1, 2, detail="private provider detail")
        custom(
            "phyto.progress",
            {
                "phase": "retrieving",
                "current": 1,
                "total": 2,
                "detail": "private provider detail",
            },
        )
        emit_reasoning_summary("Compared the public evidence.")
        emit_decision_note("Selected the bounded analysis path.")
        with pytest.raises(ValueError, match="explicitly user-visible"):
            emit_reasoning_summary(
                "private scratchpad marker", visibility="private"
            )
        custom(
            "phyto.reasoning_content",
            {"text": "hidden reasoning marker"},
        )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = build_local_graph_runtime(db_path, start_handler, journal)
    run_execution_start(
        runtime,
        ExecutionStartRequest("turn-adapted-events", "3" * 64),
    )

    page = journal.list_events("turn-adapted-events", owner="alice", limit=30)
    assert page is not None
    types = [event.type.value for event in page.items]
    assert types.count("span.progress") == 1
    assert types.count("reasoning.summary") == 1
    assert types.count("decision.note") == 1
    public = str([event.to_public_dict() for event in page.items])
    assert "private provider detail" not in public
    assert "private scratchpad marker" not in public
    assert "hidden reasoning marker" not in public


def test_review_progress_advances_the_catalog_todo_plan(
    tmp_path: Path,
) -> None:
    """Verify review progress advances the catalog todo plan."""

    db_path = str(tmp_path / "review-progress-todo.db")
    journal = SQLiteExecutionJournal(db_path)

    async def start_handler(context, command, services):
        del context, command, services
        emit_progress(
            "retrieving", 4, total=4, detail="private retrieval detail"
        )
        emit_progress("revising", 4, total=4, detail="private revision detail")
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = ExecutionRuntime(
        reservations=SQLiteExecutionReservationRepository(db_path),
        journal=journal,
        work=SQLiteExecutionWorkRepository(db_path),
        drivers={
            "resumable_graph": ResumableGraphDriver(
                {DriverOperation.START: start_handler}
            )
        },
    )
    run_execution_start(
        runtime,
        ExecutionStartRequest(
            "turn-review-progress", "7" * 64, agent_slug="review"
        ),
    )

    page = journal.list_events("turn-review-progress", owner="alice", limit=30)
    assert page is not None
    snapshots = todo_snapshots(page.items, status=EventStatus.RUNNING)
    assert [item.status for item in snapshots[-1]] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "in_progress",
    ]


def test_graph_runner_preserves_exact_optional_invocation_arguments() -> None:
    """Verify graph runner preserves exact optional invocation arguments."""

    calls = []

    @dataclass(eq=False)
    class Graph:
        """Graph that preserves optional invocation arguments unchanged."""

        async def ainvoke(self, value, **kwargs):
            """Invoke the graph with its test arguments."""
            calls.append((value, kwargs))
            return "unchanged"

    config = {"configurable": {"thread_id": "thread-1"}}
    context = {"memory_accessor": object()}
    result = asyncio.run(
        invoke_graph(
            Graph(), {"query": "rice"}, config=config, context=context
        )
    )

    assert result == "unchanged"
    assert calls == [
        ({"query": "rice"}, {"config": config, "context": context})
    ]


def test_langgraph_business_calls_use_the_shared_runner() -> None:
    """Verify langgraph business calls use the shared runner."""
    source_root = Path(__file__).parents[2] / "src" / "mcp_server_phytomni"
    allowed = {
        source_root / "runtime" / "langgraph_runner.py",
        source_root / "interop" / "mcp_client.py",
    }
    bypasses: list[str] = []
    for path in source_root.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "ainvoke"
            ):
                bypasses.append(
                    f"{path.relative_to(source_root)}:{node.lineno}"
                )
    assert not bypasses


def test_tool_boundary_records_safe_logical_work_attempts_and_duration(
    tmp_path: Path,
) -> None:
    """Verify tool boundary records safe logical work attempts and duration."""

    db_path = str(tmp_path / "tool-boundary.db")
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)

    async def start_handler(context, command, services):
        del context, command, services

        async def successful_call():
            return {"private_result": "do-not-publish"}

        assert await instrument_tool_invocation(
            "ChatAgent", successful_call
        ) == {"private_result": "do-not-publish"}

        async def failed_call():
            raise RuntimeError("private-exception-do-not-publish")

        with suppress(RuntimeError):
            await instrument_tool_invocation(
                "unregistered-private-tool-name", failed_call
            )
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = build_local_graph_runtime(db_path, start_handler, journal, work)
    assert (
        run_execution_start(
            runtime,
            ExecutionStartRequest("turn-tools", "b" * 64),
        ).status.value
        == "succeeded"
    )

    page = journal.list_events("turn-tools", owner="alice", limit=100)
    assert page is not None
    tool_events = [
        event
        for event in page.items
        if event.work_unit_id is not None and event.source.value == "tool"
    ]
    types = [event.type.value for event in tool_events]
    assert types.count("work_unit.registered") == 2
    assert types.count("work_unit.attempt_started") == 2
    assert types.count("span.created") == 2
    assert types.count("span.started") == 2
    assert types.count("work_unit.succeeded") == 1
    assert types.count("span.succeeded") == 1
    assert types.count("work_unit.failed") == 1
    assert types.count("span.failed") == 1
    start_span_events = [
        event
        for event in tool_events
        if event.type.value in {"span.created", "span.started"}
    ]
    assert all(
        event.idempotency_key
        == f"span:{event.span_id}:{event.type.value.rsplit('.', 1)[-1]}"
        for event in start_span_events
    )

    terminal = [
        event
        for event in tool_events
        if event.type.value
        in {
            "work_unit.succeeded",
            "span.succeeded",
            "work_unit.failed",
            "span.failed",
        }
    ]
    assert all(
        event.public_payload.model_dump().get("duration_ms") is not None
        for event in terminal
    )
    operation_keys = {
        event.public_payload.model_dump().get("operation_key")
        for event in tool_events
    }
    assert {"tool.chat", "tool.other"} <= operation_keys
    work_ids = {
        event.work_unit_id for event in tool_events if event.work_unit_id
    }
    statuses = {
        work.get_work_unit("turn-tools", work_id, owner="alice").status.value
        for work_id in work_ids
    }
    assert statuses == {"succeeded", "failed"}
    public_text = str([event.to_public_dict() for event in tool_events])
    assert "do-not-publish" not in public_text
    assert "private-exception" not in public_text
    assert "unregistered-private-tool-name" not in public_text


def test_model_boundary_records_retries_duration_cancellation_and_safe_failure(
    tmp_path: Path,
) -> None:
    """Verify model boundary records retries duration cancellation and safe
    failure."""

    db_path = str(tmp_path / "model-boundary.db")
    journal = SQLiteExecutionJournal(db_path)
    work = SQLiteExecutionWorkRepository(db_path)

    async def start_handler(context, command, services):
        del context, command, services

        async def retried_call():
            record_model_retry(delay_ms=25, code="model_transport_retry")
            record_model_attempt_started(2)
            return {"private_result": "do-not-publish"}

        assert await instrument_model_invocation(
            retried_call,
            max_attempts=2,
        ) == {"private_result": "do-not-publish"}

        async def failed_call():
            raise RuntimeError("private-model-exception-do-not-publish")

        with suppress(RuntimeError):
            await instrument_model_invocation(failed_call)

        async def cancelled_call():
            raise asyncio.CancelledError("private-cancellation-do-not-publish")

        with suppress(asyncio.CancelledError):
            await instrument_model_invocation(cancelled_call)
        return DriverOutcome.succeeded(TransportNeutralResult(answer="ok"))

    runtime = build_local_graph_runtime(db_path, start_handler, journal, work)
    outcome = run_execution_start(
        runtime,
        ExecutionStartRequest("turn-models", "8" * 64),
    )
    assert outcome.status.value == "succeeded"

    page = journal.list_events("turn-models", owner="alice", limit=100)
    assert page is not None
    model_events = [
        event
        for event in page.items
        if event.work_unit_id is not None
        and event.summary.key.startswith("model.generate.")
    ]
    types = [event.type.value for event in model_events]
    assert types.count("work_unit.registered") == 3
    assert types.count("work_unit.attempt_started") == 4
    assert types.count("work_unit.retry_scheduled") == 1
    assert types.count("work_unit.succeeded") == 1
    assert types.count("work_unit.failed") == 1
    assert types.count("work_unit.cancelled") == 1

    terminal = [
        event
        for event in model_events
        if event.type.value
        in {"work_unit.succeeded", "work_unit.failed", "work_unit.cancelled"}
    ]
    assert all(
        event.public_payload.model_dump().get("duration_ms") is not None
        for event in terminal
    )
    failed = next(
        event
        for event in model_events
        if event.type.value == "work_unit.failed"
    )
    assert failed.public_payload.model_dump()["code"] == (
        "model_invocation_failed"
    )
    public_text = str([event.to_public_dict() for event in model_events])
    assert "do-not-publish" not in public_text
    assert "private-model-exception" not in public_text
    assert "private-cancellation" not in public_text


def test_shared_chat_retry_loop_uses_the_model_boundary() -> None:
    """Verify shared chat retry loop uses the model boundary."""
    source = (
        Path(__file__).parents[2]
        / "src"
        / "mcp_server_phytomni"
        / "agents"
        / "chat"
        / "service.py"
    ).read_text(encoding="utf-8")

    assert "instrument_model_invocation(" in source
    assert "record_model_retry(" in source
    assert "record_model_attempt_started(" in source
