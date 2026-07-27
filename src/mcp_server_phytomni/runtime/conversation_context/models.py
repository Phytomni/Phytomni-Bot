# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Strict, bounded V1 conversation-context request models."""

from __future__ import annotations

import re
from itertools import zip_longest
from typing import Annotated, Literal
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
MAX_CONTEXT_TEXT_CHARS = 4 * 1024
MAX_CONTEXT_ITEMS = 50
MAX_AGENT_THREAD_ID_CHARS = 68
MAX_CONTEXT_ITEM_TEXT_CHARS = MAX_CONTEXT_TEXT_CHARS

BoundedContextText = Annotated[
    str,
    Field(min_length=1, max_length=MAX_CONTEXT_ITEM_TEXT_CHARS),
]
BoundedArtifactId = Annotated[
    str,
    Field(min_length=1, max_length=MAX_ARTIFACT_ID_CHARS),
]

_OPAQUE_ARTIFACT_ID_PATTERN = (
    rf"^[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_ARTIFACT_ID_CHARS - 1}}}$"
)
_URI_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

_CANONICAL_AGENT_TOOL_NAMES = frozenset(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)
_CONTEXT_ENTITY_TYPES = frozenset(
    {"gene", "transcript", "species", "dataset", "table", "task", "file"}
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
    def _require_bounded_text(self) -> LedgerEntryV1:
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
    def _validate_agent_constraints(self) -> ConversationEnvelopeV1:
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


class ContextEntity(BaseModel):
    """A small semantic entity retained by the Bot context manager."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_CHARS)
    entity_type: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=MAX_ARTIFACT_METADATA_CHARS)

    @field_validator("entity_type")
    @classmethod
    def _validate_entity_type(cls, value: str) -> str:
        if value not in _CONTEXT_ENTITY_TYPES:
            raise ValueError("entity_type is unknown")
        return value


class PerAgentMemory(BaseModel):
    """Bounded state owned by one agent namespace."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_CHARS)
    thread_id: str = Field(
        min_length=MAX_AGENT_THREAD_ID_CHARS,
        max_length=MAX_AGENT_THREAD_ID_CHARS,
        pattern=r"^ctx-[0-9a-f]{64}$",
    )
    summary: str = Field(default="", max_length=MAX_CONTEXT_TEXT_CHARS)
    checkpoint_ref: str | None = Field(
        default=None, max_length=MAX_ARTIFACT_METADATA_CHARS
    )

    @field_validator("checkpoint_ref")
    @classmethod
    def _reject_path_like_checkpoint_ref(cls, value: str | None) -> str | None:
        """Keep checkpoint references opaque and free of storage locations."""
        if value is not None and (
            "/" in value or "\\" in value or _URI_SCHEME_PREFIX.match(value)
        ):
            raise ValueError("checkpoint_ref must not contain a path or URI")
        return value


class RoleTaggedTurn(BaseModel):
    """One bounded native-role turn retained in chronological order."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: BoundedContextText


def _role_tagged_turns_from_legacy_lists(
    user_turns: list[str], assistant_summaries: list[str]
) -> list[dict[str, str]]:
    """Approximate legacy parallel arrays as chronological native turns."""
    turns: list[dict[str, str]] = []
    for user_turn, assistant_summary in zip_longest(
        user_turns, assistant_summaries
    ):
        if user_turn is not None:
            turns.append({"role": "user", "content": user_turn})
        if assistant_summary is not None:
            turns.append({"role": "assistant", "content": assistant_summary})
    return turns


def _legacy_lists_from_role_tagged_turns(
    turns: list[RoleTaggedTurn] | list[dict[str, str]],
) -> tuple[list[str], list[str]]:
    """Project ordered native turns into the legacy compatibility arrays."""
    user_turns: list[str] = []
    assistant_summaries: list[str] = []
    for raw in turns:
        role = raw.role if isinstance(raw, RoleTaggedTurn) else raw["role"]
        content = (
            raw.content if isinstance(raw, RoleTaggedTurn) else raw["content"]
        )
        if role == "user":
            user_turns.append(content)
        else:
            assistant_summaries.append(content)
    return user_turns, assistant_summaries


class BusinessContext(BaseModel):
    """Bot-owned semantic context recovered from accepted ledger turns."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    version: int = Field(ge=0)
    last_applied_ledger_cursor: int = Field(ge=0)
    last_applied_ledger_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_mode: Literal["instant", "expert"]
    task_summary: str = Field(default="", max_length=MAX_CONTEXT_TEXT_CHARS)
    active_entities: list[ContextEntity] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    open_questions: list[BoundedContextText] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    recent_turns: list[RoleTaggedTurn] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    recent_user_turns: list[BoundedContextText] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    assistant_summaries: list[BoundedContextText] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    artifact_index: list[ArtifactRefV1] = Field(
        default_factory=list, max_length=MAX_ARTIFACT_REFS
    )
    per_agent_memory: dict[str, PerAgentMemory] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _synchronize_recent_turns(cls, value: object) -> object:
        """Keep the ordered and legacy history views backward-compatible."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        recent_turns = data.get("recent_turns")
        if recent_turns:
            user_turns, assistant_summaries = (
                _legacy_lists_from_role_tagged_turns(recent_turns)
            )
            data["recent_user_turns"] = user_turns
            data["assistant_summaries"] = assistant_summaries
            return data
        user_turns = list(data.get("recent_user_turns") or [])
        assistant_summaries = list(data.get("assistant_summaries") or [])
        if user_turns or assistant_summaries:
            data["recent_turns"] = _role_tagged_turns_from_legacy_lists(
                user_turns,
                assistant_summaries,
            )
        return data


class ContextProjection(BaseModel):
    """Bounded conversational data passed to one selected agent."""

    model_config = ConfigDict(extra="forbid")

    current_query: str = Field(
        min_length=1, max_length=MAX_CURRENT_MESSAGE_CHARS
    )
    intent_kind: str = Field(default="follow_up", max_length=64)
    task_summary: str = Field(default="", max_length=MAX_CONTEXT_TEXT_CHARS)
    relevant_recent_turns: list[RoleTaggedTurn] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_ITEMS,
        exclude=True,
    )
    relevant_user_turns: list[BoundedContextText] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    relevant_assistant_summaries: list[BoundedContextText] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    active_entities: list[ContextEntity] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    open_questions: list[BoundedContextText] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    artifact_refs: list[ArtifactRefV1] = Field(
        default_factory=list, max_length=MAX_ARTIFACT_REFS
    )
    agent_thread_id: str = Field(
        min_length=MAX_AGENT_THREAD_ID_CHARS,
        max_length=MAX_AGENT_THREAD_ID_CHARS,
        pattern=r"^ctx-[0-9a-f]{64}$",
    )
    locale: SupportedLocale
    token_budget: int = Field(ge=1)
    context_truncated: bool = False

    @model_validator(mode="before")
    @classmethod
    def _synchronize_relevant_turns(cls, value: object) -> object:
        """Accept either ordered native turns or the legacy split arrays."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        relevant_turns = data.get("relevant_recent_turns")
        if relevant_turns:
            user_turns, assistant_summaries = (
                _legacy_lists_from_role_tagged_turns(relevant_turns)
            )
            data["relevant_user_turns"] = user_turns
            data["relevant_assistant_summaries"] = assistant_summaries
            return data
        user_turns = list(data.get("relevant_user_turns") or [])
        assistant_summaries = list(
            data.get("relevant_assistant_summaries") or []
        )
        if user_turns or assistant_summaries:
            data["relevant_recent_turns"] = (
                _role_tagged_turns_from_legacy_lists(
                    user_turns,
                    assistant_summaries,
                )
            )
        return data


class ContextDelta(BaseModel):
    """Candidate changes an agent may make to Bot-owned context."""

    model_config = ConfigDict(extra="forbid")

    summary_update: str | None = Field(
        default=None, max_length=MAX_CONTEXT_TEXT_CHARS
    )
    entity_upserts: list[ContextEntity] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    entity_removals: list[BoundedArtifactId] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    open_question_updates: list[BoundedContextText] = Field(
        default_factory=list, max_length=MAX_CONTEXT_ITEMS
    )
    artifact_upserts: list[ArtifactRefV1] = Field(
        default_factory=list, max_length=MAX_ARTIFACT_REFS
    )
    agent_memory_update: PerAgentMemory | None = None


RouteSource = Literal["instant_lock", "explicit_selection", "router"]


class ContextStageMetadata(BaseModel):
    """Safe routing and projection metadata for settlement."""

    model_config = ConfigDict(extra="forbid")

    selected_agent_id: str = Field(
        min_length=1, max_length=MAX_ARTIFACT_ID_CHARS
    )
    route_source: RouteSource
    route_reason_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    base_business_context_version: int = Field(ge=0)
    proposed_business_context_version: int = Field(ge=0)
    last_applied_ledger_cursor: int = Field(ge=0)
    context_truncated: bool
    context_rebuilt: bool


__all__ = [
    "MAX_ALLOWED_AGENT_IDS",
    "MAX_ARTIFACT_ID_CHARS",
    "MAX_ARTIFACT_METADATA_CHARS",
    "MAX_ARTIFACT_REFS",
    "MAX_CONTEXT_ITEM_TEXT_CHARS",
    "MAX_CONTEXT_TEXT_CHARS",
    "MAX_CURRENT_MESSAGE_CHARS",
    "MAX_HISTORY_DELTA_ENTRIES",
    "MAX_LEDGER_SUMMARY_CHARS",
    "MAX_REQUEST_ID_CHARS",
    "ArtifactRefV1",
    "BusinessContext",
    "ContextDelta",
    "ContextEntity",
    "ContextProjection",
    "ContextStageMetadata",
    "ConversationEnvelopeV1",
    "CurrentMessageV1",
    "LedgerEntryV1",
    "PerAgentMemory",
    "RoleTaggedTurn",
    "RouteSource",
]
