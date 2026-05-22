# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP API request and response schemas.

Public models: ApiErrorDetail, ApiErrorResponse, ChatMessage,
    ChatCompletionRequest, AgentRunRequest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentRunRequest",
    "ApiErrorDetail",
    "ApiErrorResponse",
    "ChatCompletionRequest",
    "ChatMessage",
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
        stream: Streaming flag; only False is supported.
        obs_file_list: Optional OBS document paths for the agent.
        resolve_gene_id: When true and the model is BriefGene-shaped,
            resolve the free-form user message into a canonical gene
            id via an LLM call before invoking the tool. Other models
            reject this flag with HTTP 400.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    messages: List[ChatMessage]
    stream: bool = False
    obs_file_list: Optional[List[str]] = None
    resolve_gene_id: Optional[bool] = None


class AgentRunRequest(BaseModel):
    """Body for ``POST /v1/agents/{agent}/runs``.

    The agent slug is positional in the URL so the body carries only
    the per-tool arguments. Validation against the per-tool Pydantic
    model lives one layer below in ``invoke_tool_formatted`` so this
    schema stays free of agent-shape coupling.

    Attributes:
        arguments: Tool-specific kwargs forwarded to the agent.
    """

    arguments: Dict[str, Any] = Field(default_factory=dict)
