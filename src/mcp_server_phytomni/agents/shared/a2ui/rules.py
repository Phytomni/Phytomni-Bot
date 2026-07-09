# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Heuristics for when to emit A2UI confirm surfaces."""

from __future__ import annotations

import re

_CONFIRM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"请确认"),
    re.compile(r"是否确认"),
    re.compile(r"确认是否"),
    re.compile(r"\bconfirm\b"),
)


def should_emit_confirm(user_query: str) -> bool:
    """Return True when the query explicitly asks for user confirmation."""
    folded = user_query.casefold()
    return any(pattern.search(folded) for pattern in _CONFIRM_PATTERNS)
