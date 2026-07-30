# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared consumer-agent-to-chat subgraph IO mappers.

Data / Knowledge / Analyst chat sites pass the same 19-key bag to
``phyto_chat`` and unwrap the chat-completion dict the same way; the
structural-mount wiring routes those calls through the shared chat
subgraph. Two optional kwargs encode
divergence: ``response_format`` for analyst's per-site overrides,
``obs_file_list`` for data / knowledge's upload-context forwarding.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.chat.state import ChatInput
from ..agents.shared.conversation_messages import (
    normalize_conversation_messages,
)
from ..agents.shared.options import build_chat_kwargs
from ..runtime.locale import SupportedLocale, current_effective_locale


def build_chat_kwargs_for(
    config: Any,
    sensitive_config: Any,
    *,
    response_format: dict[str, Any] | None = None,
    with_follow_up: bool | None = None,
    locale: SupportedLocale | None = None,
) -> dict[str, Any]:
    """Pack the ``phyto_chat`` keyword bag a consumer chat node passes.

    Delegates to :func:`agents.shared.options.build_chat_kwargs`, the
    canonical chat-kwargs builder. The optional ``response_format``
    kwarg shadows the config default when a caller (analyst) needs
    per-site overrides; passing ``None`` (data / knowledge) inherits
    ``config.RESPONSE_FORMAT``. The optional ``with_follow_up`` flag
    is tri-state to preserve back-compat: ``None`` (the default) omits
    the key so the chat subgraph router falls back to its existing
    ``True`` default, ``True`` explicitly opts the router into
    ``follow_up_node`` after ``generate_node``, and ``False`` skips
    the follow-up branch. Review's per-call-site migration to the
    explicit pattern lives in ``agents/review/``; other consumer
    agents stay on the ``None``-default until they migrate.

    Args:
        config: Domain config instance (``DataConfig`` /
            ``KnowledgeAgentConfig`` / ``AnalystConfig`` /
            ``ReviewAgentConfig``) exposing the prompt / sampling /
            retry attributes the shared builder reads (``PROMPT_FILE``,
            ``PROMPT_PATH``, ``FREQUENCY_PENALTY``, ``N``,
            ``PRESENCE_PENALTY``, ``REASONING_EFFORT``,
            ``RESPONSE_FORMAT``, ``STREAM``, ``TEMPERATURE``, ``TOP_P``,
            ``USER``, ``TIMEOUT``, ``RETRIABLE_CODES``,
            ``MAX_RETRIES``).
        sensitive_config: ``SensitiveConfig`` instance exposing
            ``API_KEY`` (a ``SecretStr``), ``BASE_URL``, and
            ``MODEL_ID``.
        response_format: Optional per-site ``response_format`` dict
            (e.g. ``{"type": "json_schema"}``) that overrides the
            config default. ``None`` inherits ``config.RESPONSE_FORMAT``.
        with_follow_up: Tri-state follow-up opt-in. ``None`` (default)
            omits the key from the returned bag and inherits the chat
            subgraph router's existing ``True`` default; ``True`` opts
            the router into ``follow_up_node``; ``False`` skips the
            follow-up branch.

    Returns:
        Flat ``dict`` ready to attach to ``ChatInput.chat_kwargs``.
    """
    overrides: dict[str, Any] = {}
    if response_format is not None:
        overrides["response_format"] = response_format
    if with_follow_up is not None:
        overrides["with_follow_up"] = with_follow_up
    return build_chat_kwargs(
        overrides,
        config,
        sensitive_config,
        locale=locale,
    )


def build_chat_input(
    user_query: str,
    chat_kwargs: Mapping[str, Any],
    *,
    obs_file_list: list[str] | None = None,
    conversation_messages: list[dict[str, str]] | None = None,
    locale: SupportedLocale | None = None,
) -> ChatInput:
    """Wrap a consumer chat call's inputs into a ``ChatInput`` dict.

    The chat subgraph treats ``obs_file_list`` as a presence flag for
    its upload-context branch, so an empty list and a missing list
    should behave identically. To keep the no-uploads contract honest
    the helper omits the key when the caller passes ``None`` or an
    empty list, and copies a non-empty list so a later mutation in
    the consumer node never leaks into the subgraph's input.

    Analyst chat sites pass ``obs_file_list=None`` because the analyst
    graph reads OBS at retrieval sites only; the chat sites do not
    forward uploaded documents to the chat call.

    Args:
        user_query: The fully-built prompt the calling node assembled
            (the site-specific template-stitched query each consumer
            chat node sends to ``phyto_chat``).
        chat_kwargs: The flat provider / retry bag from
            :func:`build_chat_kwargs_for`. Copied into the return
            value so later mutations don't leak.
        obs_file_list: Optional OBS object keys to forward as upload
            context. ``None`` or ``[]`` both omit the key.

    Returns:
        ``ChatInput`` containing the required ``user_query`` plus
        ``chat_kwargs``, and ``obs_file_list`` only when non-empty.
    """
    chat_kwargs_copy = dict(chat_kwargs)
    normalized_history = normalize_conversation_messages(conversation_messages)
    if normalized_history:
        chat_kwargs_copy["conversation_messages"] = normalized_history
    result: ChatInput = {
        "user_query": user_query,
        "chat_kwargs": chat_kwargs_copy,
        "locale": locale
        or chat_kwargs.get("locale")
        or current_effective_locale(),
    }
    if obs_file_list:
        result["obs_file_list"] = list(obs_file_list)
    return result


def extract_chat_response(chat_output: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``ChatOutput.response`` into the dict consumer nodes expect.

    Consumer chat nodes assign ``phyto_response`` from the
    ``phyto_chat`` return value (the raw upstream chat-completion
    dict). The chat subgraph stores that dict under
    ``ChatOutput.response``; this helper unwraps it and substitutes
    an empty dict when the upstream returned ``None`` so downstream
    parsing (``choices`` access, ``json.loads`` of regex matches,
    truthiness guards) still sees a dict rather than crashing on
    attribute access.

    Args:
        chat_output: The chat subgraph's final state mapping
            (``ChatOutput``-shaped).

    Returns:
        The raw chat-completion dict, or ``{}`` if the upstream
        returned ``None``.
    """
    response = chat_output.get("response")
    return response if response is not None else {}


def extract_chat_content(chat_output: Mapping[str, Any]) -> str | None:
    """Return the first chat-completion content string, if present."""
    response = extract_chat_response(chat_output)
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    message = first.get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


async def invoke_chat_content(
    chat_app: Any,
    prompt: str,
    chat_kwargs: Mapping[str, Any],
) -> str | None:
    """Invoke a compiled chat app and project its content string."""
    output = await chat_app.ainvoke(
        build_chat_input(user_query=prompt, chat_kwargs=chat_kwargs)
    )
    return extract_chat_content(output)
