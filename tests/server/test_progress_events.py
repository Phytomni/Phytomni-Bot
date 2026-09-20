# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the progress-event vocabulary and emit_progress helper."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.mcp.progress_events import (
    PROGRESS_KIND,
    ProgressEvent,
    emit_progress,
)
from mcp_server_phytomni.runtime.execution_event_sink import (
    bind_execution_event_sink,
)

pytestmark = pytest.mark.server


def test_emit_progress_outside_graph_is_silent_noop() -> None:
    """emit_progress must not raise when no stream writer is active.

    get_stream_writer() raises RuntimeError outside a runnable
    context; emit_progress swallows it so a node stays safe under
    blocking ainvoke and direct unit calls.
    """
    emit_progress("retrieving", 1, total=10, detail="gene 1/10")


def test_emit_progress_writes_full_event_when_writer_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a writer is active, emit_progress writes a complete event."""
    seen: list[Any] = []

    def _fake_writer() -> Any:
        return seen.append

    monkeypatch.setattr(
        "mcp_server_phytomni.mcp.progress_events.get_stream_writer",
        _fake_writer,
    )

    emit_progress("drafting", 2, total=5, detail="dim 2/5")

    assert seen == [
        {
            "kind": PROGRESS_KIND,
            "phase": "drafting",
            "current": 2,
            "total": 5,
            "detail": "dim 2/5",
        }
    ]


def test_emit_progress_defaults_total_and_detail_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitted total/detail land as None in the event."""
    seen: list[Any] = []
    monkeypatch.setattr(
        "mcp_server_phytomni.mcp.progress_events.get_stream_writer",
        lambda: seen.append,
    )

    emit_progress("planning", 0)

    assert seen == [
        {
            "kind": PROGRESS_KIND,
            "phase": "planning",
            "current": 0,
            "total": None,
            "detail": None,
        }
    ]


def test_progress_event_preserves_protocol_projection_inputs() -> None:
    """Protocol adapters retain progress without inventing task state.

    ``phase`` describes work inside a running task; it is not a task
    lifecycle state. A future A2A adapter can carry the progress fields
    in status metadata while deriving ``TaskState`` independently from
    the run lifecycle.
    """
    event: ProgressEvent = {
        "kind": PROGRESS_KIND,
        "phase": "retrieving",
        "current": 3,
        "total": 8,
        "detail": "gene 3/8",
    }
    projection = {
        "message": event["detail"],
        "metadata": {
            "kind": event["kind"],
            "phase": event["phase"],
            "current": event["current"],
            "total": event["total"],
        },
    }
    assert "state" not in projection
    assert projection["message"] == "gene 3/8"
    assert projection["metadata"] == {
        "kind": PROGRESS_KIND,
        "phase": "retrieving",
        "current": 3,
        "total": 8,
    }


def test_emit_progress_also_adapts_to_context_bound_canonical_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify emit progress also adapts to context bound canonical event."""
    intents: list[Any] = []
    sink = SimpleNamespace(emit=intents.append)

    monkeypatch.setattr(
        "mcp_server_phytomni.mcp.progress_events.get_stream_writer",
        lambda: lambda _event: None,
    )
    with bind_execution_event_sink(sink):
        emit_progress(
            "retrieving",
            3,
            total=8,
            detail="private provider detail",
        )

    assert [intent.kind for intent in intents] == ["phase.progress"]
    assert intents[0].payload.phase == "retrieving"
    assert intents[0].payload.completed == 3
    assert "private provider detail" not in repr(
        intents[0].model_dump(mode="json")
    )
