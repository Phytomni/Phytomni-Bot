# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared conversation-context stage metadata is the single field source."""

from __future__ import annotations

from dataclasses import fields

import pytest

from mcp_server_phytomni.contracts.conversation_context import (
    CONTEXT_STAGE_FIELDS,
    ContextStageMetadata,
)
from mcp_server_phytomni.mcp.result_formatting import context_staged
from mcp_server_phytomni.runtime.conversation_context.service_types import (
    ContextStageMetadata as ServiceStageMetadata,
)

pytestmark = pytest.mark.unit


def test_service_metadata_is_the_shared_contract_type() -> None:
    """Runtime service types reuse the contract snapshot class."""
    assert ServiceStageMetadata is ContextStageMetadata


def test_context_staged_copies_shared_fields_without_a_second_schema() -> None:
    """The AG-UI frame is the contract snapshot plus turn identity."""
    stage = ContextStageMetadata(
        selected_agent_id="ChatAgent",
        route_source="instant_lock",
        route_reason_code="INSTANT_LOCK",
        base_business_context_version=0,
        proposed_business_context_version=1,
        last_applied_ledger_cursor=21,
        context_truncated=False,
        context_rebuilt=True,
        context_degraded=False,
    )

    event = context_staged(turn_id="21", stage=stage)

    assert {item.name for item in fields(stage)} == CONTEXT_STAGE_FIELDS
    assert event.data["name"] == "phyto.context_staged"
    assert event.data["value"] == {
        "schema_version": 1,
        "turn_id": "21",
        "selected_agent_id": "ChatAgent",
        "route_source": "instant_lock",
        "route_reason_code": "INSTANT_LOCK",
        "base_business_context_version": 0,
        "proposed_business_context_version": 1,
        "last_applied_ledger_cursor": 21,
        "context_truncated": False,
        "context_rebuilt": True,
        "context_degraded": False,
    }
