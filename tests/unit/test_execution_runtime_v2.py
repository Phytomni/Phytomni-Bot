# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical ExecutionRuntime start, dispatch, and settlement behavior."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from tests.support.execution_runtime_v2 import (
    ExecutionRuntimeOverrides,
    build_execution_runtime_stack,
    todo_snapshots,
)
from tests.support.execution_supervisor_v2 import reserve_test_execution

from mcp_server_phytomni.public_agent_catalog import public_agent_spec
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    advance_execution_todo,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ExecutionStatus,
    MessagePublicPayload,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    EXPERT_ROUTER_AGENT_SLUG,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOutcome,
    ExecutionArtifactRef,
    ExecutionCommand,
    ExecutionContext,
    TransportNeutralResult,
)
from mcp_server_phytomni.runtime.execution_target_store_v2 import (
    ExecutionTargetBindingConflictError,
    ExecutionTargetBindingV2,
    SQLiteExecutionTargetStore,
)


def test_runtime_persists_private_artifact_delivery_binding(
    tmp_path: Path,
) -> None:
    """A public target is replayable without putting its OBS ref in events."""

    @dataclass(eq=False)
    class Driver:
        """Driver returning an artifact with a private delivery binding."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
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
    runtime, _reservations, journal, _work = build_execution_runtime_stack(
        db_path,
        Driver(),
        overrides=ExecutionRuntimeOverrides(target_store=target_store),
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

    artifact = ExecutionArtifactRef(
        role="scientific_report",
        target_kind="artifact",
        target_id="artifact-stable-report",
        name="report.md",
        media_type="text/markdown",
        size_bytes=12,
    )

    @dataclass(eq=False)
    class Driver:
        """Driver keeping an artifact target stable across reconciliation."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            return DriverOutcome(
                status=ExecutionStatus.RUNNING,
                result=TransportNeutralResult(artifacts=(artifact,)),
            )

    runtime, reservations, journal, _work = build_execution_runtime_stack(
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

    return ExecutionCommand(agent_slug=agent_slug, arguments={"query": "rice"})


def test_runtime_start_binds_context_root_span_and_settles_once(
    tmp_path: Path,
) -> None:
    """Verify runtime start binds context root span and settles once."""

    @dataclass(eq=False)
    class Driver:
        """Driver that captures context and settles successfully."""

        def __init__(self) -> None:
            self.calls = 0
            self.contexts: list[ExecutionContext] = []

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, command, services
            self.calls += 1
            self.contexts.append(context)
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="unchanged business answer")
            )

    driver = Driver()
    runtime, reservations, journal, work = build_execution_runtime_stack(
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
    """Verify every public agent runtime keeps todo undeclared without agent
    fact."""

    @dataclass(eq=False)
    class Driver:
        """Shared agent driver that declares no todo plan."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    spec = public_agent_spec(agent_slug)
    assert spec is not None
    runtime, _reservations, journal, _work = build_execution_runtime_stack(
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
    """Verify runtime keeps todo undeclared without agent plan fact."""

    @dataclass(eq=False)
    class Driver:
        """Driver that succeeds without declaring a todo plan."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    runtime, _reservations, journal, _work = build_execution_runtime_stack(
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
    """Verify runtime closes only an agent declared todo plan."""

    @dataclass(eq=False)
    class Driver:
        """Driver that declares and advances a runtime todo plan."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            advance_execution_todo("retrieving", completed=False)
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="done")
            )

    runtime, _reservations, journal, _work = build_execution_runtime_stack(
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
    snapshots = todo_snapshots(page.items)
    assert len(snapshots) == 2
    assert [item.id for item in snapshots[0]] == [
        item.id for item in snapshots[1]
    ]
    assert snapshots[0][0].status == "in_progress"
    assert {item.status for item in snapshots[1]} == {"completed"}


def test_runtime_normalizes_unhandled_driver_error_without_leaking_text(
    tmp_path: Path,
) -> None:
    """Verify runtime normalizes unhandled driver error without leaking
    text."""

    @dataclass(eq=False)
    class Driver:
        """Driver raising a private-text error for normalization."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            raise RuntimeError("password=private-marker")

    runtime, reservations, journal, _work = build_execution_runtime_stack(
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
    """Verify runtime publishes bounded message and opaque result targets."""

    @dataclass(eq=False)
    class Driver:
        """Driver publishing bounded messages and opaque targets."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
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

    runtime, _reservations, journal, _work = build_execution_runtime_stack(
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
    """Verify runtime message facts reconstruct large answer with stable
    identity."""

    answer = "A" * 8192 + "B" * 8192 + "tail"

    @dataclass(eq=False)
    class Driver:
        """Driver returning a large answer for reconstruction."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer=answer)
            )

    runtime, _reservations, journal, _work = build_execution_runtime_stack(
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

    @dataclass(eq=False)
    class Driver:
        """Driver reusing the admitted assistant-message identity."""

        async def execute(self, operation, context, command, services):
            """Return this test driver's configured outcome."""
            del operation, context, command, services
            return DriverOutcome.succeeded(
                TransportNeutralResult(answer="routed answer")
            )

    runtime, reservations, journal, _work = build_execution_runtime_stack(
        tmp_path / "routed-message-identity.db", Driver()
    )
    execution_id = "turn-routed-message-identity"
    fingerprint = "7" * 64
    admitted_arguments = {
        "__query": "rice",
        "__assistant_message_id": "msg-web-assistant-1",
        "__user_message_id": "msg-web-user-1",
    }
    reserve_test_execution(
        reservations,
        execution_id=execution_id,
        agent_slug=EXPERT_ROUTER_AGENT_SLUG,
        fingerprint=fingerprint,
        arguments=admitted_arguments,
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
    """Verify artifact reference rejects paths and direct URLs."""

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
