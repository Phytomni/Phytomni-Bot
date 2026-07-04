# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the AguiEvent wire model and its factories."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.mcp.result_formatting import (
    AguiEvent,
    custom,
    run_error,
    run_finished,
    run_started,
    step_started,
    text_message_content,
    text_message_end,
    text_message_start,
)

pytestmark = pytest.mark.server


def test_run_started_carries_ids_and_redundant_type() -> None:
    """run_started embeds run/dialogue ids and a redundant type key."""
    event = run_started("run-1", "dlg-1")
    assert isinstance(event, AguiEvent)
    assert event.type == "RunStarted"
    assert event.data == {
        "type": "RunStarted",
        "run_id": "run-1",
        "dialogue_id": "dlg-1",
    }


def test_text_message_content_delta() -> None:
    """text_message_content carries message_id and the delta string."""
    event = text_message_content("msg-1", "photo")
    assert event.type == "TextMessageContent"
    assert event.data == {
        "type": "TextMessageContent",
        "message_id": "msg-1",
        "delta": "photo",
    }


def test_step_started_step_name() -> None:
    """step_started emits the step_name verbatim."""
    assert step_started("retrieving").data == {
        "type": "StepStarted",
        "step_name": "retrieving",
    }


def test_custom_name_value() -> None:
    """custom frames carry the name and value payload."""
    event = custom("phyto.follow_up", ["a", "b"])
    assert event.data == {
        "type": "Custom",
        "name": "phyto.follow_up",
        "value": ["a", "b"],
    }


def test_run_error_code_message() -> None:
    """run_error pins a stable code and safe message."""
    event = run_error("stream_open_failed", "safe msg")
    assert event.type == "RunError"
    assert event.data == {
        "type": "RunError",
        "code": "stream_open_failed",
        "message": "safe msg",
    }


def test_remaining_factories_shapes() -> None:
    """text_message_start/end and run_finished match the wire shape."""
    assert text_message_start("m").data == {
        "type": "TextMessageStart",
        "message_id": "m",
    }
    assert text_message_end("m").data == {
        "type": "TextMessageEnd",
        "message_id": "m",
    }
    assert run_finished("r").data == {
        "type": "RunFinished",
        "run_id": "r",
    }
