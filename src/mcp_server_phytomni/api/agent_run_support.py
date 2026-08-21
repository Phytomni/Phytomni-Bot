# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Small request helpers for native agent runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def request_info_query(
    arguments: Mapping[str, Any], request_json: str | None
) -> str | None:
    """Resolve the original query without trusting attachment maps."""
    for key in ("user_query", "goal_description"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    try:
        payload = json.loads(request_json or "{}")
    except (AttributeError, TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    for key in ("user_query", "goal_description"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None
