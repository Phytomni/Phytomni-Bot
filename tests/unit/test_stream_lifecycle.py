# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for typed AG-UI stream lifecycle projection."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.mcp.result_formatting import (
    AguiEvent,
    run_error,
    run_finished,
    run_started,
)
from mcp_server_phytomni.mcp.stream_lifecycle import (
    MAX_STREAM_ERROR_MESSAGE_LENGTH,
    EmptyStreamError,
    StreamLifecycleState,
    prime_agui_stream,
    project_stream_failures,
)

pytestmark = pytest.mark.unit

_EMPTY_EVENTS: tuple[AguiEvent, ...] = ()


async def _collect(events: AsyncIterator[AguiEvent]) -> list[AguiEvent]:
    """Collect one projected stream for assertions."""
    return [event async for event in events]


async def _yield_then_raise() -> AsyncIterator[AguiEvent]:
    """Open a run, then raise an unexpected error with secret-shaped text."""
    yield run_started("run-1", None)
    raise RuntimeError("backend token=hidden https://internal.example/path")


async def _error_then_raise() -> AsyncIterator[AguiEvent]:
    """Emit an error frame, then raise to exercise duplicate suppression."""
    yield run_started("run-1", None)
    yield run_error("agent_execution_failed", "already safe")
    raise RuntimeError("second failure")


async def _cancelled_events() -> AsyncIterator[AguiEvent]:
    """Open a run, then propagate cancellation from the producer."""
    yield run_started("run-1", None)
    for event in _EMPTY_EVENTS:
        yield event
    raise asyncio.CancelledError


async def _error_then_finish() -> AsyncIterator[AguiEvent]:
    """Emit contradictory error and finish frames for projection testing."""
    yield run_started("run-1", None)
    yield run_error("agent_execution_failed", "already safe")
    yield run_finished("run-1")


async def _mcp_error_stream() -> AsyncIterator[AguiEvent]:
    """Raise an MCP error containing long secret-shaped details."""
    yield run_started("run-1", None)
    detail = "token=hidden https://internal.example/path " + ("x" * 2048)
    raise McpError(ErrorData(code=INTERNAL_ERROR, message=detail))


async def _unexpected_error_stream() -> AsyncIterator[AguiEvent]:
    """Raise an unexpected error whose message must not enter logs."""
    yield run_started("run-1", None)
    raise ValueError("raw secret=hidden https://internal.example/path")


async def _empty_stream() -> AsyncIterator[AguiEvent]:
    """Produce no AG-UI frames."""
    for event in _EMPTY_EVENTS:
        yield event


async def _prime_failure_stream() -> AsyncIterator[AguiEvent]:
    """Raise before producing a first frame."""
    for event in _EMPTY_EVENTS:
        yield event
    raise RuntimeError("prime failed")


async def test_opened_failure_emits_one_error_and_no_finish() -> None:
    """Unexpected opened failures become one fixed safe RunError frame."""
    state = StreamLifecycleState()
    events = await _collect(
        project_stream_failures(
            _yield_then_raise(),
            state=state,
            run_id="run-1",
            request_id="req-1",
        )
    )

    assert [event.type for event in events] == ["RunStarted", "RunError"]
    assert events[-1].data["message"] == "Agent execution failed"
    assert state.saw_error is True
    assert state.reached_finish is False


async def test_existing_run_error_suppresses_duplicate_after_raise() -> None:
    """An already emitted error is not duplicated after a later raise."""
    state = StreamLifecycleState()
    projected = await _collect(
        project_stream_failures(
            _error_then_raise(),
            state=state,
            run_id="run-1",
            request_id="req-1",
        )
    )

    assert sum(event.type == "RunError" for event in projected) == 1
    assert [event.type for event in projected] == ["RunStarted", "RunError"]


async def test_cancelled_error_propagates() -> None:
    """Cancellation is never converted into a protocol error frame."""
    with pytest.raises(asyncio.CancelledError):
        await _collect(
            project_stream_failures(
                _cancelled_events(),
                state=StreamLifecycleState(),
                run_id="run-1",
                request_id="req-1",
            )
        )


async def test_run_finished_is_suppressed_after_run_error() -> None:
    """A contradictory finish frame cannot follow an emitted error."""
    projected = await _collect(
        project_stream_failures(
            _error_then_finish(),
            state=StreamLifecycleState(),
            run_id="run-1",
            request_id="req-1",
        )
    )

    assert [event.type for event in projected] == ["RunStarted", "RunError"]


async def test_known_mcp_error_is_redacted_and_capped() -> None:
    """Known MCP detail is redacted and bounded before it reaches a frame."""
    projected = await _collect(
        project_stream_failures(
            _mcp_error_stream(),
            state=StreamLifecycleState(),
            run_id="run-1",
            request_id="req-1",
        )
    )
    message = projected[-1].data["message"]

    assert len(message) <= MAX_STREAM_ERROR_MESSAGE_LENGTH
    assert "hidden" not in message
    assert "internal.example" not in message
    assert message.startswith("<redacted-secret>")


async def test_unexpected_error_logs_location_without_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected logs include identity/location but not raw exception text."""
    caplog.set_level(
        logging.ERROR,
        logger="mcp_server_phytomni.mcp.stream_lifecycle",
    )
    await _collect(
        project_stream_failures(
            _unexpected_error_stream(),
            state=StreamLifecycleState(),
            run_id="run-1",
            request_id="req-1",
        )
    )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    rendered = caplog.text
    assert "run-1" in rendered
    assert "req-1" in rendered
    assert "ValueError" in rendered
    assert "_unexpected_error_stream" in rendered
    assert "raw secret" not in rendered
    assert "internal.example" not in rendered
    assert record.exc_info is None


async def test_prime_agui_stream_returns_first_and_remainder() -> None:
    """Priming exposes one frame while retaining the unconsumed iterator."""
    primed = await prime_agui_stream(_error_then_finish())
    remainder = await _collect(primed.remainder)

    assert primed.first.type == "RunStarted"
    assert [event.type for event in remainder] == ["RunError", "RunFinished"]


async def test_prime_empty_stream_raises() -> None:
    """An empty stream is a setup failure, not a successful response."""
    with pytest.raises(EmptyStreamError):
        await prime_agui_stream(_empty_stream())


async def test_prime_propagates_raw_failure() -> None:
    """A first-frame producer error remains the original exception."""
    with pytest.raises(RuntimeError, match="prime failed"):
        await prime_agui_stream(_prime_failure_stream())
