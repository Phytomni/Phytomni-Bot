"""Decode the committed V1 conversation-context compatibility fixture."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.api.schemas import (
    ContextMutationResponse,
    ContextSettlementRequest,
    ContextTombstoneRequest,
)
from mcp_server_phytomni.mcp.schemas import AGENT_TOOL_DEFINITIONS
from mcp_server_phytomni.runtime.conversation_context.models import (
    ContextStageMetadata as ProjectionStageMetadata,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    ContextStageMetadata,
)

pytestmark = pytest.mark.server

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "conversation_context"
    / "v1.json"
)
_CANONICAL_AGENT_IDS = [
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
]


def _fixture() -> dict[str, Any]:
    """Load the one committed, sanitized protocol fixture."""
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_protocol_advertisement_and_canonical_allowlist_are_current() -> None:
    """The fixture records the live advertisement and ten-tool order."""
    payload = _fixture()

    assert payload["protocol_advertisement"] == {
        "object": "list",
        "protocols": {"conversation_context": [1]},
    }
    assert (
        payload["requests"]["expert_unforced_envelope"]["allowed_agent_ids"]
        == _CANONICAL_AGENT_IDS
    )


def test_fixture_requests_decode_with_current_python_models() -> None:
    """Every request byte in the fixture validates against its live model."""
    requests = _fixture()["requests"]

    for name in (
        "instant_envelope",
        "expert_unforced_envelope",
        "expert_explicit_envelope",
    ):
        ConversationEnvelopeV1.model_validate(requests[name])
    ContextSettlementRequest.model_validate(requests["settlement_request"])
    ContextTombstoneRequest.model_validate(requests["tombstone_request"])


def test_fixture_responses_are_emitted_by_current_models() -> None:
    """Current response models reproduce the committed response bytes."""
    responses = _fixture()["responses"]

    for name in ("settlement_response", "tombstone_response"):
        response = ContextMutationResponse.model_validate(responses[name])
        assert response.model_dump(mode="json") == responses[name]

    staged = responses["staged_metadata_response"]
    stage = ContextStageMetadata(
        selected_agent_id=staged["selected_agent_id"],
        route_source=staged["route_source"],
        route_reason_code=staged["route_reason_code"],
        base_business_context_version=staged["base_business_context_version"],
        proposed_business_context_version=staged[
            "proposed_business_context_version"
        ],
        last_applied_ledger_cursor=staged["last_applied_ledger_cursor"],
        context_truncated=staged["context_truncated"],
        context_rebuilt=staged["context_rebuilt"],
        context_degraded=staged["context_degraded"],
    )
    assert asdict(stage) == {
        key: staged[key]
        for key in staged
        if key not in {"schema_version", "turn_id"}
    }

    degraded = responses["degraded_context_success"]
    degraded_stage = ContextStageMetadata(
        selected_agent_id=degraded["selected_agent_id"],
        route_source=degraded["route_source"],
        route_reason_code=degraded["route_reason_code"],
        base_business_context_version=degraded[
            "base_business_context_version"
        ],
        proposed_business_context_version=degraded[
            "proposed_business_context_version"
        ],
        last_applied_ledger_cursor=degraded["last_applied_ledger_cursor"],
        context_truncated=degraded["context_truncated"],
        context_rebuilt=degraded["context_rebuilt"],
        context_degraded=degraded["context_degraded"],
    )
    assert asdict(degraded_stage) == {
        key: degraded[key]
        for key in degraded
        if key not in {"schema_version", "turn_id"}
    }


def test_fixture_does_not_contain_sensitive_or_unbounded_content() -> None:
    """The shared fixture contains identifiers and metadata, never payloads."""
    serialized = _FIXTURE.read_text(encoding="utf-8").lower()

    for forbidden in (
        "http://",
        "https://",
        "@",
        "password",
        "username",
        "signed",
        "token",
        "full answer",
        "full report",
        "full table",
    ):
        assert forbidden not in serialized
    assert "/" not in serialized
    assert "\\" not in serialized


def test_projection_metadata_model_keeps_protocol_fields_bounded() -> None:
    """The lower-level metadata model still accepts the shared enum values."""
    metadata = ProjectionStageMetadata(
        selected_agent_id="ChatAgent",
        route_source="instant_lock",
        route_reason_code="INSTANT_LOCK",
        base_business_context_version=0,
        proposed_business_context_version=1,
        last_applied_ledger_cursor=1,
        context_truncated=False,
        context_rebuilt=True,
    )

    assert metadata.selected_agent_id == "ChatAgent"
    assert metadata.route_source == "instant_lock"
