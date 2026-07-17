# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared collector errors and reverse-probe evidence."""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Finding


class CollectionError(RuntimeError):
    """Raised when a collector or reverse probe cannot prove its result."""


@dataclass(frozen=True, slots=True)
class ReverseEvidence:
    """Validated output from a suppression reverse probe."""

    finding: Finding
    command: tuple[str, ...]
    returncode: int
    output: str
    necessary: bool
