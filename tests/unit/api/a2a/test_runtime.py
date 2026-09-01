# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Direct contract tests for the owner-scoped A2A runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.api.a2a import runtime
from mcp_server_phytomni.api.a2a.executor import A2ARegistration
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    invoke_public_agent_operation,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import (
    ExecutionStatus,
    SpanStatus,
)
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.execution_runtime_contracts import (
    DriverOutcome,
    ExecutionCommand,
    TerminalSettlementAuthority,
)
from mcp_server_phytomni.runtime.execution_work_store_v2 import (
    SpanSpec,
    SQLiteExecutionWorkRepository,
)
from mcp_server_phytomni.runtime.run_registry import (
    A2ACorrelation,
    RunRegistry,
    RunRequestInfo,
)

pytestmark = pytest.mark.unit


def _request_info() -> RunRequestInfo:
    """Return one A2A request containing a protocol-visible message."""
    return RunRequestInfo(
        request_json=(
            '{"message":{"messageId":"m1","contextId":"ctx",'
            '"role":"ROLE_USER","parts":[{"text":"hello"}]}}'
        )
    )


@dataclass(frozen=True, slots=True)
class _RunOptions:
    """Optional identity and outcome fields for one fixture row."""

    owner: str = "alice"
    agent: str = "chat"
    status: str = "input_required"
    result: dict[str, Any] | None = None
    task_id: str = "task-1"
    context_id: str = "ctx-1"


def _create_run(
    db_path: str,
    *,
    options: _RunOptions | None = None,
) -> None:
    """Create one Runtime V2 row with stable A2A correlation ids."""
    options = options or _RunOptions()
    run_id = f"run-{options.task_id}"
    execution_id = f"turn-{options.task_id}"
    reservations = SQLiteExecutionReservationRepository(
        db_path,
        run_id_factory=lambda: run_id,
    )
    reservations.reserve(
        owner=options.owner,
        execution_id=execution_id,
        fingerprint_version=1,
        fingerprint=f"fixture:{options.task_id}",
        command=ExecutionCommand(agent_slug=options.agent, arguments={}),
    )
    status = {
        "input_required": ExecutionStatus.WAITING_INPUT,
        "waiting_input": ExecutionStatus.WAITING_INPUT,
        "running": ExecutionStatus.RUNNING,
        "succeeded": ExecutionStatus.RUNNING,
    }[options.status]
    reservations.record_observation(
        owner=options.owner,
        execution_id=execution_id,
        status=status,
        tracking_health="healthy",
        cancellation_state="unsupported",
        next_attempt_at=None,
    )
    reserved = reservations.get(
        owner=options.owner,
        execution_id=execution_id,
    )
    work = SQLiteExecutionWorkRepository(db_path)
    span = work.create_span(
        SpanSpec(
            owner=options.owner,
            execution_id=execution_id,
            span_id=reserved.root_span_id,
            kind="agent",
            label_key=f"agent.{options.agent}",
        )
    )
    work.update_span_status(
        execution_id,
        reserved.root_span_id,
        owner=options.owner,
        status=(
            SpanStatus.WAITING_INPUT
            if status is ExecutionStatus.WAITING_INPUT
            else SpanStatus.RUNNING
        ),
        expected_revision=span.revision,
    )
    registry = RunRegistry(db_path)
    registry.update_request_info(
        run_id,
        owner=options.owner,
        request_info=replace(_request_info(), execution_id=execution_id),
    )
    registry.update_a2a_correlation(
        run_id,
        owner=options.owner,
        correlation=A2ACorrelation(
            task_id=options.task_id,
            context_id=options.context_id,
        ),
    )
    if options.result is not None:
        assert registry.update_active_result(
            run_id,
            owner=options.owner,
            result=options.result,
        )
    if options.status == "succeeded":
        reserved = reservations.get(
            owner=options.owner,
            execution_id=execution_id,
        )
        assert reservations.settle_terminal(
            TerminalSettlementAuthority(
                owner_ref=options.owner,
                execution_id=execution_id,
                expected_revision=reserved.supervisor_revision,
                actor="runtime",
                issued_at=datetime.now(UTC),
            ),
            DriverOutcome(status=ExecutionStatus.SUCCEEDED),
        )


async def _resume_through_runtime(
    db_path: str,
    task_id: str,
    context_id: str,
    arguments: dict[str, Any],
    *,
    dependencies: runtime.A2AResumeDependencies,
) -> tuple[dict[str, Any], int] | None:
    """Exercise the same Runtime operation boundary as the public route."""
    owner = dependencies.registry.current_user() or "anonymous"
    registry = dependencies.registry.registry_factory(db_path)
    record = registry.get_run_by_a2a_task(task_id, owner=owner)
    assert record is not None and record.request_info.execution_id is not None
    execution_id = record.request_info.execution_id
    reservation = SQLiteExecutionReservationRepository(db_path).get(
        owner=owner,
        execution_id=execution_id,
    )

    async def call() -> tuple[dict[str, Any], int] | None:
        return await runtime.resume_task(
            task_id,
            context_id,
            arguments,
            dependencies=dependencies,
        )

    return await invoke_public_agent_operation(
        db_path=db_path,
        owner=owner,
        execution_id=execution_id,
        agent_slug=record.spec.agent,
        operation="resume",
        action_id=f"test:{task_id}:{arguments.get('generation')}",
        expected_revision=reservation.supervisor_revision,
        arguments=arguments,
        transport="a2a_test",
        call=call,
    )


def _new_graph_marker() -> object:
    """Return a fresh graph marker for dependency wiring tests."""
    return object()


def _dependencies(
    db_path: str,
    *,
    owner: str = "alice",
    resume_result: dict[str, Any] | None = None,
) -> tuple[runtime.A2AResumeDependencies, list[dict[str, Any]]]:
    """Build explicit runtime seams and capture the resume call."""
    registry_deps = runtime.A2ARegistryDependencies(
        registry_factory=RunRegistry,
        current_user=lambda: owner,
        tasks_db_path=lambda: db_path,
    )
    calls: list[dict[str, Any]] = []

    async def resume_graph(
        _graph: object,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        calls.append({"run_id": run_id, **payload})
        return resume_result or {"response": "done"}

    def format_chat(
        _state: Mapping[str, Any],
        *,
        prior_surface: Mapping[str, Any],
        resume_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "formatted": {"answer": "done"},
            "raw": {"secret": "must not cross"},
            "prior_surface": dict(prior_surface),
            "payload": dict(resume_payload),
        }

    async def format_review(_state: Mapping[str, Any]) -> dict[str, Any]:
        return {"formatted": {"answer": "reviewed"}, "raw": None}

    dependencies = runtime.A2AResumeDependencies(
        registry=registry_deps,
        graphs=runtime.A2AGraphDependencies(
            chat_graph=_new_graph_marker,
            review_graph=_new_graph_marker,
            resume_graph=resume_graph,
        ),
        projection=runtime.A2AProjectionDependencies(
            resume_payload=runtime.resume_payload,
            interrupts=runtime.A2AInterruptDependencies(
                chat_interrupt_result=lambda interrupt: {
                    "interrupt": dict(interrupt),
                    "status": "input_required",
                },
                review_interrupt_result=lambda interrupt: {
                    "interrupt": dict(interrupt),
                    "status": "input_required",
                },
                project_review_interrupt=lambda interrupt: {
                    **dict(interrupt),
                    "projected": True,
                },
                chat_interrupt_body=lambda **kwargs: {
                    "status": "input_required",
                    **kwargs,
                },
                review_interrupt_body=lambda **kwargs: {
                    "status": "input_required",
                    **kwargs,
                },
            ),
            format_chat_result=format_chat,
            format_review_result=format_review,
        ),
    )
    return dependencies, calls


def test_registration_updates_only_runtime_reserved_rows(
    tmp_path: Path,
) -> None:
    """A2A correlation cannot create a second lifecycle row."""
    db_path = str(tmp_path / "a2a-runtime.sqlite")
    _create_run(
        db_path,
        options=_RunOptions(status="running", task_id="existing"),
    )
    dependencies = runtime.A2ARegistryDependencies(
        registry_factory=RunRegistry,
        current_user=lambda: "alice",
        tasks_db_path=lambda: db_path,
    )
    request_info = _request_info()
    runtime.record_registration(
        A2ARegistration(
            "run-existing",
            "chat",
            A2ACorrelation(task_id="existing", context_id="ctx-2"),
            request_info,
        ),
        dependencies=dependencies,
    )
    runtime.record_registration(
        A2ARegistration(
            "stream-run",
            "review",
            A2ACorrelation(task_id="stream-task", context_id="stream-ctx"),
            request_info,
        ),
        dependencies=dependencies,
    )

    registry = RunRegistry(db_path)
    existing = registry.get_run("run-existing", owner="alice")
    streamed = registry.get_run("stream-run", owner="alice")
    assert existing is not None and existing.a2a.context_id == "ctx-2"
    assert streamed is None


def test_get_task_is_owner_scoped_bounded_and_hides_raw_payload(
    tmp_path: Path,
) -> None:
    """Task lookup preserves safe artifacts and refuses foreign rows."""
    db_path = str(tmp_path / "a2a-task.sqlite")
    safe_result: dict[str, Any] = {
        "formatted": {"answer": "done"},
        "raw": {"secret": "must not cross"},
    }
    safe_result["formatted"] = {
        **safe_result["formatted"],
        "metadata": {"safe": True},
    }
    _create_run(
        db_path,
        options=_RunOptions(result=safe_result),
    )
    dependencies = runtime.A2ARegistryDependencies(
        registry_factory=RunRegistry,
        current_user=lambda: "alice",
        tasks_db_path=lambda: db_path,
    )

    task = runtime.get_task("task-1", 1, dependencies=dependencies)
    assert task is not None
    assert task.id == "task-1"
    assert len(task.history) == 1
    assert "secret" not in str(task)

    foreign = runtime.get_task(
        "task-1",
        1,
        dependencies=runtime.A2ARegistryDependencies(
            registry_factory=RunRegistry,
            current_user=lambda: "bob",
            tasks_db_path=lambda: db_path,
        ),
    )
    assert foreign is None


@pytest.mark.parametrize(
    ("agent", "arguments", "expected"),
    [
        (
            "review",
            {"approved": True, "edits": "keep"},
            {"approved": True, "edits": "keep"},
        ),
        (
            "chat",
            {"accepted": False},
            {"widget": "confirm", "accepted": False},
        ),
        (
            "chat",
            {"fields": {"x": "y"}},
            {"widget": "form", "fields": {"x": "y"}},
        ),
        ("chat", {"selected": "yes"}, {"widget": "choice", "selected": "yes"}),
        (
            "chat",
            {"cancelled": True, "widget": "confirm"},
            {"widget": "confirm", "cancelled": True},
        ),
    ],
)
def test_resume_payload_maps_supported_a2a_inputs(
    agent: str,
    arguments: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """Chat widgets and Review approval use the shared payload shapes."""
    assert runtime.resume_payload(agent, arguments) == expected


@pytest.mark.parametrize(
    ("agent", "arguments", "message"),
    [
        ("review", {}, "boolean approved"),
        ("chat", {}, "boolean approved"),
        ("other", {"approved": True}, "only for chat and review"),
    ],
)
def test_resume_payload_rejects_unsupported_inputs(
    agent: str,
    arguments: dict[str, Any],
    message: str,
) -> None:
    """Malformed or unsupported payloads fail before graph execution."""
    with pytest.raises(ValueError, match=message):
        runtime.resume_payload(agent, arguments)


@pytest.mark.asyncio
async def test_resume_missing_foreign_terminal_and_context_are_rejected(
    tmp_path: Path,
) -> None:
    """Resume validation covers lookup, ownership, state, and context."""
    db_path = str(tmp_path / "a2a-validation.sqlite")
    dependencies, calls = _dependencies(db_path)
    assert (
        await runtime.resume_task(
            "missing",
            "ctx-1",
            {"generation": 0, "accepted": True},
            dependencies=dependencies,
        )
        is None
    )
    _create_run(
        db_path,
        options=_RunOptions(owner="bob", task_id="foreign"),
    )
    assert (
        await runtime.resume_task(
            "foreign",
            "ctx-1",
            {"generation": 0, "accepted": True},
            dependencies=dependencies,
        )
        is None
    )
    _create_run(
        db_path,
        options=_RunOptions(status="succeeded", task_id="terminal"),
    )
    with pytest.raises(ValueError, match="not awaiting input"):
        await runtime.resume_task(
            "terminal",
            "ctx-1",
            {"generation": 0, "accepted": True},
            dependencies=dependencies,
        )
    _create_run(db_path)
    with pytest.raises(ValueError, match="context_id"):
        await runtime.resume_task(
            "task-1",
            "wrong",
            {"generation": 0, "accepted": True},
            dependencies=dependencies,
        )
    assert not calls


@pytest.mark.asyncio
async def test_resume_cancellation_settles_terminal_result_and_strips_raw(
    tmp_path: Path,
) -> None:
    """A cancelled Chat uplink reaches the graph and stores a safe result."""
    db_path = str(tmp_path / "a2a-cancel.sqlite")
    _create_run(
        db_path,
        options=_RunOptions(
            result={
                "interrupt": {"draft": {"a2ui": {"surface_id": "surface-1"}}},
                "generation": 0,
            }
        ),
    )
    dependencies, calls = _dependencies(db_path)

    response = await _resume_through_runtime(
        db_path,
        "task-1",
        "ctx-1",
        {"generation": 0, "cancelled": True, "widget": "confirm"},
        dependencies=dependencies,
    )
    assert response is not None
    body, status_code = response
    assert status_code == 200
    assert body["status"] == "succeeded"
    assert body["result"]["formatted"]["answer"] == "done"
    assert "raw" not in body["result"]
    assert calls == [
        {
            "run_id": "run-task-1",
            "widget": "confirm",
            "cancelled": True,
        }
    ]
    record = RunRegistry(db_path).get_run("run-task-1", owner="alice")
    assert record is not None and record.status == "succeeded"
    assert record.result is not None
    assert "raw" not in record.result


@pytest.mark.asyncio
async def test_resume_reinterrupt_increments_generation(
    tmp_path: Path,
) -> None:
    """A second pause is projected with a new generation and status."""
    db_path = str(tmp_path / "a2a-reinterrupt.sqlite")
    _create_run(
        db_path,
        options=_RunOptions(
            agent="review",
            result={
                "interrupt": {"draft": {"summary": "first"}},
                "generation": 2,
            },
            task_id="review-task",
            context_id="review-ctx",
        ),
    )
    dependencies, calls = _dependencies(
        db_path,
        resume_result={
            "__interrupt__": [SimpleNamespace(value={"summary": "next"})]
        },
    )

    response = await _resume_through_runtime(
        db_path,
        "review-task",
        "review-ctx",
        {"generation": 2, "approved": True},
        dependencies=dependencies,
    )
    assert response == (
        {
            "status": "input_required",
            "thread_id": "run-review-task",
            "interrupt": {
                "thread_id": "run-review-task",
                "draft": {"summary": "next"},
                "projected": True,
            },
            "generation": 3,
        },
        200,
    )
    assert calls == [
        {"run_id": "run-review-task", "approved": True, "edits": None}
    ]
    record = RunRegistry(db_path).get_run("run-review-task", owner="alice")
    assert record is not None and record.status == "waiting_input"
    assert record.result is not None and record.result["generation"] == 3


@pytest.mark.asyncio
async def test_resume_backend_error_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    """Backend failures propagate without settling the pause as succeeded."""
    db_path = str(tmp_path / "a2a-error.sqlite")
    _create_run(db_path)
    dependencies, _calls = _dependencies(db_path)

    async def fail_resume(
        _graph: object,
        _run_id: str,
        _payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError("backend secret")

    dependencies = replace(
        dependencies,
        graphs=replace(dependencies.graphs, resume_graph=fail_resume),
    )
    with pytest.raises(RuntimeError, match="backend secret"):
        await _resume_through_runtime(
            db_path,
            "task-1",
            "ctx-1",
            {"generation": 0, "accepted": True},
            dependencies=dependencies,
        )
    record = RunRegistry(db_path).get_run("run-task-1", owner="alice")
    assert record is not None and record.status == "failed"
