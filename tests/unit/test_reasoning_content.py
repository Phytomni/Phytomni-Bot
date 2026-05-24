# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for conservative reasoning/content normalization."""

from __future__ import annotations

from copy import deepcopy

import pytest

from mcp_server_phytomni.common.reasoning_content import (
    normalize_chat_completion_dict,
    normalize_message_fields,
)

pytestmark = pytest.mark.unit


def test_reasoning_tail_moves_into_blank_content() -> None:
    """A tail after ``</think>`` becomes the display answer."""
    content, reasoning, changed = normalize_message_fields(
        "",
        "<think>step</think>answer",
    )

    assert content == "answer"
    assert reasoning == "step"
    assert changed is True


def test_duplicate_content_is_deduped_from_reasoning_tail() -> None:
    """Duplicate content stays single while reasoning loses the answer tail."""
    content, reasoning, changed = normalize_message_fields(
        "answer",
        "<think>step</think>answer",
    )

    assert content == "answer"
    assert reasoning == "step"
    assert changed is True


def test_normal_reasoner_response_is_noop() -> None:
    """Already split content and reasoning are left untouched."""
    content, reasoning, changed = normalize_message_fields(
        "answer",
        "step without tags",
    )

    assert content == "answer"
    assert reasoning == "step without tags"
    assert changed is False


def test_unclosed_think_tag_is_noop() -> None:
    """Malformed reasoning is not guessed at or rewritten."""
    content, reasoning, changed = normalize_message_fields(
        "",
        "<think>step without close",
    )

    assert content == ""
    assert reasoning == "<think>step without close"
    assert changed is False


def test_content_prefix_think_tag_is_split_when_reasoning_empty() -> None:
    """Support providers that put the full tagged text in content."""
    content, reasoning, changed = normalize_message_fields(
        "<think>step</think>answer",
        None,
    )

    assert content == "answer"
    assert reasoning == "step"
    assert changed is True


def test_non_prefix_literal_think_in_content_is_noop() -> None:
    """A literal tag in ordinary text is not treated as provider leakage."""
    text = "The prompt asked about <think> tags."
    content, reasoning, changed = normalize_message_fields(text, None)

    assert content == text
    assert reasoning is None
    assert changed is False


def test_noop_chat_completion_returns_same_object() -> None:
    """The fast path preserves object identity for clean payloads."""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": "plain reasoning",
                }
            }
        ]
    }

    result = normalize_chat_completion_dict(payload)

    assert result is payload
    assert result == payload


def test_chat_completion_repairs_all_message_choices() -> None:
    """Every choice message is normalized, not just the first choice."""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "<think>a</think>first",
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": "<think>b</think>second",
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": "third",
                    "reasoning_content": "<think>c</think>third",
                }
            },
        ],
        "usage": {"total_tokens": 9},
    }
    original = deepcopy(payload)

    result = normalize_chat_completion_dict(payload)

    assert result is not payload
    assert result["usage"] == {"total_tokens": 9}
    assert result["choices"][0]["message"]["content"] == "first"
    assert result["choices"][0]["message"]["reasoning_content"] == "a"
    assert result["choices"][1]["message"]["content"] == "second"
    assert result["choices"][1]["message"]["reasoning_content"] == "b"
    assert result["choices"][2]["message"]["content"] == "third"
    assert result["choices"][2]["message"]["reasoning_content"] == "c"
    assert payload == original
