# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from DataAgent state to chat subgraph IO.

DataAgent's ``rewrite_node`` calls ``phyto_chat`` with the same
17-key provider bag KnowledgeAgent's chat nodes pass. The upcoming
``adapter_node`` wiring routes that call through the compiled chat
subgraph; this module supplies the ``ChatInput`` projection and the
``ChatOutput.response`` unwrap the wiring will use.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.chat.state import ChatInput
from ..agents.shared.options import build_chat_kwargs


def build_data_chat_kwargs(
    data_config: Any,
    sensitive_config: Any,
) -> dict[str, Any]:
    """Pack the 17-key ``phyto_chat`` bag the data node passes.

    Delegates to the canonical
    :func:`agents.shared.options.build_chat_kwargs` helper that the
    cross-agent chat-bag construction convention lives on (environment,
    evolution, brief_gene, and the MCP handler_support builders all
    consume it). Adapter call sites do not accept per-call override
    kwargs the way public wrappers do, so the empty ``{}`` is passed
    as ``kwargs``; every value resolves from the config defaults.

    Args:
        data_config: ``DataConfig`` instance exposing the prompt /
            sampling / retry attributes the shared helper reads
            (``PROMPT_FILE``, ``PROMPT_PATH``, ``FREQUENCY_PENALTY``,
            ``N``, ``PRESENCE_PENALTY``, ``REASONING_EFFORT``,
            ``RESPONSE_FORMAT``, ``STREAM``, ``TEMPERATURE``,
            ``TOP_P``, ``USER``, ``TIMEOUT``, ``RETRIABLE_CODES``,
            ``MAX_RETRIES``).
        sensitive_config: ``SensitiveConfig`` instance exposing
            ``API_KEY`` (a ``SecretStr``), ``BASE_URL``, and
            ``MODEL_ID``.

    Returns:
        Flat ``dict`` ready to attach to ``ChatInput.chat_kwargs``.
    """
    return build_chat_kwargs({}, data_config, sensitive_config)


def build_data_chat_input(
    user_query: str,
    chat_kwargs: Mapping[str, Any],
    obs_file_list: list[str] | None = None,
) -> ChatInput:
    """Wrap a data call's inputs into a ``ChatInput`` dict.

    The chat subgraph treats ``obs_file_list`` as a presence flag for
    its upload-context branch, so an empty list and a missing list
    should behave identically. To keep the no-uploads contract honest
    the helper omits the key when the caller passes ``None`` or an
    empty list, and copies a non-empty list so a later mutation in
    the data node never leaks into the subgraph's input.

    Args:
        user_query: The fully-built prompt the calling node assembled
            (the retrieval-context-stitched rewrite prompt that
            ``rewrite_node`` feeds into the chat call).
        chat_kwargs: The flat provider / retry bag from
            :func:`build_data_chat_kwargs`. Copied into the return
            value so later mutations don't leak.
        obs_file_list: Optional OBS object keys to forward as upload
            context. ``None`` or ``[]`` both omit the key.

    Returns:
        ``ChatInput`` containing the required ``user_query`` plus
        ``chat_kwargs``, and ``obs_file_list`` only when non-empty.
    """
    result: ChatInput = {
        "user_query": user_query,
        "chat_kwargs": dict(chat_kwargs),
    }
    if obs_file_list:
        result["obs_file_list"] = list(obs_file_list)
    return result


def extract_chat_response(chat_output: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``ChatOutput.response`` into the dict the data node expects.

    DataAgent's ``rewrite_node`` assigns ``phyto_response`` from the
    ``phyto_chat`` return value (the raw upstream chat-completion
    dict). The chat subgraph stores that dict under
    ``ChatOutput.response``; this helper unwraps it and substitutes
    an empty dict when the upstream returned ``None`` so the
    downstream truthiness / ``"choices" not in`` guard in
    ``rewrite_node`` still sees a dict rather than crashing on
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
