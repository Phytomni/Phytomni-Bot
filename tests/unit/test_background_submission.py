# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Contract tests for the shared background submission runtime."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, NoReturn

import pytest

from mcp_server_phytomni.runtime.background_submission import (
    BackgroundSubmissionLaunchError,
    launch_background_submission,
    reserve_background_submission,
)
from mcp_server_phytomni.runtime.live_tasks import is_live_running
from mcp_server_phytomni.runtime.locale import current_effective_locale
from mcp_server_phytomni.runtime.request_context import (
    current_request_id,
    current_request_user,
    current_run_id,
)
from mcp_server_phytomni.runtime.run_registry import (
    RunRegistry,
    RunRequestInfo,
)


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
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="analyst",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-1", locale="zh-CN"),
        db_path=db_path,
    )
    release = asyncio.Event()
    observed: dict[str, str | None] = {}

    async def operation() -> None:
        observed.update(
            user=current_request_user(),
            request_id=current_request_id(),
            run_id=current_run_id(),
            locale=current_effective_locale(),
        )
        await release.wait()

    launch_background_submission(
        reservation,
        operation,
        db_path=db_path,
    )

    assert is_live_running(reservation.run_id)
    assert RunRegistry(db_path).get_run(
        reservation.run_id, owner="alice"
    ).status == "running"
    release.set()
    await _wait_until(lambda: not is_live_running(reservation.run_id))
    assert observed == {
        "user": "alice",
        "request_id": "req-1",
        "run_id": reservation.run_id,
        "locale": "zh-CN",
    }


@pytest.mark.asyncio
async def test_worker_failure_settles_owned_run_without_raw_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="design",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-2", locale="en-US"),
        db_path=db_path,
    )

    async def operation() -> None:
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
async def test_cancelled_worker_settles_run_failed(tmp_path: Path) -> None:
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="network",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-3", locale="en-US"),
        db_path=db_path,
    )

    async def operation() -> None:
        raise asyncio.CancelledError

    launch_background_submission(reservation, operation, db_path=db_path)
    await _wait_until(lambda: not is_live_running(reservation.run_id))

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_cancelled"


@pytest.mark.asyncio
async def test_active_reservation_cannot_launch_twice(tmp_path: Path) -> None:
    db_path = str(tmp_path / "tasks.db")
    reservation = reserve_background_submission(
        agent="analyst",
        owner="alice",
        request_info=RunRequestInfo(request_id="req-once", locale="en-US"),
        db_path=db_path,
    )
    release = asyncio.Event()

    async def operation() -> None:
        await release.wait()

    launch_background_submission(reservation, operation, db_path=db_path)
    with pytest.raises(
        BackgroundSubmissionLaunchError,
        match="already active",
    ):
        launch_background_submission(reservation, operation, db_path=db_path)

    assert is_live_running(reservation.run_id)
    assert RunRegistry(db_path).get_run(
        reservation.run_id, owner="alice"
    ).status == "running"
    release.set()
    await _wait_until(lambda: not is_live_running(reservation.run_id))


def test_task_creation_failure_compensates_reserved_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    async def operation() -> None:
        return None

    with pytest.raises(BackgroundSubmissionLaunchError):
        launch_background_submission(reservation, operation, db_path=db_path)

    record = RunRegistry(db_path).get_run(reservation.run_id, owner="alice")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "background_submission_launch_failed"


def test_reservation_storage_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
