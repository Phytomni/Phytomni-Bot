# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP API request and response schemas."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict
from unicodedata import category, normalize
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ..mcp.schemas import AGENT_TOOL_DEFINITIONS
from ..runtime.conversation_context.models import ConversationEnvelopeV1
from ..runtime.locale import SupportedLocale
from ..runtime.resumable_uploads import UploadAssetPurpose

_CANONICAL_AGENT_TOOL_NAMES = frozenset(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)

__all__ = [
    "A2uiActionRequest",
    "AgentRunRequest",
    "ApiErrorDetail",
    "ApiErrorResponse",
    "ApiKeyCreateRequest",
    "ApiKeyCreateResponse",
    "ApiKeyDeleteResponse",
    "ApiKeyListResponse",
    "ApiKeyRecordResponse",
    "AssetDescriptor",
    "AttachmentAsset",
    "ChatCompletionRequest",
    "ChatStreamCall",
    "ChatMessage",
    "ContextMutationResponse",
    "ContextSettlementRequest",
    "ContextTombstoneRequest",
    "UploadAssetPurpose",
    "UploadCapabilityRenewRequest",
    "UploadCapabilityResponse",
    "UploadCompletionRequest",
    "UploadCreateRequest",
    "UploadCreateResponse",
    "UploadPartResponse",
    "UploadStatusResponse",
    "MemoryCreateRequest",
    "MemoryDeleteResponse",
    "MemoryExportResponse",
    "MemoryAuditListResponse",
    "MemoryAuditRecordResponse",
    "MemoryListResponse",
    "MemoryResponse",
    "MemoryUpdateRequest",
    "ResumeRequest",
]


class ChatStreamCall(TypedDict):
    """Keyword contract shared by ordinary chat streaming adapters."""

    tool_name: str
    arguments: dict[str, Any]
    payload: ChatCompletionRequest
    user_query: str
    conversation_messages: NotRequired[Sequence[Mapping[str, str]]]
    private_agent_state: NotRequired[Mapping[str, Any] | None]


class ApiErrorDetail(BaseModel):
    """Stable public error body."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str
    stage: str | None = None
    retryable: bool = False


class ApiErrorResponse(BaseModel):
    """Unified error envelope for native and agents/runs routes.

    Attributes:
        error: The single error detail object.
    """

    error: ApiErrorDetail


class ChatMessage(BaseModel):
    """One OpenAI-style chat message.

    Attributes:
        role: Message role (system, user, assistant).
        content: Message text content.
    """

    role: str
    content: str


class ContextSettlementRequest(BaseModel):
    """Acknowledge one staged conversation-context delta."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    conversation_key: UUID
    turn_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    ledger_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class ContextTombstoneRequest(BaseModel):
    """Delete durable context and its derived checkpoint threads."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    conversation_key: UUID


class ContextMutationResponse(BaseModel):
    """Public-safe result for a conversation-context mutation."""

    schema_version: Literal[1] = 1
    state: Literal["committed", "tombstoned", "already_applied"]
    context_version: int = Field(ge=0)


class AttachmentAsset(BaseModel):
    """One completed resumable asset reference from the Web client."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=128)


def _normalize_owner_subject(value: str | None) -> str | None:
    """Trim an asserted attachment owner without accepting blanks."""
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError("owner_subject must be non-empty")
    return normalized_value


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request subset.

    Unknown OpenAI fields (temperature, top_p, ...) are accepted and
    ignored so standard OpenAI clients work unchanged.

    Attributes:
        model: Selects the backing chat-like agent.
        messages: Ordered conversation messages.
        stream: Streaming flag. ``True`` is supported by the Chat,
            Knowledge, and BriefGene models, which return
            ``text/event-stream`` graph or token events. Review uses its
            A2UI-gated streaming path; other models with ``stream=True``
            return HTTP 400.
        obs_file_list: Optional OBS document paths for the agent.
        resolve_gene_id: When true and the model is BriefGene-shaped,
            resolve the free-form user message into a canonical gene
            id via an LLM call before invoking the tool. Other models
            reject this flag with HTTP 400.
        debug: When true, return the full response payload including
            raw handler data, provider extensions, and doc_list.
            Default mode strips debug-only fields to reduce volume.
            PHYTOMNI_DEBUG=1 overrides this to always return full.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    obs_file_list: list[str] | None = None
    attachments: list[AttachmentAsset] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    owner_subject: str | None = Field(
        default=None, min_length=1, max_length=320
    )
    resolve_gene_id: bool | None = None
    dialogue_id: str | None = None
    debug: bool | None = None
    locale: SupportedLocale | None = None
    conversation: ConversationEnvelopeV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    _trim_owner_subject = field_validator("owner_subject", mode="before")(
        _normalize_owner_subject
    )


class UploadCreateRequest(BaseModel):
    """Trusted Web-service request that creates one upload asset."""

    model_config = ConfigDict(extra="forbid")

    owner_subject: str = Field(min_length=1, max_length=320)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(
        default="",
        max_length=256,
        validation_alias=AliasChoices("content_type", "content_type_hint"),
    )
    last_modified_ms: int = Field(default=0, ge=0)
    size_bytes: int = Field(gt=0, le=10 * 1024**3)
    purpose: UploadAssetPurpose
    idempotency_key: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_filename(self) -> UploadCreateRequest:
        """Normalize and validate the display filename before persistence."""
        filename = normalize("NFC", self.filename)
        if not 1 <= len(filename.encode("utf-8")) <= 255:
            raise ValueError("invalid_upload_metadata")
        if filename in {".", ".."}:
            raise ValueError("invalid_upload_metadata")
        if any(
            char in {"/", "\\", "\x00"} or category(char) in {"Cc", "Cf"}
            for char in filename
        ):
            raise ValueError("invalid_upload_metadata")
        self.filename = filename
        return self


class UploadCreateResponse(BaseModel):
    """Safe v2 response returned after an idempotent asset create."""

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["obs-multipart-v2"]
    asset_id: str
    status: Literal["uploading"]
    part_size_bytes: int
    part_count: int
    max_parallel_parts: int
    upload_url: str
    capability: str
    capability_expires_at: datetime
    session_expires_at: datetime


class UploadCapabilityResponse(BaseModel):
    """Safe capability-renewal response with no cloud credential fields."""

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["obs-multipart-v2"]
    asset_id: str
    status: Literal["uploading"]
    upload_url: str
    capability: str
    capability_expires_at: datetime
    session_expires_at: datetime


class UploadCapabilityRenewRequest(BaseModel):
    """Trusted owner assertion used by the Web control plane."""

    model_config = ConfigDict(extra="forbid")

    owner_subject: str = Field(min_length=1, max_length=320)


class UploadCompletionRequest(BaseModel):
    """Optional client checksum for the completed authoritative asset."""

    model_config = ConfigDict(extra="forbid")

    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class UploadStatusResponse(BaseModel):
    """Safe status returned by the browser-facing HEAD route."""

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["obs-multipart-v2"]
    asset_id: str
    status: Literal["uploading", "completed", "aborted", "expired"]
    size_bytes: int
    part_size_bytes: int
    part_count: int
    received_parts: list[int]
    filename: str | None = None


class UploadPartResponse(BaseModel):
    """Safe response after one authoritative part upload."""

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["obs-multipart-v2"]
    asset_id: str
    status: Literal["uploading"]
    part_number: int
    byte_size: int
    received_parts: list[int]


class AssetDescriptor(BaseModel):
    """Safe descriptor for a completed owner-scoped asset."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: Literal["completed"]
    completed_at: datetime


class AgentRunRequest(BaseModel):
    """Body for ``POST /v1/agents/{agent}/runs``.

    The agent slug is positional in the URL so the body carries only
    the per-tool arguments. Validation against the per-tool Pydantic
    model lives one layer below in ``invoke_tool_formatted`` so this
    schema stays free of agent-shape coupling.

    Attributes:
        arguments: Tool-specific kwargs forwarded to the agent.
        dialogue_id: Optional chat-ai conversation id captured on the
            run row so the history page can group runs into one
            visible thread.
        conversation: Optional private V1 conversation-context envelope.
            When present, the URL slug is the only permitted agent
            selection and the ordinary native lifecycle is reused.
        debug: When true, include the raw handler payload in the
            result block. Default mode strips it to reduce volume.
            PHYTOMNI_DEBUG=1 overrides this to always return full.
    """

    arguments: dict[str, Any] = Field(default_factory=dict)
    attachments: list[AttachmentAsset] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    owner_subject: str | None = Field(
        default=None, min_length=1, max_length=320
    )
    dialogue_id: str | None = None
    debug: bool | None = None
    locale: SupportedLocale | None = None
    conversation: ConversationEnvelopeV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    _trim_owner_subject = field_validator("owner_subject", mode="before")(
        _normalize_owner_subject
    )


class ResumeRequest(BaseModel):
    """Body for ``POST /v1/runs/{thread_id}/resume``.

    Attributes:
        approved: Human approval decision returned to the paused graph.
        edits: Optional free-form revision instructions.
    """

    approved: bool
    edits: str | None = None


class A2uiActionRequest(BaseModel):
    """Body for ``POST /v1/runs/{run_id}/a2ui-actions``.

    Mirrors the shared :class:`~agents.shared.a2ui.A2uiActionEnvelope`
    so Web can submit confirm/form/choice actions against a paused run.

    Attributes:
        surface_id: Open surface id from the interrupt draft.
        widget: Widget kind (``confirm`` / ``form`` / ``choice``).
        action_id: Client-issued action identifier.
        run_id: Registry run id echoed for path/body consistency.
        payload: Widget-specific action payload.
    """

    model_config = ConfigDict(extra="forbid")

    surface_id: str
    widget: Literal["confirm", "form", "choice"]
    action_id: str
    run_id: str
    payload: dict[str, Any]


class ExpertQueryRequest(BaseModel):
    """Body for ``POST /v1/query/route`` (autonomous Expert routing).

    The Web gateway sends a natural-language turn; the Bot picks the right
    MCP agent with an LLM, dispatches it in-process, and returns the same
    ``agent.run`` envelope as ``POST /v1/agents/{agent}/runs`` with the
    resolved agent slug.

    Attributes:
        user_query: The natural-language user turn to route and answer.
        history: Prior ``{"role", "content"}`` turns used as routing
            context only; not forwarded to the dispatched agent.
        obs_file_list: OBS paths for uploaded attachments. Expert forwards
            them only when the selected capability explicitly enables Expert
            forwarding.
        dialogue_id: Optional chat-ai conversation id recorded on the run.
        allowed_tools: Ordered canonical agent tools available to the router.
        forced_tool: Optional canonical agent tool pinned by the caller.
    """

    model_config = ConfigDict(extra="forbid")

    user_query: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    obs_file_list: list[str] = Field(default_factory=list)
    attachments: list[AttachmentAsset] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    owner_subject: str | None = Field(
        default=None, min_length=1, max_length=320
    )
    dialogue_id: str | None = None
    allowed_tools: list[str] = Field(min_length=1, max_length=10)
    forced_tool: str | None = None
    locale: SupportedLocale | None = None
    conversation: ConversationEnvelopeV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    _trim_owner_subject = field_validator("owner_subject", mode="before")(
        _normalize_owner_subject
    )

    @model_validator(mode="after")
    def validate_tool_constraints(self) -> ExpertQueryRequest:
        """Ensure Expert routing stays within the caller's tool boundary."""
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError(
                "allowed_tools must contain unique canonical tool names"
            )
        unknown = [
            tool
            for tool in self.allowed_tools
            if tool not in _CANONICAL_AGENT_TOOL_NAMES
        ]
        if unknown:
            raise ValueError(
                "allowed_tools contains an unknown canonical tool"
            )
        if (
            self.forced_tool is not None
            and self.forced_tool not in self.allowed_tools
        ):
            raise ValueError("forced_tool must be a member of allowed_tools")
        return self


class ApiKeyCreateRequest(BaseModel):
    """Body for ``POST /v1/api-keys`` issued by the upstream service.

    Attributes:
        user_id: Opaque identifier the key authenticates against.
        name: Optional human label captured in the key store.
        expires_days: Days until the key expires; None means never.
    """

    user_id: str
    name: str | None = None
    expires_days: int | None = None


class ApiKeyCreateResponse(BaseModel):
    """Response to ``POST /v1/api-keys`` carrying the one-time plaintext.

    Attributes:
        object: Stable type discriminator (``"api_key"``).
        api_key: Full plaintext credential; shown exactly once.
        prefix: Public ``ptm_xxxxxxxx`` lookup prefix.
        user_id: User the key is bound to.
        expires_at: ISO-8601 expiry timestamp, or None for never.
    """

    object: str = "api_key"
    api_key: str
    prefix: str
    user_id: str
    expires_at: str | None = None


class ApiKeyRecordResponse(BaseModel):
    """Non-secret view of a stored key for ``GET /v1/api-keys``.

    Attributes:
        user_id: User the key is bound to.
        name: Optional human label.
        prefix: Public lookup prefix.
        created_at: ISO-8601 creation timestamp.
        revoked_at: ISO-8601 revoke timestamp, or None when active.
        last_used_at: ISO-8601 last successful auth, or None.
        expires_at: ISO-8601 expiry, or None for never.
        active: True when neither revoked nor expired.
        scopes: Sorted granted scopes; empty means all access (back-compat
            for keys minted before scopes existed).
    """

    user_id: str
    name: str | None = None
    prefix: str
    created_at: str
    revoked_at: str | None = None
    last_used_at: str | None = None
    expires_at: str | None = None
    active: bool
    scopes: list[str]


class ApiKeyListResponse(BaseModel):
    """Response to ``GET /v1/api-keys``.

    Attributes:
        object: Stable type discriminator (``"list"``).
        data: Non-secret records ordered by creation time.
    """

    object: str = "list"
    data: list[ApiKeyRecordResponse]


class ApiKeyDeleteResponse(BaseModel):
    """Response to ``DELETE /v1/api-keys/{prefix}``.

    Attributes:
        object: Stable type discriminator (``"api_key.deleted"``).
        prefix: The prefix the caller asked to revoke.
        deleted: True when an active key was revoked; False when no
            active key with that prefix existed (already revoked or
            never minted).
    """

    object: str = "api_key.deleted"
    prefix: str
    deleted: bool


class _MemoryRequest(BaseModel):
    """Shared body shape for user-scoped memory writes."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class MemoryCreateRequest(_MemoryRequest):
    """Body for ``POST /v1/memories``.

    The authenticated request context supplies ``user_id``. It is not
    accepted here so a caller cannot write into another user's namespace.
    """


class MemoryUpdateRequest(_MemoryRequest):
    """Body for ``PUT /v1/memories/{memory_id}``.

    ``If-Match`` carries the current integer revision; identity and owner
    fields remain server-controlled.
    """


class MemoryResponse(BaseModel):
    """Persisted user-scoped memory returned by the HTTP API."""

    object: str = "memory"
    id: str
    user_id: str
    kind: str
    content: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    revision: int


class MemoryListResponse(BaseModel):
    """Response to ``GET /v1/memories``."""

    object: str = "list"
    data: list[MemoryResponse]


class MemoryExportResponse(BaseModel):
    """Response to ``GET /v1/memories/export``."""

    object: str = "memory.export"
    data: list[MemoryResponse]


class MemoryDeleteResponse(BaseModel):
    """Response to ``DELETE /v1/memories/{memory_id}``."""

    object: str = "memory.deleted"
    id: str
    deleted: bool


class MemoryAuditRecordResponse(BaseModel):
    """Digest-only view of one audited memory mutation."""

    object: str = "memory.audit"
    audit_id: int
    user_id: str
    actor: str
    operation: Literal["create", "update", "delete"]
    memory_id: str
    occurred_at: datetime
    request_id: str | None = None
    before_digest: str | None = None
    after_digest: str | None = None
    revision_before: int | None = None
    revision_after: int | None = None


class MemoryAuditListResponse(BaseModel):
    """Response to the service-token-gated memory audit query."""

    object: str = "list"
    data: list[MemoryAuditRecordResponse]
