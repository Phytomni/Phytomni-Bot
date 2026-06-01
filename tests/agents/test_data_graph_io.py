# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the data subgraph IO TypedDicts.

Pins the public input/output contract: ``user_query`` is the only
required ``DataInput`` key (``is_rewrite`` defaults inside
``arun``), ``DataOutput`` always exposes ``final_response``, and
``DataState`` carries every input + output key plus the
intermediate slots ``retrieve_node`` and ``rewrite_node`` write
between them.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.data.state import (
    DataAgentState,
    DataInput,
    DataOutput,
    DataState,
)

pytestmark = pytest.mark.agent


def _required(td: type) -> frozenset[str]:
    """Return ``td.__required_keys__`` via getattr.

    PEP 705 attaches ``__required_keys__`` / ``__optional_keys__`` to
    every TypedDict class, and pyright + mypy resolve the attribute
    fine. Pylint's inference layer does not model TypedDict
    introspection, so direct attribute access trips E1101 no-member.
    Routing through ``getattr`` hides the access from pylint's static
    analysis without losing runtime semantics.
    """
    return getattr(td, "__required_keys__")


def _optional(td: type) -> frozenset[str]:
    """Return ``td.__optional_keys__`` via getattr (see :func:`_required`)."""
    return getattr(td, "__optional_keys__")


def test_data_input_required_keys_are_only_user_query() -> None:
    """``user_query`` is the lone required DataInput field.

    Pins the parent→subgraph contract: a parent graph mounting the
    data subgraph only owes the ``user_query`` key; ``is_rewrite``
    defaults to ``True`` inside ``arun`` so omitting it keeps the
    retrieve-then-rewrite branch active without extra wiring.
    """
    assert _required(DataInput) == frozenset({"user_query"})
    assert _optional(DataInput) == frozenset({"is_rewrite"})


def test_data_output_carries_final_response_always() -> None:
    """DataOutput always exposes final_response.

    Pins that parent callers read ``state["final_response"]``
    without an ``in`` guard; the value is the SQL database response
    dict produced by ``search_node`` and is guaranteed present even
    when the response itself is empty.
    """
    assert _required(DataOutput) == frozenset({"final_response"})
    assert _optional(DataOutput) == frozenset()


def test_data_state_covers_input_and_output_keys() -> None:
    """DataState is the union of input, intermediate, and output.

    Pins that the working state carries every ``DataInput`` key
    (so ``arun``'s initial state can be assembled from a parent
    input dict) and every ``DataOutput`` key (so the final state
    projects back without re-keying), plus the two intermediate
    slots ``retrieve_prompt`` and ``rewrite_query`` written by
    ``retrieve_node`` and ``rewrite_node``, plus the chat-subgraph
    prep/post relay keys (``chat_payload`` / ``chat_response``) and
    the knowledge-subgraph prep/post relay keys
    (``pending_post_knowledge`` / ``knowledge_payload`` /
    ``knowledge_response``) that the flag-on graph shapes thread
    between split nodes.
    """
    state_keys = _required(DataState) | _optional(DataState)
    input_keys = _required(DataInput) | _optional(DataInput)
    output_keys = _required(DataOutput) | _optional(DataOutput)
    assert input_keys <= state_keys
    assert output_keys <= state_keys
    assert {"retrieve_prompt", "rewrite_query"} <= state_keys
    assert {"chat_payload", "chat_response"} <= state_keys
    assert {
        "pending_post_knowledge",
        "knowledge_payload",
        "knowledge_response",
    } <= state_keys


def test_data_agent_state_alias_matches_data_state() -> None:
    """``DataAgentState`` is a back-compat alias for DataState.

    Pins the alias contract: legacy importers reaching for
    ``mcp_server_phytomni.agents.data.DataAgentState`` (used by the
    ``cast`` sites in ``test_data_agent.py`` /
    ``test_data_agent_nodes.py``) receive the same TypedDict
    object as ``DataState``. Without this property, internal
    annotations on ``DataAgent`` and external tests would silently
    diverge.
    """
    assert DataAgentState is DataState
