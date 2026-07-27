# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared operational models that must remain independent of formatting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionWarning:
    """Safe warning metadata for an operational execution projection."""

    code: str
    retryable: bool = False
    stage: str | None = None


__all__ = ["ExecutionWarning"]
