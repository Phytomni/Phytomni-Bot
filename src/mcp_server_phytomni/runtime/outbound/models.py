# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Public value types for logical outbound request pools."""

from dataclasses import dataclass
from enum import StrEnum


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


@dataclass(frozen=True, slots=True)
class OutboundPoolSnapshot:
    """A value-safe observation of one logical pool's current state."""

    capacity: int
    in_use: int
    waiting: int
    total_acquired: int
    total_waited: int
    total_wait_seconds: float
    closing: bool


class OutboundRuntimeClosedError(RuntimeError):
    """Raised when a lease is attempted after registry shutdown starts."""


__all__ = [
    "OutboundPoolName",
    "OutboundPoolSnapshot",
    "OutboundRuntimeClosedError",
]
