# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Public value types for logical outbound request pools."""

from enum import StrEnum
from typing import NamedTuple


class OutboundPoolName(StrEnum):
    """Finite service families with independent outbound budgets."""

    LLM = "llm"
    RETRIEVAL = "retrieval"
    RERANK = "rerank"
    NL2SQL = "nl2sql"
    ANALYSIS_CONTROL = "analysis_control"
    ANALYSIS_STATUS = "analysis_status"
    IAM = "iam"
    SPA_FAQ = "spa_faq"
    BI = "bi"
    OBS = "obs"
    RELAY_CONTROL = "relay_control"
    INTEROP = "interop"


class OutboundHttpProfile(StrEnum):
    """Finite HTTP security profiles constructed by the runtime."""

    TRUSTED = "trusted"
    DIRECT_UPSTREAM = "direct_upstream"


class OutboundPoolSnapshot(NamedTuple):
    """A value-safe observation of one logical pool's current state."""

    name: OutboundPoolName
    capacity: int
    in_use: int
    waiting: int
    max_in_use: int
    started: int
    completed: int
    failed: int
    cancelled: int
    total_wait_seconds: float
    max_wait_seconds: float


class OutboundRuntimeClosedError(RuntimeError):
    """Raised when a lease is attempted after registry shutdown starts."""


__all__ = [
    "OutboundHttpProfile",
    "OutboundPoolName",
    "OutboundPoolSnapshot",
    "OutboundRuntimeClosedError",
]
