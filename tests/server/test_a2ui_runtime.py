# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Direct contracts for the extracted A2UI pause/resume runtime."""

# pylint: disable=protected-access

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
from mcp_server_phytomni.api import (
    a2ui_projection,
    a2ui_resume,
    a2ui_runtime,
    run_lifecycle,
)
from mcp_server_phytomni.api.a2ui_projection import (
    ReviewSurfaceProjectionError,
)
from mcp_server_phytomni.api.lifecycle_contract import SafeApiError
from mcp_server_phytomni.api.schemas import A2uiActionRequest, ResumeRequest
from mcp_server_phytomni.mcp.result_formatting import run_finished
from mcp_server_phytomni.mcp.stream_lifecycle import (
    StreamLifecycleState,
    project_terminal_settlement,
)
from mcp_server_phytomni.runtime.resume import NoCheckpointError
from mcp_server_phytomni.runtime.run_registry import (
    A2UIActionClaim,
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.run_registry_models import RunRequestInfo

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
    *,
    checkpoint_present: bool = True,
    current_user: str | None = "alice",
    current_request_id: str | None = "runtime-test-request",
) -> a2ui_runtime.A2UIRuntimeDependencies:
    """Build explicit fake graph, registry, and stream seams."""

    async def has_checkpoint(_app: Any, _thread_id: str) -> bool:
        """Keep direct runtime tests focused on post-claim behavior."""
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
            current_user=lambda: current_user,
            current_request_id=lambda: current_request_id,
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
) -> None:
    """Checkpoint gaps remain resumable while graph errors fail safely."""
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
    with pytest.raises(SafeApiError) as checkpoint:
        await a2ui_runtime.resume_a2ui_run(
            run_id="run-checkpoint",
            body=_action("run-checkpoint"),
            debug=False,
            dependencies=dependencies,
        )
    assert checkpoint.value.status_code == 409
    assert checkpoint.value.code == "checkpoint_not_available"
    checkpoint_record = RunRegistry(db_path).get_run(
        "run-checkpoint", owner="alice"
    )
    assert checkpoint_record is not None
    assert checkpoint_record.status == "failed"
    checkpoint_audit = RunRegistry(db_path).list_a2ui_actions(
        owner="alice", run_id="run-checkpoint"
    )
    assert len(checkpoint_audit) == 1
    assert checkpoint_audit[0].outcome == "failed"

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
    assert failed.result is not None
    assert failed.result["formatted"] == {"answer": ""}
    assert failed.result["raw"] is None
    assert failed.result["error"] == "a2ui resume failed"
    execution = failed.result["execution"]
    assert execution["tracking"] == {"degraded": True}
    assert execution["warnings"] == [
        {"code": "a2ui_resume_failed", "retryable": False}
    ]
    assert execution["report"] is None
    assert all(
        execution[key] == []
        for key in ("tasks", "artifacts", "output_dirs", "diagnostics")
    )


async def test_first_uplink_wins(tmp_path: Any) -> None:
    """Concurrent uplinks have one graph winner and one immediate 409."""
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
    with pytest.raises(SafeApiError) as second:
        await a2ui_runtime.resume_a2ui_run(
            run_id="run-race",
            body=_action("run-race"),
            debug=False,
            dependencies=dependencies,
        )
    assert second.value.status_code == 409
    assert second.value.code == "a2ui_action_conflict"
    release.set()
    body, status_code = await first
    assert status_code == 200
    assert body["status"] == "succeeded"


def _seed_review(db_path, run_id, status="input_required"):
    RunRegistry(db_path).create_run(
        RunSpec(run_id, "alice", "review", "local"),
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


async def _successful_resume(_g, _r, _p):
    return {
        "response": {
            "choices": [
                {"message": {"content": "done", "follow_up_questions": []}}
            ]
        }
    }


def test_open_surface_rejects_missing_and_widget_mismatch(tmp_path):
    """Closed drafts and widget mismatches fail before graph access."""
    db = str(tmp_path / "runs.db")
    RunRegistry(db).create_run(
        RunSpec("run-empty", "alice", "chat", "local"),
        outcome=RunOutcome(
            status="input_required", result={"interrupt": {"draft": {}}}
        ),
    )
    empty = RunRegistry(db).get_run("run-empty", owner="alice")
    with pytest.raises(HTTPException) as missing:
        a2ui_resume.open_surface_for_action(
            empty, surface_id="surface-1", widget="confirm"
        )
    assert missing.value.status_code == 409
    _seed(db, "run-widget")
    rec = RunRegistry(db).get_run("run-widget", owner="alice")
    with pytest.raises(HTTPException) as widget:
        a2ui_resume.open_surface_for_action(
            rec, surface_id="surface-1", widget="form"
        )
    assert widget.value.status_code == 400


async def test_resume_rejects_path_mismatch_missing_and_unsupported(tmp_path):
    """Path/body identity, ownership, and agent kind stay fail-closed."""
    db = str(tmp_path / "runs.db")
    _seed(db, "run-present")
    deps = _dependencies(db, _successful_resume)
    with pytest.raises(HTTPException) as mismatch:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-other",
            body=_action("run-present"),
            debug=False,
            dependencies=deps,
        )
    assert mismatch.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-missing",
            body=_action("run-missing"),
            debug=False,
            dependencies=deps,
        )
    assert missing.value.status_code == 404
    RunRegistry(db).create_run(
        RunSpec("run-analyst", "alice", "analyst", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={"interrupt": {"draft": {"a2ui": _surface("surface-1")}}},
        ),
    )
    with pytest.raises(HTTPException) as unsupported:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-analyst",
            body=_action("run-analyst"),
            debug=False,
            dependencies=deps,
        )
    assert unsupported.value.status_code == 400


async def test_chat_reinterrupt_claimed_and_persist_failures(
    tmp_path, monkeypatch
):
    """Re-interrupt persists; claimed and persist faults fail closed."""
    db = str(tmp_path / "runs.db")
    _seed(db, "run-reenter")
    _seed(db, "run-persist")
    _seed(db, "run-false")
    _seed(db, "run-claimed")
    _seed(db, "run-pause-raise")
    _seed(db, "run-pause-false")

    async def resume_graph(_g, run_id, _p):
        if run_id in {
            "run-reenter",
            "run-pause-raise",
            "run-pause-false",
        }:
            return {
                "__interrupt__": [
                    {"text": "more?", "draft": {"a2ui": _surface("s2")}}
                ]
            }
        return {
            "response": {
                "choices": [
                    {"message": {"content": "done", "follow_up_questions": []}}
                ]
            }
        }

    deps = _dependencies(db, resume_graph)
    body, status = await a2ui_resume.resume_a2ui_run(
        run_id="run-reenter",
        body=_action("run-reenter"),
        debug=False,
        dependencies=deps,
    )
    assert status == 200 and body["status"] == "input_required"
    ok, _ = await a2ui_resume.resume_a2ui_run(
        run_id="run-claimed",
        body=_action("run-claimed"),
        debug=False,
        dependencies=deps,
    )
    assert ok["status"] == "succeeded"
    with pytest.raises(SafeApiError) as claimed:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-claimed",
            body=_action("run-claimed"),
            debug=False,
            dependencies=deps,
        )
    assert claimed.value.status_code == 409
    original = RunRegistry.settle_run

    def raise_settle(self, *a, **k):
        if k.get("status") == "succeeded":
            raise OSError("disk")
        return original(self, *a, **k)

    def false_settle(self, *a, **k):
        if k.get("status") == "succeeded":
            return False
        return original(self, *a, **k)

    def raise_pause(self, *a, **k):
        if k.get("status") == "input_required":
            raise OSError("disk")
        return original(self, *a, **k)

    def false_pause(self, *a, **k):
        if k.get("status") == "input_required":
            return False
        return original(self, *a, **k)

    monkeypatch.setattr(RunRegistry, "settle_run", raise_settle)
    with pytest.raises(HTTPException) as persist:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-persist",
            body=_action("run-persist"),
            debug=False,
            dependencies=deps,
        )
    assert persist.value.status_code == 500
    monkeypatch.setattr(RunRegistry, "settle_run", false_settle)
    with pytest.raises(HTTPException) as false_p:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-false",
            body=_action("run-false"),
            debug=False,
            dependencies=deps,
        )
    assert false_p.value.status_code == 500
    monkeypatch.setattr(RunRegistry, "settle_run", raise_pause)
    with pytest.raises(HTTPException) as pause_raise:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-pause-raise",
            body=_action("run-pause-raise"),
            debug=False,
            dependencies=deps,
        )
    assert pause_raise.value.status_code == 500
    monkeypatch.setattr(RunRegistry, "settle_run", false_pause)
    with pytest.raises(HTTPException) as pause_false:
        await a2ui_resume.resume_a2ui_run(
            run_id="run-pause-false",
            body=_action("run-pause-false"),
            debug=False,
            dependencies=deps,
        )
    assert pause_false.value.status_code == 500


async def test_locale_backfill_and_claim_helpers(tmp_path):
    """Locale backfill and claim helpers fail closed on persist errors."""
    db = str(tmp_path / "runs.db")
    registry = RunRegistry(db)
    registry.create_run(
        RunSpec("run-locale", "alice", "chat", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={"interrupt": {"draft": {"a2ui": _surface("surface-1")}}},
        ),
        request_info=RunRequestInfo(query="hello"),
    )
    record = registry.get_run("run-locale", owner="alice")
    a2ui_resume._bind_record_locale(record, registry=registry, owner="alice")
    assert (
        registry.get_run("run-locale", owner="alice").request_info.locale
        is not None
    )

    class Boom:
        def update_request_info(self, *a, **k):
            raise OSError("disk")

        def complete_a2ui_action(self, *a, **k):
            raise OSError("disk")

        def settle_run(self, *a, **k):
            raise OSError("disk")

    class FalseR:
        def update_request_info(self, *a, **k):
            return False

        def complete_a2ui_action(self, *a, **k):
            return False

        def settle_run(self, *a, **k):
            return False

    with pytest.raises(run_lifecycle.RunPersistenceError):
        a2ui_resume._bind_record_locale(
            record, registry=cast(Any, Boom()), owner="alice"
        )
    with pytest.raises(run_lifecycle.RunPersistenceError):
        a2ui_resume._bind_record_locale(
            record, registry=cast(Any, FalseR()), owner="alice"
        )
    claim = A2UIActionClaim(
        run_id="r",
        surface_id="s",
        widget="confirm",
        action_id="a",
        channel="a2ui",
    )
    a2ui_resume._complete_claim_best_effort(
        claim, owner="alice", registry=cast(Any, Boom()), outcome="failed"
    )
    a2ui_resume._settle_failed_resume(
        cast(Any, Boom()), run_id="r", owner="alice", expected_revision=1
    )
    a2ui_resume._complete_claim_best_effort(
        claim, owner="alice", registry=cast(Any, FalseR()), outcome="failed"
    )
    a2ui_resume._settle_failed_resume(
        cast(Any, FalseR()), run_id="r", owner="alice", expected_revision=1
    )
    with pytest.raises(run_lifecycle.RunPersistenceError):
        a2ui_resume._complete_claim_or_raise(
            claim,
            owner="alice",
            registry=cast(Any, Boom()),
            outcome="succeeded",
        )
    with pytest.raises(run_lifecycle.RunPersistenceError):
        a2ui_resume._complete_claim_or_raise(
            claim,
            owner="alice",
            registry=cast(Any, FalseR()),
            outcome="succeeded",
        )
    assert a2ui_resume._failed_resume_result()["error"] == "a2ui resume failed"


async def test_review_resume_terminal_reinterrupt_and_projection(
    tmp_path, monkeypatch
):
    """Review resume covers terminal, reinterrupt, and projection faults."""
    db = str(tmp_path / "runs.db")
    _seed_review(db, "review-ok")
    _seed_review(db, "review-pause")
    _seed_review(db, "review-missing-surface", status="succeeded")
    _seed_review(db, "review-project")
    _seed_review(db, "review-persist")
    _seed_review(db, "review-false")
    _seed_review(db, "review-claimed")
    _seed_review(db, "review-checkpoint")
    _seed_review(db, "review-mint")
    _seed_review(db, "review-action-ok")
    _seed_review(db, "review-action-pause")
    _seed_review(db, "review-action-project")
    _seed_review(db, "review-invalid")
    _seed_review(db, "review-a2ui-checkpoint")
    _seed_review(db, "review-claim-conflict")
    RunRegistry(db).create_run(
        RunSpec("review-bad-surface", "alice", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={"interrupt": {"draft": {"a2ui": {"widget": "nope"}}}},
        ),
    )
    RunRegistry(db).create_run(
        RunSpec("review-no-a2ui", "alice", "review", "local"),
        outcome=RunOutcome(
            status="input_required",
            result={"interrupt": {"draft": {}}},
        ),
    )

    async def resume_graph(_g, run_id, _p):
        if run_id in {
            "review-pause",
            "review-project",
            "review-action-pause",
            "review-action-project",
        }:
            return {"__interrupt__": [{"draft": "next"}]}
        return {"answer": "approved"}

    deps = _dependencies(db, resume_graph)
    body, status = await a2ui_resume.resume_review_run(
        thread_id="review-ok",
        payload=ResumeRequest(approved=True),
        debug=True,
        dependencies=deps,
    )
    assert status == 200
    paused, ps = await a2ui_resume.resume_review_run(
        thread_id="review-pause",
        payload=ResumeRequest(approved=True),
        debug=False,
        dependencies=deps,
    )
    assert ps == 200 and paused["status"] == "input_required"
    action_ok, action_status = await a2ui_resume.resume_a2ui_run(
        run_id="review-action-ok",
        body=_action("review-action-ok"),
        debug=False,
        dependencies=deps,
    )
    assert action_status == 200 and action_ok["status"] == "succeeded"
    action_paused, action_ps = await a2ui_resume.resume_a2ui_run(
        run_id="review-action-pause",
        body=_action("review-action-pause"),
        debug=False,
        dependencies=deps,
    )
    assert action_ps == 200 and action_paused["status"] == "input_required"
    with pytest.raises(HTTPException) as waiting:
        await a2ui_resume.resume_review_run(
            thread_id="review-missing-surface",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert waiting.value.status_code == 409
    with pytest.raises(SafeApiError) as bad:
        await a2ui_resume.resume_review_run(
            thread_id="review-bad-surface",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert bad.value.status_code == 409
    with pytest.raises(SafeApiError) as no_surface:
        await a2ui_resume.resume_review_run(
            thread_id="review-no-a2ui",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert no_surface.value.status_code == 409
    with pytest.raises(SafeApiError) as action_ckpt:
        await a2ui_resume.resume_a2ui_run(
            run_id="review-a2ui-checkpoint",
            body=_action("review-a2ui-checkpoint"),
            debug=False,
            dependencies=_dependencies(
                db, resume_graph, checkpoint_present=False
            ),
        )
    assert action_ckpt.value.status_code == 409

    def raise_claim(self, *a, **k):
        raise a2ui_resume.A2UIActionConflict("already claimed")

    monkeypatch.setattr(RunRegistry, "claim_a2ui_action", raise_claim)
    with pytest.raises(SafeApiError) as claim_conflict:
        await a2ui_resume.resume_review_run(
            thread_id="review-claim-conflict",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert claim_conflict.value.status_code == 409
    monkeypatch.undo()
    original = RunRegistry.settle_run

    def raise_settle(self, *a, **k):
        if k.get("status") == "succeeded":
            raise OSError("disk")
        return original(self, *a, **k)

    def false_settle(self, *a, **k):
        if k.get("status") == "succeeded":
            return False
        return original(self, *a, **k)

    monkeypatch.setattr(RunRegistry, "settle_run", raise_settle)
    with pytest.raises(HTTPException) as persist:
        await a2ui_resume.resume_review_run(
            thread_id="review-persist",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert persist.value.status_code == 500
    monkeypatch.setattr(RunRegistry, "settle_run", false_settle)
    with pytest.raises(HTTPException) as false_p:
        await a2ui_resume.resume_review_run(
            thread_id="review-false",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert false_p.value.status_code == 500
    monkeypatch.setattr(RunRegistry, "settle_run", original)
    claimed_body, claimed_status = await a2ui_resume.resume_review_run(
        thread_id="review-claimed",
        payload=ResumeRequest(approved=True),
        debug=False,
        dependencies=deps,
    )
    assert claimed_status == 200 and claimed_body["status"] == "succeeded"
    with pytest.raises(SafeApiError) as claimed:
        await a2ui_resume.resume_review_run(
            thread_id="review-claimed",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert claimed.value.status_code == 409
    with pytest.raises(SafeApiError) as missing_ckpt:
        await a2ui_resume.resume_review_run(
            thread_id="review-checkpoint",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=_dependencies(
                db, resume_graph, checkpoint_present=False
            ),
        )
    assert missing_ckpt.value.status_code == 409
    minted, minted_status = await a2ui_resume.resume_review_run(
        thread_id="review-mint",
        payload=ResumeRequest(approved=True),
        debug=False,
        dependencies=_dependencies(db, resume_graph, current_request_id=None),
    )
    assert minted_status == 200 and minted["status"] == "succeeded"
    with pytest.raises(HTTPException) as anonymous:
        await a2ui_resume.resume_review_run(
            thread_id="review-mint",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=_dependencies(db, resume_graph, current_user=None),
        )
    assert anonymous.value.status_code == 404
    monkeypatch.setattr(
        a2ui_resume,
        "project_review_interrupt",
        lambda _i: (_ for _ in ()).throw(ReviewSurfaceProjectionError("bad")),
    )
    with pytest.raises(SafeApiError) as projected:
        await a2ui_resume.resume_review_run(
            thread_id="review-project",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert projected.value.status_code == 500
    with pytest.raises(SafeApiError) as action_projected:
        await a2ui_resume.resume_a2ui_run(
            run_id="review-action-project",
            body=_action("review-action-project"),
            debug=False,
            dependencies=deps,
        )
    assert action_projected.value.status_code == 500
    with pytest.raises(HTTPException) as missing:
        await a2ui_resume.resume_review_run(
            thread_id="review-absent",
            payload=ResumeRequest(approved=True),
            debug=False,
            dependencies=deps,
        )
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as invalid:
        await a2ui_resume.resume_a2ui_run(
            run_id="review-invalid",
            body=A2uiActionRequest(
                run_id="review-invalid",
                surface_id="surface-1",
                widget="confirm",
                action_id="action-1",
                payload={},
            ),
            debug=False,
            dependencies=deps,
        )
    assert invalid.value.status_code == 400


def test_review_run_body_shapes_interrupt_and_debug():
    """Review HTTP bodies keep interrupt and debug raw payloads distinct."""
    interrupt_body = a2ui_resume.review_run_body(
        a2ui_resume.ReviewExecution(
            run_id="r1",
            status="input_required",
            interrupt={"draft": {"a2ui": _surface("s1"), "summary": "draft"}},
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
    assert (
        a2ui_resume.review_run_body(
            a2ui_resume.ReviewExecution(run_id="r3", status="succeeded"),
            debug=False,
        )["status"]
        == "succeeded"
    )
