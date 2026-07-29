# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the chat subgraph IO TypedDicts.

Pins the public input/output contract: ``user_query`` is the only
required key on both ``ChatInput`` and ``ChatState`` (the service
bag and OBS list carry defaults), ``response`` is always present on
``ChatOutput`` (consumers do not have to ``in`` it), and ``ChatState``
covers every ``ChatInput`` and ``ChatOutput`` key so a single dict
can flow through every graph node without runtime key surprises.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.chat.state import (
    ChatInput,
    ChatOutput,
    ChatState,
)

pytestmark = pytest.mark.agent


def _required(td: Any) -> frozenset[str]:
    """Return ``td.__required_keys__`` for TypedDict introspection."""
    return td.__required_keys__


def _optional(td: Any) -> frozenset[str]:
    """Return ``td.__optional_keys__`` for TypedDict introspection."""
    return td.__optional_keys__


def test_chat_input_required_keys_are_only_user_query() -> None:
    """``user_query`` is the lone required ChatInput field.

    Pins the handler→graph contract: ``handle_chat_agent`` forwards
    only ``user_query`` as a positional-equivalent kwarg with
    everything else (``obs_file_list`` / ``chat_kwargs``) supplied
    through defaults the graph fills in when missing.
    """
    assert _required(ChatInput) == frozenset({"user_query"})
    assert _optional(ChatInput) == frozenset(
        {
            "obs_file_list",
            "chat_kwargs",
            "conversation_messages",
            "locale",
        }
    )


def test_chat_output_carries_response_key_always() -> None:
    """``response`` is always present on ChatOutput.

    Pins that consumers read ``state["response"]`` without ``in``
    guards; the value may be ``None`` when the upstream provider
    returned no completion, but the key itself is guaranteed.
    """
    assert _required(ChatOutput) == frozenset({"response"})
    assert _optional(ChatOutput) == frozenset()


def test_chat_state_covers_both_input_and_output_keys() -> None:
    """ChatState is the working union for graph nodes.

    Pins that ``ChatState`` carries every ``ChatInput`` key (so node
    payloads can be assembled from the parent's input dict) and
    every ``ChatOutput`` key (so the final state can be projected
    back without re-keying), plus one intermediate slot for upload
    context. Without this property, a node body would have to bounce
    between several TypedDicts depending on which segment of the
    workflow it sits in.
    """
    state_keys = _required(ChatState) | _optional(ChatState)
    input_keys = _required(ChatInput) | _optional(ChatInput)
    output_keys = _required(ChatOutput) | _optional(ChatOutput)
    assert input_keys <= state_keys
    assert output_keys <= state_keys
    assert "upload_context" in state_keys
    assert "conversation_messages" in state_keys


def test_chat_state_required_key_matches_chat_input() -> None:
    """ChatState required-key set mirrors ChatInput's required set.

    Pins the invariant that the parent graph only owes the subgraph
    a ``user_query``; everything else on ``ChatState`` is either
    populated by an upstream node (``upload_context`` /
    ``response``) or defaulted by the prepare-context node
    (``obs_file_list`` / ``chat_kwargs``). If a future refactor
    promotes a state field to ``Required`` without also adding it to
    ``ChatInput``, that drift would mean the parent has to write a
    new key — this test fails first so the rebalance is intentional.
    """
    assert _required(ChatState) == _required(ChatInput), (
        "ChatState required keys must match ChatInput required keys; "
        "otherwise parent graphs would need to write new state fields."
    )
