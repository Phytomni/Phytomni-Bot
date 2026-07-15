# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Accumulate streamed ChatAgent answer text for run-registry settle.

Wraps an ``AsyncIterator[AguiEvent]`` between ``invoke_tool_streamed``
and the SSE shaper so HTTP settle can persist the real concatenated
answer instead of a ``"[streamed]"`` placeholder. Soft-caps only the
stored blob; wire events are always forwarded unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..mcp.result_formatting import AguiEvent
from ..mcp.stream_lifecycle import StreamLifecycleState

logger = logging.getLogger(__name__)

DEFAULT_STREAM_ANSWER_MAX_BYTES = 1_048_576


def resolve_stream_answer_max_bytes(raw: int) -> int:
    """Return a positive byte cap, falling back to the default.

    Args:
        raw: Configured max bytes from ``ApiConfig``.

    Returns:
        ``raw`` when ``raw >= 1``, otherwise
        ``DEFAULT_STREAM_ANSWER_MAX_BYTES`` after a warning.
    """
    if raw >= 1:
        return raw
    logger.warning(
        "STREAM_ANSWER_MAX_BYTES=%s is invalid; using default %s",
        raw,
        DEFAULT_STREAM_ANSWER_MAX_BYTES,
    )
    return DEFAULT_STREAM_ANSWER_MAX_BYTES


def _utf8_prefix(text: str, max_bytes: int) -> str:
    """Return a UTF-8-safe prefix of ``text`` within ``max_bytes``."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class StreamAnswerSnapshot:
    """Immutable view of accumulator state after (or during) iteration.

    Attributes:
        answer: Concatenated / truncated answer text for the registry.
        truncated: True when the soft cap dropped any stored bytes.
        reached_finish: True when a ``RunFinished`` event was seen.
        saw_error: True when a ``RunError`` event was seen.
    """

    answer: str
    truncated: bool
    reached_finish: bool
    saw_error: bool


class StreamAnswerAccumulator:
    """Async iterator that forwards AguiEvents and accumulates answer text.

    Attributes:
        max_bytes: Soft UTF-8 byte cap for the stored answer.
    """

    def __init__(
        self,
        events: AsyncIterator[AguiEvent],
        *,
        max_bytes: int,
        lifecycle_state: StreamLifecycleState | None = None,
    ) -> None:
        """Bind the upstream event iterator and storage cap.

        Args:
            events: Upstream ``AguiEvent`` stream (already typed).
            max_bytes: Soft cap for registry storage (must be >= 1;
                callers should run ``resolve_stream_answer_max_bytes``).
            lifecycle_state: Shared typed lifecycle state used by the
                opened-stream projector and the HTTP settle path.
        """
        self._events = events
        self.max_bytes = max_bytes
        self._lifecycle_state = (
            lifecycle_state
            if lifecycle_state is not None
            else StreamLifecycleState()
        )
        self._answer = ""
        self._truncated = False

    @property
    def snapshot(self) -> StreamAnswerSnapshot:
        """Return the current accumulated settle snapshot."""
        return StreamAnswerSnapshot(
            answer=self._answer,
            truncated=self._truncated,
            reached_finish=self._lifecycle_state.reached_finish,
            saw_error=self._lifecycle_state.saw_error,
        )

    def _append_delta(self, delta: str) -> None:
        """Append one content delta under the soft byte cap."""
        if self._truncated or not delta:
            return
        candidate = self._answer + delta
        encoded = candidate.encode("utf-8")
        if len(encoded) <= self.max_bytes:
            self._answer = candidate
            return
        self._answer = _utf8_prefix(candidate, self.max_bytes)
        self._truncated = True

    def __aiter__(self) -> StreamAnswerAccumulator:
        """Return self as the async iterator."""
        return self

    async def __anext__(self) -> AguiEvent:
        """Yield the next upstream event after updating snapshot flags."""
        event = await self._events.__anext__()
        event_type = event.type
        if event_type == "TextMessageContent":
            delta = event.data.get("delta")
            if isinstance(delta, str):
                self._append_delta(delta)
        self._lifecycle_state.observe(event)
        return event


__all__ = [
    "DEFAULT_STREAM_ANSWER_MAX_BYTES",
    "StreamAnswerAccumulator",
    "StreamAnswerSnapshot",
    "resolve_stream_answer_max_bytes",
]
