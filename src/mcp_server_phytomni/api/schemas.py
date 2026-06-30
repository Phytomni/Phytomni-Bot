# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP API request and response schemas.

Public models: ApiErrorDetail, ApiErrorResponse, ChatMessage,
    ChatCompletionRequest, AgentRunRequest, ExpertQueryRequest,
    ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyRecordResponse,
    ApiKeyListResponse, ApiKeyDeleteResponse, FileUploadResponse.
Public aliases: UploadPurpose.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    request_id: Optional[str] = None


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
    messages: List[ChatMessage]
    stream: bool = False
    obs_file_list: Optional[List[str]] = None
    resolve_gene_id: Optional[bool] = None
    dialogue_id: Optional[str] = None
    debug: Optional[bool] = None


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

    arguments: Dict[str, Any] = Field(default_factory=dict)
    dialogue_id: Optional[str] = None
    debug: Optional[bool] = None


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
    history: List[Dict[str, Any]] = Field(default_factory=list)
    obs_file_list: List[str] = Field(default_factory=list)
    dialogue_id: Optional[str] = None
    forced_tool: Optional[str] = None


class ApiKeyCreateRequest(BaseModel):
    """Body for ``POST /v1/api-keys`` issued by the upstream service.

    Attributes:
        user_id: Opaque identifier the key authenticates against.
        name: Optional human label captured in the key store.
        expires_days: Days until the key expires; None means never.
    """

    user_id: str
    name: Optional[str] = None
    expires_days: Optional[int] = None


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
    expires_at: Optional[str] = None


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
    """

    user_id: str
    name: Optional[str] = None
    prefix: str
    created_at: str
    revoked_at: Optional[str] = None
    last_used_at: Optional[str] = None
    expires_at: Optional[str] = None
    active: bool


class ApiKeyListResponse(BaseModel):
    """Response to ``GET /v1/api-keys``.

    Attributes:
        object: Stable type discriminator (``"list"``).
        data: Non-secret records ordered by creation time.
    """

    object: str = "list"
    data: List[ApiKeyRecordResponse]


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
        path: Alias of ``obs_path`` preserved so chat-ai's existing
            ``obs_file_list`` builder, which already reads ``path``
            from the legacy local upload bridge, can plug in unchanged.
    """

    id: str
    object: str = "file"
    bytes: int
    filename: str
    purpose: UploadPurpose
    created_at: int
    obs_path: str
    path: str
