# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Stable output layout for result-delivery child submissions."""

from __future__ import annotations

import re

RESULT_DELIVERY_AGENTS = frozenset(
    {"analyst", "research", "network", "design"}
)
_CHILD_SEGMENT = re.compile(r"^part-(?:00[1-9]|0[1-9][0-9]|1[0-9]{2})$")


def result_child_output_dir(run_root: str, child_index: int) -> str:
    """Return the stable one-based child directory below one run root."""
    root = run_root.rstrip("/")
    if not root or child_index < 0 or child_index >= 199:
        raise ValueError("invalid result child layout")
    return f"{root}/children/part-{child_index + 1:03d}"


def result_run_root_from_child(child_dir: str) -> str:
    """Recover the umbrella root from one validated result child path."""
    root, marker, part = child_dir.rstrip("/").rpartition("/children/")
    if not marker or not root or _CHILD_SEGMENT.fullmatch(part) is None:
        raise ValueError("output directory is not a result child")
    return root
