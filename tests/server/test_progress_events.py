# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the progress-event vocabulary and emit_progress helper."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.mcp.progress_events import (
    PROGRESS_KIND,
    ProgressEvent,
    emit_progress,
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


def test_progress_event_maps_losslessly_to_a2a_task_status() -> None:
    """The vocabulary must map onto A2A TaskStatusUpdateEvent shape.

    Spec §5.1 pins this as a forward-compat constraint so Phase 4
    (A2A facade) can reuse the vocabulary with zero changes: phase ->
    TaskStatus.state, current/total -> metadata, detail -> message.
    """
    event: ProgressEvent = {
        "kind": PROGRESS_KIND,
        "phase": "retrieving",
        "current": 3,
        "total": 8,
        "detail": "gene 3/8",
    }
    a2a = {
        "state": event["phase"],
        "message": event["detail"],
        "metadata": {"current": event["current"], "total": event["total"]},
    }
    assert a2a["state"] == "retrieving"
    assert a2a["message"] == "gene 3/8"
    assert a2a["metadata"] == {"current": 3, "total": 8}
