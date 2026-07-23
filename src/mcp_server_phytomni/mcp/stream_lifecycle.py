# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed lifecycle handling for opened AG-UI streams.

The HTTP layer must observe typed :class:`AguiEvent` frames before they are
serialized as SSE. This module owns first-frame priming, lifecycle flags, and
the one-error projection used when an opened producer fails.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final

from mcp.shared.exceptions import McpError

from ..common.redaction import redact_secrets
from .result_formatting import AguiEvent, run_error

logger = logging.getLogger(__name__)

MAX_STREAM_ERROR_MESSAGE_LENGTH: Final[int] = 512
GENERIC_STREAM_ERROR_MESSAGE: Final[str] = "Agent execution failed"
_SQL_RE: Final = re.compile(
    r"(?is)\b(?:select|insert|update|delete|with|create|alter|drop|"
    r"grant|revoke)\b.*"
)
_STREAM_FAILURE_CAUGHT: tuple[type[Exception], ...] = (Exception,)


@dataclass
class StreamLifecycleState:
    """Mutable typed state observed while an AG-UI stream is projected."""

    reached_finish: bool = False
    saw_error: bool = False

    def observe(self, event: AguiEvent) -> None:
        """Record lifecycle terminal frames without inspecting SSE text."""
        if event.type == "RunFinished":
            self.reached_finish = True
        elif event.type == "RunError":
            self.saw_error = True


@dataclass(frozen=True)
class PrimedAguiStream:
    """The first typed event and the still-open remainder iterator."""

    first: AguiEvent
    remainder: AsyncIterator[AguiEvent]


class StreamPrimeError(RuntimeError):
    """Base error for a stream that cannot produce a first AG-UI frame."""


class EmptyStreamError(StreamPrimeError):
    """Raised when an opened stream completes without emitting a frame."""


async def prime_agui_stream(
    events: AsyncIterator[AguiEvent],
) -> PrimedAguiStream:
    """Consume and retain the first frame, preserving the raw remainder.

    ``StopAsyncIteration`` is converted to :class:`EmptyStreamError` so the
    HTTP caller can map an empty producer to a pre-header server failure.
    Any other producer exception, including cancellation, propagates intact.
    """
    iterator = aiter(events)
    try:
        first = await anext(iterator)
    except StopAsyncIteration as exc:
        raise EmptyStreamError("stream produced no AG-UI events") from exc
    return PrimedAguiStream(first=first, remainder=iterator)


def _last_traceback_location(
    exc: Exception,
) -> tuple[str, str, int]:
    """Return only module/function/line from an exception traceback."""
    traceback = exc.__traceback__
    if traceback is None:
        return "<unknown>", "<unknown>", 0
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    module = str(traceback.tb_frame.f_globals.get("__name__", "<unknown>"))
    function = traceback.tb_frame.f_code.co_name
    return module, function, traceback.tb_lineno


def _log_unexpected_failure(
    exc: Exception,
    *,
    run_id: str,
    request_id: str,
) -> None:
    """Log safe identity, class, and source location without exception text."""
    module, function, line = _last_traceback_location(exc)
    logger.error(
        "stream failure run_id=%s request_id=%s exception=%s "
        "location=%s.%s:%s",
        run_id,
        request_id,
        type(exc).__name__,
        module,
        function,
        line,
    )


def _mcp_error_message(exc: McpError) -> str:
    """Return a redacted, bounded message from a known MCP error."""
    error = getattr(exc, "error", None)
    message = getattr(error, "message", None)
    if not isinstance(message, str) or not message:
        return GENERIC_STREAM_ERROR_MESSAGE
    redacted = redact_secrets(message)
    redacted = _SQL_RE.sub("<redacted-sql>", redacted)
    if not redacted:
        return GENERIC_STREAM_ERROR_MESSAGE
    return redacted[:MAX_STREAM_ERROR_MESSAGE_LENGTH]


def _emit_error_if_needed(
    state: StreamLifecycleState,
    message: str,
) -> AguiEvent | None:
    """Build one failure frame unless the lifecycle is already terminal."""
    if state.saw_error or state.reached_finish:
        return None
    event = run_error("agent_execution_failed", message)
    state.observe(event)
    return event


def _project_stream_failure(
    state: StreamLifecycleState,
    exc: Exception,
    *,
    run_id: str,
    request_id: str,
) -> AguiEvent | None:
    """Map one ordinary producer failure to a safe terminal frame."""
    if isinstance(exc, McpError):
        message = _mcp_error_message(exc)
    else:
        _log_unexpected_failure(exc, run_id=run_id, request_id=request_id)
        message = GENERIC_STREAM_ERROR_MESSAGE
    return _emit_error_if_needed(state, message)


async def project_stream_failures(
    events: AsyncIterator[AguiEvent],
    *,
    state: StreamLifecycleState,
    run_id: str,
    request_id: str,
) -> AsyncIterator[AguiEvent]:
    """Project opened-stream failures into one safe typed error frame.

    Existing ``RunError`` frames are preserved once and mark the lifecycle;
    contradictory or duplicate ``RunFinished``/``RunError`` frames are
    suppressed. Only ordinary ``Exception`` values are handled. Cancellation
    and generator shutdown remain ``BaseException`` paths and propagate.
    """
    try:
        async for event in events:
            if event.type == "RunError":
                if state.saw_error:
                    continue
                state.observe(event)
                yield event
                continue
            if event.type == "RunFinished":
                if state.saw_error:
                    continue
                state.observe(event)
                yield event
                continue
            yield event
    except McpError as exc:
        error_event = _emit_error_if_needed(state, _mcp_error_message(exc))
        if error_event is not None:
            yield error_event
    # This is the opened-stream safety boundary: every ordinary producer
    # exception must become one fixed protocol error. The typed tuple catches
    # all ``Exception`` instances, while BaseException cancellation/shutdown
    # paths remain outside the catch and propagate unchanged.
    except _STREAM_FAILURE_CAUGHT as exc:
        error_event = _project_stream_failure(
            state,
            exc,
            run_id=run_id,
            request_id=request_id,
        )
        if error_event is not None:
            yield error_event


__all__ = [
    "EmptyStreamError",
    "GENERIC_STREAM_ERROR_MESSAGE",
    "MAX_STREAM_ERROR_MESSAGE_LENGTH",
    "PrimedAguiStream",
    "StreamLifecycleState",
    "StreamPrimeError",
    "prime_agui_stream",
    "project_stream_failures",
]
