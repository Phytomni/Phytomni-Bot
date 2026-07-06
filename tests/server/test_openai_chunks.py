# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for ``to_chat_completion_chunks`` SSE shaper.

Pins AG-UI ``event:``/``data:`` framing, verbatim event-data
passthrough, the absence of any OpenAI-style ``model``/``object``
projection, and terminal ``[DONE]`` behavior for zero, one, or many
upstream events.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from mcp_server_phytomni.api.openai_mapping import to_chat_completion_chunks
from mcp_server_phytomni.mcp.result_formatting import (
    AguiEvent,
    run_finished,
    run_started,
    text_message_content,
)

pytestmark = pytest.mark.server


async def _async_iter(
    events: list[AguiEvent],
) -> AsyncIterator[AguiEvent]:
    """Yield each pre-built event so tests can feed lists into the shaper."""
    for event in events:
        yield event


def _parse_frame(line: str) -> dict:
    """Split an ``event: T\\ndata: {...}\\n\\n`` frame, parse the JSON."""
    assert line.endswith("\n\n")
    event_line, data_line = line[: -len("\n\n")].split("\n", 1)
    assert event_line.startswith("event: ")
    assert data_line.startswith("data: ")
    return json.loads(data_line[len("data: ") :])


async def test_events_emit_event_data_lines_and_done_terminator() -> None:
    """Two upstream events produce two frames plus ``[DONE]``.

    Pins the SSE wire format: each event line is exactly
    ``event: <Type>\\ndata: {...}\\n\\n``, and the terminator is the
    literal ``data: [DONE]\\n\\n`` line so OpenAI-compatible clients
    close their EventSource on the first match instead of waiting for
    an idle timeout.
    """
    events = [
        text_message_content("m-1", "Hel"),
        text_message_content("m-1", "lo"),
    ]

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter(events), model="phyto-chat"
        )
    ]

    assert len(lines) == 3
    assert lines[-1] == "data: [DONE]\n\n"
    parsed_first = _parse_frame(lines[0])
    assert parsed_first["message_id"] == "m-1"
    assert parsed_first["delta"] == "Hel"


async def test_shaper_emits_event_data_verbatim() -> None:
    """Arbitrary/unrecognized event data survives untouched in the frame.

    Pins the forward-compatibility promise: the shaper does not
    allowlist or filter keys inside ``event.data`` — even an event
    type the shaper has never learned about has its payload
    serialized unchanged, so producers can add new event kinds
    without a server-side migration.
    """
    event = AguiEvent(
        type="VendorPreview",
        data={
            "type": "VendorPreview",
            "detail": {"latency_ms": 12, "shard": "west"},
        },
    )

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter([event]), model="phyto-chat"
        )
    ]

    assert lines[0].startswith("event: VendorPreview\n")
    parsed = _parse_frame(lines[0])
    assert parsed["detail"] == {"latency_ms": 12, "shard": "west"}


async def test_shaper_does_not_project_model_onto_frames() -> None:
    """The requested ``model`` id is never echoed onto an AG-UI frame.

    Pins the AG-UI framing contract (spec §3.2): unlike
    :func:`to_chat_completion`, this shaper does not inject or
    override any ``model`` field — the event's ``data`` mapping is the
    sole source of frame content, regardless of what ``model`` the
    caller passes.
    """
    event = run_started("run-1", "dlg-1")

    lines_a = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter([event]), model="phyto-chat"
        )
    ]
    lines_b = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter([event]), model="some-other-model"
        )
    ]

    assert lines_a == lines_b
    assert "model" not in _parse_frame(lines_a[0])


async def test_shaper_adds_no_fields_beyond_event_data() -> None:
    """The shaper injects no default or derived fields onto any frame.

    Pins the removal of the old OpenAI-shaper projections (the
    ``object`` setdefault and the ``model`` override): the emitted
    JSON is an exact round-trip of ``event.data`` with nothing added
    and nothing removed.
    """
    event = AguiEvent(
        type="StepStarted",
        data={"type": "StepStarted", "step_name": "retrieve"},
    )

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter([event]), model="phyto-chat"
        )
    ]

    assert _parse_frame(lines[0]) == dict(event.data)


async def test_to_chat_completion_chunks_empty_stream_still_emits_done() -> (
    None
):
    """Zero upstream events still yield exactly one ``[DONE]`` terminator.

    Pins the corner case where the upstream stream is immediately
    exhausted (e.g. the run aborted before any event was emitted).
    Without the terminator, clients would hang on their EventSource
    until the read timeout fired.
    """
    events: list[AguiEvent] = []

    lines = [
        line
        async for line in to_chat_completion_chunks(
            _async_iter(events), model="phyto-chat"
        )
    ]

    assert lines == ["data: [DONE]\n\n"]


async def test_shaper_renders_event_and_data_lines() -> None:
    """Each AguiEvent becomes an ``event: T`` + ``data: {...}`` frame."""

    async def _events():
        yield run_started("run-9", "dlg-9")
        yield text_message_content("m-9", "Hi")
        yield run_finished("run-9")

    lines = [
        line
        async for line in to_chat_completion_chunks(_events(), "phyto-chat")
    ]
    assert lines[0] == (
        "event: RunStarted\n"
        'data: {"type": "RunStarted", "run_id": "run-9", '
        '"dialogue_id": "dlg-9"}\n\n'
    )
    assert lines[1].startswith("event: TextMessageContent\n")
    assert '"delta": "Hi"' in lines[1]
    assert lines[-1] == "data: [DONE]\n\n"
