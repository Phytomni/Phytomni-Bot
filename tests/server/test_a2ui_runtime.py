# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical Runtime V2 contracts for A2UI pause and resume."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from mcp_server_phytomni.agents.shared.a2ui import (
    A2UI_CATALOG_VERSION,
    A2UI_MAX_ROUNDS,
    should_reenter_a2ui,
)
from mcp_server_phytomni.api import (
    a2ui_projection,
    a2ui_resume,
    a2ui_runtime,
)
from mcp_server_phytomni.api.schemas import A2uiActionRequest
from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
    invoke_public_agent,
    invoke_public_agent_operation,
)
from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
    current_execution_boundary,
)
from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_journal_v2 import ExecutionStatus
from mcp_server_phytomni.runtime.execution_reservation_v2 import (
    ExecutionReservationConflictError,
    SQLiteExecutionReservationRepository,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunRequestInfo,
    RunSpec,
)

pytestmark = pytest.mark.server


def _surface(surface_id: str, widget: str = "confirm") -> dict[str, Any]:
    return {
        "catalog_version": A2UI_CATALOG_VERSION,
        "surface_id": surface_id,
        "widget": widget,
        "props": {"title": "Test", "body": "Continue?"},
    }


def _action(run_id: str, *, action_id: str = "action-1") -> A2uiActionRequest:
    return A2uiActionRequest(
        run_id=run_id,
        surface_id="surface-1",
        widget="confirm",
        action_id=action_id,
        payload={"accepted": True},
    )


def _empty_graph() -> Any:
    return SimpleNamespace()


def _empty_state(_arguments: Any) -> dict[str, Any]:
    return {}


async def _project_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
    if _args or _kwargs:
        yield cast(Any, None)


def _dependencies(
    db_path: str,
    resume_graph: Any,
    *,
    checkpoint_present: bool = True,
) -> a2ui_runtime.A2UIRuntimeDependencies:
    async def has_checkpoint(_app: Any, _thread_id: str) -> bool:
        return checkpoint_present

    def format_review(_state: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"formatted": {"answer": "review"}, "raw": None}

    return a2ui_runtime.A2UIRuntimeDependencies(
        graphs=a2ui_runtime.A2UIGraphDependencies(
            chat_graph=_empty_graph,
            chat_initial_state=_empty_state,
            review_graph=_empty_graph,
            review_initial_state=_empty_state,
            validate_review=a2ui_runtime.validate_review_arguments,
            has_checkpoint=has_checkpoint,
            resume_graph=cast(Any, resume_graph),
        ),
        persistence=a2ui_runtime.A2UIPersistenceDependencies(
            registry_factory=RunRegistry,
            current_user=lambda: "alice",
            current_request_id=lambda: "runtime-test-request",
            tasks_db_path=lambda: db_path,
            format_review_result=format_review,
        ),
        stream=a2ui_runtime.A2UIStreamDependencies(
            stream_setup_error=lambda _exc, *, priming: HTTPException(
                status_code=500,
                detail=f"stream setup failed: {priming}",
            ),
            failed_stream_result=lambda: {"formatted": {"answer": ""}},
            project_stream=_project_stream,
        ),
    )


async def _seed_pause(
    db_path: str,
    *,
    run_id: str,
    execution_id: str,
    agent: str,
) -> None:
    """Create a pause through Runtime instead of a compatibility writer."""

    async def pause() -> tuple[dict[str, object], int]:
        boundary = current_execution_boundary(required=True)
        assert boundary is not None
        registry = RunRegistry(db_path)
        assert registry.update_request_info(
            run_id,
            owner="alice",
            request_info=RunRequestInfo(execution_id=execution_id),
        )
        assert registry.update_active_result(
            run_id,
            owner="alice",
            result={
                "interrupt": {
                    "thread_id": run_id,
                    "draft": {"a2ui": _surface("surface-1")},
                },
                "status": "input_required",
            },
        )
        return {"status": "input_required"}, 200

    await invoke_public_agent(
        db_path=db_path,
        owner="alice",
        execution_id=execution_id,
        agent_slug=agent,
        arguments={"query": "pause"},
        transport="a2ui_test",
        call=pause,
        run_id=run_id,
        status_mapper=lambda _value: ExecutionStatus.WAITING_INPUT,
    )


async def _resume(
    db_path: str,
    *,
    run_id: str,
    execution_id: str,
    agent: str,
    body: A2uiActionRequest,
    dependencies: a2ui_runtime.A2UIRuntimeDependencies,
) -> tuple[dict[str, Any], int]:
    reservation = SQLiteExecutionReservationRepository(db_path).get(
        owner="alice",
        execution_id=execution_id,
    )

    async def domain() -> tuple[dict[str, Any], int]:
        return await a2ui_resume.resume_a2ui_run(
            run_id=run_id,
            body=body,
            debug=False,
            dependencies=dependencies,
        )

    return await invoke_public_agent_operation(
        db_path=db_path,
        owner="alice",
        execution_id=execution_id,
        agent_slug=agent,
        operation="resume",
        action_id=body.action_id,
        expected_revision=reservation.supervisor_revision,
        arguments=body.model_dump(mode="json"),
        transport="a2ui_test",
        call=domain,
    )


def test_projection_helpers_remain_runtime_facade_exports() -> None:
    for name in a2ui_projection.__all__:
        assert getattr(a2ui_runtime, name) is getattr(a2ui_projection, name)


def test_submission_mapping_and_round_cap() -> None:
    submitted = a2ui_runtime.submitted_a2ui_value(
        _surface("s1"),
        {"accepted": True},
    )
    assert submitted["props"]["accepted"] is True
    assert submitted["props"]["status"] == "submitted"
    assert (
        should_reenter_a2ui(
            text="please confirm", a2ui_round=A2UI_MAX_ROUNDS - 1
        )
        is True
    )
    assert (
        should_reenter_a2ui(text="please confirm", a2ui_round=A2UI_MAX_ROUNDS)
        is False
    )


async def test_chat_terminal_resume_uses_one_runtime_and_redacts_raw(
    tmp_path: Any,
) -> None:
    db_path = str(tmp_path / "chat-resume.db")
    run_id = "run-chat-resume"
    execution_id = "turn-chat-resume"
    await _seed_pause(
        db_path,
        run_id=run_id,
        execution_id=execution_id,
        agent="chat",
    )

    async def resume_graph(
        _graph: Any, _run_id: str, _payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "response": {
                "choices": [
                    {
                        "message": {
                            "content": "done",
                            "follow_up_questions": [],
                        }
                    }
                ]
            }
        }

    body, status = await _resume(
        db_path,
        run_id=run_id,
        execution_id=execution_id,
        agent="chat",
        body=_action(run_id),
        dependencies=_dependencies(db_path, resume_graph),
    )
    assert status == 200 and body["status"] == "succeeded"
    record = RunRegistry(db_path).get_run(run_id, owner="alice")
    assert record is not None and record.status == "succeeded"
    assert record.request_info.execution_id == execution_id
    assert (
        RunRegistry(db_path).list_a2ui_actions(owner="alice", run_id=run_id)
        == []
    )
    events = SQLiteExecutionJournal(db_path).list_events(
        execution_id, owner="alice", limit=100
    )
    assert events is not None
    assert [event.type.value for event in events.items].count(
        "execution.succeeded"
    ) == 1


async def test_review_reinterrupt_stays_on_same_execution(
    tmp_path: Any,
) -> None:
    db_path = str(tmp_path / "review-reinterrupt.db")
    run_id = "run-review-reinterrupt"
    execution_id = "turn-review-reinterrupt"
    await _seed_pause(
        db_path,
        run_id=run_id,
        execution_id=execution_id,
        agent="review",
    )

    async def resume_graph(
        _graph: Any, _run_id: str, _payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {"__interrupt__": [{"summary": "review again"}]}

    body, status = await _resume(
        db_path,
        run_id=run_id,
        execution_id=execution_id,
        agent="review",
        body=_action(run_id),
        dependencies=_dependencies(db_path, resume_graph),
    )
    assert status == 200 and body["status"] == "input_required"
    record = RunRegistry(db_path).get_run(run_id, owner="alice")
    assert record is not None and record.status == "waiting_input"
    assert record.request_info.execution_id == execution_id


async def test_duplicate_resume_cannot_reenter_business_graph(
    tmp_path: Any,
) -> None:
    db_path = str(tmp_path / "duplicate-resume.db")
    run_id = "run-duplicate-resume"
    execution_id = "turn-duplicate-resume"
    await _seed_pause(
        db_path,
        run_id=run_id,
        execution_id=execution_id,
        agent="chat",
    )
    calls = 0

    async def resume_graph(
        _graph: Any, _run_id: str, _payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"response": "done"}

    dependencies = _dependencies(db_path, resume_graph)
    action = _action(run_id)
    await _resume(
        db_path,
        run_id=run_id,
        execution_id=execution_id,
        agent="chat",
        body=action,
        dependencies=dependencies,
    )
    with pytest.raises(ExecutionReservationConflictError):
        await _resume(
            db_path,
            run_id=run_id,
            execution_id=execution_id,
            agent="chat",
            body=action,
            dependencies=dependencies,
        )
    assert calls == 1


async def test_legacy_a2ui_rows_are_read_only(tmp_path: Any) -> None:
    db_path = str(tmp_path / "legacy-read-only.db")
    run_id = "legacy-run"
    RunRegistry(db_path).create_run(
        RunSpec(run_id, "alice", "chat", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={"interrupt": {"draft": {"a2ui": _surface("surface-1")}}},
        ),
    )

    async def resume_graph(*_args: Any) -> dict[str, Any]:
        raise AssertionError("legacy route must not execute business logic")

    with pytest.raises(HTTPException) as exc:
        await a2ui_resume.resume_a2ui_run(
            run_id=run_id,
            body=_action(run_id),
            debug=False,
            dependencies=_dependencies(db_path, resume_graph),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "legacy A2UI execution is read-only"


def test_review_run_body_shapes_interrupt_and_debug() -> None:
    interrupt_body = a2ui_resume.review_run_body(
        a2ui_resume.ReviewExecution(
            run_id="r1",
            status="input_required",
            interrupt={"draft": {"a2ui": _surface("s1")}},
        ),
        debug=False,
    )
    assert interrupt_body["status"] == "input_required"
    debug_body = a2ui_resume.review_run_body(
        a2ui_resume.ReviewExecution(
            run_id="r2",
            status="succeeded",
            result={"formatted": {"answer": "ok"}, "raw": {"secret": 1}},
        ),
        debug=True,
    )
    assert debug_body["result"]["raw"] == {"secret": 1}
