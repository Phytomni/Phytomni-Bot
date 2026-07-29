# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Node functions and conditional router for the chat LangGraph.

Three nodes mirror ``phyto_chat`` / ``phyto_chat_with_follow``:
``prepare_context_node`` materialises OBS upload context,
``generate_node`` issues the primary LLM completion, and
``follow_up_node`` generates embedded follow-up questions. Nodes
delegate to ``service.py`` helpers so graph and legacy paths stay
behaviorally equivalent and ``run_phyto_chat_cached`` keys match.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.runtime import Runtime

from ...common.responses import (
    first_message,
    message_content,
    parse_follow_up_questions,
)
from ...runtime.memory import MemoryGraphContext
from ..shared.conversation_messages import build_model_messages
from ..shared.memory_context import memory_context_for_graph
from . import service
from .service import (
    CHAT_CONFIG,
    _apply_locale_instruction,
    _chat_options,
    _query_with_upload_context,
    _run_phyto_chat,
)
from .state import ChatState


def chat_messages_for_state(
    state: ChatState, system_prompt: str
) -> list[dict[str, str]]:
    """Build the provider message list from explicit Chat state."""
    return build_model_messages(
        system_prompt=system_prompt,
        user_query=state["user_query"],
        conversation_messages=state.get("conversation_messages"),
    )


async def prepare_context_node(state: ChatState) -> dict[str, Any]:
    """Materialize OBS upload context into the working state.

    When ``obs_file_list`` is empty, emit a ``None`` upload_context so
    downstream nodes can distinguish "no files attached" from "files
    attached but conversion produced no usable text". When the list
    is non-empty, delegate to the existing
    :func:`agents.chat.service._query_with_upload_context` helper so
    the OBS download + markdown conversion + bounded-context format
    stays exactly aligned with the legacy ``phyto_chat`` code path;
    the rewritten query also replaces ``user_query`` in the working
    state so ``generate_node`` reads the augmented form directly.
    """
    obs_file_list = list(state.get("obs_file_list") or [])
    if not obs_file_list:
        return {"upload_context": None}
    chat_kwargs = dict(state.get("chat_kwargs") or {})
    options = _chat_options(chat_kwargs)
    rewritten = await _query_with_upload_context(
        state["user_query"], obs_file_list, options
    )
    return {"user_query": rewritten, "upload_context": rewritten}


async def generate_node(
    state: ChatState,
    runtime: Runtime[MemoryGraphContext] | None = None,
) -> dict[str, Any]:
    """Issue the primary LLM completion using the chat service helpers.

    Mirrors the message-assembly + ``_run_phyto_chat`` dispatch shape
    inside :func:`agents.chat.service.phyto_chat` so cache keys on
    ``run_phyto_chat_cached`` match between the legacy function path
    and the new graph path. Any ``semaphore`` passed through
    ``chat_kwargs`` still gates the call to limit concurrent LLM
    requests, preserving the legacy concurrency contract.
    """
    chat_kwargs = dict(state.get("chat_kwargs") or {})
    options = _chat_options(chat_kwargs)
    system_prompt = _apply_locale_instruction(
        service.get_prompt(options["prompt_file"], options["prompt_path"]),
        options,
    )
    memory_context = memory_context_for_graph(
        runtime.context if runtime is not None else None
    )
    if memory_context:
        system_prompt = f"{system_prompt}\n\n{memory_context}"
    messages = chat_messages_for_state(state, system_prompt)
    semaphore = chat_kwargs.get("semaphore")
    if semaphore is not None:
        async with semaphore:
            response = await _run_phyto_chat(messages, options)
    else:
        response = await _run_phyto_chat(messages, options)
    return {"response": response}


async def follow_up_node(
    state: ChatState,
    runtime: Runtime[MemoryGraphContext] | None = None,
) -> dict[str, Any]:
    """Generate follow-up questions and embed them into the response.

    Mirrors the second-LLM-call shape inside
    :func:`agents.chat.service.phyto_chat_with_follow`. Mutates the
    primary response dict in place (the assistant message's
    ``follow_up_questions`` key) so consumers reading
    ``state["response"]["choices"][0]["message"]["follow_up_questions"]``
    see the same field they always have.
    """
    response = state.get("response")
    if response is None:
        return {}
    chat_kwargs = dict(state.get("chat_kwargs") or {})
    prompt_file = chat_kwargs.get("prompt_file", CHAT_CONFIG.PROMPT_FILE)
    follow_query = service.get_prompt(
        prompt_file,
        "system/follow_up_questions",
        {
            "user_query": state["user_query"],
            "system_response": message_content(response),
        },
    )
    memory_context = memory_context_for_graph(
        runtime.context if runtime is not None else None
    )
    if memory_context:
        follow_query = f"{memory_context}\n\n{follow_query}"
    follow_kwargs = {**chat_kwargs, "prompt_file": prompt_file}
    if state.get("conversation_messages"):
        follow_kwargs["conversation_messages"] = state["conversation_messages"]
    follow_response = await service.phyto_chat(follow_query, **follow_kwargs)
    follow_list = parse_follow_up_questions(message_content(follow_response))
    message = first_message(response)
    if message is not None:
        message.update({"follow_up_questions": follow_list})
    return {"response": response}


def route_after_generate(
    state: ChatState,
) -> Literal["follow_up_node", "__end__"]:
    """Route to ``follow_up_node`` unless the caller opted out.

    The legacy split between ``phyto_chat`` (no follow-up) and
    ``phyto_chat_with_follow`` (with follow-up) becomes one graph
    plus a ``with_follow_up`` switch threaded through ``chat_kwargs``
    so the same compiled subgraph serves both call sites.

    Returns:
        ``"follow_up_node"`` when ``chat_kwargs.with_follow_up`` is
        absent or truthy (legacy ``phyto_chat_with_follow`` default);
        ``"__end__"`` otherwise (legacy ``phyto_chat`` no-follow path).
    """
    chat_kwargs = state.get("chat_kwargs") or {}
    if chat_kwargs.get("with_follow_up", True):
        return "follow_up_node"
    return "__end__"
