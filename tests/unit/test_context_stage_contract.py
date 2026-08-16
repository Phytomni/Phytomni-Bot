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
    ContextStageProgress,
    ContextStageRoute,
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
        route=ContextStageRoute(
            selected_agent_id="ChatAgent",
            route_source="instant_lock",
            route_reason_code="INSTANT_LOCK",
        ),
        progress=ContextStageProgress(
            base_business_context_version=0,
            proposed_business_context_version=1,
            last_applied_ledger_cursor=21,
            context_truncated=False,
            context_rebuilt=True,
            context_degraded=False,
        ),
    )
    public_fields = stage.as_public_dict()

    event = context_staged(turn_id="21", stage=stage)

    assert {
        item.name for item in (*fields(stage.route), *fields(stage.progress))
    } == CONTEXT_STAGE_FIELDS
    assert set(public_fields) == CONTEXT_STAGE_FIELDS
    assert ContextStageMetadata.from_public(public_fields) == stage
    assert public_fields["selected_agent_id"] == "ChatAgent"
    assert public_fields["last_applied_ledger_cursor"] == 21
    assert public_fields["context_rebuilt"] is True
    assert event.data["name"] == "phyto.context_staged"
    assert event.data["value"] == {
        "schema_version": 1,
        "turn_id": "21",
        **public_fields,
    }
