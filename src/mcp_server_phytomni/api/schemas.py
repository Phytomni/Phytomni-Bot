# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP API request and response schemas.

Public models: ApiErrorDetail, ApiErrorResponse, ChatMessage,
    ChatCompletionRequest.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ApiErrorDetail",
    "ApiErrorResponse",
    "ChatMessage",
    "ChatCompletionRequest",
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
    """

    model_config = ConfigDict(extra="allow")

    model: str
    messages: List[ChatMessage]
    stream: bool = False
    obs_file_list: Optional[List[str]] = None
