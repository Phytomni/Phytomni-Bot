# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Tests for the versioned conversation-context request contract."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.api.schemas import (
    ChatCompletionRequest,
    ChatMessage,
    ExpertQueryRequest,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    MAX_ARTIFACT_METADATA_CHARS,
    MAX_CURRENT_MESSAGE_CHARS,
    MAX_HISTORY_DELTA_ENTRIES,
    MAX_LEDGER_SUMMARY_CHARS,
    ConversationEnvelopeV1,
)

pytestmark = pytest.mark.unit


def _valid_envelope() -> dict[str, object]:
    """Return a minimally valid owner-scoped V1 envelope payload."""
    return {
        "schema_version": 1,
        "conversation_key": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6",
        "dialogue_id": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7",
        "turn_id": "1",
        "request_id": "request-1",
        "operation": "append",
        "mode": "expert",
        "current_message": {
            "content": "Summarize rice samples.",
            "locale": "en-US",
        },
        "requested_agent_id": "KnowledgeAgent",
        "allowed_agent_ids": ["KnowledgeAgent", "ChatAgent"],
        "ledger_cursor": 0,
        "ledger_version": "a" * 64,
        "base_business_context_version": 0,
    }


def test_envelope_accepts_valid_ordered_allowlist() -> None:
    """A valid contract retains the server-provided allowlist order."""
    envelope = ConversationEnvelopeV1.model_validate(_valid_envelope())

    assert envelope.allowed_agent_ids == ["KnowledgeAgent", "ChatAgent"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("conversation_key", "not-a-uuid"),
        ("operation", "delete"),
    ],
)
def test_envelope_rejects_invalid_core_values(
    field: str, value: object
) -> None:
    """Version, conversation identity, and operation fail closed."""
    payload = _valid_envelope()
    payload[field] = value

    with pytest.raises(ValidationError, match=field):
        ConversationEnvelopeV1.model_validate(payload)


@pytest.mark.parametrize(
    "allowed_agent_ids",
    [
        ["ChatAgent", "ChatAgent"],
        ["ChatAgent", "UnknownAgent"],
    ],
)
def test_envelope_rejects_invalid_allowlists(
    allowed_agent_ids: list[str],
) -> None:
    """The Bot accepts only unique canonical MCP agent names."""
    payload = _valid_envelope()
    payload["allowed_agent_ids"] = allowed_agent_ids

    with pytest.raises(ValidationError, match="allowed_agent_ids"):
        ConversationEnvelopeV1.model_validate(payload)


def test_envelope_rejects_requested_agent_outside_allowlist() -> None:
    """An explicit Expert choice cannot escape Go's current allowlist."""
    payload = _valid_envelope()
    payload["requested_agent_id"] = "DataAgent"

    with pytest.raises(
        ValidationError,
        match="requested_agent_id must be allowed",
    ):
        ConversationEnvelopeV1.model_validate(payload)


def test_envelope_rejects_non_chat_instant_request() -> None:
    """Instant mode remains locked to ChatAgent."""
    payload = _valid_envelope()
    payload["mode"] = "instant"
    payload["requested_agent_id"] = "KnowledgeAgent"

    with pytest.raises(
        ValidationError,
        match="instant context requires ChatAgent",
    ):
        ConversationEnvelopeV1.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "current_message",
            {
                "content": "x" * (MAX_CURRENT_MESSAGE_CHARS + 1),
                "locale": "en-US",
            },
        ),
        (
            "history_delta",
            [
                {"turn_id": str(index + 1), "role": "user", "content": "x"}
                for index in range(MAX_HISTORY_DELTA_ENTRIES + 1)
            ],
        ),
        (
            "history_delta",
            [
                {
                    "turn_id": "1",
                    "role": "assistant",
                    "summary": "x" * (MAX_LEDGER_SUMMARY_CHARS + 1),
                }
            ],
        ),
        (
            "artifact_refs",
            [
                {
                    "artifact_id": "artifact-1",
                    "display_name": "x" * (MAX_ARTIFACT_METADATA_CHARS + 1),
                }
            ],
        ),
    ],
)
def test_envelope_rejects_oversized_context_content(
    field: str, value: object
) -> None:
    """Inbound history and artifact metadata stay inside projection bounds."""
    payload = _valid_envelope()
    payload[field] = value

    with pytest.raises(ValidationError):
        ConversationEnvelopeV1.model_validate(payload)


@pytest.mark.parametrize(
    "payload_update",
    [
        {"unexpected": "field"},
        {
            "current_message": {
                "content": "hello",
                "locale": "en-US",
                "extra": "field",
            }
        },
        {
            "history_delta": [
                {
                    "turn_id": "1",
                    "role": "user",
                    "content": "hello",
                    "extra": "field",
                }
            ]
        },
        {
            "artifact_refs": [
                {
                    "artifact_id": "artifact-1",
                    "display_name": "result.csv",
                    "extra": "field",
                }
            ]
        },
    ],
)
def test_envelope_forbids_unknown_fields(
    payload_update: dict[str, object],
) -> None:
    """The V1 envelope and nested contract records reject additive fields."""
    payload = deepcopy(_valid_envelope())
    payload.update(payload_update)

    with pytest.raises(
        ValidationError,
        match="Extra inputs are not permitted",
    ):
        ConversationEnvelopeV1.model_validate(payload)


def test_v0_request_serialization_omits_absent_conversation() -> None:
    """Existing Chat and Expert payloads retain no V1 field while disabled."""
    chat = ChatCompletionRequest(
        model="phyto-chat",
        messages=[ChatMessage(role="user", content="hello")],
    )
    expert = ExpertQueryRequest(
        user_query="hello",
        allowed_tools=["ChatAgent"],
    )

    assert "conversation" not in chat.model_dump()
    assert "conversation" not in chat.model_dump_json()
    assert "conversation" not in expert.model_dump()
    assert "conversation" not in expert.model_dump_json()
