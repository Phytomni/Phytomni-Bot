# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from AnalystAgent state to chat subgraph IO.

AnalystAgent has five ``phyto_chat`` call sites (``parse_query`` /
``data_select`` / ``plan`` / ``check`` / ``tool_extract``) that share
the same 17-key provider bag and differ only by ``response_format``.
The chat-subgraph wiring routes each call through the compiled chat
subgraph; this module supplies the ``ChatInput`` projection and the
``ChatOutput.response`` unwrap the wiring uses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.chat.state import ChatInput
from ..agents.shared.options import build_chat_kwargs


def build_analyst_chat_kwargs(
    analyst_config: Any,
    sensitive_config: Any,
    *,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pack the 17-key ``phyto_chat`` bag for an analyst node call.

    Five analyst nodes call ``phyto_chat`` with the same 17-key
    provider bag, differing only by ``response_format``. The default
    (``response_format=None``) inherits
    ``analyst_config.RESPONSE_FORMAT``; callers that need
    ``json_schema`` or ``json_object`` pass the override.

    Delegates to the canonical
    :func:`agents.shared.options.build_chat_kwargs` helper so the
    cross-agent chat-bag construction convention stays single-sourced.

    Args:
        analyst_config: ``AnalystConfig`` instance exposing the prompt
            / sampling / retry attributes the shared helper reads
            (``PROMPT_FILE``, ``PROMPT_PATH``, ``FREQUENCY_PENALTY``,
            ``N``, ``PRESENCE_PENALTY``, ``REASONING_EFFORT``,
            ``RESPONSE_FORMAT``, ``STREAM``, ``TEMPERATURE``,
            ``TOP_P``, ``USER``, ``TIMEOUT``, ``RETRIABLE_CODES``,
            ``MAX_RETRIES``).
        sensitive_config: ``SensitiveConfig`` instance exposing
            ``API_KEY`` (a ``SecretStr``), ``BASE_URL``, and
            ``MODEL_ID``.
        response_format: Optional per-site ``response_format`` dict
            (``{"type": "json_schema"}`` or ``{"type": "json_object"}``)
            that overrides the config default. ``None`` inherits
            ``analyst_config.RESPONSE_FORMAT``.

    Returns:
        Flat ``dict`` ready to attach to ``ChatInput.chat_kwargs``.
    """
    overrides: dict[str, Any] = {}
    if response_format is not None:
        overrides["response_format"] = response_format
    return build_chat_kwargs(overrides, analyst_config, sensitive_config)


def build_analyst_chat_input(
    user_query: str,
    chat_kwargs: Mapping[str, Any],
) -> ChatInput:
    """Wrap an analyst node's prompt + kwargs into a ``ChatInput`` dict.

    No ``obs_file_list`` at any analyst chat site — the analyst chat
    nodes do not forward uploaded documents to the chat call (the
    ``method_retrieve`` / ``tool_retrieve`` retrieval sites read OBS,
    not the chat sites). ``chat_kwargs`` is copied so a later
    mutation in the calling node never leaks into the subgraph's
    input.

    Args:
        user_query: The fully-built prompt the calling node assembled
            (the site-specific template-stitched query each analyst
            chat node sends to ``phyto_chat``).
        chat_kwargs: The flat provider / retry bag from
            :func:`build_analyst_chat_kwargs`. Copied into the
            return value so later mutations don't leak.

    Returns:
        ``ChatInput`` containing the required ``user_query`` plus
        ``chat_kwargs``.
    """
    return {
        "user_query": user_query,
        "chat_kwargs": dict(chat_kwargs),
    }


def extract_chat_response(chat_output: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``ChatOutput.response`` into the dict analyst posts expect.

    AnalystAgent's chat nodes assign ``phyto_response`` from the
    ``phyto_chat`` return value (the raw upstream chat-completion
    dict). The chat subgraph stores that dict under
    ``ChatOutput.response``; this helper unwraps it and substitutes
    an empty dict when the upstream returned ``None`` so downstream
    parsing (``content`` extraction, ``json.loads`` of regex matches)
    still sees a dict rather than crashing on attribute access.

    Args:
        chat_output: The chat subgraph's final state mapping
            (``ChatOutput``-shaped).

    Returns:
        The raw chat-completion dict, or ``{}`` if the upstream
        returned ``None``.
    """
    response = chat_output.get("response")
    return response if response is not None else {}
