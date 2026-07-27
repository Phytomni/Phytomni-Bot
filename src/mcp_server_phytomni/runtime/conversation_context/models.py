# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Strict, bounded V1 conversation-context request models."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ...mcp.schemas import AGENT_TOOL_DEFINITIONS
from ..locale import SupportedLocale

MAX_CURRENT_MESSAGE_CHARS = 32_768
MAX_REQUEST_ID_CHARS = 128
MAX_ALLOWED_AGENT_IDS = 10
MAX_HISTORY_DELTA_ENTRIES = 200
MAX_ARTIFACT_REFS = 50
MAX_LEDGER_SUMMARY_CHARS = 4 * 1024
MAX_ARTIFACT_METADATA_CHARS = 512
MAX_ARTIFACT_ID_CHARS = 128

_OPAQUE_ARTIFACT_ID_PATTERN = (
    rf"^[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_ARTIFACT_ID_CHARS - 1}}}$"
)
_URI_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

_CANONICAL_AGENT_TOOL_NAMES = frozenset(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)


class CurrentMessageV1(BaseModel):
    """The current user message supplied by the owner-scoped gateway."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_CURRENT_MESSAGE_CHARS)
    locale: SupportedLocale


class LedgerEntryV1(BaseModel):
    """A bounded owner-scoped history entry used for context rebuilding."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    role: Literal["user", "assistant"]
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CURRENT_MESSAGE_CHARS,
    )
    summary: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_LEDGER_SUMMARY_CHARS,
    )

    @model_validator(mode="after")
    def _require_bounded_text(self) -> "LedgerEntryV1":
        """Reject entries without content instead of widening the contract."""
        if self.content is None and self.summary is None:
            raise ValueError("ledger entry requires content or summary")
        return self


class ArtifactRefV1(BaseModel):
    """A bounded artifact reference without a storage path or binary body."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(
        min_length=1,
        max_length=MAX_ARTIFACT_ID_CHARS,
        pattern=_OPAQUE_ARTIFACT_ID_PATTERN,
    )
    display_name: str = Field(
        min_length=1,
        max_length=MAX_ARTIFACT_METADATA_CHARS,
    )

    @field_validator("display_name")
    @classmethod
    def _reject_path_like_display_name(cls, value: str) -> str:
        """Keep metadata labels separate from storage paths and URIs."""
        if "/" in value or "\\" in value or _URI_SCHEME_PREFIX.match(value):
            raise ValueError("display_name must not contain a path or URI")
        return value


class ConversationEnvelopeV1(BaseModel):
    """Versioned Go-to-Bot contract for a single conversation turn."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    conversation_key: UUID
    dialogue_id: UUID
    turn_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    request_id: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    operation: Literal["append", "replace", "rebuild"]
    mode: Literal["instant", "expert"]
    current_message: CurrentMessageV1
    requested_agent_id: str | None = None
    allowed_agent_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_ALLOWED_AGENT_IDS,
    )
    ledger_cursor: int = Field(ge=0)
    ledger_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_business_context_version: int = Field(ge=0)
    history_delta: list[LedgerEntryV1] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_DELTA_ENTRIES,
    )
    artifact_refs: list[ArtifactRefV1] = Field(
        default_factory=list,
        max_length=MAX_ARTIFACT_REFS,
    )

    @model_validator(mode="after")
    def _validate_agent_constraints(self) -> "ConversationEnvelopeV1":
        """Keep routing inside Go's ordered canonical allowlist."""
        if len(set(self.allowed_agent_ids)) != len(self.allowed_agent_ids):
            raise ValueError(
                "allowed_agent_ids must contain unique canonical tool names"
            )
        if any(
            agent_id not in _CANONICAL_AGENT_TOOL_NAMES
            for agent_id in self.allowed_agent_ids
        ):
            raise ValueError(
                "allowed_agent_ids contains an unknown canonical tool"
            )
        if self.mode == "instant" and (
            self.requested_agent_id not in (None, "ChatAgent")
            or "ChatAgent" not in self.allowed_agent_ids
        ):
            raise ValueError("instant context requires ChatAgent")
        if (
            self.requested_agent_id is not None
            and self.requested_agent_id not in self.allowed_agent_ids
        ):
            raise ValueError("requested_agent_id must be allowed")
        return self


__all__ = [
    "ArtifactRefV1",
    "ConversationEnvelopeV1",
    "CurrentMessageV1",
    "LedgerEntryV1",
    "MAX_ALLOWED_AGENT_IDS",
    "MAX_ARTIFACT_ID_CHARS",
    "MAX_ARTIFACT_METADATA_CHARS",
    "MAX_ARTIFACT_REFS",
    "MAX_CURRENT_MESSAGE_CHARS",
    "MAX_HISTORY_DELTA_ENTRIES",
    "MAX_LEDGER_SUMMARY_CHARS",
    "MAX_REQUEST_ID_CHARS",
]
