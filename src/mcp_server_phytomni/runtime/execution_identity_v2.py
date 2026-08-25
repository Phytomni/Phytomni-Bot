# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical public execution identity allocation."""

from __future__ import annotations

from uuid import uuid4


def new_execution_id() -> str:
    """Return the one public identity shape used by every transport."""
    return f"turn-{uuid4()}"
