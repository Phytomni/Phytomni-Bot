# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bounded A2UI multi-turn helpers (hard cap N=2)."""

from __future__ import annotations

from typing import Any, Final

from .rules import select_chat_a2ui_widget

A2UI_MAX_ROUNDS: Final = 2


def should_reenter_a2ui(*, text: str, a2ui_round: int) -> bool:
    """Return True when another A2UI pause is allowed after work.

    Re-entry requires ``a2ui_round`` below :data:`A2UI_MAX_ROUNDS` and a
    non-None widget heuristic match on ``text``.

    Args:
        text: Assistant (or other) text to score for a widget cue.
        a2ui_round: Completed A2UI rounds so far (1 after first mint).

    Returns:
        Whether the graph should clear the surface and remint.
    """
    if a2ui_round >= A2UI_MAX_ROUNDS:
        return False
    return select_chat_a2ui_widget(text) is not None


def clear_a2ui_for_reenter() -> dict[str, Any]:
    """Return a state delta that clears surface and decision for remint."""
    return {"a2ui_surface": None, "a2ui_decision": None}


def next_a2ui_round(current: int | None) -> int:
    """Increment the A2UI round counter (None treated as 0)."""
    return (current or 0) + 1
