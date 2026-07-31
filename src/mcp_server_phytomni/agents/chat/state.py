# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed state and IO contracts for the chat LangGraph workflow.

``ChatInput`` / ``ChatOutput`` / ``ChatState`` split parent-input,
parent-output, and internal-state shapes for the chat subgraph.
The module omits ``from __future__ import annotations`` because
``TypedDict.__required_keys__`` is computed at class-definition
time; lazy annotations would erase the ``Required[]`` markers and
collapse every ``Required`` field into a ``total=False`` key.
"""

from typing import Any, Literal, Required, TypedDict

from ...runtime.locale import SupportedLocale


class ChatConversationMessage(TypedDict):
    """One bounded native-role history item for the Chat model input."""

    role: Literal["user", "assistant"]
    content: str


class ChatInput(TypedDict, total=False):
    """Public input contract for the chat subgraph.

    Mirrors the kwargs ``mcp/handlers.py:handle_chat_agent`` forwards
    to ``phyto_chat_with_follow``: a required natural-language query,
    an optional OBS file list converted to upload context, and a
    type-erased service bag that carries the LLM / OBS / timeout settings
    through the typed ``run_phyto_chat_cached`` adapter. The service bag
    stays a single dict so a parent graph never has to track
    individual provider kwargs as the LLM client surface evolves.

    Attributes:
        user_query: Natural-language question or instruction.
        obs_file_list: OBS object keys to download and inline as
            upload context. Empty list when no files are attached.
        chat_kwargs: Flat dict of provider / OBS / timeout settings
            forwarded to the chat service (model id, api key, base
            url, server_dir, retry policy, etc.).
    """

    user_query: Required[str]
    obs_file_list: list[str]
    chat_kwargs: dict[str, Any]
    conversation_messages: list[ChatConversationMessage]
    locale: SupportedLocale


class ChatOutput(TypedDict):
    """Public output contract for the chat subgraph.

    ``response`` is the raw upstream chat-completion dict (with the
    follow-up questions list embedded on the assistant message under
    ``choices[0].message.follow_up_questions``). It may be ``None``
    when the upstream provider returns no completion; the key itself
    is always present in the final state.
    """

    response: dict[str, Any] | None


class ChatState(TypedDict, total=False):
    """Internal state spanning every chat workflow node.

    Carries every :class:`ChatInput` field plus the intermediate
    ``upload_context`` that ``prepare_context`` materialises (the
    converted markdown stitched into ``user_query`` before the LLM
    call) and the final ``response`` matching :class:`ChatOutput`.
    """

    user_query: Required[str]
    obs_file_list: list[str]
    chat_kwargs: dict[str, Any]
    conversation_messages: list[ChatConversationMessage]
    locale: SupportedLocale
    upload_context: str | None
    response: dict[str, Any] | None
