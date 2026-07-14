# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP API request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

# Allowed values for the ``purpose`` field on ``POST /v1/files`` and
# the response echo. Combines the OpenAI files API enum (assistants,
# batch, fine-tune, vision, user_data) with the Phytomni-internal
# ``agent_context`` default. AF-002 audit 2026-05-26: prior contract
# accepted any str, which drifted from the docs claim of "OpenAI-files
# compatible" and exposed an unbounded echo field.
UploadPurpose = Literal[
    "agent_context",
    "assistants",
    "batch",
    "fine-tune",
    "vision",
    "user_data",
]

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
    "ChatCompletionRequest",
    "ChatMessage",
    "FileUploadResponse",
    "MemoryCreateRequest",
    "MemoryDeleteResponse",
    "MemoryExportResponse",
    "MemoryAuditListResponse",
    "MemoryAuditRecordResponse",
    "MemoryListResponse",
    "MemoryResponse",
    "MemoryUpdateRequest",
    "ResumeRequest",
    "UploadPurpose",
]


class ApiErrorDetail(BaseModel):
    """One error description inside the unified API error envelope.

    Attributes:
        type: Stable machine-readable error category slug.
        code: HTTP status code mirrored into the body.
        message: Human-readable explanation.
        request_id: Correlation id; populated once request context lands.
    """

    type: str
    code: int
    message: str
    request_id: str | None = None


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


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request subset.

    Unknown OpenAI fields (temperature, top_p, ...) are accepted and
    ignored so standard OpenAI clients work unchanged.

    Attributes:
        model: Selects the backing chat-like agent.
        messages: Ordered conversation messages.
        stream: Streaming flag. ``True`` is only supported by
            streaming-capable models (``phyto-chat`` in v1) and
            returns ``text/event-stream`` with ``data: {...}\\n\\n``
            chunks plus a terminating ``data: [DONE]\\n\\n``. Any
            other model with ``stream=True`` returns HTTP 400.
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
    resolve_gene_id: bool | None = None
    dialogue_id: str | None = None
    debug: bool | None = None


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
        debug: When true, include the raw handler payload in the
            result block. Default mode strips it to reduce volume.
            PHYTOMNI_DEBUG=1 overrides this to always return full.
    """

    arguments: dict[str, Any] = Field(default_factory=dict)
    dialogue_id: str | None = None
    debug: bool | None = None


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
        obs_file_list: OBS paths for uploaded attachments, injected into
            the selected tool's arguments only when its schema accepts
            them (``tool_accepts_obs``).
        dialogue_id: Optional chat-ai conversation id recorded on the run.
        forced_tool: Reserved for pinning an agent inside Expert mode;
            v1 implements only the ``None`` (pure autonomous) path.
    """

    user_query: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    obs_file_list: list[str] = Field(default_factory=list)
    dialogue_id: str | None = None
    forced_tool: str | None = None


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


class FileUploadResponse(BaseModel):
    """Response to ``POST /v1/files`` multipart upload.

    The shape stays OpenAI-files-compatible (``id``, ``object``,
    ``bytes``, ``filename``, ``purpose``, ``created_at``) so chat-ai
    clients that already speak the OpenAI files schema can integrate
    without an adapter, plus two Phytomni-specific fields:

    Attributes:
        id: Per-request file id (``upload_...`` token from IdFactory)
            that also appears as the second-to-last segment of
            ``obs_path``.
        object: Stable type discriminator (``"file"``).
        bytes: Byte length of the stored payload.
        filename: Sanitized basename actually written to OBS; may
            differ from the original upload name if the client sent
            shell metacharacters, Unicode, or path traversal segments.
        purpose: Caller-declared intent for the file. Defaults to
            ``agent_context`` so chat-ai's attachment flow can omit it.
        created_at: Unix epoch seconds (UTC) when the upload was
            stored.
        obs_path: Public ``/obs/<bucket>/<key>`` path that clients can
            replay in a later ``obs_file_list`` argument.
        path: Computed alias of ``obs_path`` preserved so chat-ai's
            existing ``obs_file_list`` builder, which already reads
            ``path`` from the legacy local upload bridge, can plug in
            unchanged. Read-only mirror that can never drift from
            ``obs_path``.
    """

    id: str
    object: str = "file"
    bytes: int
    filename: str
    purpose: UploadPurpose
    created_at: int
    obs_path: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def path(self) -> str:
        """Alias of ``obs_path`` for chat-ai's ``obs_file_list`` builder."""
        return self.obs_path
