# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the streamed-answer accumulator."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from mcp_server_phytomni.api.stream_answer import (
    DEFAULT_STREAM_ANSWER_MAX_BYTES,
    StreamAnswerAccumulator,
    resolve_stream_answer_max_bytes,
)
from mcp_server_phytomni.mcp.result_formatting import (
    AguiEvent,
    run_error,
    run_finished,
    run_started,
    text_message_content,
)
from mcp_server_phytomni.mcp.stream_lifecycle import StreamLifecycleState

pytestmark = pytest.mark.unit


async def _drain(acc: StreamAnswerAccumulator) -> list[AguiEvent]:
    """Consume the accumulator and return yielded events."""
    return [event async for event in acc]


async def _events(*items: AguiEvent) -> AsyncIterator[AguiEvent]:
    """Yield a fixed AguiEvent sequence."""
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_accumulator_concatenates_text_deltas() -> None:
    """TextMessageContent deltas concatenate into snapshot.answer."""
    acc = StreamAnswerAccumulator(
        _events(
            run_started("r1", None),
            text_message_content("m1", "a"),
            text_message_content("m1", "b"),
            text_message_content("m1", "c"),
            run_finished("r1"),
        ),
        max_bytes=1024,
    )
    yielded = await _drain(acc)
    snap = acc.snapshot
    assert snap.answer == "abc"
    assert snap.truncated is False
    assert snap.reached_finish is True
    assert snap.saw_error is False
    assert [e.type for e in yielded] == [
        "RunStarted",
        "TextMessageContent",
        "TextMessageContent",
        "TextMessageContent",
        "RunFinished",
    ]


@pytest.mark.asyncio
async def test_accumulator_soft_cap_truncates_storage_not_wire() -> None:
    """Over-cap storage truncates; yielded deltas stay full."""
    acc = StreamAnswerAccumulator(
        _events(
            text_message_content("m1", "abcd"),
            text_message_content("m1", "efgh"),
            run_finished("r1"),
        ),
        max_bytes=5,
    )
    yielded = await _drain(acc)
    snap = acc.snapshot
    assert len(snap.answer.encode("utf-8")) <= 5
    assert snap.truncated is True
    assert snap.reached_finish is True
    assert yielded[0].data["delta"] == "abcd"
    assert yielded[1].data["delta"] == "efgh"


@pytest.mark.asyncio
async def test_accumulator_marks_partial_on_run_error() -> None:
    """RunError sets saw_error; answer keeps the prefix."""
    acc = StreamAnswerAccumulator(
        _events(
            text_message_content("m1", "Hi"),
            run_error("agent_execution_failed", "boom"),
        ),
        max_bytes=1024,
    )
    await _drain(acc)
    snap = acc.snapshot
    assert snap.answer == "Hi"
    assert snap.saw_error is True
    assert snap.reached_finish is False


@pytest.mark.asyncio
async def test_accumulator_reuses_shared_lifecycle_state() -> None:
    """The HTTP settle state is the accumulator's lifecycle source."""
    lifecycle = StreamLifecycleState()
    acc = StreamAnswerAccumulator(
        _events(
            text_message_content("m1", "Hi"),
            run_error("agent_execution_failed", "boom"),
        ),
        max_bytes=1024,
        lifecycle_state=lifecycle,
    )

    await _drain(acc)

    snap = acc.snapshot
    assert snap.saw_error is True
    assert snap.reached_finish is False
    assert lifecycle.saw_error is True
    assert lifecycle.reached_finish is False


@pytest.mark.asyncio
async def test_accumulator_utf8_safe_prefix() -> None:
    """A multi-byte char straddling the cap does not corrupt UTF-8."""
    # "é" is 2 bytes in UTF-8; cap=3 keeps "ab" + first byte of é dropped.
    acc = StreamAnswerAccumulator(
        _events(text_message_content("m1", "abécd")),
        max_bytes=3,
    )
    await _drain(acc)
    snap = acc.snapshot
    assert snap.truncated is True
    assert snap.answer == "ab"
    assert snap.answer.encode("utf-8")  # must not raise
    assert len(snap.answer.encode("utf-8")) <= 3


def test_resolve_stream_answer_max_bytes_rejects_non_positive() -> None:
    """Non-positive config falls back to the default."""
    assert (
        resolve_stream_answer_max_bytes(0) == DEFAULT_STREAM_ANSWER_MAX_BYTES
    )
    assert (
        resolve_stream_answer_max_bytes(-1) == DEFAULT_STREAM_ANSWER_MAX_BYTES
    )
    assert resolve_stream_answer_max_bytes(4096) == 4096
