# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for analyst/task_ops.py shared kwargs + wait_for_completion.

Pins ``_common_request_kwargs`` (override resolution + retriable-codes
normalization) and ``wait_for_completion``'s status-state machine
(SUCCEEDED return / FAILED + CANCELLED raise / RUNNING poll / unknown
raise / timeout). HTTP wrappers themselves stay e2e-covered.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.analyst import task_ops as task_ops_module
from mcp_server_phytomni.agents.analyst.defaults import ANALYST_CONFIG
from mcp_server_phytomni.agents.analyst.task_ops import (
    _common_request_kwargs,
    wait_for_completion,
)
from mcp_server_phytomni.runtime.outbound import OutboundPoolName
from tests.support.outbound_fakes import ControlledByteStream

pytestmark = pytest.mark.unit


def test_common_request_kwargs_uses_analyst_defaults_when_unset() -> None:
    """Every override falls back to the configured ANALYST_CONFIG value."""
    resolved = _common_request_kwargs({})

    assert resolved["analysis_url"] == ANALYST_CONFIG.ANALYSIS_URL
    assert resolved["region"] == ANALYST_CONFIG.ANALYSIS_REGION
    assert resolved["timeout"] == ANALYST_CONFIG.TIMEOUT
    assert resolved["max_retries"] == ANALYST_CONFIG.MAX_RETRIES
    assert resolved["retriable_codes"] == list(ANALYST_CONFIG.RETRIABLE_CODES)


def test_common_request_kwargs_forwards_user_overrides() -> None:
    """Caller overrides win over ANALYST_CONFIG defaults."""
    resolved = _common_request_kwargs(
        {
            "analysis_url": "https://example.invalid/api",
            "region": "ap-foo-1",
            "timeout": 99,
            "max_retries": 7,
            "retriable_codes": [502, 503],
        }
    )

    assert resolved["analysis_url"] == "https://example.invalid/api"
    assert resolved["region"] == "ap-foo-1"
    assert resolved["timeout"] == 99
    assert resolved["max_retries"] == 7
    assert resolved["retriable_codes"] == [502, 503]


def test_common_request_kwargs_copies_user_retriable_codes_list() -> None:
    """The caller's retriable_codes list is copied, not aliased."""
    user_codes = [504]
    resolved = _common_request_kwargs({"retriable_codes": user_codes})

    assert resolved["retriable_codes"] == [504]
    assert resolved["retriable_codes"] is not user_codes


async def test_wait_for_completion_returns_succeeded_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SUCCEEDED status terminates the loop and returns the payload."""

    async def fake_task_status(
        _task_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "SUCCEEDED", "task_id": "T1"}

    monkeypatch.setattr(task_ops_module, "task_status", fake_task_status)

    result = await wait_for_completion("T1", poll_interval=0, max_poll=5)

    assert result == {"status": "SUCCEEDED", "task_id": "T1"}


async def test_wait_for_completion_raises_on_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAILED status raises McpError mid-loop."""

    async def fake_task_status(
        _task_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "FAILED"}

    monkeypatch.setattr(task_ops_module, "task_status", fake_task_status)

    with pytest.raises(McpError, match="Task failed"):
        await wait_for_completion("T1", poll_interval=0, max_poll=5)


async def test_wait_for_completion_raises_on_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CANCELLED status raises McpError mid-loop."""

    async def fake_task_status(
        _task_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "CANCELLED"}

    monkeypatch.setattr(task_ops_module, "task_status", fake_task_status)

    with pytest.raises(McpError, match="Task cancelled"):
        await wait_for_completion("T1", poll_interval=0, max_poll=5)


async def test_wait_for_completion_raises_on_unknown_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized status raises McpError instead of silently looping."""

    async def fake_task_status(
        _task_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "UNRECOGNIZED"}

    monkeypatch.setattr(task_ops_module, "task_status", fake_task_status)

    with pytest.raises(McpError, match="Task status error"):
        await wait_for_completion("T1", poll_interval=0, max_poll=5)


async def test_wait_for_completion_polls_until_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RUNNING/PENDING statuses keep polling until SUCCEEDED."""
    statuses: list[str] = ["PENDING", "RUNNING", "SUCCEEDED"]
    call_count = {"n": 0}

    async def fake_task_status(
        _task_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        idx = call_count["n"]
        call_count["n"] += 1
        return {"status": statuses[idx]}

    monkeypatch.setattr(task_ops_module, "task_status", fake_task_status)

    result = await wait_for_completion("T1", poll_interval=0, max_poll=5)

    assert result == {"status": "SUCCEEDED"}
    assert call_count["n"] == 3


async def test_wait_for_completion_times_out_when_max_poll_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceeding ``max_poll`` raises ``asyncio.TimeoutError``."""

    async def fake_task_status(
        _task_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "RUNNING"}

    monkeypatch.setattr(task_ops_module, "task_status", fake_task_status)

    with pytest.raises(asyncio.TimeoutError, match="Exceeded max polling"):
        await wait_for_completion("T1", poll_interval=0, max_poll=0)


async def test_analysis_poll_sleep_holds_no_status_slot(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
) -> None:
    """The poll interval begins only after the status attempt releases."""
    sleep_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def fake_token(**_kwargs: Any) -> str:
        return "test-token"

    async def controlled_sleep(_delay: float) -> None:
        sleep_entered.set()
        await release_sleep.wait()

    monkeypatch.setattr(task_ops_module, "get_token", fake_token)
    monkeypatch.setattr(task_ops_module, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(task_ops_module.asyncio, "sleep", controlled_sleep)
    outbound_runtime.transport.enqueue(content=b'{"status":"RUNNING"}')
    outbound_runtime.transport.enqueue(content=b'{"status":"SUCCEEDED"}')
    task = asyncio.create_task(
        wait_for_completion("sleep-task", poll_interval=1, max_poll=30)
    )

    await sleep_entered.wait()
    snapshot = outbound_runtime.runtime.pools.snapshot(
        OutboundPoolName.ANALYSIS_STATUS
    )
    assert snapshot.started == 1
    assert snapshot.in_use == 0
    assert snapshot.waiting == 0

    release_sleep.set()
    assert await task == {"status": "SUCCEEDED"}


async def test_analysis_poll_cancellation_closes_source_and_releases_status(
    monkeypatch: pytest.MonkeyPatch,
    outbound_runtime: Any,
) -> None:
    """Cancelling an active poll leaves no borrower, waiter, or open body."""
    source_entered = asyncio.Event()
    hold_source = asyncio.Event()
    source = ControlledByteStream(
        b'{"status":"RUNNING"}',
        entered=source_entered,
        release=hold_source,
    )

    async def fake_token(**_kwargs: Any) -> str:
        return "test-token"

    monkeypatch.setattr(task_ops_module, "get_token", fake_token)
    monkeypatch.setattr(task_ops_module, "relay_mode_enabled", lambda: False)
    outbound_runtime.transport.enqueue(stream=source)
    task = asyncio.create_task(
        wait_for_completion("cancel-task", poll_interval=1, max_poll=30)
    )

    await source_entered.wait()
    held = outbound_runtime.runtime.pools.snapshot(
        OutboundPoolName.ANALYSIS_STATUS
    )
    assert held.in_use == 1
    assert held.waiting == 0

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    released = outbound_runtime.runtime.pools.snapshot(
        OutboundPoolName.ANALYSIS_STATUS
    )
    assert released.in_use == 0
    assert released.waiting == 0
    assert released.cancelled == 1
    assert source.closed is True
