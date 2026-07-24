# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Direct contracts for the extracted A2UI pause/resume runtime."""

from __future__ import annotations

import asyncio
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
from mcp_server_phytomni.api import a2ui_projection, a2ui_runtime
from mcp_server_phytomni.api.schemas import A2uiActionRequest
from mcp_server_phytomni.mcp.result_formatting import run_finished
from mcp_server_phytomni.mcp.stream_lifecycle import (
    StreamLifecycleState,
    project_terminal_settlement,
)
from mcp_server_phytomni.runtime.resume import NoCheckpointError
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)

pytestmark = pytest.mark.server


def _surface(surface_id: str, widget: str = "confirm") -> dict[str, Any]:
    """Build a minimal A2UI surface."""
    return {
        "catalog_version": A2UI_CATALOG_VERSION,
        "surface_id": surface_id,
        "widget": widget,
        "props": {"title": "Test", "body": "Continue?"},
    }


def _seed(db_path: str, run_id: str, status: str = "input_required") -> None:
    """Seed one owner-scoped Chat pause."""
    RunRegistry(db_path).create_run(
        RunSpec(run_id, "alice", "chat", "local"),
        outcome=RunOutcome(
            status=status,
            result={
                "interrupt": {
                    "thread_id": run_id,
                    "draft": {"a2ui": _surface("surface-1")},
                },
                "status": "input_required",
            },
        ),
    )


def _action(run_id: str) -> A2uiActionRequest:
    """Build a confirm action."""
    return A2uiActionRequest(
        run_id=run_id,
        surface_id="surface-1",
        widget="confirm",
        action_id="action-1",
        payload={"accepted": True},
    )


def _empty_graph() -> Any:
    return SimpleNamespace()


def _empty_state(_arguments: Any) -> dict[str, Any]:
    return {}


def _noop_create_stream_run(*_args: Any) -> None:
    return None


def _successful_stream_settlement(*_args: Any) -> bool:
    return True


def _stream_setup_error(_exc: Exception, *, priming: bool) -> HTTPException:
    del priming
    return HTTPException(status_code=500, detail="stream setup failed")


def _failed_stream_result() -> dict[str, Any]:
    return {"formatted": {"answer": ""}, "raw": None}


async def _project_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
    if _args or _kwargs:
        yield cast(Any, None)


def test_projection_helpers_remain_runtime_facade_exports() -> None:
    """Keep the historical runtime import path as a compatibility facade."""
    for name in a2ui_projection.__all__:
        assert getattr(a2ui_runtime, name) is getattr(a2ui_projection, name)


async def test_none_terminal_settlement_fails_closed() -> None:
    """A legacy ``None`` callback result is not durable success."""

    async def events() -> AsyncIterator[Any]:
        yield run_finished("run-none")

    state = StreamLifecycleState()
    projected = [
        event
        async for event in project_terminal_settlement(
            events(),
            state=state,
            settle=cast(Any, lambda: None),
        )
    ]
    assert [event.type for event in projected] == ["RunError"]
    assert projected[0].data["code"] == "run_persistence_failed"
    assert state.durably_settled is False


def _dependencies(
    db_path: str,
    resume_graph: Any,
) -> a2ui_runtime.A2UIRuntimeDependencies:
    """Build explicit fake graph, registry, and stream seams."""

    def format_review(_state: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"formatted": {"answer": "review"}, "raw": None}

    return a2ui_runtime.A2UIRuntimeDependencies(
        graphs=a2ui_runtime.A2UIGraphDependencies(
            chat_graph=_empty_graph,
            chat_initial_state=_empty_state,
            review_graph=_empty_graph,
            review_initial_state=_empty_state,
            validate_review=a2ui_runtime.validate_review_arguments,
            resume_graph=cast(Any, resume_graph),
        ),
        persistence=a2ui_runtime.A2UIPersistenceDependencies(
            registry_factory=RunRegistry,
            current_user=lambda: "alice",
            tasks_db_path=lambda: db_path,
            create_stream_run=_noop_create_stream_run,
            settle_stream_run=_successful_stream_settlement,
            format_review_result=format_review,
        ),
        stream=a2ui_runtime.A2UIStreamDependencies(
            stream_setup_error=_stream_setup_error,
            failed_stream_result=_failed_stream_result,
            project_stream=_project_stream,
        ),
    )


def test_submission_mapping_and_round_cap() -> None:
    """Confirm/form/choice projections and the hard N=2 bound stay locked."""
    surface = _surface("surface-submit")
    assert (
        a2ui_runtime.submitted_a2ui_value(surface, {"accepted": True})[
            "props"
        ]["accepted"]
        is True
    )
    assert (
        a2ui_runtime.submitted_a2ui_value(surface, {"cancelled": True})[
            "props"
        ]["cancelled"]
        is True
    )
    form = _surface("surface-form", "form")
    form["props"] = {"title": "Form", "fields": []}
    assert a2ui_runtime.submitted_a2ui_value(
        form, {"fields": {"gene_id": "AT1G01010"}}
    )["props"]["fields"] == {"gene_id": "AT1G01010"}
    choice = _surface("surface-choice", "choice")
    choice["props"] = {"title": "Choice", "options": []}
    assert a2ui_runtime.submitted_a2ui_value(choice, {"selected": ["one"]})[
        "props"
    ]["selected"] == ["one"]
    assert should_reenter_a2ui(text="请确认", a2ui_round=1)
    assert not should_reenter_a2ui(text="请确认", a2ui_round=A2UI_MAX_ROUNDS)


def test_surface_validation_rejects_wrong_or_closed(tmp_path: Any) -> None:
    """Mismatched and terminal surfaces fail before graph access."""
    db_path = str(tmp_path / "runs.db")
    _seed(db_path, "run-surface")
    record = RunRegistry(db_path).get_run("run-surface", owner="alice")
    assert record is not None
    with pytest.raises(HTTPException) as wrong:
        a2ui_runtime.open_surface_for_action(
            record, surface_id="other", widget="confirm"
        )
    assert wrong.value.status_code == 409
    _seed(db_path, "run-closed", status="succeeded")
    closed = RunRegistry(db_path).get_run("run-closed", owner="alice")
    assert closed is not None
    with pytest.raises(HTTPException) as terminal:
        a2ui_runtime.open_surface_for_action(
            closed, surface_id="surface-1", widget="confirm"
        )
    assert terminal.value.status_code == 409


async def test_checkpoint_and_failure_settlement(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpoint gaps remain resumable while graph errors fail safely."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    db_path = str(tmp_path / "runs.db")
    _seed(db_path, "run-checkpoint")
    _seed(db_path, "run-failure")

    async def resume_graph(
        _graph: Any, run_id: str, _payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if run_id == "run-checkpoint":
            raise NoCheckpointError("checkpoint missing")
        raise RuntimeError("Bearer secret-token database-password")

    dependencies = _dependencies(db_path, resume_graph)
    with pytest.raises(HTTPException) as checkpoint:
        await a2ui_runtime.resume_a2ui_run(
            run_id="run-checkpoint",
            body=_action("run-checkpoint"),
            debug=False,
            dependencies=dependencies,
        )
    assert checkpoint.value.status_code == 409
    checkpoint_record = RunRegistry(db_path).get_run(
        "run-checkpoint", owner="alice"
    )
    assert checkpoint_record is not None
    assert checkpoint_record.status == "input_required"

    with pytest.raises(HTTPException) as failure:
        await a2ui_runtime.resume_a2ui_run(
            run_id="run-failure",
            body=_action("run-failure"),
            debug=False,
            dependencies=dependencies,
        )
    assert failure.value.status_code == 500
    assert failure.value.detail == "a2ui resume failed"
    failed = RunRegistry(db_path).get_run("run-failure", owner="alice")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.result == {
        "formatted": {"answer": ""},
        "raw": None,
        "error": "a2ui resume failed",
    }


async def test_first_uplink_wins(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent uplinks have one graph winner and one immediate 409."""
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "true")
    db_path = str(tmp_path / "runs.db")
    _seed(db_path, "run-race")
    started = asyncio.Event()
    release = asyncio.Event()

    async def resume_graph(
        _graph: Any, _run_id: str, _payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {
            "response": {
                "choices": [
                    {"message": {"content": "done", "follow_up_questions": []}}
                ]
            }
        }

    dependencies = _dependencies(db_path, resume_graph)
    first = asyncio.create_task(
        a2ui_runtime.resume_a2ui_run(
            run_id="run-race",
            body=_action("run-race"),
            debug=False,
            dependencies=dependencies,
        )
    )
    await started.wait()
    with pytest.raises(HTTPException) as second:
        await a2ui_runtime.resume_a2ui_run(
            run_id="run-race",
            body=_action("run-race"),
            debug=False,
            dependencies=dependencies,
        )
    assert second.value.status_code == 409
    release.set()
    body, status_code = await first
    assert status_code == 200
    assert body["status"] == "succeeded"
