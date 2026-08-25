# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical ExecutionRuntime start, dispatch, and settlement behavior."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest


def _runtime(
    db_path: Path,
    driver,
    driver_key="local_graph",
    *,
    target_store=None,
):
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_v2 import (
        ExecutionRuntime,
    )
    from mcp_server_phytomni.runtime.execution_work_store_v2 import (
        SQLiteExecutionWorkRepository,
    )

    reservations = SQLiteExecutionReservationRepository(str(db_path))
    journal = SQLiteExecutionJournal(str(db_path))
    work = SQLiteExecutionWorkRepository(str(db_path))
    runtime = ExecutionRuntime(
        reservations=reservations,
        journal=journal,
        work=work,
        drivers=cast(Any, {driver_key: driver}),
        target_store=target_store,
    )
    return runtime, reservations, journal, work


def test_runtime_persists_private_artifact_delivery_binding(
    tmp_path: Path,
) -> None:
    """A public target is replayable without putting its OBS ref in events."""
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionArtifactRef,
        TransportNeutralResult,
    )
    from mcp_server_phytomni.runtime.execution_target_store_v2 import (
        ExecutionTargetBindingConflictError,
        ExecutionTargetBindingV2,
        SQLiteExecutionTargetStore,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(
                    artifacts=(
                        ExecutionArtifactRef(
                            role="scientific_report",
                            target_kind="artifact",
                            target_id="artifact-report",
                            name="report.md",
                            media_type="text/markdown",
                            size_bytes=12,
                            private_delivery_ref="obs://bucket/run/report.md",
                        ),
                    )
                )
            )

    db_path = tmp_path / "target-runtime.db"
    target_store = SQLiteExecutionTargetStore(str(db_path))
    runtime, _reservations, journal, _work = _runtime(
        db_path,
        Driver(),
        target_store=target_store,
    )

    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-target",
            fingerprint_version=1,
            fingerprint="d" * 64,
            command=_command(),
            transport="http",
        )
    )

    binding = target_store.get(
        owner="alice",
        execution_id="turn-target",
        kind="artifact",
        target_id="artifact-report",
    )
    assert binding is not None
    assert binding.delivery_ref == "obs://bucket/run/report.md"
    assert (
        target_store.get(
            owner="bob",
            execution_id="turn-target",
            kind="artifact",
            target_id="artifact-report",
        )
        is None
    )
    with pytest.raises(ExecutionTargetBindingConflictError):
        target_store.put(
            ExecutionTargetBindingV2(
                owner="alice",
                execution_id="turn-target",
                kind="artifact",
                target_id="artifact-report",
                role="scientific_report",
                name="report.md",
                media_type="text/markdown",
                size_bytes=12,
                delivery_ref="obs://bucket/run/other-report.md",
            )
        )
    page = journal.list_events("turn-target", owner="alice", limit=50)
    assert page is not None
    rendered = repr([event.to_public_dict() for event in page.items])
    assert "obs://" not in rendered


def test_runtime_publishes_stable_artifact_target_once_across_reconcile(
    tmp_path: Path,
) -> None:
    """Re-observing one provider target must not grow the public ledger."""
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        ExecutionStatus,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionArtifactRef,
        ExecutionCommand,
        TransportNeutralResult,
    )

    artifact = ExecutionArtifactRef(
        role="scientific_report",
        target_kind="artifact",
        target_id="artifact-stable-report",
        name="report.md",
        media_type="text/markdown",
        size_bytes=12,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome(
                status=ExecutionStatus.RUNNING,
                result=TransportNeutralResult(artifacts=(artifact,)),
            )

    runtime, reservations, journal, _work = _runtime(
        tmp_path / "stable-target-runtime.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-stable-target",
            fingerprint_version=1,
            fingerprint="e" * 64,
            command=_command(),
            transport="http",
        )
    )

    for reconcile_index in range(1, 3):
        revision = reservations.get(
            owner="alice", execution_id="turn-stable-target"
        ).supervisor_revision
        asyncio.run(
            runtime.reconcile(
                owner="alice",
                execution_id="turn-stable-target",
                command=ExecutionCommand(
                    agent_slug="knowledge",
                    arguments={},
                    action_id=f"provider-join:{reconcile_index}",
                    expected_revision=revision,
                ),
            )
        )

    page = journal.list_events("turn-stable-target", owner="alice", limit=100)
    assert page is not None
    artifact_events = [
        event
        for event in page.items
        if event.type.value == "artifact.published"
        and event.target is not None
        and event.target.id == "artifact-stable-report"
    ]
    assert len(artifact_events) == 1


def _command(agent_slug="knowledge"):
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionCommand,
    )

    return ExecutionCommand(agent_slug=agent_slug, arguments={"query": "rice"})


def test_runtime_start_binds_context_root_span_and_settles_once(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionContext,
        TransportNeutralResult,
    )

    class Driver:
        def __init__(self) -> None:
            self.calls = 0
            self.contexts: list[ExecutionContext] = []

        async def execute(self, operation, context, command, services):
            del operation, command, services
            self.calls += 1
            self.contexts.append(context)
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="unchanged business answer")
            )

    driver = Driver()
    runtime, reservations, journal, work = _runtime(
        tmp_path / "runtime.db", driver
    )
    first = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-runtime",
            fingerprint_version=1,
            fingerprint="a" * 64,
            command=_command(),
            transport="http",
        )
    )
    replay = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-runtime",
            fingerprint_version=1,
            fingerprint="a" * 64,
            command=_command(),
            transport="mcp",
        )
    )

    assert first.status.value == "succeeded"
    assert first.result is not None
    assert first.result.answer == "unchanged business answer"
    assert replay.status.value == "succeeded"
    assert driver.calls == 1
    context = driver.contexts[0]
    assert context.execution_id == "turn-runtime"
    assert context.agent.slug == "knowledge"
    assert context.current_span_id == context.root_span_id
    binding = reservations.get(owner="alice", execution_id="turn-runtime")
    assert binding.status.value == "succeeded"
    assert (
        work.get_span(
            "turn-runtime", binding.root_span_id, owner="alice"
        ).status.value
        == "succeeded"
    )
    page = journal.list_events("turn-runtime", owner="alice", limit=20)
    assert page is not None
    assert [event.type.value for event in page.items] == [
        "execution.admitted",
        "execution.started",
        "span.started",
        "message.completed",
        "span.succeeded",
        "execution.succeeded",
    ]
    assert (
        sum(event.type.value == "execution.succeeded" for event in page.items)
        == 1
    )


@pytest.mark.parametrize(
    "agent_slug",
    [
        "chat",
        "knowledge",
        "data",
        "analyst",
        "review",
        "brief_gene",
        "deep_genome",
        "research",
        "design",
        "network",
    ],
)
def test_every_public_agent_runtime_keeps_todo_undeclared_without_agent_fact(
    tmp_path: Path,
    agent_slug: str,
) -> None:
    from mcp_server_phytomni.public_agent_catalog import public_agent_spec
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        TransportNeutralResult,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    spec = public_agent_spec(agent_slug)
    assert spec is not None
    runtime, _reservations, journal, _work = _runtime(
        tmp_path / f"{agent_slug}.db", Driver(), spec.driver
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id=f"turn-{agent_slug}",
            fingerprint_version=1,
            fingerprint="7" * 64,
            command=_command(agent_slug),
            transport="http",
        )
    )

    assert outcome.status.value == "succeeded"
    page = journal.list_events(f"turn-{agent_slug}", owner="alice", limit=50)
    assert page is not None
    assert all(event.type.value != "todo.snapshot" for event in page.items)
    projection = journal.get_projection(f"turn-{agent_slug}", owner="alice")
    assert projection.todo_declared is False
    assert projection.todos == ()


def test_runtime_keeps_todo_undeclared_without_agent_plan_fact(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        TransportNeutralResult,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    runtime, _reservations, journal, _work = _runtime(
        tmp_path / "undeclared-todo.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-undeclared-todo",
            fingerprint_version=1,
            fingerprint="8" * 64,
            command=_command("knowledge"),
            transport="http",
        )
    )

    projection = journal.get_projection("turn-undeclared-todo", owner="alice")
    page = journal.list_events("turn-undeclared-todo", owner="alice", limit=30)
    assert page is not None
    assert projection.todo_declared is False
    assert projection.todos == ()
    assert all(event.type.value != "todo.snapshot" for event in page.items)


def test_runtime_closes_only_an_agent_declared_todo_plan(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
        advance_execution_todo,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        TransportNeutralResult,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            advance_execution_todo("retrieving", completed=False)
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    runtime, _reservations, journal, _work = _runtime(
        tmp_path / "declared-todo.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-declared-todo",
            fingerprint_version=1,
            fingerprint="9" * 64,
            command=_command("knowledge"),
            transport="http",
        )
    )

    page = journal.list_events("turn-declared-todo", owner="alice", limit=30)
    assert page is not None
    snapshots = [
        event.public_payload.model_dump(mode="json")["items"]
        for event in page.items
        if event.type.value == "todo.snapshot"
    ]
    assert len(snapshots) == 2
    assert [item["id"] for item in snapshots[0]] == [
        item["id"] for item in snapshots[1]
    ]
    assert snapshots[0][0]["status"] == "in_progress"
    assert {item["status"] for item in snapshots[1]} == {"completed"}


def test_runtime_normalizes_unhandled_driver_error_without_leaking_text(
    tmp_path: Path,
) -> None:
    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            raise RuntimeError("password=private-marker")

    runtime, reservations, journal, _work = _runtime(
        tmp_path / "failure.db", Driver()
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-failure",
            fingerprint_version=1,
            fingerprint="b" * 64,
            command=_command(),
            transport="http",
        )
    )

    assert outcome.status.value == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == "driver_unhandled_error"
    assert (
        reservations.get(
            owner="alice", execution_id="turn-failure"
        ).status.value
        == "failed"
    )
    page = journal.list_events("turn-failure", owner="alice", limit=20)
    assert page is not None
    encoded = str([event.to_public_dict() for event in page.items])
    assert "private-marker" not in encoded
    assert [event.type.value for event in page.items].count(
        "execution.failed"
    ) == 1


def test_runtime_publishes_bounded_message_and_opaque_result_targets(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionArtifactRef,
        TransportNeutralResult,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(
                    answer="public answer",
                    artifacts=(
                        ExecutionArtifactRef(
                            role="report",
                            target_kind="report",
                            target_id="report-opaque-1",
                            name="analysis.md",
                            media_type="text/markdown",
                            size_bytes=42,
                        ),
                    ),
                    private_value={"path": "C:/private/result.md"},
                )
            )

    runtime, _reservations, journal, _work = _runtime(
        tmp_path / "publication.db", Driver()
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-publication",
            fingerprint_version=1,
            fingerprint="1" * 64,
            command=_command(),
            transport="http",
        )
    )

    assert outcome.status.value == "succeeded"
    page = journal.list_events("turn-publication", owner="alice", limit=20)
    assert page is not None
    types = [event.type.value for event in page.items]
    assert types.count("message.completed") == 1
    assert types.count("result.published") == 1
    assert types.index("message.completed") < types.index(
        "execution.succeeded"
    )
    resource = next(
        event for event in page.items if event.type.value == "result.published"
    )
    assert resource.target is not None
    assert resource.target.model_dump(mode="json") == {
        "kind": "report",
        "id": "report-opaque-1",
    }
    assert resource.public_payload.model_dump(mode="json") == {
        "name": "analysis.md",
        "media_type": "text/markdown",
        "size_bytes": 42,
    }
    encoded = str([event.to_public_dict() for event in page.items])
    assert "C:/private" not in encoded


def test_runtime_message_facts_reconstruct_large_answer_with_stable_identity(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        MessagePublicPayload,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )

    answer = "A" * 8192 + "B" * 8192 + "tail"

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer=answer)
            )

    runtime, _reservations, journal, _work = _runtime(
        tmp_path / "large-message.db", Driver()
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-large-message",
            fingerprint_version=1,
            fingerprint="9" * 64,
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={
                    "query": "rice",
                    "__user_message_id": "msg-user-large",
                    "__assistant_message_id": "msg-assistant-large",
                },
            ),
            transport="http",
        )
    )

    assert outcome.status.value == "succeeded"
    page = journal.list_events("turn-large-message", owner="alice", limit=100)
    assert page is not None
    message_events = [
        event
        for event in page.items
        if event.type.value in {"message.snapshot", "message.completed"}
    ]
    assert len(message_events) == 3
    assert [event.type.value for event in message_events] == [
        "message.snapshot",
        "message.snapshot",
        "message.completed",
    ]
    payloads = [
        cast(MessagePublicPayload, event.public_payload)
        for event in message_events
    ]
    assert {payload.message_id for payload in payloads} == {
        "msg-assistant-large"
    }
    assert {payload.source_message_id for payload in payloads} == {
        "msg-assistant-large"
    }
    assert [payload.base_offset for payload in payloads] == [0, 8192, 16384]
    assert [payload.offset for payload in payloads] == [
        8192,
        16384,
        len(answer),
    ]
    assert {payload.total_length for payload in payloads} == {len(answer)}
    assert [payload.chunk_index for payload in payloads] == [0, 1, 2]
    assert {payload.chunk_count for payload in payloads} == {3}
    assert len({payload.content_sha256 for payload in payloads}) == 1
    assert "".join(payload.text for payload in payloads) == answer


def test_routed_runtime_reuses_the_admitted_assistant_message_identity(
    tmp_path: Path,
) -> None:
    """Expert routing must not replace Web's durable assistant identity."""
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        MessagePublicPayload,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        EXPERT_ROUTER_AGENT_SLUG,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="routed answer")
            )

    runtime, reservations, journal, _work = _runtime(
        tmp_path / "routed-message-identity.db", Driver()
    )
    execution_id = "turn-routed-message-identity"
    fingerprint = "7" * 64
    admitted_arguments = {
        "__query": "rice",
        "__assistant_message_id": "msg-web-assistant-1",
        "__user_message_id": "msg-web-user-1",
    }
    reservations.reserve(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=1,
        fingerprint=fingerprint,
        command=ExecutionCommand(
            agent_slug=EXPERT_ROUTER_AGENT_SLUG,
            arguments=admitted_arguments,
        ),
        durable_command={
            "agent": EXPERT_ROUTER_AGENT_SLUG,
            "arguments": admitted_arguments,
            "execution_id": execution_id,
            "owner_ref": "alice",
            "fingerprint_version": 1,
            "fingerprint": fingerprint,
        },
    )
    routed_command = ExecutionCommand(
        agent_slug="knowledge",
        arguments={"user_query": "rice"},
    )
    reservations.bind_routed_agent(
        owner="alice",
        execution_id=execution_id,
        command=routed_command,
    )

    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id=execution_id,
            fingerprint_version=1,
            fingerprint=fingerprint,
            command=routed_command,
            transport="service_dispatcher",
        )
    )

    assert outcome.status.value == "succeeded"
    page = journal.list_events(execution_id, owner="alice", limit=100)
    assert page is not None
    message_events = [
        event
        for event in page.items
        if event.type.value in {"message.snapshot", "message.completed"}
    ]
    assert message_events
    payloads = [
        cast(MessagePublicPayload, event.public_payload)
        for event in message_events
    ]
    assert {payload.message_id for payload in payloads} == {
        "msg-web-assistant-1"
    }
    assert {payload.source_message_id for payload in payloads} == {
        "msg-web-assistant-1"
    }


def test_artifact_reference_rejects_paths_and_direct_urls() -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionArtifactRef,
    )

    with pytest.raises(ValueError, match="opaque target"):
        ExecutionArtifactRef(
            role="report",
            target_kind="report",
            target_id="C:/private/result.md",
        )
    with pytest.raises(ValueError, match="opaque target"):
        ExecutionArtifactRef(
            role="report",
            target_kind="report",
            target_id="https://example.test/result",
        )


def test_running_result_publishes_snapshot_without_terminal_completion(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        TransportNeutralResult,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome.running(
                result=TransportNeutralResult(answer="bounded draft")
            )

    runtime, _reservations, journal, _work = _runtime(
        tmp_path / "snapshot.db", Driver()
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-snapshot",
            fingerprint_version=1,
            fingerprint="2" * 64,
            command=_command(),
            transport="http",
        )
    )

    assert outcome.status.value == "running"
    page = journal.list_events("turn-snapshot", owner="alice", limit=20)
    assert page is not None
    types = [event.type.value for event in page.items]
    assert types.count("message.snapshot") == 1
    assert "message.completed" not in types


def test_concurrent_start_dispatches_driver_once(tmp_path: Path) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        TransportNeutralResult,
    )

    class Driver:
        def __init__(self) -> None:
            self.calls = 0
            self.release = asyncio.Event()

        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            self.calls += 1
            await self.release.wait()
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    async def scenario() -> tuple[DriverOutcome, DriverOutcome, int]:
        driver = Driver()
        runtime, _reservations, _journal, _work = _runtime(
            tmp_path / "concurrent-runtime.db", driver
        )

        async def start() -> DriverOutcome:
            return await runtime.start(
                owner="alice",
                execution_id="turn-concurrent-runtime",
                fingerprint_version=1,
                fingerprint="c" * 64,
                command=_command(),
                transport="http",
            )

        first_task = asyncio.create_task(start())
        await asyncio.sleep(0)
        second = await start()
        driver.release.set()
        first = await first_task
        return first, second, driver.calls

    first, second, calls = asyncio.run(scenario())
    assert calls == 1
    assert first.status.value == "succeeded"
    assert second.status.value in {"dispatching", "running"}


def test_resume_action_is_revision_checked_and_idempotently_replayed(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
        ExecutionStatus,
        TransportNeutralResult,
    )

    class Driver:
        def __init__(self) -> None:
            self.operations: list[str] = []

        async def execute(self, operation, context, command, services):
            del context, command, services
            self.operations.append(operation.value)
            if operation.value == "start":
                return DriverOutcome(status=ExecutionStatus.WAITING_INPUT)
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="resumed")
            )

    driver = Driver()
    runtime, reservations, journal, _work = _runtime(
        tmp_path / "resume.db", driver, "resumable_graph"
    )
    started = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-resume",
            fingerprint_version=1,
            fingerprint="d" * 64,
            command=_command("review"),
            transport="http",
        )
    )
    assert started.status.value == "waiting_input"
    revision = reservations.get(
        owner="alice", execution_id="turn-resume"
    ).supervisor_revision
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        ExecutionReservationConflictError,
    )

    with pytest.raises(
        ExecutionReservationConflictError, match="stale_revision"
    ):
        asyncio.run(
            runtime.resume(
                owner="alice",
                execution_id="turn-resume",
                command=ExecutionCommand(
                    agent_slug="review",
                    arguments={"answer": "private-stale-input-marker"},
                    action_id="action-stale",
                    expected_revision=revision - 1,
                ),
                transport="http",
            )
        )
    action = ExecutionCommand(
        agent_slug="review",
        arguments={"answer": "continue"},
        action_id="action-resume-1",
        expected_revision=revision,
    )
    resumed = asyncio.run(
        runtime.resume(
            owner="alice",
            execution_id="turn-resume",
            command=action,
            transport="http",
        )
    )
    replay = asyncio.run(
        runtime.resume(
            owner="alice",
            execution_id="turn-resume",
            command=action,
            transport="mcp",
        )
    )

    assert resumed.status.value == "succeeded"
    assert replay.status.value == "succeeded"
    assert driver.operations == ["start", "resume"]
    page = journal.list_events("turn-resume", owner="alice", limit=30)
    assert page is not None
    event_types = [event.type.value for event in page.items]
    assert event_types.count("execution.resumed") == 1
    assert event_types.count("execution.succeeded") == 1
    assert event_types.count("input.action_claimed") == 1
    assert event_types.count("input.resolved") == 1
    assert event_types.count("input.action_rejected") == 2
    rejected = [
        event.public_payload.model_dump(mode="json")
        for event in page.items
        if event.type.value == "input.action_rejected"
    ]
    assert {payload["outcome"] for payload in rejected} == {
        "duplicate_action",
        "stale_revision",
    }
    encoded = str([event.to_public_dict() for event in page.items])
    assert "continue" not in encoded
    assert "private-stale-input-marker" not in encoded


def test_cancel_best_effort_remains_nonterminal_and_is_explicit(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del context, command, services
            if operation.value == "cancel":
                return DriverOutcome.running(
                    cancellation_outcome="best_effort"
                )
            return DriverOutcome.running()

    runtime, reservations, journal, _work = _runtime(
        tmp_path / "cancel.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-cancel",
            fingerprint_version=1,
            fingerprint="e" * 64,
            command=_command(),
            transport="http",
        )
    )
    revision = reservations.get(
        owner="alice", execution_id="turn-cancel"
    ).supervisor_revision
    outcome = asyncio.run(
        runtime.cancel(
            owner="alice",
            execution_id="turn-cancel",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={"reason": "user_requested"},
                action_id="cancel-1",
                expected_revision=revision,
            ),
            transport="http",
        )
    )

    record = reservations.get(owner="alice", execution_id="turn-cancel")
    assert outcome.status.value == "running"
    assert outcome.cancellation_outcome == "best_effort"
    assert record.cancellation_state == "best_effort"
    assert record.status.value == "running"
    page = journal.list_events("turn-cancel", owner="alice", limit=30)
    assert page is not None
    payloads = [
        event.public_payload.model_dump(mode="json")
        for event in page.items
        if event.type.value == "execution.cancellation_requested"
    ]
    assert {payload["outcome"] for payload in payloads} == {
        "requested",
        "best_effort",
    }


def test_best_effort_cancel_preserves_late_start_terminal(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
        TransportNeutralResult,
    )

    entered = asyncio.Event()
    release = asyncio.Event()

    class Driver:
        async def execute(self, operation, context, command, services):
            del context, command, services
            if operation.value == "cancel":
                return DriverOutcome.running(
                    cancellation_outcome="best_effort"
                )
            entered.set()
            await release.wait()
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="late provider answer")
            )

    runtime, reservations, journal, _work = _runtime(
        tmp_path / "cancel-late-terminal.db", Driver()
    )

    async def scenario() -> None:
        start = asyncio.create_task(
            runtime.start(
                owner="alice",
                execution_id="turn-cancel-late-terminal",
                fingerprint_version=1,
                fingerprint="1" * 64,
                command=_command(),
                transport="http",
            )
        )
        await entered.wait()
        revision = reservations.get(
            owner="alice", execution_id="turn-cancel-late-terminal"
        ).supervisor_revision
        cancellation = await runtime.cancel(
            owner="alice",
            execution_id="turn-cancel-late-terminal",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={"reason": "user_requested"},
                action_id="cancel-late-terminal",
                expected_revision=revision,
            ),
            transport="http",
        )
        assert cancellation.cancellation_outcome == "best_effort"
        release.set()
        terminal = await start
        assert terminal.status.value == "succeeded"

    asyncio.run(scenario())

    record = reservations.get(
        owner="alice", execution_id="turn-cancel-late-terminal"
    )
    projection = journal.get_projection(
        "turn-cancel-late-terminal", owner="alice"
    )
    assert record.status.value == "succeeded"
    assert record.cancellation_state == "best_effort"
    assert projection.terminal is not None
    assert projection.terminal.status == "succeeded"


def test_confirmed_cancellation_is_terminal_and_persisted(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_v2 import (
        ExecutionStatus,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del context, command, services
            if operation.value == "cancel":
                return DriverOutcome(
                    status=ExecutionStatus.CANCELLED,
                    cancellation_outcome="confirmed",
                )
            return DriverOutcome.running()

    runtime, reservations, _journal, _work = _runtime(
        tmp_path / "cancel-confirmed.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-cancel-confirmed",
            fingerprint_version=1,
            fingerprint="f" * 64,
            command=_command(),
            transport="http",
        )
    )
    revision = reservations.get(
        owner="alice", execution_id="turn-cancel-confirmed"
    ).supervisor_revision
    outcome = asyncio.run(
        runtime.cancel(
            owner="alice",
            execution_id="turn-cancel-confirmed",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={"reason": "user_requested"},
                action_id="cancel-confirmed-1",
                expected_revision=revision,
            ),
            transport="http",
        )
    )
    record = reservations.get(
        owner="alice", execution_id="turn-cancel-confirmed"
    )
    assert outcome.status.value == "cancelled"
    assert record.status.value == "cancelled"
    assert record.cancellation_state == "confirmed"


def test_reconcile_schedules_retry_and_tracks_degradation_recovery(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_journal_v2 import TrackingHealth
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del context, command, services
            if operation.value == "reconcile":
                return DriverOutcome.running(
                    retry_after_ms=250,
                    tracking_health=TrackingHealth.DEGRADED,
                )
            if operation.value == "recover":
                return DriverOutcome.running()
            return DriverOutcome.running()

    runtime, reservations, journal, _work = _runtime(
        tmp_path / "retry.db", Driver()
    )
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-retry",
            fingerprint_version=1,
            fingerprint="f" * 64,
            command=_command(),
            transport="http",
        )
    )
    revision = reservations.get(
        owner="alice", execution_id="turn-retry"
    ).supervisor_revision
    retry = asyncio.run(
        runtime.reconcile(
            owner="alice",
            execution_id="turn-retry",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={},
                action_id="reconcile-1",
                expected_revision=revision,
            ),
        )
    )
    record = reservations.get(owner="alice", execution_id="turn-retry")
    assert retry.retry_after_ms == 250
    assert record.next_attempt_at is not None
    assert record.tracking_health == "degraded"

    recovered = asyncio.run(
        runtime.recover(
            owner="alice",
            execution_id="turn-retry",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={},
                action_id="recover-1",
                expected_revision=record.supervisor_revision,
            ),
        )
    )
    assert recovered.status.value == "running"
    assert (
        reservations.get(
            owner="alice", execution_id="turn-retry"
        ).tracking_health
        == "healthy"
    )
    page = journal.list_events("turn-retry", owner="alice", limit=40)
    assert page is not None
    types = [event.type.value for event in page.items]
    assert "span.retry_scheduled" in types
    assert "tracking.degraded" in types
    assert "tracking.recovered" in types


def test_recovery_enforces_deadline_and_partial_is_terminal(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionCommand,
    )

    class Driver:
        def __init__(self) -> None:
            self.recover_calls = 0

        async def execute(self, operation, context, command, services):
            del context, command, services
            if operation.value == "recover":
                self.recover_calls += 1
            return DriverOutcome.running()

    driver = Driver()
    db_path = tmp_path / "deadline.db"
    runtime, reservations, journal, _work = _runtime(db_path, driver)
    asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-deadline",
            fingerprint_version=1,
            fingerprint="1" * 64,
            command=_command(),
            transport="http",
        )
    )
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE runs SET execution_deadline_at = ? WHERE execution_id = ?",
            (past, "turn-deadline"),
        )
        connection.commit()
    record = reservations.get(owner="alice", execution_id="turn-deadline")
    outcome = asyncio.run(
        runtime.recover(
            owner="alice",
            execution_id="turn-deadline",
            command=ExecutionCommand(
                agent_slug="knowledge",
                arguments={},
                action_id="recover-deadline",
                expected_revision=record.supervisor_revision,
            ),
        )
    )

    assert outcome.status.value == "timed_out"
    assert driver.recover_calls == 0
    assert (
        reservations.get(
            owner="alice", execution_id="turn-deadline"
        ).status.value
        == "timed_out"
    )
    page = journal.list_events("turn-deadline", owner="alice", limit=30)
    assert page is not None
    assert [event.type.value for event in page.items][-2:] == [
        "span.timed_out",
        "execution.timed_out",
    ]


def test_partial_driver_outcome_settles_root_and_execution(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        DriverOutcome,
        ExecutionStatus,
    )

    class Driver:
        async def execute(self, operation, context, command, services):
            del operation, context, command, services
            return DriverOutcome(status=ExecutionStatus.PARTIAL)

    runtime, reservations, journal, work = _runtime(
        tmp_path / "partial.db", Driver()
    )
    outcome = asyncio.run(
        runtime.start(
            owner="alice",
            execution_id="turn-partial",
            fingerprint_version=1,
            fingerprint="2" * 64,
            command=_command(),
            transport="http",
        )
    )

    record = reservations.get(owner="alice", execution_id="turn-partial")
    assert outcome.status.value == "partial"
    assert record.status.value == "partial"
    assert (
        work.get_span(
            "turn-partial", record.root_span_id, owner="alice"
        ).status.value
        == "partial"
    )
    page = journal.list_events("turn-partial", owner="alice", limit=20)
    assert page is not None
    assert [event.type.value for event in page.items][-2:] == [
        "span.partial",
        "execution.partial",
    ]
