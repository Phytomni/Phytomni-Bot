# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Heuristics for when to emit A2UI interaction surfaces."""

from __future__ import annotations

import re
from typing import Literal

_CONFIRM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"请确认"),
    re.compile(r"是否确认"),
    re.compile(r"确认是否"),
    re.compile(r"\bconfirm\b"),
)

_FORM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"请填写"),
    re.compile(r"请输入"),
    re.compile(r"fill in"),
    re.compile(r"please enter"),
)

_CHOICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"请选择"),
    re.compile(r"二选一"),
    re.compile(r"choose one"),
    re.compile(r"select one"),
)

ChatA2uiWidget = Literal["confirm", "form", "choice"]


def should_emit_confirm(user_query: str) -> bool:
    """Return True when the query explicitly asks for confirmation."""
    folded = user_query.casefold()
    return any(pattern.search(folded) for pattern in _CONFIRM_PATTERNS)


def should_emit_form(user_query: str) -> bool:
    """Return True when the query asks the user to fill in values."""
    folded = user_query.casefold()
    return any(pattern.search(folded) for pattern in _FORM_PATTERNS)


def should_emit_choice(user_query: str) -> bool:
    """Return True when the query asks the user to pick an option."""
    folded = user_query.casefold()
    return any(pattern.search(folded) for pattern in _CHOICE_PATTERNS)


def select_chat_a2ui_widget(
    user_query: str,
) -> ChatA2uiWidget | None:
    """Return the Chat A2UI widget to emit, or None.

    Priority: confirm > form > choice > None.
    """
    if should_emit_confirm(user_query):
        return "confirm"
    if should_emit_form(user_query):
        return "form"
    if should_emit_choice(user_query):
        return "choice"
    return None
