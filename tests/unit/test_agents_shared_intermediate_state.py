# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for ``agents.shared.intermediate_state``.

``merge_intermediate_state`` lifts every non-``final_response`` field of
the LangGraph final_state into a nested ``phytomni_state`` block on the
returned dict so the HTTP/MCP raw envelope can expose LangGraph
intermediate state without each agent having to inline the merge.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.shared.intermediate_state import (
    merge_intermediate_state,
)

pytestmark = pytest.mark.unit


def test_merges_intermediate_state_into_final_response() -> None:
    """Non-``final_response`` state keys move into ``phytomni_state``.

    The OpenAI canonical fields (``id`` / ``choices`` / ...) survive at
    the top level so existing consumers keep working; the intermediate
    keys are nested under one stable namespace.
    """
    final_state = {
        "user_query": "Os01g0177400",
        "retrieved_docs": [{"file_id": "d1"}],
        "retrieve_context": "ctx",
        "final_response": {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{"message": {"content": "answer"}}],
        },
    }

    merged = merge_intermediate_state(final_state)

    assert merged["id"] == "chatcmpl-1"
    assert merged["choices"][0]["message"]["content"] == "answer"
    assert merged["phytomni_state"] == {
        "user_query": "Os01g0177400",
        "retrieved_docs": [{"file_id": "d1"}],
        "retrieve_context": "ctx",
    }


def test_empty_final_response_returns_just_phytomni_state() -> None:
    """A missing/empty ``final_response`` still exposes the rest of state."""
    final_state = {
        "rewrite_query": "SELECT 1",
        "final_response": {},
    }

    merged = merge_intermediate_state(final_state)

    assert merged == {"phytomni_state": {"rewrite_query": "SELECT 1"}}


def test_excludes_underscored_keys_and_extra_excluded() -> None:
    """Underscore-prefixed keys and caller-excluded keys never leak through.

    ``_internal`` LangGraph bookkeeping (checkpointer hints, debug
    flags) stays private; ``extra_excluded_keys`` lets a caller hide
    fields that duplicate ``final_response`` content.
    """
    final_state = {
        "user_query": "q",
        "_internal_marker": "skip",
        "main_response": {"copy_of_final": True},
        "final_response": {"choices": []},
    }

    merged = merge_intermediate_state(
        final_state, extra_excluded_keys=("main_response",)
    )

    assert merged["phytomni_state"] == {"user_query": "q"}


def test_custom_final_response_key() -> None:
    """The merge target key is overridable for non-default state schemas."""
    final_state = {
        "intermediate": 1,
        "result_dict": {"answer": "x"},
    }

    merged = merge_intermediate_state(
        final_state, final_response_key="result_dict"
    )

    assert merged["answer"] == "x"
    assert merged["phytomni_state"] == {"intermediate": 1}


def test_non_dict_final_response_falls_back_to_empty_base() -> None:
    """A non-dict ``final_response`` is treated as an empty base.

    ``DataAgent`` initializes ``final_response`` to ``None`` before the
    graph runs; if a regression leaves it ``None`` we should still emit
    a coherent dict instead of raising or losing intermediate state.
    """
    final_state = {
        "rewrite_query": "SELECT 1",
        "final_response": None,
    }

    merged = merge_intermediate_state(final_state)

    assert merged == {"phytomni_state": {"rewrite_query": "SELECT 1"}}


def test_surface_keys_promote_subset_and_nest_rest() -> None:
    """Task-style: ``surface_keys`` selects the top-level subset.

    Generic field names (``alpha`` / ``beta`` / ``gamma`` / ``delta``)
    keep the helper contract decoupled from any specific agent's
    surface schema; the production callers (Analyst, design / network /
    research via ``invoke_analysis_agent``) pick their own surface
    sets and the helper just promotes those keys verbatim while
    nesting everything else under ``phytomni_state``.
    """
    final_state = {
        "alpha": "a-value",
        "beta": "b-value",
        "gamma": "g-value",
        "delta": "d-value",
        "extra_one": "x1",
        "extra_two": ["x2-list"],
        "extra_three": {"x3": "nested"},
    }

    surfaced = merge_intermediate_state(
        final_state,
        surface_keys=("alpha", "beta", "gamma", "delta"),
    )

    assert surfaced["alpha"] == "a-value"
    assert surfaced["beta"] == "b-value"
    assert surfaced["gamma"] == "g-value"
    assert surfaced["delta"] == "d-value"
    assert surfaced["phytomni_state"] == {
        "extra_one": "x1",
        "extra_two": ["x2-list"],
        "extra_three": {"x3": "nested"},
    }


def test_surface_keys_fills_missing_with_none() -> None:
    """A requested surface key absent from final_state surfaces as None.

    Mirrors the existing ``{key: final_state.get(key) for key in
    result_keys}`` trim semantics that ``invoke_analysis_agent``
    relies on, so swapping the trim for the helper does not change
    observable shape on the surface side.
    """
    final_state = {"present_key": "present-value", "extra_key": "extra"}

    surfaced = merge_intermediate_state(
        final_state, surface_keys=("present_key", "absent_key")
    )

    assert surfaced["present_key"] == "present-value"
    assert surfaced["absent_key"] is None
    assert surfaced["phytomni_state"] == {"extra_key": "extra"}
