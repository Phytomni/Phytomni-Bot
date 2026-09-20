# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the versioned conversation-context request contract."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from tests.support.http_fakes import build_conversation_context_envelope

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
    ArtifactRefV1,
    BusinessContext,
    ContextProjection,
    ConversationEnvelopeV1,
    LedgerEntryV1,
)

pytestmark = pytest.mark.unit


def _valid_envelope() -> dict[str, object]:
    """Return a minimally valid owner-scoped V1 envelope payload."""
    return build_conversation_context_envelope(
        "1",
        mode="expert",
        content="Summarize rice samples.",
        requested_agent_id="KnowledgeAgent",
        allowed_agent_ids=("KnowledgeAgent", "ChatAgent"),
    )


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


def test_envelope_rejects_malformed_dialogue_id() -> None:
    """The owner-scoped dialogue identifier must be a UUID."""
    payload = _valid_envelope()
    payload["dialogue_id"] = "not-a-uuid"

    with pytest.raises(ValidationError, match="dialogue_id"):
        ConversationEnvelopeV1.model_validate(payload)


@pytest.mark.parametrize("turn_id", ["0", "01", "one", "1" * 20])
def test_envelope_rejects_invalid_turn_id(turn_id: str) -> None:
    """Envelope turns are positive, non-padded integers with 19 digits max."""
    payload = _valid_envelope()
    payload["turn_id"] = turn_id

    with pytest.raises(ValidationError, match="turn_id"):
        ConversationEnvelopeV1.model_validate(payload)


@pytest.mark.parametrize("turn_id", ["0", "01", "one", "1" * 20])
def test_ledger_entry_rejects_invalid_turn_id(turn_id: str) -> None:
    """Ledger turn identifiers retain the same bounded integer contract."""
    with pytest.raises(ValidationError, match="turn_id"):
        LedgerEntryV1.model_validate(
            {"turn_id": turn_id, "role": "user", "content": "hello"}
        )


def test_current_message_accepts_research_structural_hard_maximum() -> None:
    """The current message accepts the one-million-code-point hard cap."""
    payload = _valid_envelope()
    payload["current_message"] = {
        "content": "x" * 1_048_576,
        "locale": "en-US",
    }

    assert ConversationEnvelopeV1.model_validate(payload).current_message


def test_current_message_rejects_hard_maximum_plus_one() -> None:
    """The current message rejects content beyond the structural hard cap."""
    payload = _valid_envelope()
    payload["current_message"] = {
        "content": "x" * 1_048_577,
        "locale": "en-US",
    }

    with pytest.raises(ValidationError, match="current_message"):
        ConversationEnvelopeV1.model_validate(payload)


def test_ledger_entry_retains_history_content_limit() -> None:
    """History entries remain bounded independently of current messages."""
    with pytest.raises(ValidationError, match="content"):
        LedgerEntryV1.model_validate(
            {
                "turn_id": "1",
                "role": "user",
                "content": "x" * 32_769,
            }
        )


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
    "artifact_id",
    [
        "artifact-1",
        "artifact_1",
        "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6",
    ],
)
def test_artifact_ref_accepts_safe_opaque_ids(artifact_id: str) -> None:
    """Opaque artifact IDs retain ordinary identifier formats."""
    artifact = ArtifactRefV1(
        artifact_id=artifact_id,
        display_name="result.csv",
    )

    assert artifact.artifact_id == artifact_id


@pytest.mark.parametrize(
    "artifact_id",
    ["obs://bucket/results.csv", "/srv/results.csv", r"C:\\srv\\results.csv"],
)
def test_artifact_ref_rejects_paths_and_uris_in_ids(artifact_id: str) -> None:
    """Artifact IDs cannot be storage locators disguised as identifiers."""
    with pytest.raises(ValidationError, match="artifact_id"):
        ArtifactRefV1(artifact_id=artifact_id, display_name="result.csv")


@pytest.mark.parametrize(
    "display_name",
    [
        "obs://bucket/results.csv",
        "/srv/results.csv",
        r"C:\\srv\\results.csv",
        "exports/results.csv",
    ],
)
def test_artifact_ref_rejects_path_bearing_display_names(
    display_name: str,
) -> None:
    """Display labels are names only, never relative or absolute paths."""
    with pytest.raises(ValidationError, match="display_name"):
        ArtifactRefV1(artifact_id="artifact-1", display_name=display_name)


def test_current_message_can_mention_paths_without_becoming_metadata() -> None:
    """Path text remains valid conversational content outside artifact refs."""
    payload = _valid_envelope()
    payload["current_message"] = {
        "content": "Compare /srv/results.csv with obs://bucket/results.csv.",
        "locale": "en-US",
    }

    assert ConversationEnvelopeV1.model_validate(
        payload
    ).current_message.content


def test_business_context_and_projection_derive_compatibility_history() -> (
    None
):
    """Ordered role-tagged history remains compatible with legacy fields."""
    context = BusinessContext.model_validate(
        {
            "schema_version": 1,
            "version": 2,
            "last_applied_ledger_cursor": 3,
            "last_applied_ledger_version": "a" * 64,
            "observed_mode": "expert",
            "recent_turns": [
                {"role": "user", "content": "U1"},
                {"role": "assistant", "content": "A1"},
                {"role": "user", "content": "U2"},
            ],
        }
    )
    projection = ContextProjection.model_validate(
        {
            "current_query": "U3",
            "relevant_recent_turns": [
                {"role": "user", "content": "U1"},
                {"role": "assistant", "content": "A1"},
                {"role": "user", "content": "U2"},
            ],
            "agent_thread_id": "ctx-" + "a" * 64,
            "locale": "en-US",
            "token_budget": 128,
        }
    )

    assert context.recent_user_turns == ["U1", "U2"]
    assert context.assistant_summaries == ["A1"]
    assert projection.relevant_user_turns == ["U1", "U2"]
    assert projection.relevant_assistant_summaries == ["A1"]


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
