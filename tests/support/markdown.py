# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small Markdown parsers shared by documentation contract tests."""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = ["parse_bold_records"]

_BOLD_FIELD_PATTERN = re.compile(
    r"^\s*(?:-\s+)?\*\*(?P<key>[^*]+):\*\*\s*(?P<value>.*)$"
)


def parse_bold_records(
    lines: Iterable[str],
    first_key: str,
    *,
    stop_at_heading: bool = False,
) -> list[dict[str, str]]:
    """Parse wrapped bold-field records from Markdown lines."""
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    last_key: str | None = None
    for line in lines:
        if stop_at_heading and line.startswith("## "):
            break
        match = _BOLD_FIELD_PATTERN.match(line)
        if match:
            key = match.group("key")
            if key == first_key:
                if current is not None:
                    records.append(current)
                current = {}
            if current is not None:
                current[key] = match.group("value").strip().strip("`")
                last_key = key
            continue
        if current is None or last_key is None or not line.strip():
            continue
        if line.lstrip().startswith("-"):
            continue
        current[last_key] = (
            (f"{current[last_key]} {line.strip()}").strip().strip("`")
        )
    if current is not None:
        records.append(current)
    return records
