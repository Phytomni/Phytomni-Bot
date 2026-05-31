# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure mapping helpers from KnowledgeAgent state to chat subgraph IO.

KnowledgeAgent's ``generate_node`` and ``follow_up_node`` both call
``phyto_chat`` with the same 17-key LLM / retry / provider bag and
differ only by the ``user_query`` they build. Upcoming consumer wiring
will replace those direct function calls with ``adapter_node`` hops
into the compiled chat subgraph so the chat boundary becomes visible
in LangGraph xray rendering. The helpers below project the knowledge
configs into ``ChatInput`` and unwrap ``ChatOutput.response`` back
into the dict shape the knowledge nodes already pass downstream.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agents.chat.state import ChatInput


def build_knowledge_chat_kwargs(
    knowledge_config: Any,
    sensitive_config: Any,
) -> dict[str, Any]:
    """Pack the 17-key ``phyto_chat`` bag the knowledge nodes pass.

    Mirrors the kwargs spread inside
    :meth:`agents.knowledge.agent.KnowledgeAgent.generate_node` and
    :meth:`agents.knowledge.agent.KnowledgeAgent.follow_up_node`,
    excluding only ``user_query`` (which differs per call site).
    The bag is flat by design so it slots directly into
    ``ChatInput.chat_kwargs``.

    Args:
        knowledge_config: ``KnowledgeAgentConfig`` instance exposing
            the prompt / sampling / retry attributes the agent reads
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
    return {
        "prompt_file": knowledge_config.PROMPT_FILE,
        "prompt_path": knowledge_config.PROMPT_PATH,
        "api_key": sensitive_config.API_KEY.get_secret_value(),
        "base_url": sensitive_config.BASE_URL,
        "model": sensitive_config.MODEL_ID,
        "frequency_penalty": knowledge_config.FREQUENCY_PENALTY,
        "n": knowledge_config.N,
        "presence_penalty": knowledge_config.PRESENCE_PENALTY,
        "reasoning_effort": knowledge_config.REASONING_EFFORT,
        "response_format": knowledge_config.RESPONSE_FORMAT,
        "stream": knowledge_config.STREAM,
        "temperature": knowledge_config.TEMPERATURE,
        "top_p": knowledge_config.TOP_P,
        "user": knowledge_config.USER,
        "timeout": knowledge_config.TIMEOUT,
        "retriable_codes": knowledge_config.RETRIABLE_CODES,
        "max_retries": knowledge_config.MAX_RETRIES,
    }


def build_knowledge_chat_input(
    user_query: str,
    chat_kwargs: Mapping[str, Any],
    obs_file_list: list[str] | None = None,
) -> ChatInput:
    """Wrap a knowledge call's inputs into a ``ChatInput`` dict.

    The chat subgraph treats ``obs_file_list`` as a presence flag for
    its upload-context branch, so an empty list and a missing list
    should behave identically. To keep the no-uploads contract honest
    the helper omits the key when the caller passes ``None`` or an
    empty list, and copies a non-empty list so a later mutation in
    the knowledge node never leaks into the subgraph's input.

    Args:
        user_query: The fully-built prompt the calling node assembled
            (the retrieval-context-stitched query in
            ``generate_node``, or the follow-up template in
            ``follow_up_node``).
        chat_kwargs: The flat provider / retry bag from
            :func:`build_knowledge_chat_kwargs`. Copied into the
            return value so later mutations don't leak.
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
    """Project ``ChatOutput.response`` into the dict knowledge nodes expect.

    KnowledgeAgent's nodes assign ``phyto_response`` from the
    ``phyto_chat`` return value (the raw upstream chat-completion
    dict). The chat subgraph stores that dict under
    ``ChatOutput.response``; this helper unwraps it and substitutes
    an empty dict when the upstream returned ``None`` so the
    downstream ``doc_list_payload`` patcher in ``generate_node`` and
    the message-content reader in ``follow_up_node`` still see a
    dict rather than crashing on attribute access.

    Args:
        chat_output: The chat subgraph's final state mapping
            (``ChatOutput``-shaped).

    Returns:
        The raw chat-completion dict, or ``{}`` if the upstream
        returned ``None``.
    """
    response = chat_output.get("response")
    return response if response is not None else {}
