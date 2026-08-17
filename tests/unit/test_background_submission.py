# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contract tests for the shared background submission runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import pytest

from mcp_server_phytomni.runtime import background_submission
from mcp_server_phytomni.runtime.background_submission import (
    BackgroundSubmissionLaunchError,
    BackgroundSubmissionOutcome,
    launch_background_submission,
    reserve_background_submission,
)
from mcp_server_phytomni.runtime.execution_defaults import (
    empty_execution_projection,
)
from mcp_server_phytomni.runtime.live_tasks import is_live_running
from mcp_server_phytomni.runtime.locale import current_effective_locale
from mcp_server_phytomni.runtime.request_context import (
    current_request_id,
    current_request_user,
    current_run_id,
)
from mcp_server_phytomni.runtime.run_registry import (
    A2ACorrelation,
    RunRegistry,
    RunRequestInfo,
)
from mcp_server_phytomni.runtime.task_manager import RunContext, Submission


class _SyntheticProviderFailureError(Exception):
    """Ordinary provider failure carrying deliberately private text."""


class _SyntheticEscapedSignal(BaseException):
    """Non-cancellation escape used to exercise the done observer."""


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    attempts: int = 100,
) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("background condition was not reached")


@pytest.mark.asyncio
async def test_launch_returns_before_operation_finishes(
    tmp_path: Path,
) -> None:
    """Launch returns immediately while preserving request context."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="analyst",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-1", locale="zh-CN"),
        db_path=db_path,
    )
    release = asyncio.Event()
    observed: dict[str, str | None] = {}

    async def operation() -> BackgroundSubmissionOutcome:
        observed.update(
            user=current_request_user(),
            request_id=current_request_id(),
            run_id=current_run_id(),
            locale=current_effective_locale(),
        )
        await release.wait()
        return BackgroundSubmissionOutcome()

    launch_background_submission(
        reservation,
        operation,
        db_path=db_path,
    )

    assert is_live_running(reservation.run_id)
    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "running"
    assert record.a2a == A2ACorrelation()
    release.set()
    await _wait_until(lambda: not is_live_running(reservation.run_id))
    assert observed == {
        "user": "alice",
        "request_id": "req-1",
        "run_id": reservation.run_id,
        "locale": "zh-CN",
    }


def test_reservation_discards_raw_request_payload(tmp_path: Path) -> None:
    """Reservations retain only correlation and routing metadata."""
    db_path = str(tmp_path / "tasks.db")
    sensitive_query = "secret prompt with attachment filenames"
    sensitive_payload = '{"token":"secret-token","attachments":["a.fa"]}'

    reservation = reserve_background_submission(
        agent="analyst",
        owner="alice",
        request_info=RunRequestInfo(
            dialogue_id="dialogue-1",
            request_id="req-safe",
            query=sensitive_query,
            tool_name="AnalystAgent",
            model="phyto-analyst",
            request_json=sensitive_payload,
            locale="zh-CN",
            a2a=A2ACorrelation(
                task_id="a2a-task-1",
                context_id="a2a-context-1",
                message_id="a2a-message-1",
            ),
        ),
        db_path=db_path,
    )

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.request_info.dialogue_id == "dialogue-1"
    assert record.request_info.request_id == "req-safe"
    assert record.request_info.tool_name == "AnalystAgent"
    assert record.request_info.model == "phyto-analyst"
    assert record.request_info.locale == "zh-CN"
    assert record.request_info.query is None
    assert record.request_info.request_json is None
    assert record.a2a == A2ACorrelation(
        task_id="a2a-task-1",
        context_id="a2a-context-1",
        message_id="a2a-message-1",
    )

    with sqlite3.connect(db_path) as connection:
        persisted = connection.execute(
            "SELECT query, request_json FROM runs WHERE run_id = ?",
            (reservation.run_id,),
        ).fetchone()

    assert persisted == (None, None)
    assert sensitive_query not in str(persisted)
    assert sensitive_payload not in str(persisted)


@pytest.mark.asyncio
async def test_worker_failure_settles_owned_run_without_raw_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker failures settle safely without persisting raw errors."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="design",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-2", locale="en-US"),
        db_path=db_path,
    )

    async def operation() -> BackgroundSubmissionOutcome:
        raise RuntimeError("secret prompt and token")

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_failed"
    assert "secret" not in json.dumps(record.result)
    assert "secret prompt and token" not in caplog.text
    assert not is_live_running(reservation.run_id)


@pytest.mark.asyncio
async def test_arbitrary_ordinary_exception_settles_failed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every ordinary provider failure settles without leaking its text."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="analyst",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-arbitrary"),
        db_path=db_path,
    )

    async def operation() -> BackgroundSubmissionOutcome:
        raise _SyntheticProviderFailureError(
            "Bearer private-token /private/input.fa"
        )

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_failed"
    assert "private-token" not in caplog.text
    assert "/private/input.fa" not in caplog.text


@pytest.mark.asyncio
async def test_done_observer_retrieves_unexpected_escaped_exception(
    tmp_path: Path,
) -> None:
    """The completion observer retrieves and settles an escaped signal."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="design",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-observer"),
        db_path=db_path,
    )
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))

    async def operation() -> BackgroundSubmissionOutcome:
        raise _SyntheticEscapedSignal("private escaped body")

    try:
        launch_background_submission(reservation, operation, db_path=db_path)
        await _wait_until(lambda: not is_live_running(reservation.run_id))
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_failed"
    assert not contexts


@pytest.mark.asyncio
async def test_degraded_tracking_fails_with_safe_accepted_projection(
    tmp_path: Path,
) -> None:
    """Recorder failure settles safely without creating unpollable work."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="research",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-degraded", locale="en-US"),
        db_path=db_path,
    )

    async def operation() -> BackgroundSubmissionOutcome:
        return BackgroundSubmissionOutcome(
            accepted_task_ids=("accepted-1",),
            degraded=True,
        )

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_tracking_failed"
    assert not record.task_ids
    assert record.result is not None
    assert record.result["execution"] == {
        "tracking": {"degraded": True},
        "warnings": [],
        "tasks": [
            {"id": "accepted-1", "accepted": True, "status": "submitted"}
        ],
        "artifacts": [],
        "output_dirs": [],
        "report": None,
        "diagnostics": [],
    }


@pytest.mark.asyncio
async def test_partial_submission_stays_running_with_degraded_warning(
    tmp_path: Path,
) -> None:
    """Persisted partial work remains pollable and marks tracking degraded."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="design",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-partial", locale="en-US"),
        db_path=db_path,
    )
    projection = {
        "formatted": {"answer": ""},
        "execution": {
            "warnings": [{"code": "submission_partial", "retryable": False}],
            "tasks": [
                {"id": "accepted-1", "accepted": True, "status": "submitted"}
            ],
        },
    }

    async def operation() -> BackgroundSubmissionOutcome:
        now = datetime.now(UTC).isoformat()
        assert RunRegistry(db_path).record_reserved_submissions(
            reservation.run_id,
            owner="alice",
            agent="design",
            submissions=(
                Submission(
                    task_id="accepted-1",
                    status="submitted",
                    output_dir="tenant/out",
                    run_context=RunContext(
                        run_id=reservation.run_id,
                        user_id="alice",
                        agent="design",
                        origin="remote",
                        created_at=now,
                        updated_at=now,
                    ),
                ),
            ),
            result=projection,
            now=now,
        )
        return BackgroundSubmissionOutcome(
            accepted_task_ids=("accepted-1",),
            result=projection,
        )

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(
        lambda: (
            (
                record := RunRegistry(db_path).get_run(
                    reservation.run_id, owner="alice"
                )
            )
            is not None
            and record.result is not None
            and record.result.get("execution", {}).get("tracking")
            == {"degraded": True}
        )
    )

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "running"
    assert record.task_ids == ("accepted-1",)
    assert record.result is not None
    assert record.result["execution"]["tracking"] == {"degraded": True}


@pytest.mark.asyncio
async def test_worker_projection_keeps_submit_delivery_marker(
    tmp_path: Path,
) -> None:
    """Formatted worker output must not erase the archive-delivery marker."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="analyst",
        owner="alice",
        request_info=RunRequestInfo(
            request_id="req-keep-delivery", locale="en-US"
        ),
        db_path=db_path,
    )
    marked = empty_execution_projection(result_archive_required=True)
    marked["execution"]["tasks"] = [
        {"id": "accepted-1", "accepted": True, "status": "submitted"}
    ]
    unmarked = {
        "formatted": {"answer": ""},
        "execution": {
            "tasks": [
                {"id": "accepted-1", "accepted": True, "status": "submitted"}
            ],
            "delivery": None,
        },
    }

    async def operation() -> BackgroundSubmissionOutcome:
        now = datetime.now(UTC).isoformat()
        assert RunRegistry(db_path).record_reserved_submissions(
            reservation.run_id,
            owner="alice",
            agent="analyst",
            submissions=(
                Submission(
                    task_id="accepted-1",
                    status="submitted",
                    output_dir="tenant/out",
                    run_context=RunContext(
                        run_id=reservation.run_id,
                        user_id="alice",
                        agent="analyst",
                        origin="remote",
                        created_at=now,
                        updated_at=now,
                    ),
                ),
            ),
            result=marked,
            now=now,
        )
        return BackgroundSubmissionOutcome(
            accepted_task_ids=("accepted-1",),
            result=unmarked,
        )

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(
        lambda: (
            (
                record := RunRegistry(db_path).get_run(
                    reservation.run_id, owner="alice"
                )
            )
            is not None
            and record.result is not None
            and record.result.get("execution", {}).get("tasks")
        )
    )

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "running"
    assert record.result is not None
    delivery = record.result["execution"]["delivery"]
    assert delivery["required"] is True
    assert delivery["status"] == "pending"


@pytest.mark.asyncio
async def test_accepted_ids_without_persisted_children_fail_safely(
    tmp_path: Path,
) -> None:
    """An unqueryable accepted id cannot leave a run perpetually running."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="research",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-orphan", locale="en-US"),
        db_path=db_path,
    )

    async def operation() -> BackgroundSubmissionOutcome:
        return BackgroundSubmissionOutcome(accepted_task_ids=("orphan",))

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_failed"


@pytest.mark.asyncio
async def test_terminal_reconciliation_owns_projection_race(
    tmp_path: Path,
) -> None:
    """A terminal reconciliation result is never overwritten by the worker."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="network",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-race", locale="en-US"),
        db_path=db_path,
    )

    async def operation() -> BackgroundSubmissionOutcome:
        registry = RunRegistry(db_path)
        assert registry.settle_run(
            reservation.run_id,
            owner="alice",
            status="succeeded",
            result={"formatted": {"answer": "reconciled"}},
            expected_revision=reservation.revision,
        )
        return BackgroundSubmissionOutcome(
            accepted_task_ids=("accepted-race",),
            result={"formatted": {"answer": "worker"}},
        )

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "succeeded"
    assert record.result == {"formatted": {"answer": "reconciled"}}


@pytest.mark.asyncio
async def test_cancelled_worker_settles_run_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelled workers stay cancelled and settle their owned run failed."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="network",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-3", locale="en-US"),
        db_path=db_path,
    )

    captured: dict[str, asyncio.Task[None]] = {}
    original_register = background_submission.register_live_task

    def capture_task(run_id: str, task: asyncio.Task[None]) -> None:
        captured["task"] = task
        original_register(run_id, task)

    monkeypatch.setattr(
        background_submission,
        "register_live_task",
        capture_task,
    )
    release = asyncio.Event()

    async def operation() -> BackgroundSubmissionOutcome:
        await release.wait()
        return BackgroundSubmissionOutcome()

    launch_background_submission(reservation, operation, db_path=db_path)
    captured_task = captured["task"]
    captured_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await captured_task
    assert captured_task.cancelled() is True

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_cancelled"


@pytest.mark.asyncio
async def test_settlement_failure_is_sanitized_and_deregisters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Settlement storage failures expose only bounded safe log metadata."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="research",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-settlement"),
        db_path=db_path,
    )

    def fail_registry(_db_path: str) -> NoReturn:
        raise _SyntheticProviderFailureError(
            "Bearer settlement-token /private/tasks.db"
        )

    monkeypatch.setattr(background_submission, "RunRegistry", fail_registry)

    async def operation() -> BackgroundSubmissionOutcome:
        pytest.fail("operation must not run after registry init failure")

    package_logger = logging.getLogger("mcp_server_phytomni")
    package_logger.addHandler(caplog.handler)
    try:
        launch_background_submission(reservation, operation, db_path=db_path)
        await _wait_until(lambda: not is_live_running(reservation.run_id))
    finally:
        package_logger.removeHandler(caplog.handler)

    assert "settlement-token" not in caplog.text
    assert "/private/tasks.db" not in caplog.text
    settlement_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "Background submission settlement failed"
    )
    assert getattr(settlement_record, "run_id") == reservation.run_id
    assert getattr(settlement_record, "agent") == "research"
    assert getattr(settlement_record, "stage") == "settle_failed"
    assert getattr(settlement_record, "error_type") == (
        "_SyntheticProviderFailureError"
    )
    assert not is_live_running(reservation.run_id)


@pytest.mark.asyncio
async def test_active_reservation_cannot_launch_twice(tmp_path: Path) -> None:
    """A live reservation rejects a second background launch."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="analyst",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-once", locale="en-US"),
        db_path=db_path,
    )
    release = asyncio.Event()

    async def operation() -> BackgroundSubmissionOutcome:
        await release.wait()
        return BackgroundSubmissionOutcome()

    launch_background_submission(reservation, operation, db_path=db_path)
    with pytest.raises(
        BackgroundSubmissionLaunchError,
        match="already active",
    ):
        launch_background_submission(reservation, operation, db_path=db_path)

    assert is_live_running(reservation.run_id)
    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "running"
    release.set()
    await _wait_until(lambda: not is_live_running(reservation.run_id))


def test_task_creation_failure_compensates_reserved_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task creation errors compensate the reserved registry row."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="research",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-4", locale="en-US"),
        db_path=db_path,
    )

    def fail_create_task(
        _coroutine: Coroutine[Any, Any, None],
        *,
        name: str | None = None,
    ) -> NoReturn:
        del name
        raise RuntimeError("loop unavailable")

    monkeypatch.setattr(asyncio, "create_task", fail_create_task)

    async def operation() -> BackgroundSubmissionOutcome:
        return BackgroundSubmissionOutcome()

    with pytest.raises(BackgroundSubmissionLaunchError):
        launch_background_submission(reservation, operation, db_path=db_path)

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_launch_failed"


@pytest.mark.asyncio
async def test_registration_failure_compensates_and_observes_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Registration failure cancels the task and compensates the run."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="research",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-register"),
        db_path=db_path,
    )
    created: dict[str, asyncio.Task[None]] = {}
    original_create_task = asyncio.create_task

    def capture_create_task(
        coroutine: Coroutine[Any, Any, None],
        *,
        name: str | None = None,
    ) -> asyncio.Task[None]:
        task = original_create_task(coroutine, name=name)
        created["task"] = task
        return task

    def fail_registration(
        _run_id: str,
        _task: asyncio.Task[object],
    ) -> NoReturn:
        raise _SyntheticProviderFailureError(
            "Bearer register-token /private/register.db"
        )

    monkeypatch.setattr(asyncio, "create_task", capture_create_task)
    monkeypatch.setattr(
        background_submission,
        "register_live_task",
        fail_registration,
    )

    async def operation() -> BackgroundSubmissionOutcome:
        return BackgroundSubmissionOutcome()

    with pytest.raises(BackgroundSubmissionLaunchError):
        launch_background_submission(reservation, operation, db_path=db_path)
    await asyncio.sleep(0)

    task = created["task"]
    assert task.cancelled() or task.done()
    if not task.cancelled():
        assert task.exception() is None
    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_launch_failed"
    assert "register-token" not in caplog.text
    assert "/private/register.db" not in caplog.text
    assert not is_live_running(reservation.run_id)


def test_task_creation_compensation_init_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Compensation initialization errors remain sanitized."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="research",
        owner="alice",
        request_info=RunRequestInfo(
            request_id="req-launch-init", locale="en-US"
        ),
        db_path=db_path,
    )

    def fail_create_task(
        _coroutine: Coroutine[Any, Any, None],
        *,
        name: str | None = None,
    ) -> NoReturn:
        del name
        raise RuntimeError("private event loop detail")

    def fail_registry(_db_path: str) -> NoReturn:
        raise OSError("private database path")

    monkeypatch.setattr(asyncio, "create_task", fail_create_task)
    monkeypatch.setattr(background_submission, "RunRegistry", fail_registry)

    async def operation() -> BackgroundSubmissionOutcome:
        return BackgroundSubmissionOutcome()

    with pytest.raises(BackgroundSubmissionLaunchError) as caught:
        launch_background_submission(reservation, operation, db_path=db_path)

    assert "private" not in str(caught.value)
    assert "private event loop detail" not in caplog.text
    assert "private database path" not in caplog.text
    assert not is_live_running(reservation.run_id)


@pytest.mark.asyncio
async def test_worker_registry_init_failure_settles_and_deregisters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Worker registry initialization failures settle and deregister."""
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="research",
        owner="alice",
        request_info=RunRequestInfo(
            request_id="req-worker-init", locale="en-US"
        ),
        db_path=db_path,
    )
    original_registry = background_submission.RunRegistry
    calls = 0

    def fail_first_registry(path: str) -> RunRegistry:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("private database path")
        return original_registry(path)

    monkeypatch.setattr(
        background_submission,
        "RunRegistry",
        fail_first_registry,
    )

    async def operation() -> BackgroundSubmissionOutcome:
        pytest.fail("operation must not run after registry init failure")

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_failed"
    assert calls == 2
    assert "private database path" not in caplog.text
    assert not is_live_running(reservation.run_id)


def test_reservation_storage_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reservation storage failures expose only a stable public error."""
    db_path = str(tmp_path / "tasks.db")

    def fail_reserve(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.OperationalError("private database path")

    monkeypatch.setattr(RunRegistry, "reserve_run", fail_reserve)

    with pytest.raises(
        BackgroundSubmissionLaunchError,
        match="unable to reserve background run",
    ) as caught:
        reserve_background_submission(
            agent="analyst",
            owner="alice",
            request_info=RunRequestInfo(
                request_id="req-storage",
                locale="en-US",
            ),
            db_path=db_path,
        )

    assert "private database path" not in str(caught.value)
