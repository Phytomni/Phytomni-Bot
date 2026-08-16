# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Stable conversation-context stage field identities.

This module is intentionally neutral: it imports no server runtime, client,
storage, network, or formatting code. Public formatters and conversation
services share one frozen snapshot so the wire frame and in-process
metadata cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class ContextStageMetadata:
    """Routing and projection metadata for one staged context update."""

    selected_agent_id: str
    route_source: str
    route_reason_code: str
    base_business_context_version: int
    proposed_business_context_version: int
    last_applied_ledger_cursor: int
    context_truncated: bool
    context_rebuilt: bool
    context_degraded: bool


CONTEXT_STAGE_FIELDS = frozenset(
    item.name for item in fields(ContextStageMetadata)
)
