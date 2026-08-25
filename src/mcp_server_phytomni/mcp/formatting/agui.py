# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""AG-UI event models and wire-shape constructors."""

from __future__ import annotations

from typing import Any

from .models import AguiEvent


def run_started(run_id: str, dialogue_id: str | None) -> AguiEvent:
    """Return the opening ``RunStarted`` frame carrying registry ids."""
    return AguiEvent(
        type="RunStarted",
        data={
            "type": "RunStarted",
            "run_id": run_id,
            "dialogue_id": dialogue_id,
        },
    )


def text_message_start(message_id: str) -> AguiEvent:
    """Return the ``TextMessageStart`` frame."""
    return AguiEvent(
        type="TextMessageStart",
        data={"type": "TextMessageStart", "message_id": message_id},
    )


def text_message_content(message_id: str, delta: str) -> AguiEvent:
    """Return one ``TextMessageContent`` delta frame."""
    from ...runtime.execution_instrumentation_v2 import (
        publish_execution_content_delta,
    )

    publish_execution_content_delta(delta)
    return AguiEvent(
        type="TextMessageContent",
        data={
            "type": "TextMessageContent",
            "message_id": message_id,
            "delta": delta,
        },
    )


def text_message_end(message_id: str) -> AguiEvent:
    """Return the ``TextMessageEnd`` frame."""
    return AguiEvent(
        type="TextMessageEnd",
        data={"type": "TextMessageEnd", "message_id": message_id},
    )


def run_finished(run_id: str) -> AguiEvent:
    """Return the terminal ``RunFinished`` frame."""
    return AguiEvent(
        type="RunFinished",
        data={"type": "RunFinished", "run_id": run_id},
    )


def run_error(code: str, message: str) -> AguiEvent:
    """Return a ``RunError`` frame with a stable safe message."""
    return AguiEvent(
        type="RunError",
        data={"type": "RunError", "code": code, "message": message},
    )


def step_started(step_name: str) -> AguiEvent:
    """Return a ``StepStarted`` frame naming one semantic stage."""
    return AguiEvent(
        type="StepStarted",
        data={"type": "StepStarted", "step_name": step_name},
    )


def custom(name: str, value: Any) -> AguiEvent:
    """Return a ``Custom`` profile frame."""
    from ...runtime.legacy_event_adapter_v2 import adapt_agui_custom_to_v2

    adapt_agui_custom_to_v2(name, value)
    return AguiEvent(
        type="Custom",
        data={"type": "Custom", "name": name, "value": value},
    )


__all__ = [
    "AguiEvent",
    "custom",
    "run_error",
    "run_finished",
    "run_started",
    "step_started",
    "text_message_content",
    "text_message_end",
    "text_message_start",
]
